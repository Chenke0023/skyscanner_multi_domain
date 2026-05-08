from __future__ import annotations

import argparse
import asyncio
from unittest.mock import MagicMock, patch

import pytest

from skyscanner_multi_domain.models import RegionConfig, FlightQuote
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

        with patch("skyscanner_multi_domain.transports.opencli.compare_via_opencli", return_value=[opencli_quote]):
            with patch("skyscanner_multi_domain.scan.orchestrator.build_scan_batches") as mock_batches:
                from skyscanner_multi_domain.planning.search_plan import ScanBatch, ScanTask, RouteCandidate, DateCandidate, MarketCandidate
                route = RouteCandidate("BJS", "ALA", "BJS", "ALA", 1, "reason", 1.0, 1.0, {})
                date_cand = DateCandidate(date, None, 0, "anchor", "reason", 1.0, {})
                market_cn = MarketCandidate("CN", 1, "reason", 1.0, 0.5, 1.0, {})

                batch = ScanBatch(1, "probe", [
                    ScanTask(route, date_cand, market_cn, 1.0, "probe", "reason")
                ], "reason")
                mock_batches.return_value = [batch]

                # Patch google_jump to succeed (imported inline in orchestrator)
                gj_quote = FlightQuote("CN", "domain1", 150.0, "CNY", "url1", "ok")
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
                        FlightQuote("CN", "skyscanner.com", 100.0, "CNY", "url", "ok"),
                        "page text content here",
                        {"extract_attempt_count": 1, "progressive_wait_used": 0, "max_chunk_size_used": 0},
                    )
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
        assert "scrapling_fallback" in transports

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
                FlightQuote(region.code, region.domain, 100.0, "CNY", "url", "ok"),
                "page text",
                {"extract_attempt_count": 1, "progressive_wait_used": 0, "max_chunk_size_used": 0},
            )

        with patch.object(OpenCLITabSession, "ensure_tab_async", return_value=("tab-1", {"tab_open_count": 1, "reused_tab_count": 0})):
            with patch.object(OpenCLITabSession, "wait_progressive_state", return_value=(True, 0)):
                with patch.object(OpenCLITabSession, "extract_with_progressive_content_wait", capture_extract):
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
                FlightQuote(region.code, region.domain, 100.0, "CNY", "url", "ok"),
                "page text",
                {"extract_attempt_count": 1, "progressive_wait_used": 0, "max_chunk_size_used": 0},
            )

        with patch.object(OpenCLITabSession, "ensure_tab_async", return_value=("tab-1", {"tab_open_count": 1, "reused_tab_count": 0})):
            with patch.object(OpenCLITabSession, "wait_progressive_state", return_value=(True, 0)):
                with patch.object(OpenCLITabSession, "extract_with_progressive_content_wait", slow_extract):
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
