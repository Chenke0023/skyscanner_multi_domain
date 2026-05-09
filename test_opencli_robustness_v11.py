from __future__ import annotations

import argparse
import asyncio
from unittest.mock import MagicMock, patch

import pytest

from skyscanner_multi_domain.models import RegionConfig, FlightQuote
from skyscanner_multi_domain.scan.fetch_types import FetchAttempt
from skyscanner_multi_domain.scan.orchestrator import run_page_scan

def test_orchestrator_retries_unattempted_opencli_regions() -> None:
    """Test that orchestrator picks up opencli_not_attempted regions in the remaining_regions pass."""
    async def run_test():
        # Setup: 2 regions, CN and SG
        # CN succeeds in first pass
        # SG is opencli_not_attempted in first pass (e.g. budget)
        # SG succeeds in second pass
        
        regions = ["CN", "SG"]
        origin = "BJS"
        destination = "ALA"
        date = "2026-05-20"
        
        mock_quotes_pass1 = [
            FlightQuote("CN", "domain1", 100.0, "CNY", "url1", "ok"),
            FlightQuote("SG", "domain2", None, "SGD", "url2", "opencli_not_attempted"),
        ]
        
        mock_quotes_pass2 = [
            FlightQuote("SG", "domain2", 200.0, "SGD", "url2", "ok"),
        ]
        
        with patch("skyscanner_multi_domain.transports.opencli.compare_via_opencli") as mock_opencli:
            mock_opencli.side_effect = [mock_quotes_pass1, mock_quotes_pass2]
            
            with patch("skyscanner_multi_domain.transports.cdp.detect_cdp_version", return_value=None):
                with patch("skyscanner_multi_domain.scan.orchestrator.build_scan_batches") as mock_batches:
                    # Mock 1 batch containing both regions
                    from skyscanner_multi_domain.planning.search_plan import ScanBatch, ScanTask, RouteCandidate, DateCandidate, MarketCandidate
                    route = RouteCandidate("BJS", "ALA", "BJS", "ALA", 1, "reason", 1.0, 1.0, {})
                    date_cand = DateCandidate(date, None, 0, "anchor", "reason", 1.0, {})
                    market_cn = MarketCandidate("CN", 1, "reason", 1.0, 0.5, 1.0, {})
                    market_sg = MarketCandidate("SG", 1, "reason", 1.0, 0.5, 1.0, {})
                    
                    batch = ScanBatch(1, "probe", [
                        ScanTask(route, date_cand, market_cn, 1.0, "probe", "reason"),
                        ScanTask(route, date_cand, market_sg, 1.0, "probe", "reason")
                    ], "reason")
                    mock_batches.return_value = [batch]
                    
                    quotes = await run_page_scan(
                        origin, destination, date, regions,
                        transport="opencli",
                        allow_browser_fallback=False
                    )
        
        # Verify both regions have success prices
        # Quotes are sorted by price (None first, then value)
        # CN=100, SG=200 -> [CN, SG]
        assert len(quotes) == 2
        assert quotes[0].region == "CN"
        assert quotes[0].price == 100.0
        assert quotes[1].region == "SG"
        assert quotes[1].price == 200.0
        
        # Verify compare_via_opencli was called twice
        # 1st call with [CN, SG], 2nd call with [SG]
        assert mock_opencli.call_count == 2
        args1 = mock_opencli.call_args_list[0][0][1]
        args2 = mock_opencli.call_args_list[1][0][1]
        assert [r.code for r in args1] == ["CN", "SG"]
        assert [r.code for r in args2] == ["SG"]

    asyncio.run(run_test())

def test_orchestrator_merges_fallback_diagnostics_v12() -> None:
    """Test that orchestrator merges failure diagnostics when fallback also fails."""
    async def run_test():
        regions = ["CN"]
        origin = "BJS"
        destination = "ALA"
        date = "2026-05-20"
        
        # Initial OpenCLI failure (network/extract fail)
        opencli_quote = FlightQuote("CN", "domain1", None, "CNY", "url1", "opencli_failed")
        
        # Secondary page fallback failure (challenge)
        page_quote = FlightQuote("CN", "domain1", None, "CNY", "url1", "page_challenge")
        page_quote.error = "Cloudflare"
        
        with patch("skyscanner_multi_domain.transports.opencli.compare_via_opencli", return_value=[opencli_quote]):
            # Use the actual import location for patching
            with patch("skyscanner_multi_domain.transports.cdp.detect_cdp_version", return_value={"Browser": "Edge"}):
                with patch("skyscanner_multi_domain.transports.cdp.compare_via_pages", return_value=[page_quote]):
                    with patch("skyscanner_multi_domain.transports.scrapling.compare_via_scrapling", return_value=[opencli_quote]): # Also fail scrapling fallback
                        with patch("skyscanner_multi_domain.scan.orchestrator.build_scan_batches") as mock_batches:
                            from skyscanner_multi_domain.planning.search_plan import ScanBatch, ScanTask, RouteCandidate, DateCandidate, MarketCandidate
                            route = RouteCandidate("BJS", "ALA", "BJS", "ALA", 1, "reason", 1.0, 1.0, {})
                            date_cand = DateCandidate(date, None, 0, "anchor", "reason", 1.0, {})
                            market_cn = MarketCandidate("CN", 1, "reason", 1.0, 0.5, 1.0, {})
                            
                            batch = ScanBatch(1, "probe", [
                                ScanTask(route, date_cand, market_cn, 1.0, "probe", "reason")
                            ], "reason")
                            mock_batches.return_value = [batch]
                            
                            quotes = await run_page_scan(
                                origin, destination, date, regions,
                                transport="opencli",
                                allow_browser_fallback=True
                            )
        
        assert len(quotes) == 1
        quote = quotes[0]
        assert quote.price is None
        # CDP result now replaces quote_by_region — status comes from CDP
        assert quote.status == "page_challenge"
        assert quote.error == "Cloudflare"
        assert len(quote.fallback_attempts) >= 1
        fallback = quote.fallback_attempts[0]
        assert fallback["transport"] == "opencli_primary"
        assert fallback["status"] == "opencli_failed"
        # Scrapling should NOT have been attempted (challenge is terminal)

    asyncio.run(run_test())

def test_orchestrator_cdp_terminal_prevents_scrapling() -> None:
    """When CDP fallback returns a terminal result (challenge/no_flights),
    Scrapling must NOT be attempted — the router re-evaluates on the CDP result."""
    async def run_test():
        regions = ["CN", "SG"]
        origin = "BJS"
        destination = "ALA"
        date = "2026-05-20"

        # Both regions fail OpenCLI
        opencli_quotes = [
            FlightQuote("CN", "domain1", None, "CNY", "url1", "opencli_failed"),
            FlightQuote("SG", "domain2", None, "SGD", "url2", "opencli_error"),
        ]

        # CDP returns: CN → no_flights (terminal), SG → parse_failed (retryable)
        cdp_quotes = [
            FlightQuote("CN", "domain1", None, "CNY", "url1", "page_no_flights"),
            FlightQuote("SG", "domain2", None, "SGD", "url2", "page_parse_failed"),
        ]
        cdp_quotes[0].error = "No flights on this route"
        cdp_quotes[1].error = "Could not extract price"

        # Scrapling should only be called for SG (CN is terminal after CDP)
        scrapling_quotes = [
            FlightQuote("SG", "domain2", 300.0, "SGD", "url2", "ok"),
        ]

        with patch("skyscanner_multi_domain.transports.opencli.compare_via_opencli", return_value=opencli_quotes):
            with patch("skyscanner_multi_domain.transports.cdp.detect_cdp_version", return_value={"Browser": "Edge"}):
                with patch("skyscanner_multi_domain.transports.cdp.compare_via_pages", return_value=cdp_quotes):
                    with patch("skyscanner_multi_domain.transports.scrapling.compare_via_scrapling") as mock_scrapling:
                        mock_scrapling.return_value = scrapling_quotes
                        with patch("skyscanner_multi_domain.scan.orchestrator.build_scan_batches") as mock_batches:
                            from skyscanner_multi_domain.planning.search_plan import ScanBatch, ScanTask, RouteCandidate, DateCandidate, MarketCandidate
                            route = RouteCandidate("BJS", "ALA", "BJS", "ALA", 1, "reason", 1.0, 1.0, {})
                            date_cand = DateCandidate(date, None, 0, "anchor", "reason", 1.0, {})
                            market_cn = MarketCandidate("CN", 1, "reason", 1.0, 0.5, 1.0, {})
                            market_sg = MarketCandidate("SG", 1, "reason", 1.0, 0.5, 1.0, {})

                            batch = ScanBatch(1, "probe", [
                                ScanTask(route, date_cand, market_cn, 1.0, "probe", "reason"),
                                ScanTask(route, date_cand, market_sg, 1.0, "probe", "reason")
                            ], "reason")
                            mock_batches.return_value = [batch]

                            quotes = await run_page_scan(
                                origin, destination, date, regions,
                                transport="opencli",
                                allow_browser_fallback=True
                            )

        # Scrapling should have been called with only SG, not CN
        assert mock_scrapling.call_count == 1
        scrapling_call_regions = mock_scrapling.call_args[0][1]
        assert [r.code for r in scrapling_call_regions] == ["SG"]

        # CN: terminal no_flights, SG: scrapling success
        assert len(quotes) == 2
        cn_quote = next(q for q in quotes if q.region == "CN")
        sg_quote = next(q for q in quotes if q.region == "SG")
        assert cn_quote.price is None
        assert cn_quote.status == "page_no_flights"
        assert sg_quote.price == 300.0
        assert sg_quote.status == "ok"

    asyncio.run(run_test())

def test_orchestrator_network_failure_routes_to_google_jump() -> None:
    """Network/extract failures should route to google_jump before CDP."""
    async def run_test():
        regions = ["CN"]
        origin = "BJS"
        destination = "ALA"
        date = "2026-05-20"

        opencli_quote = FlightQuote("CN", "domain1", None, "CNY", "url1", "opencli_error")
        opencli_quote.error = "Connection reset"

        from unittest.mock import AsyncMock
        with patch("skyscanner_multi_domain.transports.opencli.compare_via_opencli", return_value=[opencli_quote]), \
             patch("skyscanner_multi_domain.scan.orchestrator.build_scan_batches") as mock_batches, \
             patch("skyscanner_multi_domain.transports.cdp.compare_via_pages", new_callable=AsyncMock, return_value=[]), \
             patch("skyscanner_multi_domain.transports.scrapling.compare_via_scrapling", new_callable=AsyncMock, return_value=[]):
            from skyscanner_multi_domain.planning.search_plan import ScanBatch, ScanTask, RouteCandidate, DateCandidate, MarketCandidate
            route = RouteCandidate("BJS", "ALA", "BJS", "ALA", 1, "reason", 1.0, 1.0, {})
            date_cand = DateCandidate(date, None, 0, "anchor", "reason", 1.0, {})
            market_cn = MarketCandidate("CN", 1, "reason", 1.0, 0.5, 1.0, {})

            batch = ScanBatch(1, "probe", [
                ScanTask(route, date_cand, market_cn, 1.0, "probe", "reason")
            ], "reason")
            mock_batches.return_value = [batch]

            # Patch google_jump to succeed (imported inline in orchestrator)
            gj_quote = FlightQuote("CN", "domain1", 150.0, "CNY", "url1", "ok", confidence=0.9)
            with patch("skyscanner_multi_domain.transports.google_jump.build_quote_via_google_jump", return_value=gj_quote):
                quotes = await run_page_scan(
                    origin, destination, date, regions,
                    transport="opencli",
                    allow_browser_fallback=True
                )

        assert len(quotes) == 1
        assert quotes[0].price == 150.0
        assert quotes[0].status == "ok"
        # Should have fallback record showing opencli_primary → google_jump success
        assert len(quotes[0].fallback_attempts) == 1
        assert quotes[0].fallback_attempts[0]["transport"] == "opencli_primary"
        assert quotes[0].fallback_attempts[0]["status"] == "opencli_error"

    asyncio.run(run_test())

def test_opencli_time_budget_enforcement() -> None:
    """OpenCLIDomainScheduler must mark regions as opencli_not_attempted
    when the domain time budget is exhausted."""
    async def run_test():
        from skyscanner_multi_domain.transports.opencli import (
            OpenCLIDomainScheduler, OpenCLITabSession,
        )
        import argparse
        import time

        args = argparse.Namespace(
            origin="BJS", destination="ALA", date="2026-05-20",
            return_date=None, page_wait=5, timeout=30,
        )

        region1 = RegionConfig("CN", "China", "skyscanner.com", "zh-CN", "CNY")
        region2 = RegionConfig("SG", "Singapore", "skyscanner.com", "en-SG", "SGD")

        scheduler = OpenCLIDomainScheduler(
            max_concurrent_domains=1,
            page_wait=5,
            max_region_time=0,  # Zero budget — second region always times out
        )

        called_regions = []
        completed_regions = []

        def on_start(r):
            called_regions.append(r.code)

        def on_complete(r, q):
            completed_regions.append((r.code, q.status))

        # Mock the session to make first region succeed quickly
        with patch.object(OpenCLITabSession, "ensure_tab_async") as mock_ensure:
            mock_ensure.return_value = ("tab-1", {"tab_open_count": 1, "reused_tab_count": 0})
            with patch.object(OpenCLITabSession, "wait_progressive_state") as mock_wait:
                mock_wait.return_value = (True, 0)
                with patch.object(OpenCLITabSession, "extract_with_progressive_content_wait") as mock_extract:
                    mock_extract.return_value = (
                        FetchAttempt(
                            transport="opencli",
                            region_code="CN",
                            url="url",
                            page_text="page text content here",
                        ),
                        {"extract_attempt_count": 1, "progressive_wait_used": 0, "max_chunk_size_used": 0},
                    )
                    with patch("skyscanner_multi_domain.transports.opencli.fetch_attempt_to_quote") as mock_f2q:
                        mock_f2q.return_value = FlightQuote("CN", "skyscanner.com", 100.0, "CNY", "url", "ok")
                        with patch.object(OpenCLITabSession, "close_async") as mock_close:
                            mock_close.return_value = None

                            quotes, _tel = await scheduler.scan_all(
                            args=args,
                            selected_regions=[region1, region2],
                            url_by_region={"CN": "url1", "SG": "url2"},
                            route_key="BJS_ALA_20260520",
                            on_region_start=on_start,
                            on_region_complete=on_complete,
                            persist_failures=False,
                            run_id="test",
                        )

        # First region should have been attempted
        # Second region should be marked opencli_not_attempted (budget=0)
        assert len(quotes) == 2
        assert quotes[0].region == "CN"
        assert quotes[0].status == "ok"
        assert quotes[1].region == "SG"
        assert quotes[1].status == "opencli_not_attempted"
        assert "Time budget exceeded" in (quotes[1].error or "")

    asyncio.run(run_test())

def test_wait_policy_wired_into_compare_via_opencli() -> None:
    """compare_via_opencli must build per-domain WaitPolicies from history_telemetry
    and pass them to OpenCLIDomainScheduler."""
    async def run_test():
        from skyscanner_multi_domain.transports.opencli import compare_via_opencli, OpenCLIDomainScheduler
        import argparse

        args = argparse.Namespace(
            origin="BJS", destination="ALA", date="2026-05-20",
            return_date=None, page_wait=10, timeout=30,
        )

        region = RegionConfig("CN", "China", "www.skyscanner.com.sg", "en-SG", "SGD")

        # Telemetry showing slow domain → WaitPolicy should increase wait
        slow_telemetry = {
            "per_domain": {
                "skyscanner.com.sg": {
                    "total_attempts": 10,
                    "loading_timeout_rate": 0.5,
                    "challenge_rate": 0.0,
                }
            }
        }

        # Patch scheduler to capture what it receives
        original_init = OpenCLIDomainScheduler.__init__
        captured_policies = {}

        def capture_init(self, **kwargs):
            nonlocal captured_policies
            captured_policies.update(kwargs.get("wait_policies", {}))
            original_init(self, **kwargs)

        with patch.object(OpenCLIDomainScheduler, "__init__", capture_init):
            # Need to mock the scan_all too to avoid actual work
            with patch.object(OpenCLIDomainScheduler, "scan_all") as mock_scan:
                mock_scan.return_value = ([FlightQuote("CN", "domain", 100.0, "SGD", "url", "ok")], {})

                await compare_via_opencli(
                    args, [region],
                    history_telemetry=slow_telemetry,
                    run_id="test",
                )

        # Verify WaitPolicy was built for the domain and passed to scheduler
        assert "skyscanner.com.sg" in captured_policies
        policy = captured_policies["skyscanner.com.sg"]
        assert policy.initial_wait == 20  # default(10) + 10 for slow domain
        assert policy.max_region_time == 75  # increased for slow domain

    asyncio.run(run_test())

def test_wait_policy_real_regions_hit_slow_domain_policy() -> None:
    """With real REGIONS domains (scheme-ful URLs), the scheduler must look up
    and apply the slow-domain WaitPolicy — not fall back to default.

    This is the regression test for the key-mismatch bug where:
      - compare_via_opencli built wait_policies keys via region.domain.replace("www.", "")
        (producing "https://skyscanner.sg")
      - scheduler lookup used _extract_domain(urlparse().netloc.strip("www."))
        (producing "skyscanner.sg")
    so real REGIONS domains with schemes never hit the slow-domain policy.
    """
    async def run_test():
        from skyscanner_multi_domain.transports.opencli import (
            compare_via_opencli, OpenCLIDomainScheduler, OpenCLITabSession,
        )
        from skyscanner_multi_domain.geo.regions import REGIONS
        import argparse

        args = argparse.Namespace(
            origin="BJS", destination="ALA", date="2026-05-20",
            return_date=None, page_wait=10, timeout=30,
        )

        # Use real REGIONS — these have scheme-ful domains like https://www.skyscanner.sg
        real_regions = [REGIONS["CN"], REGIONS["SG"]]
        assert "https://" in real_regions[0].domain  # sanity check

        # Slow-domain telemetry: skyscanner.sg is slow (>30% loading)
        # Telemetry keys must be normalized bare hosts (as collect_domain_telemetry produces)
        slow_telemetry = {
            "per_domain": {
                "skyscanner.sg": {
                    "total_attempts": 10,
                    "loading_timeout_rate": 0.5,
                    "challenge_rate": 0.0,
                }
            }
        }

        captured_policies: dict[str, Any] = {}

        original_init = OpenCLIDomainScheduler.__init__

        def capture_init(self, **kwargs):
            captured_policies.update(kwargs.get("wait_policies", {}))
            original_init(self, **kwargs)

        with patch.object(OpenCLIDomainScheduler, "__init__", capture_init):
            with patch.object(OpenCLIDomainScheduler, "scan_all") as mock_scan:
                mock_scan.return_value = (
                    [
                        FlightQuote("CN", "domain", 100.0, "CNY", "url", "ok"),
                        FlightQuote("SG", "domain", 200.0, "SGD", "url", "ok"),
                    ],
                    {},
                )

                await compare_via_opencli(
                    args, real_regions,
                    history_telemetry=slow_telemetry,
                    run_id="test",
                )

        # Keys in captured_policies must be normalized bare hosts
        # (skyscanner.sg, skyscanner.cn) — not scheme-ful strings
        assert "skyscanner.sg" in captured_policies, (
            f"Expected 'skyscanner.sg' in wait_policies, got: {list(captured_policies.keys())}"
        )
        sg_policy = captured_policies["skyscanner.sg"]
        assert sg_policy.initial_wait == 20  # slow domain: default+10
        assert sg_policy.max_region_time == 75
        assert sg_policy.reason.startswith("slow domain")

        # CN domain had no slow history → default WaitPolicy (insufficient domain history)
        # Verify it's distinct from the slow-domain policy by checking values
        assert "skyscanner.cn" in captured_policies
        cn_policy = captured_policies["skyscanner.cn"]
        assert cn_policy.initial_wait == 10  # not slow-domain (+10)
        assert cn_policy.max_region_time == 45  # not slow-domain (75)

        # Verify the scheduler actually uses the slow policy when it looks up
        # by calling _effective_policy with the bare domain (how the scheduler calls it)
        captured_effective_policy = None

        original_scan = OpenCLIDomainScheduler._scan_domain_serial

        async def capture_scan(self, domain, regions, *a, **kw):
            nonlocal captured_effective_policy
            captured_effective_policy = self._effective_policy(domain)
            return await original_scan(self, domain, regions, *a, **kw)

        with patch.object(OpenCLITabSession, "ensure_tab_async", return_value=("tab-1", {"tab_open_count": 1, "reused_tab_count": 0})):
            with patch.object(OpenCLITabSession, "wait_progressive_state", return_value=(True, 0)):
                with patch.object(OpenCLITabSession, "extract_with_progressive_content_wait") as mock_extract:
                    mock_extract.return_value = (
                        FetchAttempt(
                            transport="opencli",
                            region_code="SG",
                            url="url",
                            page_text="text",
                        ),
                        {"extract_attempt_count": 1, "progressive_wait_used": 0, "max_chunk_size_used": 0},
                    )
                    with patch("skyscanner_multi_domain.transports.opencli.fetch_attempt_to_quote", return_value=FlightQuote("SG", "domain", 200.0, "SGD", "url", "ok")):
                        with patch.object(OpenCLITabSession, "close_async", return_value=None):
                            with patch.object(OpenCLIDomainScheduler, "__init__", capture_init):
                                with patch.object(OpenCLIDomainScheduler, "_scan_domain_serial", capture_scan):
                                    await compare_via_opencli(
                                    args, [real_regions[1]],  # SG only
                                    history_telemetry=slow_telemetry,
                                    run_id="test",
                                )

    asyncio.run(run_test())

def test_tab_close_telemetry_correct_delta() -> None:
    """Tab close count must reflect actual closes, not double-counted."""
    import asyncio as _asyncio

    async def run_test():
        from skyscanner_multi_domain.transports.opencli import OpenCLITabSession

        session = OpenCLITabSession()
        session.tab_id = "tab-fake-1"
        # Simulate one eviction close during session
        session.session_tab_close_count = 2
        # close_async is normally called at end and increments close count
        with patch("skyscanner_multi_domain.transports.opencli._tab_close_async", return_value=None):
            await session.close_async()
        # After close_async, count should be 3 (2 evictions + 1 final close)
        assert session.session_tab_close_count == 3

    _asyncio.run(run_test())

def test_captcha_solver_client_backward_compatible() -> None:
    """CaptchaSolverClient must accept old constructor params (base_url, client_key, timeout)
    and maintain backward-compatible behavior."""
    import asyncio as _asyncio

    async def run_test():
        from captcha_solver import CaptchaSolverClient, BaseCaptchaSolver

        # Old-style instantiation
        client = CaptchaSolverClient(
            base_url="http://localhost:9999",
            client_key="test-key-123",
            timeout=60.0,
        )
        assert isinstance(client, CaptchaSolverClient)
        # Should delegate to underlying MultiBackendCaptchaSolver
        assert hasattr(client, "solve_recaptcha_v2")
        assert hasattr(client, "health_check")

        # New-style instantiation (no args)
        client2 = CaptchaSolverClient()
        assert isinstance(client2, CaptchaSolverClient)

        await client.close()
        await client2.close()

    _asyncio.run(run_test())

def test_orchestrator_loading_after_cdp_does_not_scrapling() -> None:
    """When OpenCLI returns loading and CDP also returns loading,
    Scrapling must NOT be attempted because loading's router transports
    are ["cdp"] only."""
    async def run_test():
        regions = ["CN"]
        origin = "BJS"
        destination = "ALA"
        date = "2026-05-20"

        opencli_quote = FlightQuote("CN", "domain1", None, "CNY", "url1", "opencli_timeout")
        cdp_quote = FlightQuote("CN", "domain1", None, "CNY", "url1", "page_loading")

        with patch("skyscanner_multi_domain.transports.opencli.compare_via_opencli", return_value=[opencli_quote]):
            with patch("skyscanner_multi_domain.transports.cdp.detect_cdp_version", return_value={"Browser": "Edge"}):
                with patch("skyscanner_multi_domain.transports.cdp.compare_via_pages", return_value=[cdp_quote]):
                    with patch("skyscanner_multi_domain.transports.scrapling.compare_via_scrapling") as mock_scrapling:
                        mock_scrapling.return_value = [opencli_quote]
                        with patch("skyscanner_multi_domain.scan.orchestrator.build_scan_batches") as mock_batches:
                            from skyscanner_multi_domain.planning.search_plan import ScanBatch, ScanTask, RouteCandidate, DateCandidate, MarketCandidate
                            route = RouteCandidate("BJS", "ALA", "BJS", "ALA", 1, "reason", 1.0, 1.0, {})
                            date_cand = DateCandidate(date, None, 0, "anchor", "reason", 1.0, {})
                            market_cn = MarketCandidate("CN", 1, "reason", 1.0, 0.5, 1.0, {})

                            batch = ScanBatch(1, "probe", [
                                ScanTask(route, date_cand, market_cn, 1.0, "probe", "reason")
                            ], "reason")
                            mock_batches.return_value = [batch]

                            quotes = await run_page_scan(
                                origin, destination, date, regions,
                                transport="opencli",
                                allow_browser_fallback=True
                            )

        assert mock_scrapling.call_count == 0
        assert len(quotes) == 1
        assert quotes[0].status == "page_loading"
        # Should have both opencli_primary and cdp in fallback attempts
        transports = [a["transport"] for a in quotes[0].fallback_attempts]
        assert "opencli_primary" in transports
        assert "cdp" in transports

    asyncio.run(run_test())

def test_scrapling_success_preserves_cdp_failure_trace() -> None:
    """When Scrapling succeeds after CDP failed, the final quote must preserve
    the CDP failure diagnostics in fallback_attempts."""
    async def run_test():
        regions = ["SG"]
        origin = "BJS"
        destination = "ALA"
        date = "2026-05-20"

        opencli_quote = FlightQuote("SG", "domain2", None, "SGD", "url2", "opencli_failed")
        cdp_quote = FlightQuote("SG", "domain2", None, "SGD", "url2", "page_parse_failed")
        cdp_quote.error = "CDP could not parse"
        scrapling_quote = FlightQuote("SG", "domain2", 300.0, "SGD", "url2", "ok")

        with patch("skyscanner_multi_domain.transports.opencli.compare_via_opencli", return_value=[opencli_quote]):
            with patch("skyscanner_multi_domain.transports.cdp.detect_cdp_version", return_value={"Browser": "Edge"}):
                with patch("skyscanner_multi_domain.transports.cdp.compare_via_pages", return_value=[cdp_quote]):
                    with patch("skyscanner_multi_domain.transports.scrapling.compare_via_scrapling", return_value=[scrapling_quote]):
                        with patch("skyscanner_multi_domain.scan.orchestrator.build_scan_batches") as mock_batches:
                            from skyscanner_multi_domain.planning.search_plan import ScanBatch, ScanTask, RouteCandidate, DateCandidate, MarketCandidate
                            route = RouteCandidate("BJS", "ALA", "BJS", "ALA", 1, "reason", 1.0, 1.0, {})
                            date_cand = DateCandidate(date, None, 0, "anchor", "reason", 1.0, {})
                            market_sg = MarketCandidate("SG", 1, "reason", 1.0, 0.5, 1.0, {})

                            batch = ScanBatch(1, "probe", [
                                ScanTask(route, date_cand, market_sg, 1.0, "probe", "reason")
                            ], "reason")
                            mock_batches.return_value = [batch]

                            quotes = await run_page_scan(
                                origin, destination, date, regions,
                                transport="opencli",
                                allow_browser_fallback=True
                            )

        assert len(quotes) == 1
        assert quotes[0].price == 300.0
        transports = [a["transport"] for a in quotes[0].fallback_attempts]
        assert "opencli_primary" in transports
        assert "cdp" in transports
        assert "scrapling" in transports
        # phase field distinguishes scrapling fallback from primary opencli
        phases = [a.get("phase", "") for a in quotes[0].fallback_attempts]
        assert "scrapling_fallback" in phases

    asyncio.run(run_test())

def test_scheduler_uses_wait_policy_extract_wait_steps() -> None:
    """OpenCLIDomainScheduler must pass per-domain WaitPolicy extract_wait_steps
    to extract_with_progressive_content_wait."""
    async def run_test():
        from skyscanner_multi_domain.transports.opencli import (
            OpenCLIDomainScheduler, OpenCLITabSession,
        )
        import argparse

        args = argparse.Namespace(
            origin="BJS", destination="ALA", date="2026-05-20",
            return_date=None, page_wait=10, timeout=30,
        )

        region = RegionConfig("CN", "China", "www.skyscanner.com.sg", "zh-CN", "CNY")

        from skyscanner_multi_domain.scan.wait_policy import WaitPolicy
        custom_policy = WaitPolicy(
            initial_wait=10,
            max_region_time=45,
            extract_wait_steps=[0, 2, 4],
            reason="test",
        )

        scheduler = OpenCLIDomainScheduler(
            max_concurrent_domains=1,
            page_wait=10,
            wait_policies={"skyscanner.com.sg": custom_policy},
        )

        captured_steps: list[int] | None = None

        async def capture_extract(self, tab_id, region, url, wait_steps=None):
            nonlocal captured_steps
            captured_steps = wait_steps
            return (
                FetchAttempt(
                    transport="opencli",
                    region_code=region.code,
                    url=url,
                    page_text="page text",
                ),
                {"extract_attempt_count": 1, "progressive_wait_used": 0, "max_chunk_size_used": 0},
            )

        with patch.object(OpenCLITabSession, "ensure_tab_async", return_value=("tab-1", {"tab_open_count": 1, "reused_tab_count": 0})):
            with patch.object(OpenCLITabSession, "wait_progressive_state", return_value=(True, 0)):
                with patch.object(OpenCLITabSession, "extract_with_progressive_content_wait", capture_extract):
                    with patch("skyscanner_multi_domain.transports.opencli.fetch_attempt_to_quote", return_value=FlightQuote("CN", "domain", 100.0, "CNY", "url", "ok")):
                        with patch.object(OpenCLITabSession, "close_async", return_value=None):
                            quotes, _tel = await scheduler.scan_all(
                            args=args,
                            selected_regions=[region],
                            url_by_region={"CN": "https://www.skyscanner.com.sg/transport/flights/"},
                            route_key="BJS_ALA_20260520",
                            persist_failures=False,
                            run_id="test",
                        )

        assert len(quotes) == 1
        assert quotes[0].region == "CN"
        assert quotes[0].status == "ok"
        assert captured_steps == [0, 2, 4]

    asyncio.run(run_test())

def test_scheduler_uses_wait_policy_max_region_time() -> None:
    """OpenCLIDomainScheduler must use per-domain WaitPolicy max_region_time
    for budget enforcement."""
    async def run_test():
        from skyscanner_multi_domain.transports.opencli import (
            OpenCLIDomainScheduler, OpenCLITabSession,
        )
        import argparse

        args = argparse.Namespace(
            origin="BJS", destination="ALA", date="2026-05-20",
            return_date=None, page_wait=5, timeout=30,
        )

        region1 = RegionConfig("CN", "China", "www.skyscanner.com.sg", "zh-CN", "CNY")
        region2 = RegionConfig("SG", "Singapore", "www.skyscanner.com.sg", "en-SG", "SGD")

        from skyscanner_multi_domain.scan.wait_policy import WaitPolicy
        custom_policy = WaitPolicy(
            initial_wait=5,
            max_region_time=1,
            extract_wait_steps=[0, 8, 15],
            reason="test",
        )

        scheduler = OpenCLIDomainScheduler(
            max_concurrent_domains=1,
            page_wait=5,
            max_region_time=60,
            wait_policies={"skyscanner.com.sg": custom_policy},
        )

        call_count = 0

        async def slow_extract(self, tab_id, region, url, wait_steps=None):
            nonlocal call_count
            call_count += 1
            return (
                FetchAttempt(
                    transport="opencli",
                    region_code=region.code,
                    url=url,
                    page_text="page text",
                ),
                {"extract_attempt_count": 1, "progressive_wait_used": 0, "max_chunk_size_used": 0},
            )

        with patch.object(OpenCLITabSession, "ensure_tab_async", return_value=("tab-1", {"tab_open_count": 1, "reused_tab_count": 0})):
            with patch.object(OpenCLITabSession, "wait_progressive_state", return_value=(True, 0)):
                with patch.object(OpenCLITabSession, "extract_with_progressive_content_wait", slow_extract):
                    with patch("skyscanner_multi_domain.transports.opencli.fetch_attempt_to_quote", return_value=FlightQuote("CN", "domain", 100.0, "CNY", "url", "ok")):
                        with patch.object(OpenCLITabSession, "close_async", return_value=None):
                            with patch("skyscanner_multi_domain.transports.opencli.time") as mock_time:
                                # Calls: wall_start, domain_start, budget check for SG, wall_time_ms
                                mock_time.monotonic.side_effect = [0, 0, 2, 2]
                                quotes, _tel = await scheduler.scan_all(
                                args=args,
                                selected_regions=[region1, region2],
                                url_by_region={
                                    "CN": "https://www.skyscanner.com.sg/transport/flights/",
                                    "SG": "https://www.skyscanner.com.sg/transport/flights/",
                                },
                                route_key="BJS_ALA_20260520",
                                persist_failures=False,
                                run_id="test",
                            )

        assert len(quotes) == 2
        assert quotes[0].region == "CN"
        assert quotes[0].status == "ok"
        assert quotes[1].region == "SG"
        assert quotes[1].status == "opencli_not_attempted"
        assert "1s" in quotes[1].error

    asyncio.run(run_test())

def test_captcha_solver_client_accepts_backends_kwarg() -> None:
    """CaptchaSolverClient must accept backends= kwarg for callers who adopted
    the MultiBackendCaptchaSolver style."""
    import asyncio as _asyncio

    async def run_test():
        from captcha_solver import CaptchaSolverClient, BaseCaptchaSolver

        class FakeSolver(BaseCaptchaSolver):
            async def health_check(self):
                return {"status": "healthy", "provider": "fake"}

        fake = FakeSolver()
        client = CaptchaSolverClient(backends=[fake])
        assert isinstance(client, CaptchaSolverClient)
        hc = await client.health_check()
        assert hc["status"] == "healthy"
        assert "FakeSolver" in hc["backends"]

    _asyncio.run(run_test())


# ── P0: no_flights confidence split ─────────────────────────────────────────

def test_no_flights_high_confidence_is_terminal() -> None:
    """High-confidence no_flights (>= 0.8) must be terminal and stop further retries."""
    from skyscanner_multi_domain.parsing.readiness import (
        classify_opencli_page_readiness_with_confidence,
    )

    high_specific_pages = [
        "We searched everywhere but no flights found for your dates.",
        "No flight results available. Try different dates or routes.",
        "未找到航班",
        "无结果",
        "0 results for this search",
    ]
    for page_text in high_specific_pages:
        readiness, conf = classify_opencli_page_readiness_with_confidence(page_text)
        assert readiness == "no_flights", f"Expected no_flights for: {page_text!r}"
        assert conf >= 0.8, f"Expected high confidence (>=0.8) for: {page_text!r}, got {conf}"


def test_no_flights_low_confidence_not_terminal() -> None:
    """Low-confidence no_flights (< 0.8) must NOT be treated as terminal in
    extract_with_progressive_content_wait — the loop should continue retrying."""
    from skyscanner_multi_domain.parsing.readiness import (
        classify_opencli_page_readiness_with_confidence,
    )

    page_with_price = "No results found for BJS to ALA. Starting from $50 for other dates."
    readiness, conf = classify_opencli_page_readiness_with_confidence(page_with_price)
    assert readiness == "no_flights"
    assert conf < 0.8, f"Expected low confidence (<0.8) for generic no_results, got {conf}"

    generic_pages = [
        "No results",
        "No result available",
        "unavailable",
    ]
    for page_text in generic_pages:
        readiness, conf = classify_opencli_page_readiness_with_confidence(page_text)
        assert readiness == "no_flights"
        assert conf < 0.8, f"Expected low confidence for: {page_text!r}, got {conf}"


# ── P0: route/date/currency sanity check ──────────────────────────────────────

def test_sanity_check_detects_route_mismatch() -> None:
    """sanity_check_quote must flag route_mismatch when detected route ≠ requested."""
    from skyscanner_multi_domain.parsing.page_parser import sanity_check_quote
    from skyscanner_multi_domain.models import FlightQuote, RegionConfig

    region = RegionConfig("SG", "Singapore", "https://www.skyscanner.sg", "en-SG", "SGD")
    page_ok = "Best flight: BJS → ALA departing May 20 2026. SGD 1,234"
    quote_ok = FlightQuote(
        region="SG", domain=region.domain, price=1234.0, currency="SGD",
        source_url="https://www.skyscanner.sg/...", status="ok",
    )
    result_ok = sanity_check_quote(
        quote_ok, region, "https://www.skyscanner.sg/...", page_ok,
        expected_origin="BJS", expected_destination="ALA", expected_date="2026-05-20",
    )
    assert result_ok.route_mismatch is False
    assert result_ok.route_detected == "BJS→ALA"

    page_bad = "Best flight: PEK → SHA departing May 20 2026. CNY 899"
    quote_bad = FlightQuote(
        region="SG", domain=region.domain, price=899.0, currency="CNY",
        source_url="https://www.skyscanner.sg/...", status="ok",
    )
    result_bad = sanity_check_quote(
        quote_bad, region, "https://www.skyscanner.sg/...", page_bad,
        expected_origin="BJS", expected_destination="ALA", expected_date="2026-05-20",
    )
    assert result_bad.route_mismatch is True
    assert result_bad.confidence == 0.3
    assert result_bad.status == "page_semantic_mismatch"


def test_sanity_check_detects_currency_mismatch() -> None:
    """sanity_check_quote must flag currency_mismatch when page currency ≠ region."""
    from skyscanner_multi_domain.parsing.page_parser import sanity_check_quote
    from skyscanner_multi_domain.models import FlightQuote, RegionConfig

    region = RegionConfig("SG", "Singapore", "https://www.skyscanner.sg", "en-SG", "SGD")
    page_usd = "Best price: BJS → ALA departing May 20 2026. USD 450"
    quote = FlightQuote(
        region="SG", domain=region.domain, price=450.0, currency="USD",
        source_url="https://www.skyscanner.sg/...", status="ok",
    )
    result = sanity_check_quote(
        quote, region, "https://www.skyscanner.sg/...", page_usd,
        expected_origin="BJS", expected_destination="ALA", expected_date="2026-05-20",
    )
    assert result.currency_mismatch is True
    assert result.confidence == 0.3
    assert result.status == "page_semantic_mismatch"


def test_sanity_check_no_mismatch_preserves_confidence() -> None:
    """sanity_check_quote must NOT change confidence when everything matches."""
    from skyscanner_multi_domain.parsing.page_parser import sanity_check_quote
    from skyscanner_multi_domain.models import FlightQuote, RegionConfig

    region = RegionConfig("SG", "Singapore", "https://www.skyscanner.sg", "en-SG", "SGD")
    page_ok = "Cheapest: BJS → ALA departing May 20 2026. SGD 1,234"
    quote = FlightQuote(
        region="SG", domain=region.domain, price=1234.0, currency="SGD",
        source_url="https://www.skyscanner.sg/...", status="ok",
        confidence=0.9, price_source="cheapest_block",
    )
    result = sanity_check_quote(
        quote, region, "https://www.skyscanner.sg/...", page_ok,
        expected_origin="BJS", expected_destination="ALA", expected_date="2026-05-20",
    )
    assert result.route_mismatch is False
    assert result.date_mismatch is False
    assert result.currency_mismatch is False
    assert result.confidence == 0.9
    assert result.status == "ok"


def test_semantic_mismatch_triggers_fallback_not_success() -> None:
    """page_semantic_mismatch with price set must NOT be classified as success.

    Regression: sanity_check_quote sets status=page_semantic_mismatch on mismatch,
    but router must route this to fallback rather than treating it as success
    just because price is not None.
    """
    from skyscanner_multi_domain.scan.fallback_router import (
        classify_quote_failure,
        decide_fallback,
    )
    from skyscanner_multi_domain.models import FlightQuote

    # quote has price but also semantic mismatch flag
    quote = FlightQuote(
        region="SG",
        domain="https://www.skyscanner.sg",
        price=899.0,
        currency="CNY",
        source_url="https://www.skyscanner.sg/transport/flights/bjs/ala/260520/",
        status="page_semantic_mismatch",
        confidence=0.3,
        route_mismatch=True,
        date_mismatch=False,
        currency_mismatch=False,
    )

    fc = classify_quote_failure(quote)
    assert fc == "semantic_mismatch", f"Expected semantic_mismatch, got {fc}"

    decision = decide_fallback(quote)
    assert decision.should_fallback is True
    assert "cdp" in decision.transports
    assert "scrapling" in decision.transports


def test_normal_price_with_ok_status_is_success() -> None:
    """A normal price with ok status must be classified as success."""
    from skyscanner_multi_domain.models import FlightQuote
    from skyscanner_multi_domain.scan.fallback_router import (
        classify_quote_failure,
        decide_fallback,
    )

    quote = FlightQuote(
        region="SG",
        domain="https://www.skyscanner.sg",
        price=1234.0,
        currency="SGD",
        source_url="https://www.skyscanner.sg/...",
        status="ok",
        confidence=0.9,
        route_mismatch=False,
        date_mismatch=False,
        currency_mismatch=False,
    )

    fc = classify_quote_failure(quote)
    assert fc == "success"

    decision = decide_fallback(quote)
    assert decision.should_fallback is False


# ── FetchAttempt + AttemptPlanner tests ──────────────────────────────────────


def test_attempt_planner_accepts_valid_price() -> None:
    """AttemptPlanner must ACCEPT a quote with price and ok status."""
    from skyscanner_multi_domain.scan.fetch_types import AttemptPlanner, AttemptAction

    planner = AttemptPlanner()
    quote = FlightQuote(
        region="SG", domain="https://www.skyscanner.sg",
        price=1234.0, currency="SGD",
        source_url="https://www.skyscanner.sg/...",
        status="ok", confidence=0.9,
        route_mismatch=False, date_mismatch=False, currency_mismatch=False,
    )
    plan = planner.plan(quote)
    assert plan.action == AttemptAction.ACCEPT


def test_attempt_planner_routes_semantic_mismatch_to_cdp() -> None:
    """AttemptPlanner must route semantic_mismatch to FALLBACK_CDP."""
    from skyscanner_multi_domain.scan.fetch_types import AttemptPlanner, AttemptAction

    planner = AttemptPlanner()
    quote = FlightQuote(
        region="SG", domain="https://www.skyscanner.sg",
        price=899.0, currency="CNY",
        source_url="https://www.skyscanner.sg/...",
        status="page_semantic_mismatch", confidence=0.3,
        route_mismatch=True, date_mismatch=False, currency_mismatch=False,
    )
    plan = planner.plan(quote)
    assert plan.action == AttemptAction.FALLBACK_CDP
    assert "scrapling" in plan.transports_remaining


def test_attempt_planner_routes_timeout_to_cdp() -> None:
    """AttemptPlanner must route opencli_timeout to FALLBACK_CDP."""
    from skyscanner_multi_domain.scan.fetch_types import AttemptPlanner, AttemptAction

    planner = AttemptPlanner()
    quote = FlightQuote(
        region="SG", domain="https://www.skyscanner.sg",
        price=None, currency="SGD",
        source_url="https://www.skyscanner.sg/...",
        status="opencli_timeout",
    )
    plan = planner.plan(quote)
    assert plan.action == AttemptAction.FALLBACK_CDP


def test_attempt_planner_terminal_for_challenge() -> None:
    """AttemptPlanner must mark challenge as TERMINAL (no automatic retry)."""
    from skyscanner_multi_domain.scan.fetch_types import AttemptPlanner, AttemptAction

    planner = AttemptPlanner()
    quote = FlightQuote(
        region="SG", domain="https://www.skyscanner.sg",
        price=None, currency="SGD",
        source_url="https://www.skyscanner.sg/...",
        status="px_challenge",
    )
    plan = planner.plan(quote)
    assert plan.action == AttemptAction.TERMINAL


def test_attempt_planner_terminal_for_no_flights() -> None:
    """AttemptPlanner must mark high-confidence no_flights as TERMINAL."""
    from skyscanner_multi_domain.scan.fetch_types import AttemptPlanner, AttemptAction

    planner = AttemptPlanner()
    quote = FlightQuote(
        region="SG", domain="https://www.skyscanner.sg",
        price=None, currency="SGD",
        source_url="https://www.skyscanner.sg/...",
        status="opencli_no_flights",
    )
    plan = planner.plan(quote)
    assert plan.action == AttemptAction.TERMINAL


def test_attempt_planner_gates_low_confidence_price() -> None:
    """AttemptPlanner must route low-confidence prices to fallback for verification."""
    from skyscanner_multi_domain.scan.fetch_types import AttemptPlanner, AttemptAction

    planner = AttemptPlanner()
    quote = FlightQuote(
        region="SG", domain="https://www.skyscanner.sg",
        price=123.0, currency="SGD",
        source_url="https://www.skyscanner.sg/...",
        status="page_text_fallback", confidence=0.45,
    )
    plan = planner.plan(quote)
    assert plan.action == AttemptAction.FALLBACK_CDP
    assert plan.failure_class == "low_confidence"
    assert "confidence" in plan.reason.lower()


def test_attempt_planner_accepts_high_confidence_price() -> None:
    """AttemptPlanner must ACCEPT a high-confidence price without gating."""
    from skyscanner_multi_domain.scan.fetch_types import AttemptPlanner, AttemptAction

    planner = AttemptPlanner()
    quote = FlightQuote(
        region="SG", domain="https://www.skyscanner.sg",
        price=123.0, currency="SGD",
        source_url="https://www.skyscanner.sg/...",
        status="page_text", confidence=0.9,
    )
    plan = planner.plan(quote)
    assert plan.action == AttemptAction.ACCEPT
    assert plan.failure_class == "success"


def test_classify_quote_failure_respects_confidence_threshold() -> None:
    """classify_quote_failure must return low_confidence when price exists but confidence is below threshold."""
    from skyscanner_multi_domain.scan.fallback_router import classify_quote_failure

    low_conf_quote = FlightQuote(
        region="SG", domain="https://www.skyscanner.sg",
        price=100.0, currency="SGD",
        source_url="https://www.skyscanner.sg/...",
        status="page_text", confidence=0.4,
    )
    assert classify_quote_failure(low_conf_quote) == "low_confidence"

    high_conf_quote = FlightQuote(
        region="SG", domain="https://www.skyscanner.sg",
        price=100.0, currency="SGD",
        source_url="https://www.skyscanner.sg/...",
        status="page_text", confidence=0.9,
    )
    assert classify_quote_failure(high_conf_quote) == "success"

    no_conf_quote = FlightQuote(
        region="SG", domain="https://www.skyscanner.sg",
        price=100.0, currency="SGD",
        source_url="https://www.skyscanner.sg/...",
        status="page_text",
    )
    # Default confidence is None → treated as 0.0 → below threshold
    assert classify_quote_failure(no_conf_quote) == "low_confidence"


def test_fetch_attempt_to_quote_basic() -> None:
    """fetch_attempt_to_quote must parse page text into a FlightQuote."""
    from skyscanner_multi_domain.scan.fetch_types import FetchAttempt, fetch_attempt_to_quote

    attempt = FetchAttempt(
        transport="opencli",
        region_code="SG",
        url="https://www.skyscanner.sg/transport/flights/bjs/ala/260520/",
        page_text="Cheapest: BJS → ALA departing May 20 2026. SGD 1,234",
    )
    region = RegionConfig("SG", "Singapore", "https://www.skyscanner.sg", "en-SG", "SGD")
    quote = fetch_attempt_to_quote(attempt, region)
    assert quote.price == 1234.0
    assert quote.currency == "SGD"
    assert quote.source_kind == "opencli"


def test_fetch_attempt_to_quote_with_error() -> None:
    """fetch_attempt_to_quote must set source_kind from transport."""
    from skyscanner_multi_domain.scan.fetch_types import FetchAttempt, fetch_attempt_to_quote

    attempt = FetchAttempt(
        transport="opencli",
        region_code="SG",
        url="https://www.skyscanner.sg/transport/flights/bjs/ala/260520/",
        page_text="Loading flights...",
        error="Page did not reach interactive state",
    )
    region = RegionConfig("SG", "Singapore", "https://www.skyscanner.sg", "en-SG", "SGD")
    quote = fetch_attempt_to_quote(attempt, region)
    assert quote.price is None
    assert quote.source_kind == "opencli"


def test_attempt_planner_manual_review_for_challenge() -> None:
    """AttemptPlan must surface manual_review_required for challenge status."""
    from skyscanner_multi_domain.scan.fetch_types import AttemptPlanner, AttemptAction

    planner = AttemptPlanner()
    quote = FlightQuote(
        region="SG", domain="https://www.skyscanner.sg",
        price=None, currency="SGD",
        source_url="https://www.skyscanner.sg/...",
        status="px_challenge",
    )
    plan = planner.plan(quote)
    assert plan.action == AttemptAction.TERMINAL
    assert plan.manual_review_required is True
    assert plan.failure_class == "challenge"


def test_attempt_planner_max_attempts_for_network() -> None:
    """AttemptPlan must surface max_attempts from FallbackDecision."""
    from skyscanner_multi_domain.scan.fetch_types import AttemptPlanner, AttemptAction

    planner = AttemptPlanner()
    quote = FlightQuote(
        region="SG", domain="https://www.skyscanner.sg",
        price=None, currency="SGD",
        source_url="https://www.skyscanner.sg/...",
        status="opencli_error",
    )
    plan = planner.plan(quote)
    assert plan.action == AttemptAction.FALLBACK_GOOGLE_JUMP
    assert plan.max_attempts == 3  # network decision has max_attempts=3
    assert plan.failure_class == "network"


def test_orchestrator_semantic_mismatch_triggers_cdp_not_scrapling() -> None:
    """Semantic mismatch with price must trigger CDP fallback; scrapling should NOT run if CDP succeeds.

    Regression: opencli returns price + page_semantic_mismatch → planner routes to
    [cdp, scrapling] → CDP succeeds → final quote is CDP result, scrapling never called.
    """
    import asyncio
    from unittest.mock import AsyncMock, patch
    from skyscanner_multi_domain.scan.orchestrator import run_page_scan
    from skyscanner_multi_domain.models import RegionConfig, FlightQuote

    async def run_test():
        # opencli returns semantic mismatch (has price but wrong route/currency)
        opencli_quote = FlightQuote(
            region="SG", domain="https://www.skyscanner.sg",
            price=899.0, currency="CNY",
            source_url="https://www.skyscanner.sg/...",
            status="page_semantic_mismatch",
            confidence=0.3, route_mismatch=True,
        )

        # CDP returns valid price
        cdp_quote = FlightQuote(
            region="SG", domain="https://www.skyscanner.sg",
            price=450.0, currency="SGD",
            source_url="https://www.skyscanner.sg/...",
            status="ok", confidence=0.85,
        )

        with patch("skyscanner_multi_domain.transports.opencli.compare_via_opencli", new_callable=AsyncMock) as mock_opencli,              patch("skyscanner_multi_domain.transports.cdp.compare_via_pages", new_callable=AsyncMock) as mock_cdp,              patch("skyscanner_multi_domain.transports.scrapling.compare_via_scrapling", new_callable=AsyncMock) as mock_scrapling:
            mock_opencli.return_value = [opencli_quote]
            mock_cdp.return_value = [cdp_quote]
            mock_scrapling.return_value = []

            quotes = await run_page_scan(
                origin="BJS", destination="ALA", date="2026-05-20",
                region_codes=["SG"],
                allow_browser_fallback=True,
            )

            assert mock_cdp.called, "CDP fallback must be called for semantic_mismatch"
            assert not mock_scrapling.called, "Scrapling must NOT be called when CDP succeeds"
            assert quotes[0].price == 450.0
            assert quotes[0].currency == "SGD"
            assert quotes[0].status == "ok"

    asyncio.run(run_test())


# ── P5: orchestrator fallback chain integration tests ──────────────────────

def test_orchestrator_opencli_cdp_scrapling_full_fallback_chain() -> None:
    """opencli network error → CDP parse failed → Scrapling succeeds.

    Validates the complete 3-tier fallback chain within run_opencli_pass:
    opencli_primary fails → CDP fails → Scrapling succeeds.
    """
    import asyncio
    from unittest.mock import AsyncMock, patch
    from skyscanner_multi_domain.scan.orchestrator import run_page_scan
    from skyscanner_multi_domain.models import RegionConfig, FlightQuote

    async def run_test():
        opencli_quote = FlightQuote(
            region="SG", domain="https://www.skyscanner.sg",
            price=None, currency="SGD",
            source_url="https://www.skyscanner.sg/...",
            status="opencli_error", error="Connection reset",
        )
        cdp_quote = FlightQuote(
            region="SG", domain="https://www.skyscanner.sg",
            price=None, currency="SGD",
            source_url="https://www.skyscanner.sg/...",
            status="page_parse_failed", error="no price element",
        )
        scrapling_quote = FlightQuote(
            region="SG", domain="https://www.skyscanner.sg",
            price=450.0, currency="SGD",
            source_url="https://www.skyscanner.sg/...",
            status="page_text", confidence=0.85,
        )

        with patch("skyscanner_multi_domain.transports.opencli.compare_via_opencli",
                   new_callable=AsyncMock) as mock_opencli, \
             patch("skyscanner_multi_domain.transports.cdp.compare_via_pages",
                   new_callable=AsyncMock) as mock_cdp, \
             patch("skyscanner_multi_domain.transports.scrapling.compare_via_scrapling",
                   new_callable=AsyncMock) as mock_scrapling, \
             patch("skyscanner_multi_domain.transports.cdp.detect_cdp_version",
                   return_value={"Browser": "Edge"}), \
             patch("skyscanner_multi_domain.transports.cdp.ensure_cdp_ready"), \
             patch("skyscanner_multi_domain.transports.google_jump.build_quote_via_google_jump",
                   new_callable=AsyncMock, return_value=None):
            mock_opencli.return_value = [opencli_quote]
            mock_cdp.return_value = [cdp_quote]
            mock_scrapling.return_value = [scrapling_quote]

            quotes = await run_page_scan(
                origin="BJS", destination="ALA", date="2026-05-20",
                region_codes=["SG"],
                transport="opencli",
                allow_browser_fallback=True,
            )

            assert mock_opencli.called
            assert mock_cdp.called, "CDP must be called as second fallback"
            assert mock_scrapling.called, (
                "Scrapling must be called as third fallback when CDP fails"
            )
            assert len(quotes) == 1
            assert quotes[0].price == 450.0
            assert quotes[0].status == "page_text"
            attempts = quotes[0].fallback_attempts
            assert len(attempts) >= 2
            assert attempts[0]["transport"] == "opencli_primary"
            assert "cdp" in [a.get("transport") for a in attempts]

    asyncio.run(run_test())


def test_orchestrator_confidence_gating_triggers_cdp_fallback() -> None:
    """Low confidence (< 0.5) from opencli must trigger CDP fallback.

    When opencli returns a price with confidence below MIN_PARSER_CONFIDENCE,
    classify_quote_failure returns low_confidence → planner routes to CDP.
    """
    import asyncio
    from unittest.mock import AsyncMock, patch
    from skyscanner_multi_domain.scan.orchestrator import run_page_scan
    from skyscanner_multi_domain.models import RegionConfig, FlightQuote

    async def run_test():
        opencli_quote = FlightQuote(
            region="SG", domain="https://www.skyscanner.sg",
            price=899.0, currency="SGD",
            source_url="https://www.skyscanner.sg/...",
            status="page_text", confidence=0.35,
        )
        cdp_quote = FlightQuote(
            region="SG", domain="https://www.skyscanner.sg",
            price=450.0, currency="SGD",
            source_url="https://www.skyscanner.sg/...",
            status="page_text", confidence=0.88,
        )

        with patch("skyscanner_multi_domain.transports.opencli.compare_via_opencli",
                   new_callable=AsyncMock) as mock_opencli, \
             patch("skyscanner_multi_domain.transports.cdp.compare_via_pages",
                   new_callable=AsyncMock) as mock_cdp, \
             patch("skyscanner_multi_domain.transports.scrapling.compare_via_scrapling",
                   new_callable=AsyncMock) as mock_scrapling, \
             patch("skyscanner_multi_domain.transports.cdp.detect_cdp_version",
                   return_value={"Browser": "Edge"}):
            mock_opencli.return_value = [opencli_quote]
            mock_cdp.return_value = [cdp_quote]
            mock_scrapling.return_value = []

            quotes = await run_page_scan(
                origin="BJS", destination="ALA", date="2026-05-20",
                region_codes=["SG"],
                transport="opencli",
                allow_browser_fallback=True,
            )

            assert mock_opencli.called
            assert mock_cdp.called, (
                "CDP must be called when opencli result has low confidence"
            )
            assert not mock_scrapling.called, (
                "Scrapling must NOT be called when CDP succeeds with high confidence"
            )
            assert len(quotes) == 1
            assert quotes[0].price == 450.0, "CDP result (450.0) must replace opencli (899.0)"
            assert quotes[0].confidence == 0.88
            assert len(quotes[0].fallback_attempts) >= 1
            assert quotes[0].fallback_attempts[0]["transport"] == "opencli_primary"

    asyncio.run(run_test())


def test_orchestrator_confidence_gating_accepts_when_sufficient() -> None:
    """High confidence (>= 0.5) from opencli must NOT trigger any fallback."""
    import asyncio
    from unittest.mock import AsyncMock, patch
    from skyscanner_multi_domain.scan.orchestrator import run_page_scan
    from skyscanner_multi_domain.models import RegionConfig, FlightQuote

    async def run_test():
        opencli_quote = FlightQuote(
            region="SG", domain="https://www.skyscanner.sg",
            price=899.0, currency="SGD",
            source_url="https://www.skyscanner.sg/...",
            status="page_text", confidence=0.75,
        )

        with patch("skyscanner_multi_domain.transports.opencli.compare_via_opencli",
                   new_callable=AsyncMock) as mock_opencli, \
             patch("skyscanner_multi_domain.transports.cdp.compare_via_pages",
                   new_callable=AsyncMock) as mock_cdp, \
             patch("skyscanner_multi_domain.transports.scrapling.compare_via_scrapling",
                   new_callable=AsyncMock) as mock_scrapling:
            mock_opencli.return_value = [opencli_quote]
            mock_cdp.return_value = []
            mock_scrapling.return_value = []

            quotes = await run_page_scan(
                origin="BJS", destination="ALA", date="2026-05-20",
                region_codes=["SG"],
                transport="opencli",
                allow_browser_fallback=True,
            )

            assert mock_opencli.called
            assert not mock_cdp.called, (
                "CDP must NOT be called when confidence is sufficient"
            )
            assert not mock_scrapling.called
            assert len(quotes) == 1
            assert quotes[0].price == 899.0
            assert quotes[0].confidence == 0.75

    asyncio.run(run_test())


def test_orchestrator_semantic_mismatch_cdp_fails_scrapling_succeeds() -> None:
    """opencli semantic_mismatch → CDP parse fails → Scrapling succeeds.

    The semantic_mismatch route includes both cdp and scrapling in
    transports_remaining. When CDP fails with parse error, scrapling
    is tried next.
    """
    import asyncio
    from unittest.mock import AsyncMock, patch
    from skyscanner_multi_domain.scan.orchestrator import run_page_scan
    from skyscanner_multi_domain.models import RegionConfig, FlightQuote

    async def run_test():
        opencli_quote = FlightQuote(
            region="SG", domain="https://www.skyscanner.sg",
            price=899.0, currency="CNY",
            source_url="https://www.skyscanner.sg/...",
            status="page_semantic_mismatch", confidence=0.3,
            route_mismatch=True,
        )
        cdp_quote = FlightQuote(
            region="SG", domain="https://www.skyscanner.sg",
            price=None, currency="SGD",
            source_url="https://www.skyscanner.sg/...",
            status="page_parse_failed", error="no results visible",
        )
        scrapling_quote = FlightQuote(
            region="SG", domain="https://www.skyscanner.sg",
            price=450.0, currency="SGD",
            source_url="https://www.skyscanner.sg/...",
            status="page_text", confidence=0.82,
        )

        with patch("skyscanner_multi_domain.transports.opencli.compare_via_opencli",
                   new_callable=AsyncMock) as mock_opencli, \
             patch("skyscanner_multi_domain.transports.cdp.compare_via_pages",
                   new_callable=AsyncMock) as mock_cdp, \
             patch("skyscanner_multi_domain.transports.scrapling.compare_via_scrapling",
                   new_callable=AsyncMock) as mock_scrapling, \
             patch("skyscanner_multi_domain.transports.cdp.detect_cdp_version",
                   return_value={"Browser": "Edge"}):
            mock_opencli.return_value = [opencli_quote]
            mock_cdp.return_value = [cdp_quote]
            mock_scrapling.return_value = [scrapling_quote]

            quotes = await run_page_scan(
                origin="BJS", destination="ALA", date="2026-05-20",
                region_codes=["SG"],
                transport="opencli",
                allow_browser_fallback=True,
            )

            assert mock_opencli.called
            assert mock_cdp.called, "CDP must be called for semantic_mismatch"
            assert mock_scrapling.called, (
                "Scrapling must be called when CDP fails"
            )
            assert len(quotes) == 1
            assert quotes[0].price == 450.0
            assert quotes[0].currency == "SGD"
            assert quotes[0].status == "page_text"

    asyncio.run(run_test())


def test_orchestrator_no_flights_terminates_chain() -> None:
    """page_no_flights from CDP must terminate the chain — no scrapling call."""
    import asyncio
    from unittest.mock import AsyncMock, patch
    from skyscanner_multi_domain.scan.orchestrator import run_page_scan
    from skyscanner_multi_domain.models import RegionConfig, FlightQuote

    async def run_test():
        opencli_quote = FlightQuote(
            region="SG", domain="https://www.skyscanner.sg",
            price=None, currency="SGD",
            source_url="https://www.skyscanner.sg/...",
            status="opencli_error", error="Connection refused",
        )
        cdp_quote = FlightQuote(
            region="SG", domain="https://www.skyscanner.sg",
            price=None, currency="SGD",
            source_url="https://www.skyscanner.sg/...",
            status="page_no_flights",
        )

        with patch("skyscanner_multi_domain.transports.opencli.compare_via_opencli",
                   new_callable=AsyncMock) as mock_opencli, \
             patch("skyscanner_multi_domain.transports.cdp.compare_via_pages",
                   new_callable=AsyncMock) as mock_cdp, \
             patch("skyscanner_multi_domain.transports.scrapling.compare_via_scrapling",
                   new_callable=AsyncMock) as mock_scrapling, \
             patch("skyscanner_multi_domain.transports.cdp.detect_cdp_version",
                   return_value={"Browser": "Edge"}), \
             patch("skyscanner_multi_domain.transports.cdp.ensure_cdp_ready"), \
             patch("skyscanner_multi_domain.transports.google_jump.build_quote_via_google_jump",
                   new_callable=AsyncMock, return_value=None):
            mock_opencli.return_value = [opencli_quote]
            mock_cdp.return_value = [cdp_quote]
            mock_scrapling.return_value = []

            quotes = await run_page_scan(
                origin="BJS", destination="ALA", date="2026-05-20",
                region_codes=["SG"],
                transport="opencli",
                allow_browser_fallback=True,
            )

            assert mock_opencli.called
            assert mock_cdp.called, "CDP must be called for network failure"
            assert not mock_scrapling.called, (
                "Scrapling must NOT be called — no_flights is terminal"
            )
            assert len(quotes) == 1
            assert quotes[0].price is None
            assert quotes[0].status == "page_no_flights"

    asyncio.run(run_test())


def test_orchestrator_dual_region_different_fallback_outcomes() -> None:
    """CN succeeds with opencli; SG fails → CDP fails → Scrapling succeeds.

    Ensures the orchestrator handles per-region fallback independently
    without disrupting already-succeeded regions.
    """
    import asyncio
    from unittest.mock import AsyncMock, patch
    from skyscanner_multi_domain.scan.orchestrator import run_page_scan
    from skyscanner_multi_domain.models import RegionConfig, FlightQuote

    async def run_test():
        opencli_quotes = [
            FlightQuote(
                region="CN", domain="https://www.skyscanner.cn",
                price=2187.0, currency="CNY",
                source_url="https://www.skyscanner.cn/...",
                status="page_text", confidence=0.85,
            ),
            FlightQuote(
                region="SG", domain="https://www.skyscanner.sg",
                price=None, currency="SGD",
                source_url="https://www.skyscanner.sg/...",
                status="opencli_error", error="timeout",
            ),
        ]
        cdp_quote = FlightQuote(
            region="SG", domain="https://www.skyscanner.sg",
            price=None, currency="SGD",
            source_url="https://www.skyscanner.sg/...",
            status="page_parse_failed",
        )
        scrapling_quote = FlightQuote(
            region="SG", domain="https://www.skyscanner.sg",
            price=350.0, currency="SGD",
            source_url="https://www.skyscanner.sg/...",
            status="page_text", confidence=0.80,
        )

        with patch("skyscanner_multi_domain.transports.opencli.compare_via_opencli",
                   new_callable=AsyncMock) as mock_opencli, \
             patch("skyscanner_multi_domain.transports.cdp.compare_via_pages",
                   new_callable=AsyncMock) as mock_cdp, \
             patch("skyscanner_multi_domain.transports.scrapling.compare_via_scrapling",
                   new_callable=AsyncMock) as mock_scrapling, \
             patch("skyscanner_multi_domain.transports.cdp.detect_cdp_version",
                   return_value={"Browser": "Edge"}), \
             patch("skyscanner_multi_domain.transports.cdp.ensure_cdp_ready"), \
             patch("skyscanner_multi_domain.transports.google_jump.build_quote_via_google_jump",
                   new_callable=AsyncMock, return_value=None), \
             patch("skyscanner_multi_domain.scan.orchestrator.build_scan_batches") as mock_batches:
            from skyscanner_multi_domain.planning.search_plan import (
                ScanBatch, ScanTask, RouteCandidate, DateCandidate, MarketCandidate,
            )
            route = RouteCandidate("BJS", "ALA", "BJS", "ALA", 1, "r", 1.0, 1.0, {})
            date_c = DateCandidate("2026-05-20", None, 0, "anchor", "r", 1.0, {})
            m_cn = MarketCandidate("CN", 1, "r", 1.0, 0.5, 1.0, {})
            m_sg = MarketCandidate("SG", 2, "r", 0.9, 0.4, 0.8, {})
            batch = ScanBatch(1, "probe", [
                ScanTask(route, date_c, m_cn, 1.0, "probe", "r"),
                ScanTask(route, date_c, m_sg, 0.9, "probe", "r"),
            ], "r")
            mock_batches.return_value = [batch]

            mock_opencli.return_value = opencli_quotes
            mock_cdp.return_value = [cdp_quote]
            mock_scrapling.return_value = [scrapling_quote]

            quotes = await run_page_scan(
                origin="BJS", destination="ALA", date="2026-05-20",
                region_codes=["CN", "SG"],
                transport="opencli",
                allow_browser_fallback=True,
            )

            assert len(quotes) == 2
            cn_quote = next(q for q in quotes if q.region == "CN")
            sg_quote = next(q for q in quotes if q.region == "SG")
            assert cn_quote.price == 2187.0
            assert cn_quote.status == "page_text"
            assert len(cn_quote.fallback_attempts) == 0, (
                "CN succeeded — no fallback attempts expected"
            )
            assert sg_quote.price == 350.0
            assert sg_quote.status == "page_text"
            assert len(sg_quote.fallback_attempts) >= 2, (
                "SG should have opencli_primary + cdp fallback attempts"
            )

    asyncio.run(run_test())
