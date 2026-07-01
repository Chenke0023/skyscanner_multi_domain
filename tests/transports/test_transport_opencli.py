from __future__ import annotations

import argparse
import asyncio
import json
import time
from unittest.mock import MagicMock, patch, call, AsyncMock

import pytest

from skyscanner_multi_domain.models import RegionConfig, FlightQuote
from skyscanner_multi_domain.transports.opencli import (
    compare_via_opencli,
    OpenCLITabSession,
    OpenCLICommandResult,
)

def test_opencli_tab_pool_domain_pinning_v12() -> None:
    """Test domain-aware scheduler pins sessions to domains and handles cross-domain transitions."""
    async def run_test():
        regions = [
            RegionConfig("CN", "China", "https://www.skyscanner.com.cn", "zh-CN", "CNY"),
            RegionConfig("HK", "Hong Kong", "https://www.skyscanner.com.cn", "zh-HK", "HKD"),
            RegionConfig("UK", "UK", "https://www.skyscanner.net", "en-GB", "GBP"),
            RegionConfig("SG", "Singapore", "https://www.skyscanner.sg", "en-SG", "SGD"),
        ]

        args = argparse.Namespace(origin="BJS", destination="ALA", date="2026-05-20", page_wait=0)

        def _mock_result(data: dict) -> OpenCLICommandResult:
            return OpenCLICommandResult(returncode=0, stdout=json.dumps(data), stderr="", duration_ms=0)

        def _mock_run_opencli(cmd_args, *, timeout=None):
            cmd = " ".join(cmd_args)
            if "tab new" in cmd:
                if "skyscanner.com.cn" in cmd:
                    return _mock_result({"page": "tab-1"})
                elif "skyscanner.net" in cmd:
                    return _mock_result({"page": "tab-2"})
                elif "skyscanner.sg" in cmd:
                    return _mock_result({"page": "tab-3"})
            return _mock_result({})

        # Use tab-ID-based routing so concurrent groups don't interfere
        _extracts_by_tab: dict[str, list[dict]] = {
            "tab-1": [{"content": "Price 100", "url": "url1"},
                       {"content": "Price 200", "url": "url2"}],
            "tab-2": [{"content": "Price 300", "url": "url3"}],
            "tab-3": [{"content": "Price 400", "url": "url4"}],
        }

        def _mock_opencli_json(cmd_args, *, timeout=None):
            cmd = " ".join(cmd_args)
            if "browser extract" in cmd:
                for tab_id, extracts in _extracts_by_tab.items():
                    if tab_id in cmd and extracts:
                        return extracts.pop(0)
            return {}

        with patch("skyscanner_multi_domain.transports.opencli._run_opencli_async",
                   side_effect=_mock_run_opencli), \
             patch("skyscanner_multi_domain.transports.opencli._opencli_json",
                   side_effect=_mock_opencli_json), \
             patch("skyscanner_multi_domain.transports.opencli._tab_wait_interactive_async",
                   return_value=True), \
             patch("skyscanner_multi_domain.transports.opencli.fetch_attempt_to_quote") as mock_parser:

            # Use region-keyed results so concurrent extract order doesn't matter
            _parser_results = {
                "CN": FlightQuote("CN", "d1", 100.0, "CNY", "u1", "ok"),
                "HK": FlightQuote("HK", "d1", 200.0, "HKD", "u2", "ok"),
                "UK": FlightQuote("UK", "d2", 300.0, "GBP", "u3", "ok"),
                "SG": FlightQuote("SG", "d3", 400.0, "SGD", "u4", "ok"),
            }
            mock_parser.side_effect = lambda attempt, region, fallback_url: _parser_results[region.code]

            quotes = await compare_via_opencli(
                args, regions, persist_failures=False, region_concurrency=2,
                build_search_url=lambda r, o, d, dt, rd: f"{r.domain}/flights/{o}/{d}/{dt}",
            )

        assert len(quotes) == 4
        # CN: new tab in its domain group session
        assert quotes[0].tab_open_count == 1
        assert quotes[0].reused_tab_count == 0

        # HK: reused CN tab (same domain group, same session)
        assert quotes[1].tab_open_count == 0
        assert quotes[1].reused_tab_count == 1

        # UK: new tab in its own domain group session
        assert quotes[2].tab_open_count == 1
        assert quotes[2].reused_tab_count == 0

        # SG: new tab in its own domain group session
        assert quotes[3].tab_open_count == 1
        assert quotes[3].reused_tab_count == 0

        # Three domain groups, close counts go to last region in each group.
        # Ordered by selected_regions: CN(0), HK(1), UK(1), SG(1)
        assert quotes[3].tab_close_count == 1

    asyncio.run(run_test())

def test_terminal_status_protection() -> None:
    """Verify challenge statuses are not overwritten by timeout."""
    async def run_test():
        args = argparse.Namespace(origin="SJS", destination="SHA", date="2026-05-10", page_wait=5, return_date=None)
        regions = [RegionConfig("CN", "China", "skyscanner.com.cn", "zh", "CNY")]
        
        mock_quote = FlightQuote(
            region="CN", domain="skyscanner.com.cn", price=None, 
            currency="CNY", source_url="http://test", status="px_challenge"
        )
        
        def _mock_result(data: dict) -> OpenCLICommandResult:
            return OpenCLICommandResult(returncode=0, stdout=json.dumps(data), stderr="", duration_ms=0)

        from skyscanner_multi_domain.scan.fetch_types import FetchAttempt
        with patch("skyscanner_multi_domain.transports.opencli._run_opencli_async",
                   return_value=_mock_result({"page": "t1"})), \
             patch("skyscanner_multi_domain.transports.opencli._tab_wait_interactive_async",
                   return_value=False), \
             patch("skyscanner_multi_domain.transports.opencli.OpenCLITabSession.extract_with_progressive_content_wait",
                   AsyncMock(return_value=(
                       FetchAttempt(
                           transport="opencli",
                           region_code="CN",
                           url="http://test",
                           page_text="text",
                           evidence={"readiness": "challenge"},
                       ),
                       {"extract_attempt_count": 1, "progressive_wait_used": 0, "max_chunk_size_used": 15000},
                   ))):

            with patch("skyscanner_multi_domain.transports.opencli.fetch_attempt_to_quote", return_value=mock_quote):
                quotes = await compare_via_opencli(args, regions)
                assert quotes[0].status == "px_challenge"
                assert "Page did not reach interactive state" not in (quotes[0].error or "")

    asyncio.run(run_test())


def test_fallback_diagnostic_persistence_v12_1() -> None:
    """Verify primary failure reason is preserved when fallback succeeds."""
    async def run_test():
        from skyscanner_multi_domain.scan.orchestrator import run_page_scan
        with patch("skyscanner_multi_domain.transports.opencli.compare_via_opencli") as mock_opencli, \
             patch("skyscanner_multi_domain.transports.cdp.compare_via_pages") as mock_pages, \
             patch("skyscanner_multi_domain.transports.scrapling.compare_via_scrapling") as mock_scrapling, \
             patch("skyscanner_multi_domain.transports.cdp.detect_cdp_version", return_value={"version": "1.0"}), \
             patch("skyscanner_multi_domain.transports.cdp.ensure_cdp_ready"):
            
            mock_opencli.return_value = [FlightQuote(
                region="UK", domain="skyscanner.net", price=None, 
                currency="GBP", source_url="http://test", status="opencli_error", error="Network error"
            )]
            
            mock_pages.return_value = [FlightQuote(
                region="UK", domain="skyscanner.net", price=100, 
                currency="GBP", source_url="http://test", status="success"
            )]
            
            quotes = await run_page_scan(
                "LON", "NYC", "2026-05-10", ["UK"], 
                transport="opencli", allow_browser_fallback=True,
                scan_mode="quick",
                on_progress=MagicMock()
            )
            
            assert quotes[0].price == 100
            assert len(quotes[0].fallback_attempts) == 1
            assert quotes[0].fallback_attempts[0]["transport"] == "opencli_primary"
            assert quotes[0].fallback_attempts[0]["status"] == "opencli_error"

    asyncio.run(run_test())
