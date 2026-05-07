from __future__ import annotations

import argparse
import asyncio
import json
import time
from unittest.mock import MagicMock, patch, call

import pytest

from skyscanner_multi_domain.models import RegionConfig, FlightQuote
from skyscanner_multi_domain.transports.opencli import (
    compare_via_opencli,
    OpenCLITabSession,
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
        regions = [
            RegionConfig("CN", "China", "https://www.skyscanner.cn", "zh-CN", "CNY"),
            RegionConfig("SG", "Singapore", "https://www.skyscanner.sg", "en-SG", "SGD"),
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
                        FlightQuote("SG", "domain2", 20.0, "SGD", "url2", "ok"),
                    ]

                    quotes = await compare_via_opencli(args, regions, persist_failures=False)

        assert len(quotes) == 2
        quote_cn = quotes[0]
        quote_sg = quotes[1]
        
        assert quote_cn.price == 100.0
        assert quote_sg.price == 20.0
        
        # CN telemetry (Point #3: per-region delta)
        assert quote_cn.tab_open_count == 1
        assert quote_cn.reused_tab_count == 0
        assert quote_cn.tab_close_count == 0 # Not closed yet
        assert quote_cn.extract_attempt_count == 1
        
        # SG telemetry (Point #3: per-region delta)
        assert quote_sg.tab_open_count == 0
        assert quote_sg.reused_tab_count == 1
        # Point #2: total_closes after pool.close_all attributed to last quote
        assert quote_sg.tab_close_count == 1 
        assert quote_sg.extract_attempt_count == 1
    
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
            
            # now = 1000 (start), then now = 1100 (after 1st region) -> elapsed = 100 > 90
            mock_time.side_effect = [
                1000, # start_time
                1001, # 1st region check budget (elapsed=1 < 90)
                1100, # 1st region finish, 2nd region check budget (elapsed=100 > 90)
                1101, # 1st region attempt trace
                1102, # 2nd region attempt trace
            ]
            
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
                {},                 # Navigate tab-1 for SG (Reuse tab-1, oldest/least pinned domain UK stays in tab-2)
                {"content": "Price 400", "url": "url4"}, # Extract SG
                {}, # tab-1 close
                {}, # tab-2 close
            ]

            with patch("skyscanner_multi_domain.transports.opencli._tab_wait_interactive_async") as mock_wait:
                mock_wait.return_value = True

                with patch("skyscanner_multi_domain.transports.opencli.extract_page_quote") as mock_parser:
                    mock_parser.side_effect = [
                        FlightQuote("CN", "d1", 100.0, "C", "u1", "ok"),
                        FlightQuote("HK", "d1", 200.0, "H", "u2", "ok"),
                        FlightQuote("UK", "d2", 300.0, "G", "u3", "ok"),
                        FlightQuote("SG", "d3", 400.0, "S", "u4", "ok"),
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
        
        # SG: reused oldest tab (pool full at 2)
        assert quotes[3].tab_open_count == 0
        assert quotes[3].reused_tab_count == 1
        
        # Final close count attributed to last quote
        # We opened 2 tabs (tab-1, tab-2) and closed them all.
        assert quotes[3].tab_close_count == 2

    asyncio.run(run_test())
