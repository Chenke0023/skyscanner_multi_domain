from __future__ import annotations

import argparse
import asyncio
import json
import time
from unittest.mock import MagicMock, patch

import pytest

from skyscanner_multi_domain.models import RegionConfig, FlightQuote
from skyscanner_multi_domain.transports.opencli import (
    compare_via_opencli,
    OpenCLITabSession,
)

def test_opencli_tab_reuse() -> None:
    """Test that multiple regions reuse the same tab and track telemetry correctly."""
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
                {"page": "interactive"}, # _tab_wait_interactive_async (CN)
                {"content": "Price CNY 100", "url": "url1"}, # _tab_extract (CN)
                {}, # _tab_navigate (SG)
                {"page": "interactive"}, # _tab_wait_interactive_async (SG)
                {"content": "Price SGD 20", "url": "url2"}, # _tab_extract (SG)
                {}, # _tab_close
            ]

            with patch("skyscanner_multi_domain.transports.opencli.extract_page_quote") as mock_parser:
                mock_parser.side_effect = [
                    FlightQuote("CN", "domain1", 100.0, "CNY", "url1", "ok"),
                    FlightQuote("SG", "domain2", 20.0, "SGD", "url2", "ok"),
                ]

                quotes = await compare_via_opencli(args, regions, persist_failures=False)

        assert len(quotes) == 2
        assert quotes[0].price == 100.0
        assert quotes[1].price == 20.0
        
        # Telemetry checks
        last_quote = quotes[1]
        assert last_quote.tab_open_count == 1
        assert last_quote.reused_tab_count == 1
    
    asyncio.run(run_test())


def test_opencli_adaptive_extraction() -> None:
    """Test that it tries larger chunk sizes if price is not found."""
    async def run_test():
        args = argparse.Namespace(
            origin="BJS",
            destination="ALA",
            date="2026-05-20",
            page_wait=0,
        )
        regions = [RegionConfig("CN", "China", "https://www.skyscanner.cn", "zh-CN", "CNY")]

        mock_tab_id = "test-tab-adaptive"
        
        with patch("skyscanner_multi_domain.transports.opencli._opencli_json") as mock_json:
            mock_json.side_effect = [
                {"page": mock_tab_id},  # _tab_new
                {"page": "interactive"}, # _tab_wait_interactive_async
                {"content": "small content", "url": "url1"}, # _tab_extract 15000
                {"content": "medium content", "url": "url1"}, # _tab_extract 50000
                {"content": "large content Price CNY 300", "url": "url1"}, # _tab_extract 100000
                {}, # _tab_close
            ]

            with patch("skyscanner_multi_domain.transports.opencli.extract_page_quote") as mock_parser:
                # First two attempts return no price
                q1 = FlightQuote("CN", "domain1", None, "CNY", "url1", "failed")
                q2 = FlightQuote("CN", "domain1", None, "CNY", "url1", "failed")
                q3 = FlightQuote("CN", "domain1", 300.0, "CNY", "url1", "ok")
                mock_parser.side_effect = [q1, q2, q3]

                quotes = await compare_via_opencli(args, regions, persist_failures=False)

        assert len(quotes) == 1
        assert quotes[0].price == 300.0
        assert quotes[0].extract_attempt_count == 3
        assert quotes[0].max_chunk_size_used == 100000

    asyncio.run(run_test())


def test_opencli_progressive_wait() -> None:
    """Test that it does progressive wait if page is not interactive initially."""
    async def run_test():
        args = argparse.Namespace(
            origin="BJS",
            destination="ALA",
            date="2026-05-20",
            page_wait=0,
        )
        regions = [RegionConfig("CN", "China", "https://www.skyscanner.cn", "zh-CN", "CNY")]

        mock_tab_id = "test-tab-wait"
        
        with patch("skyscanner_multi_domain.transports.opencli._opencli_json") as mock_json:
            mock_json.side_effect = [
                {"page": mock_tab_id},  # _tab_new
                {"content": "Price CNY 400", "url": "url1"}, # _tab_extract
                {}, # _tab_close
            ]

            with patch("skyscanner_multi_domain.transports.opencli._tab_wait_interactive_async") as mock_wait:
                # First call returns False (timeout), second call returns True (success)
                mock_wait.side_effect = [False, True]

                with patch("skyscanner_multi_domain.transports.opencli.extract_page_quote") as mock_parser:
                    mock_parser.return_value = FlightQuote("CN", "domain1", 400.0, "CNY", "url1", "ok")

                    quotes = await compare_via_opencli(args, regions, persist_failures=False)

        assert len(quotes) == 1
        assert quotes[0].price == 400.0
        assert quotes[0].progressive_wait_used == 1

    asyncio.run(run_test())


def test_opencli_time_budget_not_attempted() -> None:
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
            # Initial time, then time after first region (exceeding budget for second)
            # MAX_REGION_TIME is 45, len(regions) is 2, so total budget is 90.
            mock_time.side_effect = [1000, 1001, 1100, 1101, 1102, 1103, 1104, 1105]
            
            mock_tab_id = "test-tab-budget"
            with patch("skyscanner_multi_domain.transports.opencli._opencli_json") as mock_json:
                mock_json.side_effect = [
                    {"page": mock_tab_id},  # _tab_new (CN)
                    {"page": "interactive"}, # _tab_wait (CN)
                    {"content": "Price CNY 500", "url": "url1"}, # _extract (CN)
                    {}, # _tab_close (finally block)
                ]

                with patch("skyscanner_multi_domain.transports.opencli.extract_page_quote") as mock_parser:
                    mock_parser.return_value = FlightQuote("CN", "domain1", 500.0, "CNY", "url1", "ok")

                    quotes = await compare_via_opencli(args, regions, persist_failures=False)

        assert len(quotes) == 2
        assert quotes[0].region == "CN"
        assert quotes[0].price == 500.0
        assert quotes[1].region == "SG"
        assert quotes[1].status == "opencli_not_attempted"
        assert "budget exceeded" in quotes[1].error.lower()

    asyncio.run(run_test())
