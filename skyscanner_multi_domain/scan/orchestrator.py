"""Direct-CDP scan orchestration, failure logging, and output formatting."""

from __future__ import annotations

import argparse
import inspect
import json
import logging
import re
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Any, Awaitable, Callable, Literal, Optional, Union
from urllib.parse import urlencode

from skyscanner_multi_domain.runtime.paths import get_failure_log_file, get_traces_dir
from skyscanner_multi_domain.diagnostics.attempt_trace import flush as flush_attempt_trace
from skyscanner_multi_domain.models import FlightQuote, RegionConfig
from skyscanner_multi_domain.scan.trace import (
    ScanTraceContext,
    ScanTraceWriter,
    append_attempt_history,
    emit_attempt_trace,
)
from skyscanner_multi_domain.planning.search_plan import (
    DateCandidate,
    RouteCandidate,
    ScanBatch,
    build_date_candidates,
    build_market_candidates,
    build_scan_tasks,
    rank_region_codes,
)
from skyscanner_multi_domain.geo.regions import REGIONS, get_selected_regions

logger = logging.getLogger(__name__)

FAILURE_LOG_TEXT_LIMIT = 12000

# ── Failure taxonomy ─────────────────────────────────────────────────────────

FailureClass = Literal[
    "network", "loading", "challenge", "parse", "empty_shell", "no_flights",
    "redirect", "unsupported", "browser_missing", "transport_error",
    "semantic_mismatch", "other",
]

_STATUS_TO_CLASS: dict[str, FailureClass] = {
    "page_loading": "loading",
    "px_challenge": "challenge",
    "page_challenge": "challenge",
    "page_parse_failed": "parse",
    "page_no_flights": "no_flights",
    "page_region_redirect": "redirect",
    "page_unsupported_route": "unsupported",
    "page_empty_shell": "empty_shell",
    "page_missing": "browser_missing",
    "page_missing_ws": "browser_missing",
    "browser_unavailable": "browser_missing",
    "page_eval_error": "transport_error",
    "page_semantic_mismatch": "semantic_mismatch",
}


def classify_failure(status: str) -> FailureClass:
    return _STATUS_TO_CLASS.get(status, "other")


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
    query = urlencode(
        [
            ("adultsv2", "1"),
            ("cabinclass", "economy"),
            ("childrenv2", ""),
            ("ref", "home"),
            ("rtn", rtn),
            ("preferdirects", "false"),
            ("outboundaltsenabled", "false"),
            ("inboundaltsenabled", "false"),
            ("market", region.code),
            ("locale", region.locale),
            ("currency", region.currency),
        ]
    )
    return f"{path}?{query}"


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
        except (TypeError, ValueError) as exc:
            logger.debug("Failed to attach parser snapshot to failure log", exc_info=exc)

    # Add failure class to extra for richer failure logs
    failure_class = classify_failure(quote.status)
    merged_extra.setdefault("failure_class", failure_class)
    action = "manual_review" if failure_class == "challenge" else "retry_cdp"
    merged_extra.setdefault("failure_action", action)

    sections = [
        f"timestamp: {datetime.now().isoformat(timespec='seconds')}",
        f"transport: {transport}",
        f"route: {route_key}",
        f"region: {quote.region}",
        f"domain: {quote.domain}",
        f"status: {quote.status}",
        f"failure_class: {failure_class}",
        f"failure_action: {action}",
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
            "price_candidates_count": quote.price_candidates_count,
            "selected_candidate_rank": quote.selected_candidate_rank,
            "candidate_sources": list(quote.candidate_sources or []),
            "readiness": quote.readiness,
            "route_detected": quote.route_detected,
            "date_detected": quote.date_detected,
            "currency_detected": quote.currency_detected,
            "route_mismatch": quote.route_mismatch,
            "date_mismatch": quote.date_mismatch,
            "currency_mismatch": quote.currency_mismatch,
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
    transport: str = "page",
    on_region_start: Callable[[RegionConfig], None] | None = None,
    on_region_complete: Callable[[RegionConfig, FlightQuote], None] | None = None,
    scan_mode: str = "full_scan",
    rerun_scope: str = "all",
    selected_region_codes: list[str] | None = None,
    region_concurrency: int = 3,
    query_payload: dict[str, Any] | None = None,
    history_store: Any | None = None,
    on_progress: ScanProgressCallback | None = None,
    config: Any | None = None,
) -> list[FlightQuote]:
    """Scan selected markets through the system browser's CDP endpoint.

    ``page`` is the stable parser. ``cdp_structured`` is an explicit
    experimental parser on the same browser session. There is no cross-
    transport retry.
    """
    from skyscanner_multi_domain.models import new_run_id
    from skyscanner_multi_domain.scan.fetch_types import AttemptPlanner
    from skyscanner_multi_domain.transports.cdp import compare_via_pages, ensure_cdp_ready
    from skyscanner_multi_domain.transports.cdp_structured import compare_via_cdp_structured

    del scan_mode, region_concurrency  # kept for saved-query call compatibility
    run_id = new_run_id()
    route_key = build_route_key(origin, destination, date, return_date)
    config_transport = getattr(getattr(config, "transport", None), "value", None)
    normalized_transport = str(config_transport or transport or "page").lower()

    selected_regions = get_selected_regions(region_codes)
    selected_codes = {code.strip().upper() for code in (selected_region_codes or []) if code.strip()}
    if (rerun_scope or "all").lower() in {"failed_only", "selected_regions"} and selected_codes:
        selected_regions = [region for region in selected_regions if region.code in selected_codes]
    if not selected_regions:
        return []

    latest_rows = None
    if query_payload is not None:
        try:
            from skyscanner_multi_domain.scan.history import ScanHistoryStore

            record = (history_store or ScanHistoryStore()).get_latest_scan(query_payload)
            latest_rows = record.rows_by_date if record is not None else None
        except (OSError, json.JSONDecodeError, sqlite3.Error) as exc:
            logger.debug("Failed to load scan history for ranking", exc_info=exc)

    identity = query_payload.get("identity", {}) if isinstance(query_payload, dict) else {}
    identity = identity if isinstance(identity, dict) else {}
    origin_country = str(identity.get("origin_country") or "")
    destination_country = str(identity.get("destination_country") or "")
    origin_code = str(identity.get("origin_code") or "")
    destination_code = str(identity.get("destination_code") or "")
    if not origin_country and origin_code.endswith("_ANY"):
        origin_country = origin_code.removesuffix("_ANY")
    if not destination_country and destination_code.endswith("_ANY"):
        destination_country = destination_code.removesuffix("_ANY")
    manual_regions = [str(code) for code in identity.get("manual_regions", []) if isinstance(code, str)]

    ranked_codes = rank_region_codes(
        [region.code for region in selected_regions],
        latest_rows,
        origin_country=origin_country,
        destination_country=destination_country,
        manual_region_codes=manual_regions,
    )
    by_code = {region.code: region for region in selected_regions}
    selected_regions = [by_code[code] for code in ranked_codes if code in by_code]

    route = RouteCandidate(
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
    dates = build_date_candidates(
        str(identity.get("date") or date),
        str(identity.get("return_date") or return_date or "") or None,
        int(identity.get("date_window_days") or 0),
    )
    current_date = next(
        (candidate for candidate in dates if (candidate.depart_date, candidate.return_date) == (date, return_date)),
        DateCandidate(date, return_date, 0, "anchor", "当前扫描日期", 1.0, {"current_date": 1.0}),
    )
    markets = build_market_candidates(
        [region.code for region in selected_regions],
        latest_rows,
        origin_country=origin_country,
        destination_country=destination_country,
        manual_region_codes=manual_regions,
    )
    all_tasks = build_scan_tasks([route], dates, markets)
    task_by_key = {
        (task.date.depart_date, task.date.return_date, task.market.region_code): (index + 1, task)
        for index, task in enumerate(all_tasks)
    }

    def apply_plan_metadata(quotes: list[FlightQuote]) -> list[FlightQuote]:
        for quote in quotes:
            ranked = task_by_key.get((date, return_date, quote.region))
            if ranked is None:
                continue
            rank, task = ranked
            quote.plan_rank = rank
            quote.plan_score = task.priority
            quote.plan_phase = task.phase
            quote.plan_reason = task.reason
            quote.route_rank = task.route.rank
            quote.date_rank = dates.index(current_date) + 1 if current_date in dates else 1
            quote.market_rank = task.market.rank
        return quotes

    trace_ctx: ScanTraceContext | None = None
    no_trace = bool(getattr(config, "no_trace", False))
    trace_dir = getattr(config, "trace_dir", None) or str(get_traces_dir())
    if not no_trace:
        trace_ctx = ScanTraceContext(
            scan_id=run_id,
            route_id=route_key,
            origin=origin,
            destination=destination,
            depart_date=date,
            writer=ScanTraceWriter(Path(trace_dir) / f"{run_id}.jsonl"),
        )

    async def notify(callback: Any, *args: Any) -> None:
        if callback is None:
            return
        result = callback(*args)
        if inspect.isawaitable(result):
            await result

    async def emit_progress(stage: str, quotes: list[FlightQuote], completed: list[str], *, final: bool = False) -> None:
        if on_progress is None:
            return
        result = on_progress(
            build_plan_progress_payload(
                stage=stage,
                quotes=quotes,
                completed_regions=completed,
                batch=None,
                batch_index=None,
                batch_count=None,
                total_tasks=len(selected_regions),
                is_final=final,
            )
        )
        if inspect.isawaitable(result):
            await result

    args = argparse.Namespace(
        origin=origin,
        destination=destination,
        date=date,
        return_date=return_date,
        page_wait=page_wait,
        timeout=timeout,
    )
    cdp_mode = getattr(getattr(config, "cdp_mode", None), "value", "attach")
    manual_tabs = dict(getattr(config, "manual_tabs", None) or {})
    keep_tabs = bool(getattr(config, "keep_tabs", False))
    planner = AttemptPlanner(config)
    quotes: list[FlightQuote]

    try:
        for region in selected_regions:
            await notify(on_region_start, region)
        await emit_progress("scan_start", [], [])

        if normalized_transport not in {"page", "cdp_structured"}:
            quotes = [
                FlightQuote(
                    region=region.code,
                    domain=region.domain,
                    price=None,
                    currency=region.currency,
                    source_url=build_search_url(region, origin, destination, date, return_date),
                    status="invalid_transport",
                    error=f"未知 transport: {normalized_transport}（可选: page, cdp_structured）",
                )
                for region in selected_regions
            ]
        else:
            try:
                ensure_cdp_ready(
                    start_url=build_search_url(selected_regions[0], origin, destination, date, return_date)
                )
            except RuntimeError as exc:
                browser_error = str(exc)
                if not browser_error.lower().startswith("browser-unavailable"):
                    browser_error = f"browser-unavailable: {browser_error}"
                quotes = [
                    FlightQuote(
                        region=region.code,
                        domain=region.domain,
                        price=None,
                        currency=region.currency,
                        source_url=build_search_url(region, origin, destination, date, return_date),
                        status="browser_unavailable",
                        error=browser_error,
                        source_kind=normalized_transport,
                    )
                    for region in selected_regions
                ]
            else:
                if normalized_transport == "page":
                    quotes = await compare_via_pages(
                        args,
                        selected_regions,
                        persist_failures=False,
                        build_search_url=build_search_url,
                        persist_failure_log=_persist_failure_log,
                        run_id=run_id,
                        cdp_mode=cdp_mode,
                        manual_tabs=manual_tabs,
                        keep_tabs=keep_tabs,
                    )
                else:
                    quotes = await compare_via_cdp_structured(
                        args,
                        selected_regions,
                        build_search_url=build_search_url,
                        persist_failure_log=_persist_failure_log,
                        run_id=run_id,
                        cdp_mode=cdp_mode,
                        manual_tabs=manual_tabs,
                        keep_tabs=keep_tabs,
                    )

        quotes = apply_plan_metadata(quotes)
        completed: list[str] = []
        for region, quote in zip(selected_regions, quotes, strict=False):
            quote.source_kind = quote.source_kind or normalized_transport
            plan = planner.plan(quote)
            append_attempt_history(quote, transport=normalized_transport, attempt_index=1, plan=plan)
            emit_attempt_trace(
                trace_ctx=trace_ctx,
                quote=quote,
                plan=plan,
                region=region.code,
                domain=region.domain,
                transport=normalized_transport,
                attempt_index=1,
            )
            completed.append(region.code)
            await notify(on_region_complete, region, quote)
            await emit_progress("region_complete", quotes, completed)

        for quote in quotes:
            if quote.price is None and not quote.debug_log_path:
                _persist_failure_log(quote, transport=normalized_transport, route_key=route_key)
        quotes.sort(key=lambda item: (item.price is None, item.price or float("inf")))
        await emit_progress("final", quotes, completed, final=True)
        return quotes
    finally:
        flush_attempt_trace()
        if trace_ctx is not None:
            trace_ctx.writer.flush()
