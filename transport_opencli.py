"""OpenCLI-based browser transport."""

from __future__ import annotations

import argparse
import asyncio
import json
import subprocess
import time
from typing import Any, Optional

from attempt_trace import emit_trace
from skyscanner_models import FlightQuote, RegionConfig
from skyscanner_page_parser import extract_page_quote

TAB_WAIT_TIMEOUT = 20
TAB_POLL_INTERVAL = 2.0
MAX_REGION_TIME = 45


def _run_opencli(args: list[str], timeout: int = 60) -> subprocess.CompletedProcess:
    """Run opencli command and return the completed process."""
    return subprocess.run(
        ["opencli"] + args,
        capture_output=True,
        text=True,
        timeout=timeout,
    )


def _opencli_json(args: list[str], timeout: int = 60) -> Any:
    """Run opencli command and parse JSON output."""
    result = _run_opencli(args, timeout=timeout)
    if result.returncode != 0:
        raise RuntimeError(f"opencli failed: {result.stderr}")
    return json.loads(result.stdout)


def _tab_new(url: str) -> str:
    """Open a new tab with the given URL, return tab ID."""
    result = _opencli_json(["browser", "tab", "new", url])
    return result.get("page", "")


def _tab_select(tab_id: str) -> None:
    """Switch to a specific tab."""
    _opencli_json(["browser", "tab", "select", tab_id])


def _tab_close(tab_id: str) -> None:
    """Close a specific tab."""
    try:
        _opencli_json(["browser", "tab", "close", tab_id], timeout=5)
    except Exception:
        pass


async def _tab_wait_interactive_async(tab_id: str, timeout: float = TAB_WAIT_TIMEOUT) -> bool:
    """Wait for tab to reach interactive state (async)."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            state = _opencli_json(["browser", "state", "--tab", tab_id], timeout=5)
            page_state = state.get("page", "")
            if page_state == "interactive":
                return True
            if page_state == "error":
                return False
        except Exception:
            pass
        await asyncio.sleep(TAB_POLL_INTERVAL)
    return False


def _tab_extract(tab_id: str, chunk_size: int = 15000) -> dict[str, Any]:
    """Extract text content from a tab."""
    return _opencli_json(
        ["browser", "extract", "--tab", tab_id, "--chunk-size", str(chunk_size)],
        timeout=30,
    )


def _quote_from_opencli_result(
    region: RegionConfig,
    result: dict[str, Any],
    fallback_url: str,
) -> FlightQuote:
    """Convert opencli extract result to FlightQuote using existing parser."""
    page_url = str(result.get("url", fallback_url))
    page_text = str(result.get("content", ""))
    page_title = str(result.get("title", ""))

    quote = extract_page_quote(region, page_url, page_text)
    quote.source_kind = "opencli"

    if quote.price is not None:
        return quote

    from types import SimpleNamespace
    from transport_scrapling import _build_captcha_quote, _check_captcha_in_page

    has_captcha, captcha_type = _check_captcha_in_page(
        page_text,
        SimpleNamespace(url=page_url),
    )
    if has_captcha:
        quote = _build_captcha_quote(
            region,
            page_url,
            captcha_type,
            source_label="opencli",
        )
        quote.source_kind = "opencli"
    return quote


async def compare_via_opencli(
    args: argparse.Namespace,
    selected_regions: list[RegionConfig],
    *,
    persist_failures: bool = True,
    build_search_url: Any = None,
    on_region_start: Any = None,
    on_region_complete: Any = None,
    region_concurrency: int = 3,
    run_id: str = "",
) -> list[FlightQuote]:
    """Fetch flight quotes using opencli browser automation."""
    if build_search_url is None:
        from scan_orchestrator import build_search_url as _bsu
        build_search_url = _bsu

    return_date = getattr(args, "return_date", None)
    page_wait = max(args.page_wait, 3)

    requested_urls: dict[str, str] = {}
    for region in selected_regions:
        url = build_search_url(region, args.origin, args.destination, args.date, return_date)
        requested_urls[region.code] = url

    route_key = f"{args.origin}_{args.destination}_{args.date.replace('-', '')}"
    if return_date:
        route_key = f"{route_key}_rt{return_date.replace('-', '')}"

    latest_quotes: dict[str, FlightQuote] = {}
    start_time = time.time()

    for region in selected_regions:
        if time.time() - start_time > MAX_REGION_TIME * len(selected_regions):
            break

        if on_region_start:
            on_region_start(region)

        url = requested_urls[region.code]
        tab_id = ""
        page_text = ""
        quote: Optional[FlightQuote] = None

        try:
            tab_id = _tab_new(url)
            if not tab_id:
                raise RuntimeError("No tab ID returned")

            await asyncio.sleep(page_wait)

            if await _tab_wait_interactive_async(tab_id, timeout=TAB_WAIT_TIMEOUT):
                result = _tab_extract(tab_id)
                quote = _quote_from_opencli_result(region, result, url)
                page_text = str(result.get("content", ""))
            else:
                page_text = ""
                quote = FlightQuote(
                    region=region.code,
                    domain=region.domain,
                    price=None,
                    currency=region.currency,
                    source_url=url,
                    status="opencli_timeout",
                    error="Page did not reach interactive state within timeout",
                )
        except Exception as exc:
            quote = FlightQuote(
                region=region.code,
                domain=region.domain,
                price=None,
                currency=region.currency,
                source_url=url,
                status="opencli_error",
                error=str(exc)[:200],
            )
        finally:
            if tab_id:
                _tab_close(tab_id)

        if quote is None:
            quote = FlightQuote(
                region=region.code,
                domain=region.domain,
                price=None,
                currency=region.currency,
                source_url=url,
                status="opencli_failed",
                error="No price extracted",
            )

        if persist_failures and quote.price is None:
            from scan_orchestrator import _persist_failure_log as _pfl
            _pfl(
                quote,
                transport="opencli",
                route_key=route_key,
                page_text=page_text,
            )

        latest_quotes[region.code] = quote

        if on_region_complete:
            on_region_complete(region, quote)

    ordered_quotes: list[FlightQuote] = []
    for region in selected_regions:
        q = latest_quotes.get(region.code)
        if q is not None:
            ordered_quotes.append(q)

    for quote in ordered_quotes:
        emit_trace(
            run_id=run_id,
            route_key=route_key,
            region=quote.region,
            transport="opencli",
            attempt_index=0,
            source_kind="opencli",
            used_cdp_cookies=False,
            used_profile_dir=False,
            wait_ms=max(args.page_wait, 3) * 1000,
            load_dom=False,
            network_idle=False,
            page_text_len=len(page_text),
            page_url=quote.source_url,
            status=quote.status,
            failure_reason=quote.error,
            price=quote.price,
            currency=quote.currency,
        )

    return ordered_quotes