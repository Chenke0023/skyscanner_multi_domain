"""Fallback order and orchestrator routing tests (migrated from test_skyscanner_neo.py)."""

from __future__ import annotations

import asyncio
import tempfile
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import AsyncMock, patch

from skyscanner_models import FlightQuote
from skyscanner_neo import run_page_scan
from scan_history import ScanHistoryStore


class RunPageScanFallbackTests(unittest.TestCase):
    def test_run_page_scan_defaults_to_opencli(self) -> None:
        opencli_quotes = [
            FlightQuote(
                region="CN", domain="https://www.skyscanner.cn",
                price=2187.0, currency="CNY",
                source_url="https://www.skyscanner.cn/transport/flights/bjsa/ala/260429/",
                status="page_text", confidence=0.9,
                best_price=3217.0, cheapest_price=2187.0,
            )
        ]

        async def run_case() -> None:
            with (
                patch(
                    "transport_opencli.compare_via_opencli",
                    new=AsyncMock(return_value=opencli_quotes),
                ) as opencli_mock,
                patch(
                    "transport_scrapling.compare_via_scrapling",
                    new=AsyncMock(return_value=[]),
                ) as scrapling_mock,
                patch(
                    "transport_cdp.compare_via_pages",
                    new=AsyncMock(return_value=[]),
                ) as page_mock,
            ):
                quotes = await run_page_scan(
                    origin="BJSA", destination="ALA",
                    date="2026-04-29", region_codes=["CN"],
                )

                self.assertEqual(len(quotes), 1)
                self.assertEqual(quotes[0].price, 2187.0)
                opencli_mock.assert_awaited_once()
                page_mock.assert_not_awaited()
                scrapling_mock.assert_not_awaited()

        asyncio.run(run_case())

    def test_run_page_scan_opencli_falls_back_to_page_then_scrapling_legacy(self) -> None:
        opencli_quotes = [
            FlightQuote(
                region="CN", domain="https://www.skyscanner.cn",
                price=None, currency="CNY",
                source_url="https://www.skyscanner.cn/transport/flights/bjsa/ala/260429/",
                status="opencli_error", error="opencli failed",
            ),
            FlightQuote(
                region="HK", domain="https://www.skyscanner.com.hk",
                price=None, currency="HKD",
                source_url="https://www.skyscanner.com.hk/transport/flights/bjsa/ala/260429/",
                status="opencli_error", error="opencli failed",
            ),
        ]
        page_quotes = [
            FlightQuote(
                region="CN", domain="https://www.skyscanner.cn",
                price=2187.0, currency="CNY",
                source_url="https://www.skyscanner.cn/transport/flights/bjsa/ala/260429/",
                status="page_text", confidence=0.9,
                best_price=3217.0, cheapest_price=2187.0,
            ),
            FlightQuote(
                region="HK", domain="https://www.skyscanner.com.hk",
                price=None, currency="HKD",
                source_url="https://www.skyscanner.com.hk/transport/flights/bjsa/ala/260429/",
                status="page_parse_failed", error="no price",
            ),
        ]
        scrapling_quotes = [
            FlightQuote(
                region="HK", domain="https://www.skyscanner.com.hk",
                price=2465.0, currency="HKD",
                source_url="https://www.skyscanner.com.hk/transport/flights/bjsa/ala/260429/",
                status="page_text", confidence=0.9,
                best_price=2539.0, cheapest_price=2465.0,
            )
        ]

        async def run_case() -> None:
            with (
                patch(
                    "transport_opencli.compare_via_opencli",
                    new=AsyncMock(return_value=opencli_quotes),
                ) as opencli_mock,
                patch(
                    "transport_cdp.compare_via_pages",
                    new=AsyncMock(return_value=page_quotes),
                ) as page_mock,
                patch(
                    "transport_scrapling.compare_via_scrapling",
                    new=AsyncMock(return_value=scrapling_quotes),
                ) as scrapling_mock,
                patch(
                    "transport_cdp.detect_cdp_version",
                    return_value={"Browser": "Edg/146.0.3856.97"},
                ),
                patch("transport_cdp.ensure_cdp_ready") as ensure_cdp_ready_mock,
            ):
                quotes = await run_page_scan(
                    origin="BJSA", destination="ALA",
                    date="2026-04-29", region_codes=["CN", "HK"],
                    transport="opencli",
                )

                quotes_by_region = {quote.region: quote for quote in quotes}
                self.assertEqual(quotes_by_region["CN"].price, 2187.0)
                self.assertEqual(quotes_by_region["HK"].price, 2465.0)
                opencli_mock.assert_awaited_once()
                page_mock.assert_awaited_once()
                scrapling_mock.assert_awaited_once()
                page_regions = page_mock.await_args.args[1]
                legacy_regions = scrapling_mock.await_args.args[1]
                self.assertEqual([region.code for region in page_regions], ["CN", "HK"])
                self.assertEqual([region.code for region in legacy_regions], ["HK"])
                ensure_cdp_ready_mock.assert_not_called()

        asyncio.run(run_case())

    def test_run_page_scan_skips_browser_launch_for_partial_scrapling_success(self) -> None:
        call_count = [0]

        async def scrapling_side_effect(_args, _selected_regions, **_kwargs):
            call_count[0] += 1
            if call_count[0] == 1:
                return [
                    FlightQuote(
                        region="CN", domain="https://www.skyscanner.cn",
                        price=2187.0, currency="CNY",
                        source_url="https://www.skyscanner.cn/transport/flights/bjsa/ala/260429/",
                        status="page_text", confidence=0.9,
                        best_price=3217.0, cheapest_price=2187.0,
                    ),
                    FlightQuote(
                        region="HK", domain="https://www.skyscanner.com.hk",
                        price=None, currency="HKD",
                        source_url="https://www.skyscanner.com.hk/transport/flights/bjsa/ala/260429/",
                        status="page_loading", error="页面仍在加载结果: loading",
                    ),
                ]
            return [
                FlightQuote(
                    region="CN", domain="https://www.skyscanner.cn",
                    price=2187.0, currency="CNY",
                    source_url="https://www.skyscanner.cn/transport/flights/bjsa/ala/260429/",
                    status="page_text", confidence=0.9,
                    best_price=3217.0, cheapest_price=2187.0,
                ),
                FlightQuote(
                    region="HK", domain="https://www.skyscanner.com.hk",
                    price=2150.0, currency="HKD",
                    source_url="https://www.skyscanner.com.hk/transport/flights/bjsa/ala/260429/",
                    status="page_text", confidence=0.9,
                    best_price=2539.0, cheapest_price=2150.0,
                ),
            ]

        async def run_case() -> None:
            with (
                patch(
                    "transport_scrapling.compare_via_scrapling",
                    new=AsyncMock(side_effect=scrapling_side_effect),
                ) as scrapling_mock,
                patch(
                    "transport_cdp.compare_via_pages",
                    new=AsyncMock(return_value=[]),
                ) as page_mock,
                patch("transport_cdp.detect_cdp_version", return_value=None),
                patch("transport_cdp.ensure_cdp_ready") as ensure_cdp_ready_mock,
            ):
                quotes = await run_page_scan(
                    origin="BJSA", destination="ALA",
                    date="2026-04-29", region_codes=["CN", "HK"],
                    transport="scrapling",
                )

                self.assertEqual(len(quotes), 2)
                quotes_by_region = {quote.region: quote for quote in quotes}
                self.assertEqual(quotes_by_region["CN"].price, 2187.0)
                self.assertEqual(quotes_by_region["HK"].price, 2150.0)
                self.assertEqual(quotes_by_region["HK"].status, "page_text")
                ensure_cdp_ready_mock.assert_not_called()
                self.assertEqual(scrapling_mock.call_count, 2)
                page_mock.assert_not_awaited()

        asyncio.run(run_case())

    def test_run_page_scan_reuses_connected_cdp_for_failed_markets(self) -> None:
        scrapling_quotes = [
            FlightQuote(
                region="CN", domain="https://www.skyscanner.cn",
                price=2187.0, currency="CNY",
                source_url="https://www.skyscanner.cn/transport/flights/bjsa/ala/260429/",
                status="page_text", confidence=0.9,
                best_price=3217.0, cheapest_price=2187.0,
            ),
            FlightQuote(
                region="HK", domain="https://www.skyscanner.com.hk",
                price=None, currency="HKD",
                source_url="https://www.skyscanner.com.hk/transport/flights/bjsa/ala/260429/",
                status="scrapling_failed", error="connection refused",
            ),
        ]
        page_fallback_quotes = [
            FlightQuote(
                region="HK", domain="https://www.skyscanner.com.hk",
                price=2465.0, currency="HKD",
                source_url="https://www.skyscanner.com.hk/transport/flights/bjsa/ala/260429/",
                status="page_text", confidence=0.9,
                best_price=2539.0, cheapest_price=2465.0,
            )
        ]

        async def run_case() -> None:
            with (
                patch(
                    "transport_scrapling.compare_via_scrapling",
                    new=AsyncMock(return_value=scrapling_quotes),
                ) as scrapling_mock,
                patch(
                    "transport_cdp.compare_via_pages",
                    new=AsyncMock(return_value=page_fallback_quotes),
                ) as page_mock,
                patch(
                    "transport_cdp.detect_cdp_version",
                    return_value={"Browser": "Edg/146.0.3856.97"},
                ),
                patch("transport_cdp.ensure_cdp_ready") as ensure_cdp_ready_mock,
            ):
                quotes = await run_page_scan(
                    origin="BJSA", destination="ALA",
                    date="2026-04-29", region_codes=["CN", "HK"],
                    transport="scrapling",
                )

                self.assertEqual(len(quotes), 2)
                quotes_by_region = {quote.region: quote for quote in quotes}
                self.assertEqual(quotes_by_region["CN"].price, 2187.0)
                self.assertEqual(quotes_by_region["HK"].price, 2465.0)
                ensure_cdp_ready_mock.assert_not_called()
                scrapling_mock.assert_awaited_once()
                page_mock.assert_awaited_once()
                fallback_regions = page_mock.await_args.args[1]
                self.assertEqual([region.code for region in fallback_regions], ["HK"])

        asyncio.run(run_case())

    def test_run_page_scan_launches_browser_when_no_scrapling_market_succeeds(self) -> None:
        scrapling_quotes = [
            FlightQuote(
                region="CN", domain="https://www.skyscanner.cn",
                price=None, currency="CNY",
                source_url="https://www.skyscanner.cn/transport/flights/bjsa/ala/260429/",
                status="scrapling_failed", error="connection failed",
            ),
            FlightQuote(
                region="HK", domain="https://www.skyscanner.com.hk",
                price=None, currency="HKD",
                source_url="https://www.skyscanner.com.hk/transport/flights/bjsa/ala/260429/",
                status="scrapling_failed", error="connection failed",
            ),
        ]
        page_fallback_quotes = [
            FlightQuote(
                region="CN", domain="https://www.skyscanner.cn",
                price=2187.0, currency="CNY",
                source_url="https://www.skyscanner.cn/transport/flights/bjsa/ala/260429/",
                status="page_text", confidence=0.9,
                best_price=3217.0, cheapest_price=2187.0,
            )
        ]

        async def run_case() -> None:
            with (
                patch(
                    "transport_scrapling.compare_via_scrapling",
                    new=AsyncMock(return_value=scrapling_quotes),
                ),
                patch(
                    "transport_cdp.compare_via_pages",
                    new=AsyncMock(return_value=page_fallback_quotes),
                ) as page_mock,
                patch("transport_cdp.detect_cdp_version", return_value=None),
                patch("transport_cdp.ensure_cdp_ready") as ensure_cdp_ready_mock,
            ):
                quotes = await run_page_scan(
                    origin="BJSA", destination="ALA",
                    date="2026-04-29", region_codes=["CN", "HK"],
                    transport="scrapling",
                )

                self.assertEqual(len(quotes), 2)
                ensure_cdp_ready_mock.assert_called_once()
                page_mock.assert_awaited_once()
                self.assertEqual(quotes[0].price, 2187.0)

        asyncio.run(run_case())

    def test_run_page_scan_passes_return_date_to_transport(self) -> None:
        async def run_case() -> None:
            with patch(
                "transport_scrapling.compare_via_scrapling",
                new=AsyncMock(return_value=[]),
            ) as scrapling_mock:
                quotes = await run_page_scan(
                    origin="BJSA", destination="ALA",
                    date="2026-04-29", return_date="2026-05-03",
                    region_codes=["CN"], transport="scrapling",
                )

                self.assertEqual(quotes, [])
                args = scrapling_mock.await_args.args[0]
                self.assertEqual(args.return_date, "2026-05-03")

        asyncio.run(run_case())

    def test_run_page_scan_filters_selected_regions_and_forwards_concurrency(self) -> None:
        async def run_case() -> None:
            with patch(
                "transport_scrapling.compare_via_scrapling",
                new=AsyncMock(return_value=[]),
            ) as scrapling_mock:
                quotes = await run_page_scan(
                    origin="BJSA", destination="ALA",
                    date="2026-04-29", region_codes=["CN", "HK", "SG"],
                    transport="scrapling",
                    rerun_scope="selected_regions",
                    selected_region_codes=["HK", "SG"],
                    region_concurrency=3,
                )

                self.assertEqual(quotes, [])
                selected_regions = scrapling_mock.await_args.args[1]
                self.assertEqual([region.code for region in selected_regions], ["HK", "SG"])
                self.assertEqual(
                    scrapling_mock.await_args.kwargs["region_concurrency"], 3,
                )

        asyncio.run(run_case())

    def test_run_page_scan_preview_first_emits_cached_then_live_progress(self) -> None:
        progress_events: list[dict[str, object]] = []

        async def run_case() -> None:
            with TemporaryDirectory() as tmpdir:
                store = ScanHistoryStore(Path(tmpdir) / "scan_history.sqlite3")
                query_payload = {
                    "identity": {
                        "mode": "point_to_point",
                        "origin_code": "BJSA",
                        "destination_code": "ALA",
                        "date": "2026-04-29",
                        "return_date": None,
                    },
                    "display": {"title": "北京 -> 阿拉木图 (2026-04-29)"},
                }
                store.record_scan(
                    query_payload,
                    [
                        (
                            "2026-04-29",
                            [
                                {
                                    "region_code": "HK",
                                    "region_name": "香港",
                                    "route": "BJSA -> ALA",
                                    "cheapest_cny_price": 900.0,
                                    "source_kind": "cached",
                                }
                            ],
                        )
                    ],
                    [
                        (
                            "2026-04-29",
                            [
                                {
                                    "region": "HK",
                                    "domain": "https://www.skyscanner.com.hk",
                                    "price": 900.0,
                                    "currency": "HKD",
                                    "source_url": "https://example.com/hk",
                                    "status": "page_text",
                                }
                            ],
                        )
                    ],
                    scan_mode="preview_first",
                )

                live_quotes = [
                    FlightQuote(
                        region="HK", domain="https://www.skyscanner.com.hk",
                        price=850.0, currency="HKD",
                        source_url="https://example.com/hk",
                        status="page_text", confidence=0.9,
                        cheapest_price=850.0,
                    ),
                    FlightQuote(
                        region="CN", domain="https://www.skyscanner.cn",
                        price=820.0, currency="CNY",
                        source_url="https://example.com/cn",
                        status="page_text", confidence=0.9,
                        cheapest_price=820.0,
                    ),
                ]

                async def on_progress(payload: dict[str, object]) -> None:
                    progress_events.append(payload)

                with (
                    patch(
                        "transport_scrapling.compare_via_scrapling",
                        new=AsyncMock(return_value=live_quotes),
                    ),
                    patch("transport_cdp.compare_via_pages", new=AsyncMock(return_value=[])),
                ):
                    quotes = await run_page_scan(
                        origin="BJSA", destination="ALA",
                        date="2026-04-29", region_codes=["CN", "HK"],
                        transport="scrapling",
                        scan_mode="preview_first",
                        query_payload=query_payload,
                        history_store=store,
                        on_progress=on_progress,
                    )

                self.assertEqual(len(quotes), 2)
                self.assertEqual(
                    [event["stage"] for event in progress_events[:2]],
                    ["preview_cache", "quick_live"],
                )
                self.assertEqual(progress_events[-1]["stage"], "final")

        asyncio.run(run_case())

    def test_run_page_scan_preview_first_skips_browser_fallback_after_live_success(self) -> None:
        call_count = [0]

        async def compare_side_effect(_args, selected_regions, **_kwargs):
            call_count[0] += 1
            region_codes = [region.code for region in selected_regions]
            if region_codes == ["CN"]:
                return [
                    FlightQuote(
                        region="CN", domain="https://www.skyscanner.cn",
                        price=820.0, currency="CNY",
                        source_url="https://example.com/cn",
                        status="page_text", confidence=0.9,
                        cheapest_price=820.0,
                    )
                ]
            if call_count[0] == 1:
                return [
                    FlightQuote(
                        region="HK", domain="https://www.skyscanner.com.hk",
                        price=None, currency="HKD",
                        source_url="https://example.com/hk",
                        status="page_loading", error="still loading",
                    )
                ]
            return [
                FlightQuote(
                    region="HK", domain="https://www.skyscanner.com.hk",
                    price=None, currency="HKD",
                    source_url="https://example.com/hk",
                    status="page_loading", error="still loading after retry",
                )
            ]

        async def run_case() -> None:
            progress_events: list[dict[str, object]] = []

            async def on_progress(payload: dict[str, object]) -> None:
                progress_events.append(payload)

            with (
                patch(
                    "transport_scrapling.compare_via_scrapling",
                    new=AsyncMock(side_effect=compare_side_effect),
                ) as scrapling_mock,
                patch("transport_cdp.compare_via_pages", new=AsyncMock(return_value=[])) as page_mock,
                patch("transport_cdp.ensure_cdp_ready") as ensure_cdp_ready_mock,
            ):
                quotes = await run_page_scan(
                    origin="BJSA", destination="ALA",
                    date="2026-04-29", region_codes=["CN", "HK"],
                    transport="scrapling",
                    scan_mode="preview_first",
                    region_concurrency=1,
                    on_progress=on_progress,
                )

            self.assertEqual(len(quotes), 2)
            self.assertEqual(scrapling_mock.call_count, 3)
            page_mock.assert_awaited_once()
            ensure_cdp_ready_mock.assert_not_called()
            self.assertIn("quick_live", [event["stage"] for event in progress_events])

        asyncio.run(run_case())


if __name__ == "__main__":
    unittest.main()