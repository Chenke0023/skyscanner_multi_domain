import unittest

from skyscanner_neo import PAGE_TEXT_CAPTURE_LIMIT, REGIONS, extract_page_quote
from skyscanner_page_parser import slice_page_text_for_scan


class ExtractPageQuoteTests(unittest.TestCase):
    def test_best_label_allows_extra_text_on_same_line(self) -> None:
        page_text = "\n".join(
            [
                "Show results by",
                "Best option",
                "US$123",
                "Cheapest",
                "US$111",
            ]
        )

        quote = extract_page_quote(REGIONS["US"], "https://example.com", page_text)

        self.assertEqual(quote.best_price, 123.0)
        self.assertEqual(quote.cheapest_price, 111.0)

    def test_scope_can_find_labels_when_sort_section_is_not_near_top(self) -> None:
        page_text = ("header\n" * 1400) + "\n".join(
            [
                "Show results by",
                "Best",
                "US$345",
                "Cheapest",
                "US$222",
            ]
        )

        quote = extract_page_quote(REGIONS["US"], "https://example.com", page_text)

        self.assertEqual(quote.best_price, 345.0)
        self.assertEqual(quote.cheapest_price, 222.0)

    def test_slice_keeps_sort_section_even_with_old_12000_char_budget(self) -> None:
        page_text = ("header\n" * 7000) + "\n".join(
            [
                "Show results by",
                "Best",
                "US$345",
                "Cheapest",
                "US$222",
            ]
        )

        captured = slice_page_text_for_scan(page_text, max_chars=12000, context_chars=120)
        quote = extract_page_quote(REGIONS["US"], "https://example.com", captured)

        self.assertIn("Show results by", captured)
        self.assertEqual(quote.best_price, 345.0)
        self.assertEqual(quote.cheapest_price, 222.0)

    def test_slice_can_anchor_on_sort_labels_without_section_hint(self) -> None:
        page_text = ("header\n" * 7000) + "\n".join(
            [
                "Best",
                "US$333",
                "Cheapest",
                "US$222",
            ]
        )

        captured = slice_page_text_for_scan(page_text, max_chars=12000, context_chars=120)
        quote = extract_page_quote(REGIONS["US"], "https://example.com", captured)

        self.assertIn("Best", captured)
        self.assertEqual(quote.best_price, 333.0)
        self.assertEqual(quote.cheapest_price, 222.0)

    def test_default_capture_limit_keeps_sort_section_near_end_of_long_page(self) -> None:
        page_text = ("header\n" * 15000) + "\n".join(
            [
                "Show results by",
                "Best",
                "US$456",
                "Cheapest",
                "US$234",
            ]
        )

        captured = slice_page_text_for_scan(page_text)
        quote = extract_page_quote(REGIONS["US"], "https://example.com", captured)

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
                "US$333",
                "Cheapest",
                "US$222",
            ]
        )

        quote = extract_page_quote(REGIONS["US"], "https://example.com", page_text)

        self.assertEqual(quote.best_price, 333.0)
        self.assertEqual(quote.cheapest_price, 222.0)

    def test_cheapest_only_page_text_is_marked_explicitly(self) -> None:
        page_text = "\n".join(
            [
                "Show results by",
                "Cheapest",
                "US$111",
            ]
        )

        quote = extract_page_quote(REGIONS["US"], "https://example.com", page_text)

        self.assertIsNone(quote.best_price)
        self.assertEqual(quote.cheapest_price, 111.0)
        self.assertEqual(quote.status, "page_text_cheapest_only")

    def test_recommended_label_is_treated_as_best(self) -> None:
        page_text = "\n".join(
            [
                "Show results by",
                "Recommended for most travellers",
                "US$321",
                "Cheapest",
                "US$222",
            ]
        )

        quote = extract_page_quote(REGIONS["US"], "https://example.com", page_text)

        self.assertEqual(quote.best_price, 321.0)
        self.assertEqual(quote.cheapest_price, 222.0)

    def test_inconsistent_best_can_recover_from_later_candidate(self) -> None:
        page_text = "\n".join(
            [
                "Show results by",
                "Best",
                "US$100",
                "Best",
                "US$300",
                "Cheapest",
                "US$200",
            ]
        )

        quote = extract_page_quote(REGIONS["US"], "https://example.com", page_text)

        self.assertEqual(quote.best_price, 300.0)
        self.assertEqual(quote.cheapest_price, 200.0)
        self.assertEqual(quote.status, "page_text_recovered_best")

    def test_inconsistent_best_lower_than_cheapest_is_rejected(self) -> None:
        page_text = "\n".join(
            [
                "Show results by",
                "Best",
                "US$261",
                "Cheapest",
                "US$493",
            ]
        )

        quote = extract_page_quote(REGIONS["US"], "https://example.com", page_text)

        self.assertIsNone(quote.best_price)
        self.assertEqual(quote.cheapest_price, 493.0)
        self.assertEqual(quote.status, "page_text_inconsistent")


if __name__ == "__main__":
    unittest.main()
