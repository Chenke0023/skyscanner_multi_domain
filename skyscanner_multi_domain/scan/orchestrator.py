"""Scan orchestration: routing, fallback, failure logging, output formatting."""

from __future__ import annotations

import argparse
import inspect
import json
import re
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import Any, Awaitable, Callable, Literal, Optional, Union

from skyscanner_multi_domain.runtime.paths import get_failure_log_file
from skyscanner_multi_domain.diagnostics.attempt_trace import flush as flush_attempt_trace
from skyscanner_multi_domain.models import FlightQuote, RegionConfig
from skyscanner_multi_domain.planning.search_plan import (
    DateCandidate,
    RouteCandidate,
    ScanBatch,
    build_date_candidates,
    build_market_candidates,
    build_scan_batches,
    build_scan_tasks,
    scan_batch_region_codes,
    rank_region_codes,
)
from skyscanner_multi_domain.geo.regions import REGIONS, get_selected_regions

FAILURE_LOG_TEXT_LIMIT = 12000

# ── FailureClass taxonomy ─────────────────────────────────────────────────────

FailureClass = Literal[
    "network",         # transport-level error (timeout, DNS, connection refused)
    "loading",         # page still rendering, price not yet visible
    "challenge_px",   # PerimeterX captcha — stop auto-retry, mark manual
    "challenge_cf",    # Cloudflare challenge — longer wait / browser session
    "challenge_other", # reCAPTCHA / hCaptcha / Turnstile — captcha solver
    "parse",           # page visible but price not found in parser
    "empty_shell",     # page body nearly empty / near-blank shell
    "browser_missing", # CDP transport: no browser tab for domain
    "transport_error", # opencli / page eval internal error
    "other",           # everything else
]


class FailureAction(Enum):
    NONE = "none"           # accept failure, stop
    RETRY_SAME = "retry_same"       # retry with same transport, backoff
    RETRY_BROWSER = "retry_browser"   # switch to CDP/browser
    WAIT_RENDER = "wait_render"   # retry same transport with longer wait
    MANUAL_SESSION = "manual_session"  # mark needs human verification
    SKIP = "skip"           # skip silently (already handled elsewhere)


_STATUS_TO_CLASS: dict[str, FailureClass] = {
    # network
    "scrapling_failed":      "network",
    "opencli_error":         "network",
    "opencli_failed":        "network",
    "captcha_solve_failed":  "transport_error",
    # loading
    "page_loading":          "loading",
    "opencli_timeout":       "loading",
    # challenge
    "px_challenge":          "challenge_px",
    "page_challenge":        "challenge_cf",
    "scrapling_unavailable": "transport_error",
    # parse
    "page_parse_failed":     "parse",
    "scrapling_parse_failed": "parse",
    # shell
    "page_missing":          "browser_missing",
    "page_missing_ws":       "browser_missing",
    "page_eval_error":       "transport_error",
}


def classify_failure(status: str) -> FailureClass:
    return _STATUS_TO_CLASS.get(status, "other")


def failure_action(failure_class: FailureClass) -> FailureAction:
    return {
        "network":         FailureAction.RETRY_BROWSER,
        "loading":         FailureAction.WAIT_RENDER,
        "challenge_px":    FailureAction.MANUAL_SESSION,
        "challenge_cf":    FailureAction.RETRY_BROWSER,
        "challenge_other": FailureAction.RETRY_SAME,
        "parse":           FailureAction.RETRY_BROWSER,
        "empty_shell":     FailureAction.RETRY_BROWSER,
        "browser_missing": FailureAction.NONE,
        "transport_error": FailureAction.RETRY_BROWSER,
        "other":           FailureAction.NONE,
    }.get(failure_class, FailureAction.NONE)


def can_fallback_to_browser(status: str) -> bool:
    """Return True if this status should trigger browser (CDP) fallback.

    WAIT_RENDER is deliberately excluded — those failures retry within the
    same transport with longer wait first.
    """
    cls = classify_failure(status)
    action = failure_action(cls)
    return action == FailureAction.RETRY_BROWSER


def should_retry_wait_render(status: str) -> bool:
    """Return True if this status indicates the page is still loading and
    should be retried with longer wait in the same transport."""
    cls = classify_failure(status)
    action = failure_action(cls)
    return action == FailureAction.WAIT_RENDER


# Legacy alias — keep for backward compatibility with tests/imports
SCRAPLING_FALLBACK_STATUSES: set[str] = {
    status
    for status, cls in _STATUS_TO_CLASS.items()
    if failure_action(cls) == FailureAction.RETRY_BROWSER
}

ScanProgressCallback = Callable[[dict[str, Any]], Union[Awaitable[None], None]]


def build_plan_progress_payload(
    *,
    stage: str,
    quotes: list[FlightQuote],
    completed_regions: list[str],
    batch: ScanBatch | None,
    batch_index: int | None,
    batch_count: int | None,
    total_tasks: int | None,
    is_final: bool = False,
    used_cached_preview: bool = False,
    batch_completed: bool = False,
) -> dict[str, Any]:
    return {
        "stage": stage,
        "quotes": quotes_to_dicts(quotes),
        "completed_regions": list(completed_regions),
        "is_final": bool(is_final),
        "used_cached_preview": bool(used_cached_preview),
        "plan_phase": batch.phase if batch is not None else None,
        "active_plan_phase": batch.phase if batch is not None else None,
        "plan_phase_label": batch.reason if batch is not None else None,
        "plan_batch_id": batch_index,
        "plan_batch_count": batch_count,
        "plan_batch_reason": batch.reason if batch is not None else None,
        "plan_batch_completed": bool(batch_completed),
        "plan_tasks_total": total_tasks,
        "plan_tasks_in_batch": len(batch.tasks) if batch is not None else None,
    }


def parse_date(date_str: str) -> tuple[datetime, str, str]:
    parsed = datetime.strptime(date_str, "%Y-%m-%d")
    return parsed, parsed.strftime("%Y-%m-%d"), parsed.strftime("%y%m%d")


def build_route_key(
    origin: str, destination: str, travel_date: str, return_date: str | None = None
) -> str:
    token = travel_date.replace("-", "")
    if return_date:
        token = f"{token}_rt{return_date.replace('-', '')}"
    return f"{origin}_{destination}_{token}"


def build_search_url(
    region: RegionConfig,
    origin: str,
    destination: str,
    travel_date: str,
    return_date: str | None = None,
) -> str:
    departure_date, _, short_date = parse_date(travel_date)
    path = f"{region.domain}/transport/flights/{origin.lower()}/{destination.lower()}/{short_date}/"
    rtn = "0"
    if return_date:
        inbound_date, _, return_short_date = parse_date(return_date)
        if inbound_date < departure_date:
            raise ValueError("return_date must be >= travel_date")
        path = f"{path}{return_short_date}/"
        rtn = "1"
    return (
        f"{path}?adultsv2=1&cabinclass=economy&childrenv2=&ref=home&rtn={rtn}"
        "&preferdirects=false&outboundaltsenabled=false&inboundaltsenabled=false"
    )


def _safe_failure_token(value: str) -> str:
    normalized = re.sub(r"[^A-Za-z0-9._-]+", "-", value.strip())
    normalized = normalized.strip("-._")
    return normalized or "unknown"


def _persist_failure_log(
    quote: FlightQuote,
    *,
    transport: str,
    route_key: str,
    page_text: str = "",
    extra: Optional[dict[str, Any]] = None,
    log_path: Optional[Path] = None,
) -> FlightQuote:
    if quote.price is not None:
        return quote
    if quote.debug_log_path:
        return quote

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    filename = (
        f"{timestamp}_{_safe_failure_token(route_key)}_{_safe_failure_token(quote.region)}_"
        f"{_safe_failure_token(transport)}_{_safe_failure_token(quote.status)}.log"
    )
    target = log_path or get_failure_log_file(filename)
    excerpt = (page_text or "").strip()
    if len(excerpt) > FAILURE_LOG_TEXT_LIMIT:
        excerpt = excerpt[:FAILURE_LOG_TEXT_LIMIT] + "\n...[truncated]"

    merged_extra: dict[str, Any] = dict(extra or {})
    if excerpt:
        try:
            from skyscanner_multi_domain.parsing.page_parser import (
                extract_page_quote_with_diagnostics,
                page_parse_diagnostics_to_dict,
            )
            from skyscanner_multi_domain.geo.regions import REGIONS

            region = REGIONS.get(
                quote.region,
                RegionConfig(
                    quote.region,
                    quote.region,
                    quote.domain,
                    merged_extra.get("locale", ""),
                    quote.currency or "",
                ),
            )
            _, diagnostics = extract_page_quote_with_diagnostics(
                region,
                quote.source_url,
                excerpt,
            )
            merged_extra.setdefault(
                "parser_snapshot",
                page_parse_diagnostics_to_dict(diagnostics),
            )
        except Exception:
            pass

    # Add failure class to extra for richer failure logs
    failure_class = classify_failure(quote.status)
    merged_extra.setdefault("failure_class", failure_class)
    action = failure_action(failure_class)
    merged_extra.setdefault("failure_action", action.value)

    sections = [
        f"timestamp: {datetime.now().isoformat(timespec='seconds')}",
        f"transport: {transport}",
        f"route: {route_key}",
        f"region: {quote.region}",
        f"domain: {quote.domain}",
        f"status: {quote.status}",
        f"failure_class: {failure_class}",
        f"failure_action: {action.value}",
        f"error: {quote.error or '-'}",
        f"source_url: {quote.source_url}",
    ]
    if merged_extra:
        sections.append(
            "extra: " + json.dumps(merged_extra, ensure_ascii=False, sort_keys=True)
        )
    sections.append("")
    sections.append("--- page_text_excerpt ---")
    sections.append(excerpt or "(empty)")
    target.write_text("\n".join(sections) + "\n", encoding="utf-8")
    quote.debug_log_path = str(target)
    return quote


def print_quotes(quotes: list[FlightQuote]) -> None:
    print("\n" + "=" * 96)
    print(f"{'地区':<8}{'价格':<14}{'货币':<8}{'状态':<18}{'来源':<48}")
    print("-" * 96)
    for quote in quotes:
        region_name = REGIONS.get(
            quote.region, RegionConfig(quote.region, quote.region, quote.domain, "", "")
        ).name
        price_text = f"{quote.price:,.2f}" if quote.price is not None else "-"
        print(
            f"{region_name:<8}{price_text:<14}{(quote.currency or '-'): <8}"
            f"{quote.status:<18}{quote.source_url[:48]:<48}"
        )
    print("=" * 96)

    failures = [quote for quote in quotes if quote.price is None]
    if failures:
        print("\n失败详情:")
        for quote in failures:
            fc = classify_failure(quote.status)
            print(f"[{quote.region}] {quote.error or quote.status} (class={fc})")


def quotes_to_dicts(quotes: list[FlightQuote]) -> list[dict[str, Any]]:
    return [
        {
            "region": quote.region,
            "region_name": REGIONS.get(
                quote.region,
                RegionConfig(quote.region, quote.region, quote.domain, "", ""),
            ).name,
            "domain": quote.domain,
            "price": quote.price,
            "currency": quote.currency,
            "source_url": quote.source_url,
            "status": quote.status,
            "failure_class": classify_failure(quote.status),
            "price_path": quote.price_path,
            "best_price": quote.best_price,
            "best_price_path": quote.best_price_path,
            "cheapest_price": quote.cheapest_price,
            "cheapest_price_path": quote.cheapest_price_path,
            "error": quote.error,
            "source_kind": quote.source_kind,
            "plan_rank": quote.plan_rank,
            "plan_score": quote.plan_score,
            "plan_phase": quote.plan_phase,
            "plan_reason": quote.plan_reason,
            "route_rank": quote.route_rank,
            "date_rank": quote.date_rank,
            "market_rank": quote.market_rank,
            "confidence": quote.confidence,
            "price_source": quote.price_source,
            "evidence_text": quote.evidence_text,
            "parser_warnings": list(quote.parser_warnings or []),
            "fallback_attempts": list(quote.fallback_attempts or []),
            "tab_open_count": quote.tab_open_count,
            "tab_close_count": quote.tab_close_count,
            "reused_tab_count": quote.reused_tab_count,
            "extract_attempt_count": quote.extract_attempt_count,
            "max_chunk_size_used": quote.max_chunk_size_used,
            "progressive_wait_used": quote.progressive_wait_used,
        }
        for quote in quotes
    ]



async def run_page_scan(
    origin: str,
    destination: str,
    date: str,
    region_codes: list[str],
    return_date: str | None = None,
    page_wait: int = 8,
    timeout: int = 30,
    transport: str = "opencli",
    on_region_start: Callable[[RegionConfig], None] | None = None,
    on_region_complete: Callable[[RegionConfig, FlightQuote], None] | None = None,
    scan_mode: str = "full_scan",
    rerun_scope: str = "all",
    selected_region_codes: list[str] | None = None,
    region_concurrency: int = 3,
    query_payload: dict[str, Any] | None = None,
    history_store: Any | None = None,
    on_progress: ScanProgressCallback | None = None,
    allow_browser_fallback: bool = True,
    fetch_pipeline: str = "balanced",
) -> list[FlightQuote]:
    from skyscanner_multi_domain.transports.cdp import compare_via_pages, detect_cdp_version, ensure_cdp_ready
    from skyscanner_multi_domain.transports.scrapling import compare_via_scrapling
    from skyscanner_multi_domain.transports.opencli import compare_via_opencli
    from skyscanner_multi_domain.planning.date_window import format_trip_date_label
    from skyscanner_multi_domain.scan.history import ScanHistoryStore, get_quotes_for_trip_label, select_preview_region_batches
    from skyscanner_multi_domain.models import new_run_id

    run_id = new_run_id()

    try:
        normalized_rerun_scope = (rerun_scope or "all").lower()
        normalized_scan_mode = (scan_mode or "full_scan").lower()
        normalized_selected_codes = {
            code.strip().upper()
            for code in (selected_region_codes or [])
            if code and code.strip()
        }
        selected_regions = get_selected_regions(region_codes)
        if normalized_rerun_scope in {"failed_only", "selected_regions"} and normalized_selected_codes:
            selected_regions = [
                region for region in selected_regions if region.code in normalized_selected_codes
            ]
        if not selected_regions:
            flush_attempt_trace()
            return []

        resolved_history_store = None
        latest_record_for_plan = None
        if query_payload is not None:
            try:
                from skyscanner_multi_domain.scan.history import ScanHistoryStore

                resolved_history_store = history_store or ScanHistoryStore()
                latest_record_for_plan = resolved_history_store.get_latest_scan(query_payload)
            except Exception:
                latest_record_for_plan = None
        identity = query_payload.get("identity") if isinstance(query_payload, dict) else {}
        identity = identity if isinstance(identity, dict) else {}
        origin_country_hint = str(identity.get("origin_country") or "")
        destination_country_hint = str(identity.get("destination_country") or "")
        origin_code_hint = str(identity.get("origin_code") or "")
        destination_code_hint = str(identity.get("destination_code") or "")
        if not origin_country_hint and origin_code_hint.endswith("_ANY"):
            origin_country_hint = origin_code_hint.removesuffix("_ANY")
        if not destination_country_hint and destination_code_hint.endswith("_ANY"):
            destination_country_hint = destination_code_hint.removesuffix("_ANY")
        ranked_region_codes = rank_region_codes(
            [region.code for region in selected_regions],
            latest_record_for_plan.rows_by_date if latest_record_for_plan is not None else None,
            origin_country=origin_country_hint,
            destination_country=destination_country_hint,
            manual_region_codes=[
                str(code)
                for code in identity.get("manual_regions", [])
                if isinstance(code, str)
            ],
        )
        selected_region_by_code = {region.code: region for region in selected_regions}
        selected_regions = [
            selected_region_by_code[code]
            for code in ranked_region_codes
            if code in selected_region_by_code
        ]
        plan_route = RouteCandidate(
            origin_code=origin,
            destination_code=destination,
            origin_label=origin,
            destination_label=destination,
            rank=1,
            reason="当前扫描航段",
            confidence=1.0,
            score=1.0,
            score_breakdown={"current_route": 1.0},
        )
        plan_dates = build_date_candidates(
            str(identity.get("date") or date),
            str(identity.get("return_date") or return_date or "") or None,
            int(identity.get("date_window_days") or 0),
        )
        plan_date_by_label = {
            (candidate.depart_date, candidate.return_date): (index + 1, candidate)
            for index, candidate in enumerate(plan_dates)
        }
        plan_date_rank, plan_date = plan_date_by_label.get(
            (date, return_date),
            (
                1,
                DateCandidate(
                    depart_date=date,
                    return_date=return_date,
                    offset=0,
                    phase="anchor",
                    reason="当前扫描日期",
                    score=1.0,
                    score_breakdown={"current_date": 1.0},
                ),
            ),
        )
        plan_markets = build_market_candidates(
            [region.code for region in selected_regions],
            latest_record_for_plan.rows_by_date if latest_record_for_plan is not None else None,
            origin_country=origin_country_hint,
            destination_country=destination_country_hint,
            manual_region_codes=[
                str(code)
                for code in identity.get("manual_regions", [])
                if isinstance(code, str)
            ],
        )
        plan_tasks = build_scan_tasks([plan_route], plan_dates, plan_markets)
        execution_plan_tasks = build_scan_tasks([plan_route], [plan_date], plan_markets)
        plan_batches = build_scan_batches(execution_plan_tasks)
        plan_task_by_key = {
            (task.date.depart_date, task.date.return_date, task.market.region_code): (index + 1, task)
            for index, task in enumerate(plan_tasks)
        }

        def apply_plan_metadata(quotes: list[FlightQuote]) -> list[FlightQuote]:
            for quote in quotes:
                ranked_task = plan_task_by_key.get((date, return_date, quote.region))
                if ranked_task is None:
                    continue
                plan_rank, task = ranked_task
                quote.plan_rank = plan_rank
                quote.plan_score = task.priority
                quote.plan_phase = task.phase
                quote.plan_reason = task.reason
                quote.route_rank = task.route.rank
                quote.date_rank = plan_date_rank
                quote.market_rank = task.market.rank
            return quotes

        async def emit_progress(
            *,
            stage: str,
            quotes: list[FlightQuote],
            completed_regions: list[str],
            batch: ScanBatch | None = None,
            batch_index: int | None = None,
            batch_count: int | None = None,
            batch_completed: bool = False,
            is_final: bool = False,
            used_cached_preview: bool = False,
        ) -> None:
            if on_progress is None:
                return
            result = on_progress(
                build_plan_progress_payload(
                    stage=stage,
                    quotes=quotes,
                    completed_regions=completed_regions,
                    batch=batch,
                    batch_index=batch_index,
                    batch_count=batch_count,
                    total_tasks=len(execution_plan_tasks),
                    is_final=is_final,
                    used_cached_preview=used_cached_preview,
                    batch_completed=batch_completed,
                )
            )
            if inspect.isawaitable(result):
                await result

        def merge_quotes_by_region(
            base_quotes: list[FlightQuote],
            updates: list[FlightQuote],
        ) -> list[FlightQuote]:
            ordered_regions: list[str] = []
            merged: dict[str, FlightQuote] = {}
            for quote in base_quotes:
                if quote.region not in merged:
                    ordered_regions.append(quote.region)
                merged[quote.region] = quote
            for quote in updates:
                if quote.region not in merged:
                    ordered_regions.append(quote.region)
                merged[quote.region] = quote
            return [merged[region_code] for region_code in ordered_regions]

        normalized_transport = (transport or "opencli").lower()
        if normalized_transport == "page":
            ensure_cdp_ready(
                start_url=build_search_url(
                    selected_regions[0], origin, destination, date, return_date
                )
            )

        args = argparse.Namespace(
            origin=origin,
            destination=destination,
            date=date,
            return_date=return_date,
            page_wait=page_wait,
            timeout=timeout,
        )

        async def run_scrapling_pass(
            batch_regions: list[RegionConfig],
            *,
            enable_browser_fallback: bool,
            on_region_complete: Callable[[RegionConfig, FlightQuote], None] | None = None,
        ) -> list[FlightQuote]:
            quotes = await compare_via_scrapling(
                args,
                batch_regions,
                persist_failures=False,
                on_region_start=on_region_start,
                on_region_complete=on_region_complete,
                region_concurrency=max(int(region_concurrency), 1),
                run_id=run_id,
                fetch_pipeline=fetch_pipeline,
            )

            # WAIT_RENDER retry logic... (existing)
            wait_render_regions = [
                region
                for region, quote in zip(batch_regions, quotes)
                if quote.price is None and should_retry_wait_render(quote.status)
            ]
            if wait_render_regions:
                longer_args = argparse.Namespace(**vars(args))
                longer_args.page_wait = max(args.page_wait * 3, 30)
                wait_quotes = await compare_via_scrapling(
                    longer_args,
                    wait_render_regions,
                    persist_failures=False,
                    on_region_start=on_region_start,
                    on_region_complete=on_region_complete,
                    region_concurrency=max(int(region_concurrency), 1),
                    run_id=run_id,
                    fetch_pipeline=fetch_pipeline,
                )
                wait_by_region = {quote.region: quote for quote in wait_quotes}
                merged: list[FlightQuote] = []
                for quote in quotes:
                    replacement = wait_by_region.get(quote.region)
                    if replacement is not None:
                        # Merge wait render attempt
                        if replacement.price is None:
                            quote.fallback_attempts.append({
                                "transport": "scrapling_wait",
                                "status": replacement.status,
                                "failure_class": classify_failure(replacement.status),
                                "error": replacement.error,
                            })
                            merged.append(quote)
                        else:
                            merged.append(replacement)
                    else:
                        merged.append(quote)
                quotes = merged

            # Browser fallback: only if enabled and only for RETRY_BROWSER failures
            fallback_regions = [
                region
                for region, quote in zip(batch_regions, quotes)
                if quote.price is None and can_fallback_to_browser(quote.status)
            ]
            if fallback_regions and enable_browser_fallback:
                has_scrapling_success = any(quote.price is not None for quote in quotes)
                cdp_info = detect_cdp_version()
                if cdp_info or not has_scrapling_success:
                    if not cdp_info:
                        ensure_cdp_ready(
                            start_url=build_search_url(
                                fallback_regions[0], origin, destination, date, return_date
                            )
                        )
                    fallback_quotes = await compare_via_pages(
                        args, fallback_regions, persist_failures=False, run_id=run_id
                    )
                    fallback_by_region = {
                        quote.region: quote for quote in fallback_quotes if quote is not None
                    }
                    merged_quotes: list[FlightQuote] = []
                    for quote in quotes:
                        fallback_quote = fallback_by_region.get(quote.region)
                        if fallback_quote:
                            if fallback_quote.price is not None:
                                # Preserve primary failure diagnostic even on success
                                fallback_quote.fallback_attempts = [
                                    {
                                        "transport": "scrapling_primary",
                                        "status": quote.status,
                                        "failure_class": classify_failure(quote.status),
                                        "error": quote.error,
                                    },
                                    *list(fallback_quote.fallback_attempts or []),
                                ]
                                merged_quotes.append(fallback_quote)
                            else:
                                # Merge fallback diagnostics even if it failed (v1.2 goal #3)
                                quote.fallback_attempts.append({
                                    "transport": "page_fallback",
                                    "status": fallback_quote.status,
                                    "failure_class": classify_failure(fallback_quote.status),
                                    "error": fallback_quote.error,
                                })
                                merged_quotes.append(quote)
                        else:
                            merged_quotes.append(quote)
                    quotes = merged_quotes
            return quotes

        async def run_opencli_pass(
            batch_regions: list[RegionConfig],
            *,
            enable_fallbacks: bool,
            on_region_complete: Callable[[RegionConfig, FlightQuote], None] | None = None,
        ) -> list[FlightQuote]:
            quotes = await compare_via_opencli(
                args,
                batch_regions,
                persist_failures=False,
                build_search_url=build_search_url,
                on_region_start=on_region_start,
                on_region_complete=on_region_complete,
                region_concurrency=max(int(region_concurrency), 1),
                run_id=run_id,
            )

            if not enable_fallbacks:
                return quotes

            page_regions = [
                region
                for region, quote in zip(batch_regions, quotes)
                if quote.price is None and can_fallback_to_browser(quote.status)
            ]
            if page_regions:
                cdp_info = detect_cdp_version()
                if not cdp_info:
                    ensure_cdp_ready(
                        start_url=build_search_url(
                            page_regions[0], origin, destination, date, return_date
                        )
                    )
                page_quotes = await compare_via_pages(
                    args, page_regions, persist_failures=False, run_id=run_id
                )
                page_by_region = {quote.region: quote for quote in page_quotes}
                
                new_quotes = []
                for quote in quotes:
                    page_quote = page_by_region.get(quote.region)
                    if page_quote:
                        if page_quote.price is not None:
                            # Preserve primary failure diagnostic even on success
                            page_quote.fallback_attempts = [
                                {
                                    "transport": "opencli_primary",
                                    "status": quote.status,
                                    "failure_class": classify_failure(quote.status),
                                    "error": quote.error,
                                },
                                *list(page_quote.fallback_attempts or []),
                            ]
                            new_quotes.append(page_quote)
                        else:
                            quote.fallback_attempts.append({
                                "transport": "page_fallback",
                                "status": page_quote.status,
                                "failure_class": classify_failure(page_quote.status),
                                "error": page_quote.error,
                            })
                            new_quotes.append(quote)
                    else:
                        new_quotes.append(quote)
                quotes = new_quotes

            legacy_regions = [
                region
                for region, quote in zip(batch_regions, quotes)
                if quote.price is None and quote.status != "px_challenge" and quote.status != "opencli_not_attempted"
            ]
            if legacy_regions:
                legacy_quotes = await compare_via_scrapling(
                    args,
                    legacy_regions,
                    persist_failures=False,
                    on_region_start=on_region_start,
                    on_region_complete=on_region_complete,
                    region_concurrency=max(int(region_concurrency), 1),
                    run_id=run_id,
                    fetch_pipeline=fetch_pipeline,
                )
                legacy_by_region = {quote.region: quote for quote in legacy_quotes}
                
                new_quotes = []
                for quote in quotes:
                    legacy_quote = legacy_by_region.get(quote.region)
                    if legacy_quote:
                        if legacy_quote.price is not None:
                            # Preserve primary failure diagnostic even on success
                            legacy_quote.fallback_attempts = [
                                {
                                    "transport": "opencli_primary",
                                    "status": quote.status,
                                    "failure_class": classify_failure(quote.status),
                                    "error": quote.error,
                                },
                                *list(legacy_quote.fallback_attempts or []),
                            ]
                            new_quotes.append(legacy_quote)
                        else:
                            quote.fallback_attempts.append({
                                "transport": "scrapling_fallback",
                                "status": legacy_quote.status,
                                "failure_class": classify_failure(legacy_quote.status),
                                "error": legacy_quote.error,
                            })
                            new_quotes.append(quote)
                    else:
                        new_quotes.append(quote)
                quotes = new_quotes
            return quotes

        if normalized_transport == "page":
            quotes = await compare_via_pages(args, selected_regions, run_id=run_id)
            quotes = apply_plan_metadata(quotes)
            await emit_progress(
                stage="final",
                quotes=quotes,
                completed_regions=[region.code for region in selected_regions],
                is_final=True,
            )
        elif normalized_transport == "scrapling":
            if normalized_scan_mode == "preview_first":
                latest_record = (
                    latest_record_for_plan
                    if resolved_history_store is not None and query_payload is not None
                    else None
                )
                preview_record = (
                    resolved_history_store.get_cached_preview(query_payload)
                    if resolved_history_store is not None and query_payload is not None
                    else None
                )
                trip_label = format_trip_date_label(date, return_date)
                if preview_record is not None:
                    preview_quotes = [
                        FlightQuote(
                            region=str(quote.get("region") or ""),
                            domain=str(quote.get("domain") or ""),
                            price=quote.get("price"),
                            currency=quote.get("currency"),
                            source_url=str(quote.get("source_url") or ""),
                            status=str(quote.get("status") or ""),
                            price_path=quote.get("price_path"),
                            best_price=quote.get("best_price"),
                            best_price_path=quote.get("best_price_path"),
                            cheapest_price=quote.get("cheapest_price"),
                            cheapest_price_path=quote.get("cheapest_price_path"),
                            error=quote.get("error"),
                            source_kind="cached",
                        )
                        for quote in get_quotes_for_trip_label(preview_record.quotes_by_date, trip_label)
                    ]
                    if preview_quotes:
                        await emit_progress(
                            stage="preview_cache",
                            quotes=preview_quotes,
                            completed_regions=[],
                            used_cached_preview=True,
                        )

                first_batch_codes, remaining_region_codes = select_preview_region_batches(
                    [region.code for region in selected_regions],
                    latest_record.rows_by_date if latest_record is not None else None,
                    first_batch_size=max(int(region_concurrency), 1),
                )
                region_by_code = {region.code: region for region in selected_regions}
                batches: list[list[RegionConfig]] = []
                if first_batch_codes:
                    batches.append(
                        [region_by_code[code] for code in first_batch_codes if code in region_by_code]
                    )
                chunk_size = max(int(region_concurrency), 1)
                for index in range(0, len(remaining_region_codes), chunk_size):
                    chunk_codes = remaining_region_codes[index : index + chunk_size]
                    batches.append(
                        [region_by_code[code] for code in chunk_codes if code in region_by_code]
                    )

                merged_quotes: list[FlightQuote] = []

                async def on_region_complete_wrapper(region: RegionConfig, quote: FlightQuote) -> None:
                    nonlocal merged_quotes
                    apply_plan_metadata([quote])
                    if on_region_complete is not None:
                        on_region_complete(region, quote)
                    merged_quotes = merge_quotes_by_region(merged_quotes, [quote])
                    await emit_progress(
                        stage="region_update",
                        quotes=list(merged_quotes),
                        completed_regions=[q.region for q in merged_quotes],
                        used_cached_preview=preview_record is not None,
                    )

                for batch_index, batch_regions in enumerate(batches):
                    if not batch_regions:
                        continue
                    batch_quotes = await run_scrapling_pass(
                        batch_regions,
                        enable_browser_fallback=False,
                        on_region_complete=on_region_complete_wrapper,
                    )
                    batch_quotes = apply_plan_metadata(batch_quotes)
                    merged_quotes = merge_quotes_by_region(merged_quotes, batch_quotes)
                    await emit_progress(
                        stage="quick_live" if batch_index == 0 else "background_live",
                        quotes=merged_quotes,
                        completed_regions=[quote.region for quote in merged_quotes],
                        used_cached_preview=preview_record is not None,
                    )

                    # Per-batch browser fallback: go directly to CDP for failed regions.
                    # No re-run through Scrapling — these markets already failed there.
                    if allow_browser_fallback:
                        batch_failed = [
                            region for region in batch_regions
                            if any(
                                quote.region == region.code
                                and quote.price is None
                                and can_fallback_to_browser(quote.status)
                                for quote in batch_quotes
                            )
                        ]
                        if batch_failed:
                            cdp_info = detect_cdp_version()
                            if not cdp_info:
                                ensure_cdp_ready(
                                    start_url=build_search_url(
                                        batch_failed[0], origin, destination, date, return_date
                                    )
                                )
                            batch_fallback_quotes = await compare_via_pages(
                                args, batch_failed, persist_failures=False, run_id=run_id
                            )
                            batch_fallback_quotes = apply_plan_metadata(batch_fallback_quotes)
                            merged_quotes = merge_quotes_by_region(merged_quotes, batch_fallback_quotes)
                            await emit_progress(
                                stage="background_live",
                                quotes=merged_quotes,
                                completed_regions=[quote.region for quote in merged_quotes],
                                used_cached_preview=preview_record is not None,
                            )
                quotes = merged_quotes
            else:
                quotes = await run_scrapling_pass(
                    selected_regions,
                    enable_browser_fallback=allow_browser_fallback,
                )
                quotes = apply_plan_metadata(quotes)
            await emit_progress(
                stage="final",
                quotes=quotes,
                completed_regions=[quote.region for quote in quotes],
                is_final=True,
            )
        elif normalized_transport == "opencli":
            merged_quotes: list[FlightQuote] = []
            completed_regions: list[str] = []
            scanned_region_codes: set[str] = set()
            region_by_code = {region.code: region for region in selected_regions}
            selected_region_codes_set = set(region_by_code)

            for batch_index, batch in enumerate(plan_batches, start=1):
                batch_region_codes = scan_batch_region_codes(batch)
                batch_regions = [
                    region_by_code[code]
                    for code in batch_region_codes
                    if code in region_by_code and code not in scanned_region_codes
                ]
                if not batch_regions:
                    continue
                await emit_progress(
                    stage="plan_batch_start",
                    quotes=apply_plan_metadata(list(merged_quotes)),
                    completed_regions=completed_regions,
                    batch=batch,
                    batch_index=batch_index,
                    batch_count=len(plan_batches),
                    batch_completed=False,
                )
                batch_quotes = await run_opencli_pass(
                    batch_regions,
                    enable_fallbacks=allow_browser_fallback,
                )
                batch_quotes = apply_plan_metadata(batch_quotes)
                merged_quotes = merge_quotes_by_region(merged_quotes, batch_quotes)
                for quote in batch_quotes:
                    # Point 1: opencli_not_attempted must not be treated as successfully scanned in batches.
                    # This allows them to be picked up in the remaining_regions (deep補掃) pass.
                    if quote.status != "opencli_not_attempted":
                        scanned_region_codes.add(quote.region)
                        if quote.region not in completed_regions:
                            completed_regions.append(quote.region)
                await emit_progress(
                    stage="plan_batch_complete",
                    quotes=apply_plan_metadata(list(merged_quotes)),
                    completed_regions=completed_regions,
                    batch=batch,
                    batch_index=batch_index,
                    batch_count=len(plan_batches),
                    batch_completed=True,
                )

            remaining_regions = [
                region
                for region in selected_regions
                if region.code not in scanned_region_codes
            ]
            if remaining_regions:
                fallback_batch = ScanBatch(
                    batch_id=len(plan_batches) + 1,
                    phase="deep",
                    tasks=[],
                    reason="补扫未覆盖市场，保持完整扫描集合",
                )
                await emit_progress(
                    stage="plan_batch_start",
                    quotes=apply_plan_metadata(list(merged_quotes)),
                    completed_regions=completed_regions,
                    batch=fallback_batch,
                    batch_index=fallback_batch.batch_id,
                    batch_count=len(plan_batches) + 1,
                    batch_completed=False,
                )
                remaining_quotes = await run_opencli_pass(
                    remaining_regions,
                    enable_fallbacks=allow_browser_fallback,
                )
                remaining_quotes = apply_plan_metadata(remaining_quotes)
                merged_quotes = merge_quotes_by_region(merged_quotes, remaining_quotes)
                for quote in remaining_quotes:
                    scanned_region_codes.add(quote.region)
                    if quote.region not in completed_regions:
                        completed_regions.append(quote.region)
                await emit_progress(
                    stage="plan_batch_complete",
                    quotes=apply_plan_metadata(list(merged_quotes)),
                    completed_regions=completed_regions,
                    batch=fallback_batch,
                    batch_index=fallback_batch.batch_id,
                    batch_count=len(plan_batches) + 1,
                    batch_completed=True,
                )

            missing_regions = selected_region_codes_set - scanned_region_codes
            if missing_regions:
                raise RuntimeError(f"SearchPlan batch scan missed regions: {sorted(missing_regions)}")
            quotes = apply_plan_metadata(merged_quotes)
            await emit_progress(
                stage="final",
                quotes=quotes,
                completed_regions=completed_regions,
                is_final=True,
            )
        else:
            quotes = [
                FlightQuote(
                    region=region.code,
                    domain=region.domain,
                    price=None,
                    currency=region.currency,
                    source_url=build_search_url(
                        region, origin, destination, date, return_date
                    ),
                    status="invalid_transport",
                    error=f"未知 transport: {transport}（可选: scrapling, page, opencli）",
                )
                for region in selected_regions
            ]
            quotes = apply_plan_metadata(quotes)
            await emit_progress(
                stage="final",
                quotes=quotes,
                completed_regions=[region.code for region in selected_regions],
                is_final=True,
            )

        route_key = build_route_key(origin, destination, date, return_date)
        for quote in quotes:
            if quote.price is None and not quote.debug_log_path:
                _persist_failure_log(
                    quote,
                    transport=normalized_transport,
                    route_key=route_key,
                )

        quotes.sort(key=lambda item: (item.price is None, item.price or float("inf")))
    finally:
        flush_attempt_trace()
    return quotes
