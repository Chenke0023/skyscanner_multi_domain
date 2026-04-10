"""Scan orchestration: routing, fallback, failure logging, output formatting."""

from __future__ import annotations

import argparse
import json
import re
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Optional

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
) -> list[FlightQuote]:
    from transport_cdp import compare_via_pages, detect_cdp_version, ensure_cdp_ready
    from transport_scrapling import compare_via_scrapling

    selected_regions = get_selected_regions(region_codes)
    if not selected_regions:
        return []

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

    if normalized_transport == "page":
        quotes = await compare_via_pages(args, selected_regions)
    elif normalized_transport == "scrapling":
        quotes = await compare_via_scrapling(
            args, selected_regions, persist_failures=False,
            on_region_start=on_region_start,
        )
        fallback_regions = [
            region
            for region, quote in zip(selected_regions, quotes)
            if quote.price is None and quote.status in SCRAPLING_FALLBACK_STATUSES
        ]
        if fallback_regions:
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
                error=f"未知 transport: {transport}（可选: scrapling, page）",
            )
            for region in selected_regions
        ]

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
