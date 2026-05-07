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
        assert quote.status == "opencli_failed" # Keeps original status as base
        assert len(quote.fallback_attempts) >= 1
        fallback = quote.fallback_attempts[0]
        assert fallback["transport"] == "page_fallback"
        assert fallback["status"] == "page_challenge"
        assert "Cloudflare" in fallback["error"]

    asyncio.run(run_test())
