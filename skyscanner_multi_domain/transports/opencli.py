"""OpenCLI-based browser transport — v3 with async subprocess + domain-aware concurrency."""

from __future__ import annotations

import argparse
import asyncio
import json
import time
from dataclasses import dataclass, field
from typing import Any, Optional
from urllib.parse import urlparse

from skyscanner_multi_domain.diagnostics.attempt_trace import emit_trace
from skyscanner_multi_domain.diagnostics.snapshots import (
    save_opencli_snapshot,
    should_save_opencli_snapshot,
)
from skyscanner_multi_domain.models import FlightQuote, RegionConfig
from skyscanner_multi_domain.parsing.page_parser import extract_page_quote
from skyscanner_multi_domain.parsing.readiness import classify_opencli_page_readiness

TAB_WAIT_TIMEOUT = 20
TAB_POLL_INTERVAL = 2.0
MAX_REGION_TIME = 45
DEFAULT_MAX_CONCURRENT_DOMAINS = 3

# ── Async subprocess infrastructure ──────────────────────────────────────────


@dataclass
class OpenCLICommandResult:
    returncode: int
    stdout: str
    stderr: str
    duration_ms: int
    timed_out: bool = False

    @property
    def ok(self) -> bool:
        return self.returncode == 0

    @property
    def stdout_json(self) -> Any:
        return json.loads(self.stdout)


async def _run_opencli_async(
    args: list[str],
    *,
    timeout: float | None = None,
) -> OpenCLICommandResult:
    """Run an opencli command via async subprocess — non-blocking."""
    effective_timeout = timeout or 60.0
    started = time.monotonic()
    proc = await asyncio.create_subprocess_exec(
        "opencli",
        *args,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        stdout_bytes, stderr_bytes = await asyncio.wait_for(
            proc.communicate(), timeout=effective_timeout,
        )
    except asyncio.TimeoutError:
        proc.kill()
        stdout_bytes, stderr_bytes = await proc.communicate()
        return OpenCLICommandResult(
            returncode=proc.returncode or -1,
            stdout=stdout_bytes.decode("utf-8", errors="replace") if stdout_bytes else "",
            stderr=stderr_bytes.decode("utf-8", errors="replace") if stderr_bytes else "",
            duration_ms=int((time.monotonic() - started) * 1000),
            timed_out=True,
        )
    return OpenCLICommandResult(
        returncode=proc.returncode or 0,
        stdout=stdout_bytes.decode("utf-8", errors="replace") if stdout_bytes else "",
        stderr=stderr_bytes.decode("utf-8", errors="replace") if stderr_bytes else "",
        duration_ms=int((time.monotonic() - started) * 1000),
    )


# Backward-compatible sync wrapper
def _run_opencli(args: list[str], timeout: int = 60) -> OpenCLICommandResult:
    """Synchronous wrapper — use _run_opencli_async in async contexts."""
    import subprocess as _sp
    result = _sp.run(
        ["opencli"] + args,
        capture_output=True,
        text=True,
        timeout=timeout,
    )
    return OpenCLICommandResult(
        returncode=result.returncode,
        stdout=result.stdout or "",
        stderr=result.stderr or "",
        duration_ms=0,
    )


async def _opencli_json(args: list[str], timeout: int = 60) -> Any:
    """Run opencli command and parse JSON output (async)."""
    result = await _run_opencli_async(args, timeout=timeout)
    if not result.ok:
        raise RuntimeError(f"opencli failed (rc={result.returncode}): {result.stderr[:300]}")
    return result.stdout_json


# ── Async tab operations ─────────────────────────────────────────────────────


async def _tab_new_async(url: str = "") -> str:
    """Open a new tab, return tab ID."""
    cmd_args = ["browser", "tab", "new"]
    if url:
        cmd_args.append(url)
    result = await _opencli_json(cmd_args)
    return result.get("page", "")


async def _tab_navigate_async(tab_id: str, url: str) -> None:
    """Navigate tab to a new URL."""
    await _opencli_json(["browser", "open", "--tab", tab_id, url])


async def _tab_close_async(tab_id: str) -> None:
    """Close a specific tab."""
    try:
        await _run_opencli_async(["browser", "tab", "close", tab_id], timeout=5)
    except Exception:
        pass


async def _tab_wait_interactive_async(tab_id: str, timeout: float = TAB_WAIT_TIMEOUT) -> bool:
    """Wait for tab to reach interactive state (fully async)."""
    deadline = time.monotonic() + timeout
    first = True
    while time.monotonic() < deadline:
        if not first:
            await asyncio.sleep(TAB_POLL_INTERVAL)
        first = False
        try:
            state = await _opencli_json(["browser", "state", "--tab", tab_id], timeout=5)
            page_state = state.get("page", "")
            if page_state == "interactive":
                return True
            if page_state == "error":
                return False
        except Exception:
            pass
    return False


async def _tab_extract_async(tab_id: str, chunk_size: int = 15000) -> dict[str, Any]:
    """Extract text content from a tab (async)."""
    return await _opencli_json(
        ["browser", "extract", "--tab", tab_id, "--chunk-size", str(chunk_size)],
        timeout=30,
    )


# Legacy sync stubs for any callers that haven't migrated
_tab_new = lambda url="": asyncio.get_event_loop().run_until_complete(_tab_new_async(url))  # type: ignore[arg-type]
_tab_navigate = lambda tab_id, url: asyncio.get_event_loop().run_until_complete(_tab_navigate_async(tab_id, url))  # type: ignore[arg-type]
_tab_close = lambda tab_id: asyncio.get_event_loop().run_until_complete(_tab_close_async(tab_id))  # type: ignore[arg-type]
_tab_extract = lambda tab_id, chunk_size=15000: asyncio.get_event_loop().run_until_complete(_tab_extract_async(tab_id, chunk_size))  # type: ignore[arg-type]


# ── OpenCLITabSession (v3: fully async internals) ────────────────────────────


class OpenCLITabSession:
    """Manages OpenCLI tab lifecycle with async-aware extraction/waiting."""

    def __init__(self, tab_id: str = ""):
        self.tab_id = tab_id
        self.session_tab_open_count = 0
        self.session_tab_close_count = 0
        self.session_reused_tab_count = 0
        self.session_extract_attempt_count = 0
        self.session_progressive_wait_count = 0
        self.last_used_index = 0
        # Per-session command telemetry
        self.command_count = 0
        self.command_duration_ms_total = 0
        self.command_timeout_count = 0

    async def ensure_tab_async(self, url: str, *, clean: bool = False) -> tuple[str, dict[str, int]]:
        delta: dict[str, int] = {"tab_open_count": 0, "reused_tab_count": 0}
        if clean and self.tab_id:
            await _tab_close_async(self.tab_id)
            self.tab_id = ""
            self.session_tab_close_count += 1

        if not self.tab_id:
            result = await _run_opencli_async(["browser", "tab", "new", url])
            self._track_command(result)
            self.tab_id = result.stdout_json.get("page", "") if result.ok else ""
            self.session_tab_open_count += 1
            delta["tab_open_count"] = 1
        else:
            result = await _run_opencli_async(["browser", "open", "--tab", self.tab_id, url])
            self._track_command(result)
            self.session_reused_tab_count += 1
            delta["reused_tab_count"] = 1
        return self.tab_id, delta

    # Sync compatibility
    def ensure_tab(self, url: str, clean: bool = False) -> tuple[str, dict[str, int]]:
        return asyncio.get_event_loop().run_until_complete(self.ensure_tab_async(url, clean=clean))

    def _track_command(self, result: OpenCLICommandResult) -> None:
        self.command_count += 1
        self.command_duration_ms_total += result.duration_ms
        if result.timed_out:
            self.command_timeout_count += 1

    async def close_async(self):
        if self.tab_id:
            await _tab_close_async(self.tab_id)
            self.tab_id = ""
            self.session_tab_close_count += 1

    def close(self):
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
        """Wait for page to be interactive. Returns (success, delta_wait_count)."""
        delta_wait = 0
        if initial_wait > 0:
            await asyncio.sleep(initial_wait)

        success = await _tab_wait_interactive_async(tab_id, timeout=timeout)
        if not success:
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
        wait_steps: list[int] | None = None,
    ) -> tuple[FlightQuote, str, dict[str, int]]:
        if wait_steps is None:
            wait_steps = [0, 8, 15]
        chunk_sizes = [15000, 50000, 100000]
        attempts: list[tuple[int, int]] = []
        for idx, extra_wait in enumerate(wait_steps):
            chunk_size = chunk_sizes[idx] if idx < len(chunk_sizes) else chunk_sizes[-1]
            attempts.append((chunk_size, extra_wait))
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

            result = await _tab_extract_async(tab_id, chunk_size=chunk_size)
            last_text = str(result.get("content", ""))
            quote = _quote_from_opencli_result(region, result, url)
            quote.readiness = classify_opencli_page_readiness(last_text)

            if quote.price is not None:
                return quote, last_text, delta

            if quote.readiness == "challenge" or quote.status in ("px_challenge", "page_challenge"):
                if quote.status not in ("px_challenge", "page_challenge"):
                    quote.status = "page_challenge"
                    quote.error = "OpenCLI page readiness classified the page as a challenge"
                return quote, last_text, delta
            if quote.readiness == "no_flights":
                quote.status = "opencli_no_flights"
                quote.error = "OpenCLI page readiness classified the page as no flights"
                return quote, last_text, delta

            last_quote = quote

        return last_quote or _quote_from_opencli_result(region, {}, url), last_text, delta


def _quote_from_opencli_result(
    region: RegionConfig,
    result: dict[str, Any],
    fallback_url: str,
) -> FlightQuote:
    page_url = str(result.get("url", fallback_url))
    page_text = str(result.get("content", ""))
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


# ── OpenCLIDomainScheduler ───────────────────────────────────────────────────


def _extract_domain(url_str: str) -> str:
    parsed = urlparse(url_str)
    # Normalize: strip www. prefix for grouping
    netloc = parsed.netloc.replace("www.", "")
    return netloc


def _group_regions_by_domain(
    regions: list[RegionConfig],
    url_by_region: dict[str, str],
) -> list[tuple[str, list[RegionConfig]]]:
    """Group regions by their normalized domain. Preserves input order within groups."""
    groups: dict[str, list[RegionConfig]] = {}
    group_order: list[str] = []
    for region in regions:
        url = url_by_region.get(region.code, region.domain)
        domain = _extract_domain(url)
        if domain not in groups:
            groups[domain] = []
            group_order.append(domain)
        groups[domain].append(region)
    return [(domain, groups[domain]) for domain in group_order]


class OpenCLITabPool:
    """Backward-compatible tab pool with domain pinning and LRU eviction.

    Deprecated: prefer OpenCLIDomainScheduler for new code.
    This shim exists so existing tests and callers that use the old pool API
    continue to work."""

    def __init__(self, max_tabs: int = 2):
        self.max_tabs = max_tabs
        self.sessions: list[OpenCLITabSession] = []
        self.domain_to_session: dict[str, OpenCLITabSession] = {}
        self._tab_counter = 0

    def acquire(self, domain: str, url: str) -> tuple[OpenCLITabSession, dict[str, int]]:
        """Acquire a session for *domain*. Returns (session, telemetry_delta).

        If *domain* is already pinned, the existing session is reused.
        Otherwise a new tab is opened; if the pool is full the least-recently-used
        session is evicted (its old tab closed and a new one opened for the
        incoming domain)."""

        # Domain already pinned — reuse
        if domain in self.domain_to_session:
            session = self.domain_to_session[domain]
            session.last_used_index = self._next_index()
            _tab_navigate(session.tab_id, url)
            session.session_reused_tab_count += 1
            return session, {"tab_open_count": 0, "reused_tab_count": 1}

        # Pool has room — create fresh session
        if len(self.sessions) < self.max_tabs:
            session = OpenCLITabSession()
            self.sessions.append(session)
            tab_id = _tab_new(url)
            session.tab_id = tab_id
            session.session_tab_open_count += 1
            session.last_used_index = self._next_index()
            self.domain_to_session[domain] = session
            return session, {"tab_open_count": 1, "reused_tab_count": 0}

        # Pool full — evict LRU session
        victim = min(self.sessions, key=lambda s: s.last_used_index)
        # Find and remove the old domain mapping
        old_domain = next(d for d, s in self.domain_to_session.items() if s is victim)
        del self.domain_to_session[old_domain]

        # Clean transition: close old tab, open new one
        _tab_close(victim.tab_id)
        victim.session_tab_close_count += 1
        victim.tab_id = _tab_new(url)
        victim.session_tab_open_count += 1
        victim.last_used_index = self._next_index()
        self.domain_to_session[domain] = victim
        return victim, {"tab_open_count": 1, "reused_tab_count": 0}

    def _next_index(self) -> int:
        self._tab_counter += 1
        return self._tab_counter


class OpenCLIDomainScheduler:
    """Domain-aware parallel scheduler: same domain serial, different domains concurrent."""

    def __init__(
        self,
        *,
        max_concurrent_domains: int = DEFAULT_MAX_CONCURRENT_DOMAINS,
        page_wait: int = 10,
        max_region_time: int = MAX_REGION_TIME,
        wait_policies: dict[str, Any] | None = None,
    ):
        self.max_concurrent_domains = max_concurrent_domains
        self.page_wait = max(page_wait, 3)
        self.max_region_time = max_region_time
        self._wait_policies = wait_policies or {}

    def _effective_page_wait(self, domain: str) -> int:
        """Return the page_wait for a domain, respecting WaitPolicy if configured."""
        policy = self._wait_policies.get(domain)
        if policy is not None:
            return max(policy.initial_wait, 3)
        return self.page_wait

    def _effective_policy(self, domain: str) -> Any:
        """Return the WaitPolicy for a domain, or a default."""
        from skyscanner_multi_domain.scan.wait_policy import (
            WaitPolicy,
            DEFAULT_MAX_REGION_TIME,
            DEFAULT_EXTRACT_WAIT_STEPS,
        )

        policy = self._wait_policies.get(domain)
        if policy is not None:
            return policy
        return WaitPolicy(
            initial_wait=self.page_wait,
            max_region_time=self.max_region_time,
            extract_wait_steps=list(DEFAULT_EXTRACT_WAIT_STEPS),
            reason="default",
        )

    async def scan_all(
        self,
        args: argparse.Namespace,
        selected_regions: list[RegionConfig],
        *,
        url_by_region: dict[str, str],
        route_key: str,
        on_region_start: Any = None,
        on_region_complete: Any = None,
        persist_failures: bool = True,
        run_id: str = "",
    ) -> tuple[list[FlightQuote], dict[str, Any]]:
        """Scan all regions with domain-aware parallelism.

        Returns (ordered_quotes, telemetry).
        """
        groups = _group_regions_by_domain(selected_regions, url_by_region)
        sem = asyncio.Semaphore(self.max_concurrent_domains)
        wall_start = time.monotonic()

        async def run_domain_group(domain: str, regions: list[RegionConfig]) -> list[FlightQuote]:
            async with sem:
                return await self._scan_domain_serial(
                    domain, regions, args, url_by_region, route_key,
                    on_region_start, on_region_complete, persist_failures, run_id,
                )

        group_results: list[list[FlightQuote]] = await asyncio.gather(
            *(run_domain_group(domain, regions) for domain, regions in groups),
        )

        wall_time_ms = int((time.monotonic() - wall_start) * 1000)

        # Flatten while preserving selected_regions order
        all_quotes: dict[str, FlightQuote] = {}
        for group_quotes in group_results:
            for quote in group_quotes:
                all_quotes[quote.region] = quote

        ordered_quotes = []
        for region in selected_regions:
            quote = all_quotes.get(region.code)
            if quote is None:
                quote = FlightQuote(
                    region=region.code, domain=region.domain,
                    price=None, currency=region.currency,
                    source_url=url_by_region.get(region.code, ""),
                    status="opencli_not_attempted",
                    error="Scheduler did not produce a quote for this region",
                )
            ordered_quotes.append(quote)

        telemetry = {
            "opencli_execution_mode": "domain_aware_parallel",
            "opencli_domain_group_count": len(groups),
            "opencli_max_concurrent_domains": self.max_concurrent_domains,
            "opencli_wall_time_ms": wall_time_ms,
            "opencli_domain_lane_count": sum(1 for _, qs in groups if qs),
        }
        return ordered_quotes, telemetry

    async def _scan_domain_serial(
        self,
        domain: str,
        regions: list[RegionConfig],
        args: argparse.Namespace,
        url_by_region: dict[str, str],
        route_key: str,
        on_region_start: Any,
        on_region_complete: Any,
        persist_failures: bool,
        run_id: str,
    ) -> list[FlightQuote]:
        session = OpenCLITabSession()
        results: list[FlightQuote] = []
        page_text_len_by_region: dict[str, int] = {}
        page_url_by_region: dict[str, str] = {}
        return_date = getattr(args, "return_date", None)
        domain_start = time.monotonic()

        try:
            for i, region in enumerate(regions):
                policy = self._effective_policy(domain)
                # Time budget enforcement: skip subsequent regions if budget exhausted
                if i > 0:
                    elapsed = time.monotonic() - domain_start
                    if elapsed > policy.max_region_time:
                        budget_quote = FlightQuote(
                            region=region.code, domain=region.domain,
                            price=None, currency=region.currency,
                            source_url=url_by_region.get(region.code, ""),
                            status="opencli_not_attempted",
                            error=f"Time budget exceeded ({elapsed:.0f}s > {policy.max_region_time}s max)",
                        )
                        if on_region_complete:
                            on_region_complete(region, budget_quote)
                        results.append(budget_quote)
                        continue
                if on_region_start:
                    on_region_start(region)

                url = url_by_region.get(region.code, "")
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
                    tab_id, delta_ensure = await session.ensure_tab_async(url)
                    if not tab_id:
                        raise RuntimeError("No tab ID returned")

                    success, delta_wait_state = await session.wait_progressive_state(
                        tab_id, self._effective_page_wait(domain),
                    )
                    quote, page_text, delta_extract = await session.extract_with_progressive_content_wait(
                        tab_id, region, url, wait_steps=policy.extract_wait_steps,
                    )

                    quote.tab_open_count = delta_ensure["tab_open_count"]
                    quote.reused_tab_count = delta_ensure["reused_tab_count"]
                    quote.progressive_wait_used = delta_wait_state + delta_extract["progressive_wait_used"]
                    quote.extract_attempt_count = delta_extract["extract_attempt_count"]
                    quote.max_chunk_size_used = delta_extract["max_chunk_size_used"]

                    if not success and quote.price is None:
                        if quote.status not in TERMINAL_STATUSES:
                            quote.status = "opencli_timeout"
                            quote.error = "Page did not reach interactive state and no price extracted"

                except Exception as exc:
                    quote = FlightQuote(
                        region=region.code, domain=region.domain,
                        price=None, currency=region.currency, source_url=url,
                        status="opencli_error", error=str(exc)[:200],
                    )
                    quote.tab_open_count = delta_ensure["tab_open_count"]
                    quote.reused_tab_count = delta_ensure["reused_tab_count"]

                if quote is None:
                    quote = FlightQuote(
                        region=region.code, domain=region.domain,
                        price=None, currency=region.currency,
                        source_url=url, status="opencli_failed",
                        error="No price extracted",
                    )

                if should_save_opencli_snapshot(quote):
                    try:
                        save_opencli_snapshot(
                            route={
                                "origin": args.origin, "destination": args.destination,
                                "date": args.date, "return_date": return_date,
                            },
                            region=region, quote=quote, page_text=page_text,
                        )
                    except Exception:
                        pass

                if persist_failures and quote.price is None:
                    from skyscanner_multi_domain.scan.orchestrator import _persist_failure_log as _pfl
                    _pfl(quote, transport="opencli", route_key=route_key, page_text=page_text)

                page_text_len_by_region[region.code] = len(page_text)
                page_url_by_region[region.code] = quote.source_url
                results.append(quote)

                if on_region_complete:
                    on_region_complete(region, quote)
        finally:
            await session.close_async()
            tab_close_count = session.session_tab_close_count
            # Attach tab_close to last quote
            if results:
                results[-1].tab_close_count = tab_close_count

        # Emit traces
        for quote in results:
            emit_trace(
                run_id=run_id, route_key=route_key, region=quote.region,
                transport="opencli", attempt_index=0, source_kind="opencli",
                used_cdp_cookies=False, used_profile_dir=False,
                wait_ms=max(args.page_wait, 3) * 1000,
                load_dom=False, network_idle=False,
                page_text_len=page_text_len_by_region.get(quote.region, 0),
                page_url=page_url_by_region.get(quote.region, quote.source_url),
                status=quote.status, failure_reason=quote.error,
                price=quote.price, currency=quote.currency,
                tab_open_count=quote.tab_open_count,
                tab_close_count=quote.tab_close_count,
                reused_tab_count=quote.reused_tab_count,
                extract_attempt_count=quote.extract_attempt_count,
                max_chunk_size_used=quote.max_chunk_size_used,
                progressive_wait_used=quote.progressive_wait_used,
            )
        return results


TERMINAL_STATUSES = {"px_challenge", "page_challenge", "opencli_no_flights", "page_no_flights"}


# ── compare_via_opencli (v3 entry point) ────────────────────────────────────


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
    history_telemetry: dict[str, Any] | None = None,
) -> list[FlightQuote]:
    """Fetch flight quotes using opencli with domain-aware parallel scheduling."""
    if build_search_url is None:
        from skyscanner_multi_domain.scan.orchestrator import build_search_url as _bsu
        build_search_url = _bsu

    return_date = getattr(args, "return_date", None)
    page_wait = max(args.page_wait, 3)
    max_concurrent = min(max(int(region_concurrency), 1), DEFAULT_MAX_CONCURRENT_DOMAINS)

    requested_urls: dict[str, str] = {}
    for region in selected_regions:
        url = build_search_url(region, args.origin, args.destination, args.date, return_date)
        requested_urls[region.code] = url

    # Build per-domain WaitPolicies from history telemetry
    from skyscanner_multi_domain.scan.wait_policy import build_wait_policy
    wait_policies: dict[str, Any] = {}
    for region in selected_regions:
        domain_key = region.domain.replace("www.", "")
        if domain_key not in wait_policies:
            wait_policies[domain_key] = build_wait_policy(
                region_code=region.code,
                domain=region.domain,
                history_telemetry=history_telemetry,
                default_page_wait=page_wait,
            )

    route_key = f"{args.origin}_{args.destination}_{args.date.replace('-', '')}"
    if return_date:
        route_key = f"{route_key}_rt{return_date.replace('-', '')}"

    scheduler = OpenCLIDomainScheduler(
        max_concurrent_domains=max_concurrent,
        page_wait=page_wait,
        wait_policies=wait_policies,
    )
    quotes, _telemetry = await scheduler.scan_all(
        args=args,
        selected_regions=selected_regions,
        url_by_region=requested_urls,
        route_key=route_key,
        on_region_start=on_region_start,
        on_region_complete=on_region_complete,
        persist_failures=persist_failures,
        run_id=run_id,
    )
    return quotes
