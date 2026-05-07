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
        # Session-level counters (cumulative for the whole pass)
        self.session_tab_open_count = 0
        self.session_tab_close_count = 0
        self.session_reused_tab_count = 0
        self.session_extract_attempt_count = 0
        self.session_progressive_wait_count = 0
        self.last_used_index = 0

    def ensure_tab(self, url: str, clean: bool = False) -> tuple[str, dict[str, int]]:
        """Open a new tab if none exists, or navigate existing tab. Returns delta telemetry.
        
        If clean=True and tab exists, it will be closed and a new one opened to ensure
        no cross-domain pollution.
        """
        delta = {"tab_open_count": 0, "reused_tab_count": 0}
        if clean and self.tab_id:
            _tab_close(self.tab_id)
            self.tab_id = ""
            self.session_tab_close_count += 1
            # Note: we don't return tab_close_count in delta yet as it's pool-level

        if not self.tab_id:
            self.tab_id = _tab_new(url)
            self.session_tab_open_count += 1
            delta["tab_open_count"] = 1
        else:
            _tab_navigate(self.tab_id, url)
            self.session_reused_tab_count += 1
            delta["reused_tab_count"] = 1
        return self.tab_id, delta

    def close(self):
        """Close the managed tab."""
        if self.tab_id:
            _tab_close(self.tab_id)
            self.tab_id = ""
            self.session_tab_close_count += 1

    async def wait_progressive_state(
        self,
        tab_id: str,
        initial_wait: float,
        poll_interval: float = TAB_POLL_INTERVAL,
        timeout: float = TAB_WAIT_TIMEOUT,
    ) -> tuple[bool, int]:
        """Wait for page to be interactive (state-based). Returns (success, delta_wait_count)."""
        delta_wait = 0
        if initial_wait > 0:
            await asyncio.sleep(initial_wait)
        
        success = await _tab_wait_interactive_async(tab_id, timeout=timeout)
        if not success:
            # First progressive wait (state-based)
            delta_wait += 1
            self.session_progressive_wait_count += 1
            await asyncio.sleep(poll_interval * 2)
            success = await _tab_wait_interactive_async(tab_id, timeout=timeout / 2)
        return success, delta_wait

    async def extract_with_progressive_content_wait(
        self,
        tab_id: str,
        region: RegionConfig,
        url: str,
    ) -> tuple[FlightQuote, str, dict[str, int]]:
        """Try extraction with increasing chunk sizes and content-aware waiting.
        
        If price is missing, wait and try larger chunks.
        """
        attempts = [
            (15000, 0),  # (chunk_size, extra_wait)
            (50000, 8),
            (100000, 15),
        ]
        
        delta = {
            "extract_attempt_count": 0,
            "progressive_wait_used": 0,
            "max_chunk_size_used": 0,
        }
        
        last_quote = None
        last_text = ""

        for chunk_size, extra_wait in attempts:
            if extra_wait > 0:
                await asyncio.sleep(extra_wait)
                delta["progressive_wait_used"] += 1
                self.session_progressive_wait_count += 1

            delta["extract_attempt_count"] += 1
            self.session_extract_attempt_count += 1
            delta["max_chunk_size_used"] = max(delta["max_chunk_size_used"], chunk_size)

            result = _tab_extract(tab_id, chunk_size=chunk_size)
            last_text = str(result.get("content", ""))
            quote = _quote_from_opencli_result(region, result, url)

            if quote.price is not None:
                return quote, last_text, delta
            
            # If it's a captcha, stop immediately
            if quote.status in ("px_challenge", "page_challenge"):
                return quote, last_text, delta

            last_quote = quote

        # Fallback to last attempt if no price found after all retries
        return last_quote or _quote_from_opencli_result(region, {}, url), last_text, delta


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


class OpenCLITabPool:
    """Bounded pool of OpenCLI tabs with domain-level reuse prioritization and LRU eviction."""

    def __init__(self, max_tabs: int = 1):
        self.max_tabs = max_tabs
        self.sessions: list[OpenCLITabSession] = []
        # Mapping of domain to session for pinning
        self.domain_to_session: dict[str, OpenCLITabSession] = {}
        self.pool_tab_close_count = 0
        self._use_counter = 0

    def _mark_used(self, session: OpenCLITabSession) -> None:
        self._use_counter += 1
        session.last_used_index = self._use_counter

    def acquire(self, domain: str, url: str) -> tuple[OpenCLITabSession, dict[str, int]]:
        """Acquire a session for the given domain. Prioritize domain pinning and use LRU for eviction."""
        # 1. Try pinned session for this domain
        if domain in self.domain_to_session:
            session = self.domain_to_session[domain]
            self._mark_used(session)
            _, delta = session.ensure_tab(url, clean=False)
            return session, delta

        # 2. Try to create a new session if pool is not full
        if len(self.sessions) < self.max_tabs:
            session = OpenCLITabSession()
            self.sessions.append(session)
            self.domain_to_session[domain] = session
            self._mark_used(session)
            _, delta = session.ensure_tab(url, clean=False)
            return session, delta

        # 3. Reuse an existing session using LRU strategy
        # Find session with the oldest deterministic use index.
        session = min(self.sessions, key=lambda s: s.last_used_index)
        
        # Remove old pin if exists
        old_domain = next((d for d, s in self.domain_to_session.items() if s == session), None)
        if old_domain:
            del self.domain_to_session[old_domain]
        
        self.domain_to_session[domain] = session
        # Use clean=True for cross-domain repurpose to avoid pollution
        self._mark_used(session)
        _, delta = session.ensure_tab(url, clean=True)
        return session, delta

    def close_all(self):
        """Close all tabs in the pool."""
        for session in self.sessions:
            session.close()
            self.pool_tab_close_count += session.session_tab_close_count
        self.sessions.clear()
        self.domain_to_session.clear()

    def get_total_tab_close_count(self) -> int:
        current_active = sum(s.session_tab_close_count for s in self.sessions)
        return self.pool_tab_close_count + current_active


TERMINAL_STATUSES = {"px_challenge", "page_challenge"}


async def compare_via_opencli(
    args: argparse.Namespace,
    selected_regions: list[RegionConfig],
    *,
    persist_failures: bool = True,
    build_search_url: Any = None,
    on_region_start: Any = None,
    on_region_complete: Any = None,
    region_concurrency: int = 1,
    run_id: str = "",
) -> list[FlightQuote]:
    """Fetch flight quotes using opencli browser automation with a bounded tab pool.

    OpenCLI runs regions serially. region_concurrency controls the maximum retained
    tab lanes for reuse, capped at three; it does not parallelize region execution.
    """
    if build_search_url is None:
        from skyscanner_multi_domain.scan.orchestrator import build_search_url as _bsu
        build_search_url = _bsu

    return_date = getattr(args, "return_date", None)
    page_wait = max(args.page_wait, 3)
    # Linked to region_concurrency but capped for safety (v1.2 goal #1)
    max_tabs = min(max(int(region_concurrency), 1), 3)

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
    
    pool = OpenCLITabPool(max_tabs=max_tabs)

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
            delta_ensure = {"tab_open_count": 0, "reused_tab_count": 0}
            delta_wait_state = 0
            delta_extract = {
                "extract_attempt_count": 0,
                "progressive_wait_used": 0,
                "max_chunk_size_used": 0,
            }

            try:
                session, delta_ensure = pool.acquire(region.domain, url)
                tab_id = session.tab_id

                if not tab_id:
                    raise RuntimeError("No tab ID returned from pool")

                # State-based progressive wait
                success, delta_wait_state = await session.wait_progressive_state(tab_id, page_wait)
                
                # Content-aware adaptive extraction (v1.2 goal: wait + re-extract loop is now in extract_with_progressive_content_wait)
                quote, page_text, delta_extract = await session.extract_with_progressive_content_wait(tab_id, region, url)
                
                # Apply telemetry to quote (per-region deltas)
                quote.tab_open_count = delta_ensure["tab_open_count"]
                quote.reused_tab_count = delta_ensure["reused_tab_count"]
                quote.progressive_wait_used = delta_wait_state + delta_extract["progressive_wait_used"]
                quote.extract_attempt_count = delta_extract["extract_attempt_count"]
                quote.max_chunk_size_used = delta_extract["max_chunk_size_used"]
                
                if not success and quote.price is None:
                    # Point 2: Do not overwrite challenge statuses with opencli_timeout
                    if quote.status not in TERMINAL_STATUSES:
                        quote.status = "opencli_timeout"
                        quote.error = "Page did not reach interactive state and no price extracted"

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
                quote.tab_open_count = delta_ensure["tab_open_count"]
                quote.reused_tab_count = delta_ensure["reused_tab_count"]

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
        # Final update of tab_close_count to the LAST quote in the pass
        total_closes_before = pool.get_total_tab_close_count()
        pool.close_all()
        total_closes_after = pool.get_total_tab_close_count()
        total_closes = max(total_closes_before, total_closes_after)

        if selected_regions:
            # We assign it to the last region that was actually ATTEMPTED
            last_attempted_code = None
            for r in reversed(selected_regions):
                if r.code in latest_quotes and latest_quotes[r.code].status != "opencli_not_attempted":
                    last_attempted_code = r.code
                    break
            
            if last_attempted_code and last_attempted_code in latest_quotes:
                latest_quotes[last_attempted_code].tab_close_count = total_closes

    # Final return
    final_quotes = []
    for region in selected_regions:
        q = latest_quotes.get(region.code)
        if q is not None:
            final_quotes.append(q)

    for quote in final_quotes:
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

    return final_quotes
