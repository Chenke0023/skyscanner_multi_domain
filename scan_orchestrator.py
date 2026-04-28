"""Scan orchestration: routing, fallback, failure logging, output formatting."""

from __future__ import annotations

import argparse
import inspect
import json
import re
from datetime import datetime
from pathlib import Path
from typing import Any, Awaitable, Callable, Optional, Union

from app_paths import get_failure_log_file
from skyscanner_models import FlightQuote, RegionConfig
from skyscanner_regions import REGIONS, get_selected_regions

FAILURE_LOG_TEXT_LIMIT = 12000
SCRAPLING_FALLBACK_STATUSES = {
    "page_challenge",
    "px_challenge",
    "page_loading",
    "page_parse_failed",
    "scrapling_failed",
    "scrapling_parse_failed",
    "captcha_solve_failed",
}
ScanProgressCallback = Callable[[dict[str, Any]], Union[Awaitable[None], None]]


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
            from skyscanner_page_parser import (
                extract_page_quote_with_diagnostics,
                page_parse_diagnostics_to_dict,
            )
            from skyscanner_regions import REGIONS

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

    sections = [
        f"timestamp: {datetime.now().isoformat(timespec='seconds')}",
        f"transport: {transport}",
        f"route: {route_key}",
        f"region: {quote.region}",
        f"domain: {quote.domain}",
        f"status: {quote.status}",
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
            print(f"[{quote.region}] {quote.error or quote.status}")


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
            "price_path": quote.price_path,
            "best_price": quote.best_price,
            "best_price_path": quote.best_price_path,
            "cheapest_price": quote.cheapest_price,
            "cheapest_price_path": quote.cheapest_price_path,
            "error": quote.error,
            "source_kind": quote.source_kind,
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
    transport: str = "scrapling",
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
) -> list[FlightQuote]:
    from transport_cdp import compare_via_pages, detect_cdp_version, ensure_cdp_ready
    from transport_scrapling import compare_via_scrapling
    from transport_opencli import compare_via_opencli
    from date_window import format_trip_date_label
    from scan_history import ScanHistoryStore, get_quotes_for_trip_label, select_preview_region_batches

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
        return []

    async def emit_progress(
        *,
        stage: str,
        quotes: list[FlightQuote],
        completed_regions: list[str],
        is_final: bool = False,
        used_cached_preview: bool = False,
    ) -> None:
        if on_progress is None:
            return
        result = on_progress(
            {
                "stage": stage,
                "quotes": quotes_to_dicts(quotes),
                "completed_regions": list(completed_regions),
                "is_final": bool(is_final),
                "used_cached_preview": bool(used_cached_preview),
            }
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

    normalized_transport = (transport or "scrapling").lower()
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
        )
        fallback_regions = [
            region
            for region, quote in zip(batch_regions, quotes)
            if quote.price is None and quote.status in SCRAPLING_FALLBACK_STATUSES
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
                    args, fallback_regions, persist_failures=False
                )
                fallback_by_region = {
                    quote.region: quote for quote in fallback_quotes if quote is not None
                }
                merged_quotes: list[FlightQuote] = []
                for quote in quotes:
                    fallback_quote = fallback_by_region.get(quote.region)
                    if fallback_quote and fallback_quote.price is not None:
                        merged_quotes.append(fallback_quote)
                    else:
                        merged_quotes.append(quote)
                quotes = merged_quotes
        return quotes

    if normalized_transport == "page":
        quotes = await compare_via_pages(args, selected_regions)
        await emit_progress(
            stage="final",
            quotes=quotes,
            completed_regions=[region.code for region in selected_regions],
            is_final=True,
        )
    elif normalized_transport == "scrapling":
        if normalized_scan_mode == "preview_first":
            resolved_history_store = history_store or (ScanHistoryStore() if query_payload else None)
            latest_record = (
                resolved_history_store.get_latest_scan(query_payload)
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
                merged_quotes = merge_quotes_by_region(merged_quotes, batch_quotes)
                await emit_progress(
                    stage="quick_live" if batch_index == 0 else "background_live",
                    quotes=merged_quotes,
                    completed_regions=[quote.region for quote in merged_quotes],
                    used_cached_preview=preview_record is not None,
                )

                # Per-batch browser fallback: retry failed regions immediately
                if allow_browser_fallback:
                    batch_failed = [
                        region for region in batch_regions
                        if any(
                            quote.region == region.code
                            and quote.price is None
                            and quote.status in SCRAPLING_FALLBACK_STATUSES
                            for quote in batch_quotes
                        )
                    ]
                    if batch_failed:
                        batch_fallback_quotes = await run_scrapling_pass(
                            batch_failed,
                            enable_browser_fallback=True,
                        )
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
        await emit_progress(
            stage="final",
            quotes=quotes,
            completed_regions=[quote.region for quote in quotes],
            is_final=True,
        )
    elif normalized_transport == "opencli":
        quotes = await compare_via_opencli(
            args,
            selected_regions,
            persist_failures=True,
            build_search_url=build_search_url,
            on_region_start=on_region_start,
            on_region_complete=on_region_complete,
            region_concurrency=max(int(region_concurrency), 1),
        )
        await emit_progress(
            stage="final",
            quotes=quotes,
            completed_regions=[region.code for region in selected_regions],
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
    return quotes
