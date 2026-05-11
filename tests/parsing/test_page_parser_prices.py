"""Page parser price extraction tests (migrated from test_skyscanner_neo.py)."""

from __future__ import annotations

import unittest

from skyscanner_neo import (
    PAGE_TEXT_CAPTURE_LIMIT,
    REGIONS,
    _extract_scrapling_page_text,
    extract_page_quote,
)
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


class ParserTrustMetadataTests(unittest.TestCase):
    def test_cheapest_block_yields_high_confidence_and_no_warnings(self) -> None:
        page_text = "\n".join(
            [
                "Show results by",
                "Best",
                "£222",
                "Cheapest",
                "£222",
            ]
        )

        quote = extract_page_quote(REGIONS["UK"], "https://example.com", page_text)

        self.assertEqual(quote.price_source, "cheapest_block")
        self.assertGreaterEqual(quote.confidence or 0.0, 0.85)
        self.assertEqual(quote.parser_warnings, [])
        self.assertTrue(quote.evidence_text)

    def test_cheapest_only_lowers_confidence_and_emits_warning(self) -> None:
        page_text = "\n".join(["Show results by", "Cheapest", "£111"])

        quote = extract_page_quote(REGIONS["UK"], "https://example.com", page_text)

        self.assertEqual(quote.price_source, "cheapest_block")
        self.assertLessEqual(quote.confidence or 1.0, 0.7)
        self.assertTrue(
            any("只解析到一侧" in warning for warning in quote.parser_warnings)
        )

    def test_recovered_best_marks_source_and_warns(self) -> None:
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

        self.assertEqual(quote.price_source, "recovered_best")
        self.assertLessEqual(quote.confidence or 1.0, 0.72)
        self.assertTrue(
            any("恢复后的 Best" in warning for warning in quote.parser_warnings)
        )

    def test_best_and_cheapest_normal_price_gap_no_warning(self) -> None:
        # Best > Cheapest is the normal case — no "价格不一致" warning
        page_text = "\n".join(
            [
                "Show results by",
                "Best",
                "£300",
                "Cheapest",
                "£222",
            ]
        )

        quote = extract_page_quote(REGIONS["UK"], "https://example.com", page_text)

        self.assertEqual(quote.best_price, 300.0)
        self.assertEqual(quote.cheapest_price, 222.0)
        self.assertEqual(quote.parser_warnings, [])

    def test_inconsistent_best_only_keeps_cheapest_with_warning(self) -> None:
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

        self.assertIsNotNone(quote.confidence)
        self.assertLessEqual(quote.confidence or 1.0, 0.55)
        self.assertTrue(
            any(
                "Best/Cheapest 不一致" in warning for warning in quote.parser_warnings
            )
        )


if __name__ == "__main__":
    unittest.main()