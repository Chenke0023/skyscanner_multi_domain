import asyncio
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

from skyscanner_neo import (
    PAGE_TEXT_CAPTURE_LIMIT,
    REGIONS,
    _extract_scrapling_page_text,
    _persist_failure_log,
    extract_page_quote,
    run_page_scan,
)
from skyscanner_models import FlightQuote
from skyscanner_page_parser import slice_page_text_for_scan


class ExtractPageQuoteTests(unittest.TestCase):
    def test_extract_scrapling_page_text_ignores_script_payloads(self) -> None:
        class FakePage:
            html = """
                <html>
                  <body>
                    <script>window.__internal = {"state":"loading"};</script>
                    <div>显示结果依据</div>
                    <div>综合最佳</div>
                    <div>¥3,215</div>
                    <div>最便宜</div>
                    <div>¥2,184</div>
                  </body>
                </html>
            """

        page_text = _extract_scrapling_page_text(FakePage())
        quote = extract_page_quote(REGIONS["CN"], "https://example.com", page_text)

        self.assertNotIn("window.__internal", page_text)
        self.assertNotIn("loading", page_text.lower())
        self.assertEqual(quote.status, "page_text")
        self.assertEqual(quote.best_price, 3215.0)
        self.assertEqual(quote.cheapest_price, 2184.0)

    def test_best_label_allows_extra_text_on_same_line(self) -> None:
        page_text = "\n".join(
            [
                "Show results by",
                "Best option",
                "£123",
                "Cheapest",
                "£111",
            ]
        )

        quote = extract_page_quote(REGIONS["UK"], "https://example.com", page_text)

        self.assertEqual(quote.best_price, 123.0)
        self.assertEqual(quote.cheapest_price, 111.0)

    def test_loading_hint_does_not_hide_real_prices(self) -> None:
        page_text = "\n".join(
            [
                "Flights",
                "Loading results...",
                "Show results by",
                "Best",
                "£345",
                "Cheapest",
                "£222",
            ]
        )

        quote = extract_page_quote(REGIONS["UK"], "https://example.com", page_text)

        self.assertEqual(quote.status, "page_text")
        self.assertEqual(quote.best_price, 345.0)
        self.assertEqual(quote.cheapest_price, 222.0)

    def test_scope_can_find_labels_when_sort_section_is_not_near_top(self) -> None:
        page_text = ("header\n" * 1400) + "\n".join(
            [
                "Show results by",
                "Best",
                "£345",
                "Cheapest",
                "£222",
            ]
        )

        quote = extract_page_quote(REGIONS["UK"], "https://example.com", page_text)

        self.assertEqual(quote.best_price, 345.0)
        self.assertEqual(quote.cheapest_price, 222.0)

    def test_slice_keeps_sort_section_even_with_old_12000_char_budget(self) -> None:
        page_text = ("header\n" * 7000) + "\n".join(
            [
                "Show results by",
                "Best",
                "£345",
                "Cheapest",
                "£222",
            ]
        )

        captured = slice_page_text_for_scan(page_text, max_chars=12000, context_chars=120)
        quote = extract_page_quote(REGIONS["UK"], "https://example.com", captured)

        self.assertIn("Show results by", captured)
        self.assertEqual(quote.best_price, 345.0)
        self.assertEqual(quote.cheapest_price, 222.0)

    def test_slice_can_anchor_on_sort_labels_without_section_hint(self) -> None:
        page_text = ("header\n" * 7000) + "\n".join(
            [
                "Best",
                "£333",
                "Cheapest",
                "£222",
            ]
        )

        captured = slice_page_text_for_scan(page_text, max_chars=12000, context_chars=120)
        quote = extract_page_quote(REGIONS["UK"], "https://example.com", captured)

        self.assertIn("Best", captured)
        self.assertEqual(quote.best_price, 333.0)
        self.assertEqual(quote.cheapest_price, 222.0)

    def test_default_capture_limit_keeps_sort_section_near_end_of_long_page(self) -> None:
        page_text = ("header\n" * 15000) + "\n".join(
            [
                "Show results by",
                "Best",
                "£456",
                "Cheapest",
                "£234",
            ]
        )

        captured = slice_page_text_for_scan(page_text)
        quote = extract_page_quote(REGIONS["UK"], "https://example.com", captured)

        self.assertLessEqual(len(captured), PAGE_TEXT_CAPTURE_LIMIT)
        self.assertIn("Show results by", captured)
        self.assertEqual(quote.best_price, 456.0)
        self.assertEqual(quote.cheapest_price, 234.0)

    def test_indonesian_labels_are_supported(self) -> None:
        page_text = "\n".join(
            [
                "Tampilkan hasil berdasarkan",
                "Terbaik",
                "IDR 1.234.567",
                "Termurah",
                "IDR 1.111.111",
            ]
        )

        quote = extract_page_quote(REGIONS["ID"], "https://example.com", page_text)

        self.assertEqual(quote.best_price, 1234567.0)
        self.assertEqual(quote.cheapest_price, 1111111.0)

    def test_best_price_can_be_found_when_price_is_farther_from_label(self) -> None:
        page_text = "\n".join(
            [
                "Show results by",
                "Best",
                "Fastest overall",
                "1 stop",
                "Carry-on included",
                "Flexible ticket",
                "Popular with travellers",
                "£333",
                "Cheapest",
                "£222",
            ]
        )

        quote = extract_page_quote(REGIONS["UK"], "https://example.com", page_text)

        self.assertEqual(quote.best_price, 333.0)
        self.assertEqual(quote.cheapest_price, 222.0)

    def test_cheapest_only_page_text_is_marked_explicitly(self) -> None:
        page_text = "\n".join(
            [
                "Show results by",
                "Cheapest",
                "£111",
            ]
        )

        quote = extract_page_quote(REGIONS["UK"], "https://example.com", page_text)

        self.assertIsNone(quote.best_price)
        self.assertEqual(quote.cheapest_price, 111.0)
        self.assertEqual(quote.status, "page_text_cheapest_only")

    def test_recommended_label_is_treated_as_best(self) -> None:
        page_text = "\n".join(
            [
                "Show results by",
                "Recommended for most travellers",
                "£321",
                "Cheapest",
                "£222",
            ]
        )

        quote = extract_page_quote(REGIONS["UK"], "https://example.com", page_text)

        self.assertEqual(quote.best_price, 321.0)
        self.assertEqual(quote.cheapest_price, 222.0)

    def test_inconsistent_best_can_recover_from_later_candidate(self) -> None:
        page_text = "\n".join(
            [
                "Show results by",
                "Best",
                "£100",
                "Best",
                "£300",
                "Cheapest",
                "£200",
            ]
        )

        quote = extract_page_quote(REGIONS["UK"], "https://example.com", page_text)

        self.assertEqual(quote.best_price, 300.0)
        self.assertEqual(quote.cheapest_price, 200.0)
        self.assertEqual(quote.status, "page_text_recovered_best")

    def test_inconsistent_best_lower_than_cheapest_is_rejected(self) -> None:
        page_text = "\n".join(
            [
                "Show results by",
                "Best",
                "£261",
                "Cheapest",
                "£493",
            ]
        )

        quote = extract_page_quote(REGIONS["UK"], "https://example.com", page_text)

        self.assertIsNone(quote.best_price)
        self.assertEqual(quote.cheapest_price, 493.0)
        self.assertEqual(quote.status, "page_text_inconsistent")


class FailureLogTests(unittest.TestCase):
    def test_persist_failure_log_writes_excerpt_and_path(self) -> None:
        quote = FlightQuote(
            region="CN",
            domain="https://www.skyscanner.cn",
            price=None,
            currency="CNY",
            source_url="https://www.skyscanner.cn/transport/flights/bjsa/ala/260429/",
            status="page_parse_failed",
            error="页面正文未识别到 Best/Cheapest 价格",
        )

        with tempfile.TemporaryDirectory() as temp_dir:
            target = Path(temp_dir) / "failure.log"
            _persist_failure_log(
                quote,
                transport="scrapling",
                route_key="BJSA_ALA_20260429",
                page_text="综合最佳\n¥3215\n最便宜\n¥2184",
                extra={"locale": "zh-CN"},
                log_path=target,
            )

            self.assertEqual(quote.debug_log_path, str(target))
            content = target.read_text(encoding="utf-8")
            self.assertIn("transport: scrapling", content)
            self.assertIn("route: BJSA_ALA_20260429", content)
            self.assertIn("locale", content)
            self.assertIn("综合最佳", content)


class RunPageScanFallbackTests(unittest.TestCase):
    def test_run_page_scan_falls_back_failed_markets_to_page(self) -> None:
        scrapling_quotes = [
            FlightQuote(
                region="CN",
                domain="https://www.skyscanner.cn",
                price=2187.0,
                currency="CNY",
                source_url="https://www.skyscanner.cn/transport/flights/bjsa/ala/260429/",
                status="page_text",
                best_price=3217.0,
                cheapest_price=2187.0,
            ),
            FlightQuote(
                region="HK",
                domain="https://www.skyscanner.com.hk",
                price=None,
                currency="HKD",
                source_url="https://www.skyscanner.com.hk/transport/flights/bjsa/ala/260429/",
                status="page_loading",
                error="页面仍在加载结果: loading",
            ),
        ]
        page_fallback_quotes = [
            FlightQuote(
                region="HK",
                domain="https://www.skyscanner.com.hk",
                price=2465.0,
                currency="HKD",
                source_url="https://www.skyscanner.com.hk/transport/flights/bjsa/ala/260429/",
                status="page_text",
                best_price=2539.0,
                cheapest_price=2465.0,
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
                patch("transport_cdp.ensure_cdp_ready") as ensure_cdp_ready_mock,
            ):
                quotes = await run_page_scan(
                    origin="BJSA",
                    destination="ALA",
                    date="2026-04-29",
                    region_codes=["CN", "HK"],
                    transport="scrapling",
                )

                self.assertEqual(len(quotes), 2)
                quotes_by_region = {quote.region: quote for quote in quotes}
                self.assertEqual(quotes_by_region["CN"].price, 2187.0)
                self.assertEqual(quotes_by_region["HK"].price, 2465.0)
                self.assertEqual(quotes_by_region["HK"].status, "page_text")
                ensure_cdp_ready_mock.assert_called_once()
                scrapling_mock.assert_awaited_once()
                page_mock.assert_awaited_once()
                fallback_regions = page_mock.await_args.args[1]
                self.assertEqual([region.code for region in fallback_regions], ["HK"])

        asyncio.run(run_case())


if __name__ == "__main__":
    unittest.main()
