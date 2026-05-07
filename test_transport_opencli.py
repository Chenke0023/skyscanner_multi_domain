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
    OpenCLITabPool,
)

def test_opencli_tab_reuse_and_telemetry_deltas_v11() -> None:
    """Test that multiple regions reuse the same tab and track telemetry correctly as deltas."""
    async def run_test():
        args = argparse.Namespace(
            origin="BJS",
            destination="ALA",
            date="2026-05-20",
            page_wait=0,
        )
        # Use same domain to ensure reuse without clean transition (v1.2 behavior)
        regions = [
            RegionConfig("CN", "China", "https://www.skyscanner.cn", "zh-CN", "CNY"),
            RegionConfig("CN2", "China2", "https://www.skyscanner.cn", "zh-CN", "CNY"),
        ]

        mock_tab_id = "test-tab-123"
        
        # Mock OpenCLI calls
        with patch("skyscanner_multi_domain.transports.opencli._opencli_json") as mock_json:
            mock_json.side_effect = [
                {"page": mock_tab_id},  # _tab_new
                {"content": "Price CNY 100", "url": "url1"}, # _tab_extract (CN)
                {}, # _tab_navigate (SG)
                {"content": "Price SGD 20", "url": "url2"}, # _tab_extract (SG)
                {}, # _tab_close
            ]

            with patch("skyscanner_multi_domain.transports.opencli._tab_wait_interactive_async") as mock_wait:
                mock_wait.return_value = True # Always interactive for this test

                with patch("skyscanner_multi_domain.transports.opencli.extract_page_quote") as mock_parser:
                    mock_parser.side_effect = [
                        FlightQuote("CN", "domain1", 100.0, "CNY", "url1", "ok"),
                        FlightQuote("CN2", "domain2", 20.0, "CNY", "url2", "ok"),
                    ]

                    quotes = await compare_via_opencli(args, regions, persist_failures=False)

        assert len(quotes) == 2
        quote_cn = quotes[0]
        quote_cn2 = quotes[1]
        
        assert quote_cn.price == 100.0
        assert quote_cn2.price == 20.0
        
        # CN telemetry (Point #3: per-region delta)
        assert quote_cn.tab_open_count == 1
        assert quote_cn.reused_tab_count == 0
        assert quote_cn.tab_close_count == 0 # Not closed yet
        
        # CN2 telemetry (Point #3: per-region delta)
        assert quote_cn2.tab_open_count == 0
        assert quote_cn2.reused_tab_count == 1
        assert quote_cn2.tab_close_count == 1 
    
    asyncio.run(run_test())


def test_opencli_content_aware_wait_v11() -> None:
    """Test content-aware progressive wait (wait + re-extract)."""
    async def run_test():
        args = argparse.Namespace(
            origin="BJS",
            destination="ALA",
            date="2026-05-20",
            page_wait=0,
        )
        regions = [RegionConfig("CN", "China", "https://www.skyscanner.cn", "zh-CN", "CNY")]

        mock_tab_id = "test-tab-content-aware"
        
        with patch("skyscanner_multi_domain.transports.opencli._opencli_json") as mock_json:
            mock_json.side_effect = [
                {"page": mock_tab_id},  # _tab_new
                {"content": "small content", "url": "url1"}, # _tab_extract 15000
                {"content": "medium content", "url": "url1"}, # _tab_extract 50000
                {"content": "large content Price CNY 300", "url": "url1"}, # _tab_extract 100000
                {}, # _tab_close
            ]

            with patch("skyscanner_multi_domain.transports.opencli._tab_wait_interactive_async") as mock_wait:
                mock_wait.return_value = True # Interactive initially

                with patch("skyscanner_multi_domain.transports.opencli.extract_page_quote") as mock_parser:
                    # First two attempts return no price but status suggesting loading/parse fail
                    q1 = FlightQuote("CN", "domain1", None, "CNY", "url1", "page_parse_failed")
                    q2 = FlightQuote("CN", "domain1", None, "CNY", "url1", "opencli_failed")
                    q3 = FlightQuote("CN", "domain1", 300.0, "CNY", "url1", "ok")
                    mock_parser.side_effect = [q1, q2, q3]

                    with patch("skyscanner_multi_domain.transports.opencli.asyncio.sleep") as mock_sleep:
                        quotes = await compare_via_opencli(args, regions, persist_failures=False)

                        # Point #5: content-aware progressive wait triggers specific sleep times
                        sleep_calls = [c.args[0] for c in mock_sleep.call_args_list]
                        # 8s and 15s are the "extra_wait" values in extract_with_progressive_content_wait
                        assert 8 in sleep_calls
                        assert 15 in sleep_calls

        assert len(quotes) == 1
        assert quotes[0].price == 300.0
        assert quotes[0].extract_attempt_count == 3
        assert quotes[0].progressive_wait_used == 2 

    asyncio.run(run_test())


def test_opencli_time_budget_not_attempted_v11() -> None:
    """Test that regions are marked as not_attempted if time budget is exceeded."""
    async def run_test():
        args = argparse.Namespace(
            origin="BJS",
            destination="ALA",
            date="2026-05-20",
            page_wait=0,
        )
        regions = [
            RegionConfig("CN", "China", "https://www.skyscanner.cn", "zh-CN", "CNY"),
            RegionConfig("SG", "Singapore", "https://www.skyscanner.sg", "en-SG", "SGD"),
        ]

        with patch("time.time") as mock_time:
            # Budget check logic: elapsed > MAX_REGION_TIME * len(selected_regions)
            # elapsed = now - start_time
            # start_time = 1000
            # MAX_REGION_TIME = 45, len=2 -> budget = 90
            
            # Values match the budget checks; LRU timestamps are deterministic
            # counters and no longer consume time.time().
            # We want the first region (CN) to stay under budget, but the second (SG) to exceed it.
            mock_time.side_effect = [1000, 1001, 1100]
            
            mock_tab_id = "test-tab-budget"
            with patch("skyscanner_multi_domain.transports.opencli._opencli_json") as mock_json:
                mock_json.side_effect = [
                    {"page": mock_tab_id},  # _tab_new (CN)
                    {"content": "Price CNY 500", "url": "url1"}, # _extract (CN)
                    {}, # _tab_close (finally block)
                ]

                with patch("skyscanner_multi_domain.transports.opencli._tab_wait_interactive_async") as mock_wait:
                    mock_wait.return_value = True

                    with patch("skyscanner_multi_domain.transports.opencli.extract_page_quote") as mock_parser:
                        mock_parser.return_value = FlightQuote("CN", "domain1", 500.0, "CNY", "url1", "ok")

                        quotes = await compare_via_opencli(args, regions, persist_failures=False)

        assert len(quotes) == 2
        assert quotes[0].region == "CN"
        assert quotes[0].price == 500.0
        assert quotes[1].region == "SG"
        assert quotes[1].status == "opencli_not_attempted"
    
    asyncio.run(run_test())

def test_opencli_tab_pool_domain_pinning_v12() -> None:
    """Test that OpenCLITabPool pins domains to sessions and limits max tabs."""
    async def run_test():
        # max_tabs = 2
        # Regions: CN, HK (same domain), UK (different domain), SG (third domain)
        regions = [
            RegionConfig("CN", "China", "https://www.skyscanner.com.cn", "zh-CN", "CNY"),
            RegionConfig("HK", "Hong Kong", "https://www.skyscanner.com.cn", "zh-HK", "HKD"), # Same domain
            RegionConfig("UK", "UK", "https://www.skyscanner.net", "en-GB", "GBP"),
            RegionConfig("SG", "Singapore", "https://www.skyscanner.sg", "en-SG", "SGD"),
        ]
        
        args = argparse.Namespace(origin="BJS", destination="ALA", date="2026-05-20", page_wait=0)
        
        # Mock OpenCLI calls
        with patch("skyscanner_multi_domain.transports.opencli._opencli_json") as mock_json:
            mock_json.side_effect = [
                {"page": "tab-1"},  # New tab for CN (CN domain)
                {"content": "Price 100", "url": "url1"}, # Extract CN
                {},                 # Navigate tab-1 for HK (Reuse same domain)
                {"content": "Price 200", "url": "url2"}, # Extract HK
                {"page": "tab-2"},  # New tab for UK (Pool size < 2)
                {"content": "Price 300", "url": "url3"}, # Extract UK
                {},                 # Close tab-1 (Oldest LRU session, cross-domain clean transition)
                {"page": "tab-1-new"}, # New tab for SG
                {"content": "Price 400", "url": "url4"}, # Extract SG
                {}, # tab-1-new close
                {}, # tab-2 close
            ]

            with patch("skyscanner_multi_domain.transports.opencli._tab_wait_interactive_async") as mock_wait:
                mock_wait.return_value = True

                with patch("skyscanner_multi_domain.transports.opencli.extract_page_quote") as mock_parser:
                    mock_parser.side_effect = [
                        FlightQuote("CN", "d1", 100.0, "CNY", "u1", "ok"),
                        FlightQuote("HK", "d1", 200.0, "HKD", "u2", "ok"),
                        FlightQuote("UK", "d2", 300.0, "GBP", "u3", "ok"),
                        FlightQuote("SG", "d3", 400.0, "SGD", "u4", "ok"),
                    ]

                    # Call with concurrency 2
                    quotes = await compare_via_opencli(args, regions, persist_failures=False, region_concurrency=2)

        assert len(quotes) == 4
        # CN: new tab
        assert quotes[0].tab_open_count == 1
        assert quotes[0].reused_tab_count == 0
        
        # HK: reused CN tab (same domain)
        assert quotes[1].tab_open_count == 0
        assert quotes[1].reused_tab_count == 1
        
        # UK: new tab (pool size 1 -> 2)
        assert quotes[2].tab_open_count == 1
        assert quotes[2].reused_tab_count == 0
        
        # SG: repurposed oldest tab (LRU) with clean transition (cross-domain)
        # It was: d1 pinned to tab-1, d2 pinned to tab-2.
        # Acquire d3: d1 and d2 are pinned. Pick oldest (d1/tab-1).
        # Clean transition: close tab-1, open new tab for d3.
        assert quotes[3].tab_open_count == 1
        assert quotes[3].reused_tab_count == 0
        
        # Final close count attributed to last quote
        # Total tabs opened: tab-1, tab-2, tab-1-new. Total closed: 3.
        assert quotes[3].tab_close_count == 3

    asyncio.run(run_test())

def test_pool_lru_and_clean_transition() -> None:
    """Verify true LRU eviction and clean cross-domain transitions."""
    async def run_test():
        with patch("skyscanner_multi_domain.transports.opencli._tab_new", return_value="tab123") as mock_new, \
             patch("skyscanner_multi_domain.transports.opencli._tab_navigate") as mock_nav, \
             patch("skyscanner_multi_domain.transports.opencli._tab_close") as mock_close:
            
            pool = OpenCLITabPool(max_tabs=2)
            
            # 1. Fill pool
            mock_new.side_effect = ["t1", "t2", "t3"]
            s1, _ = pool.acquire("domain1", "url1")
            s2, _ = pool.acquire("domain2", "url2")
            
            assert len(pool.sessions) == 2
            assert s1.tab_id == "t1"
            assert s2.tab_id == "t2"
            
            # Update last_used_index for s1
            s1.last_used_index = 100
            s2.last_used_index = 200
            
            # 2. Acquire domain3 (should evict s1 because it's older)
            s3, _ = pool.acquire("domain3", "url3")
            
            assert s3 == s1
            assert "domain1" not in pool.domain_to_session
            assert pool.domain_to_session["domain3"] == s3
            
            # Since it was domain1 -> domain3, it should have called _tab_close for t1 and _tab_new for url3
            assert mock_close.called
            assert mock_new.call_count == 3 
            assert s3.tab_id == "t3"

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
        
        with patch("skyscanner_multi_domain.transports.opencli.OpenCLITabPool.acquire") as mock_acquire, \
             patch("skyscanner_multi_domain.transports.opencli._tab_extract"):
            session = MagicMock(spec=OpenCLITabSession)
            session.tab_id = "t1"
            session.wait_progressive_state = AsyncMock(return_value=(False, 1))
            session.extract_with_progressive_content_wait = AsyncMock(return_value=(mock_quote, "text", {"extract_attempt_count": 1, "progressive_wait_used": 0, "max_chunk_size_used": 15000}))
            
            mock_acquire.return_value = (session, {"tab_open_count": 0, "reused_tab_count": 1})
            
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
