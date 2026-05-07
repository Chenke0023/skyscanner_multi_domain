"""OpenCLI-based browser transport."""

from __future__ import annotations

import argparse
import asyncio
import json
import subprocess
import time
from typing import Any, Optional

from skyscanner_multi_domain.diagnostics.attempt_trace import emit_trace
from skyscanner_multi_domain.models import FlightQuote, RegionConfig
from skyscanner_multi_domain.parsing.page_parser import extract_page_quote

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


def _tab_new(url: str = "") -> str:
    """Open a new tab with the given URL, return tab ID."""
    args = ["browser", "tab", "new"]
    if url:
        args.append(url)
    result = _opencli_json(args)
    return result.get("page", "")


def _tab_navigate(tab_id: str, url: str) -> None:
    """Navigate current tab to a new URL."""
    _opencli_json(["browser", "open", "--tab", tab_id, url])


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


class OpenCLITabSession:
    """Manages OpenCLI tab lifecycle and adaptive extraction/waiting."""

    def __init__(self, tab_id: str = ""):
        self.tab_id = tab_id
        self.tab_open_count = 0
        self.tab_close_count = 0
        self.reused_tab_count = 0
        self.extract_attempt_count = 0
        self.max_chunk_size_used = 0
        self.progressive_wait_used = 0

    def ensure_tab(self, url: str) -> str:
        """Open a new tab if none exists, or navigate existing tab."""
        if not self.tab_id:
            self.tab_id = _tab_new(url)
            self.tab_open_count += 1
        else:
            _tab_navigate(self.tab_id, url)
            self.reused_tab_count += 1
        return self.tab_id

    def close(self):
        """Close the managed tab."""
        if self.tab_id:
            _tab_close(self.tab_id)
            self.tab_id = ""
            self.tab_close_count += 1

    async def wait_progressive(
        self,
        tab_id: str,
        initial_wait: float,
        poll_interval: float = TAB_POLL_INTERVAL,
        timeout: float = TAB_WAIT_TIMEOUT,
    ) -> bool:
        """Wait for page to be interactive, with progressive steps if needed."""
        await asyncio.sleep(initial_wait)
        success = await _tab_wait_interactive_async(tab_id, timeout=timeout)
        if not success:
            # First progressive wait
            self.progressive_wait_used += 1
            await asyncio.sleep(poll_interval * 2)
            success = await _tab_wait_interactive_async(tab_id, timeout=timeout / 2)
        return success

    async def extract_adaptive(
        self,
        tab_id: str,
        region: RegionConfig,
        url: str,
    ) -> tuple[FlightQuote, str]:
        """Try extraction with increasing chunk sizes until price is found or limit reached."""
        chunk_sizes = [15000, 50000, 100000]
        last_quote = None
        last_text = ""

        for chunk_size in chunk_sizes:
            self.extract_attempt_count += 1
            self.max_chunk_size_used = max(self.max_chunk_size_used, chunk_size)

            result = _tab_extract(tab_id, chunk_size=chunk_size)
            last_text = str(result.get("content", ""))
            quote = _quote_from_opencli_result(region, result, url)

            if quote.price is not None:
                return quote, last_text

            last_quote = quote

        return last_quote or _quote_from_opencli_result(region, {}, url), last_text


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
    from skyscanner_multi_domain.transports.scrapling import _build_captcha_quote, _check_captcha_in_page

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
        from skyscanner_multi_domain.scan.orchestrator import build_search_url as _bsu
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
    page_text_len_by_region: dict[str, int] = {}
    page_url_by_region: dict[str, str] = {}
    start_time = time.time()
    session = OpenCLITabSession()

    try:
        for region in selected_regions:
            # Check time budget
            elapsed = time.time() - start_time
            if elapsed > MAX_REGION_TIME * len(selected_regions):
                latest_quotes[region.code] = FlightQuote(
                    region=region.code,
                    domain=region.domain,
                    price=None,
                    currency=region.currency,
                    source_url=requested_urls[region.code],
                    status="opencli_not_attempted",
                    error="Time budget exceeded before attempt",
                )
                continue

            if on_region_start:
                on_region_start(region)

            url = requested_urls[region.code]
            page_text = ""
            quote: Optional[FlightQuote] = None

            try:
                tab_id = session.ensure_tab(url)
                if not tab_id:
                    raise RuntimeError("No tab ID returned")

                if await session.wait_progressive(tab_id, page_wait):
                    quote, page_text = await session.extract_adaptive(tab_id, region, url)
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

            # Apply telemetry to quote
            quote.tab_open_count = session.tab_open_count
            quote.tab_close_count = session.tab_close_count
            quote.reused_tab_count = session.reused_tab_count
            quote.extract_attempt_count = session.extract_attempt_count
            quote.max_chunk_size_used = session.max_chunk_size_used
            quote.progressive_wait_used = session.progressive_wait_used

            if persist_failures and quote.price is None:
                from skyscanner_multi_domain.scan.orchestrator import _persist_failure_log as _pfl
                _pfl(
                    quote,
                    transport="opencli",
                    route_key=route_key,
                    page_text=page_text,
                )

            page_text_len_by_region[region.code] = len(page_text)
            page_url_by_region[region.code] = quote.source_url
            latest_quotes[region.code] = quote

            if on_region_complete:
                on_region_complete(region, quote)
    finally:
        session.close()

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
            page_text_len=page_text_len_by_region.get(quote.region, 0),
            page_url=page_url_by_region.get(quote.region, quote.source_url),
            status=quote.status,
            failure_reason=quote.error,
            price=quote.price,
            currency=quote.currency,
            tab_open_count=quote.tab_open_count,
            tab_close_count=quote.tab_close_count,
            reused_tab_count=quote.reused_tab_count,
            extract_attempt_count=quote.extract_attempt_count,
            max_chunk_size_used=quote.max_chunk_size_used,
            progressive_wait_used=quote.progressive_wait_used,
        )

    return ordered_quotes
