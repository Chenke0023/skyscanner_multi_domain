from __future__ import annotations
import unittest
from skyscanner_multi_domain.parsing.page_parser import extract_page_quote_with_diagnostics
from skyscanner_multi_domain.parsing.readiness import classify_opencli_page_readiness
from skyscanner_multi_domain.geo.regions import REGIONS

class ReadinessImprovementTests(unittest.TestCase):
    def test_classify_opencli_redirect(self) -> None:
        self.assertEqual(classify_opencli_page_readiness("Go to Skyscanner United Kingdom"), "region_redirect")

    def test_classify_opencli_unsupported(self) -> None:
        self.assertEqual(classify_opencli_page_readiness("Sorry, we don't fly this route"), "unsupported_route")

    def test_recognizes_region_redirect(self) -> None:
        page_text = "Go to Skyscanner United Kingdom to see results for your search."
        quote, diagnostics = extract_page_quote_with_diagnostics(REGIONS["UK"], "https://example.com", page_text)
        
        self.assertEqual(quote.status, "page_region_redirect")
        self.assertEqual(diagnostics.state.state, "redirect")
        self.assertIn("go to skyscanner", diagnostics.state.redirect_hint.lower())

    def test_recognizes_unsupported_route(self) -> None:
        page_text = "Sorry, we don't fly this route. Try another route."
        quote, diagnostics = extract_page_quote_with_diagnostics(REGIONS["UK"], "https://example.com", page_text)
        
        self.assertEqual(quote.status, "page_unsupported_route")
        self.assertEqual(diagnostics.state.state, "unsupported")
        self.assertIn("we don't fly", diagnostics.state.unsupported_hint.lower())

    def test_recognizes_no_flights(self) -> None:
        page_text = "No flights found. Try different dates."
        quote, diagnostics = extract_page_quote_with_diagnostics(REGIONS["UK"], "https://example.com", page_text)
        
        self.assertEqual(quote.status, "page_no_flights")
        self.assertEqual(diagnostics.state.state, "no_flights")
        self.assertIn("no flights found", diagnostics.state.no_flights_hint.lower())

    def test_recognizes_blank_page(self) -> None:
        page_text = "   \n   "
        quote, diagnostics = extract_page_quote_with_diagnostics(REGIONS["UK"], "https://example.com", page_text)
        
        self.assertEqual(quote.status, "page_empty_shell")
        self.assertEqual(diagnostics.state.state, "blank")
        self.assertTrue(diagnostics.state.is_blank)

    def test_still_prefers_results_if_present(self) -> None:
        # If there are results, even if "loading" or "no flights" text is present (e.g. in some context), 
        # it should still try to parse results if sorting marker or prices are found.
        page_text = "Searching for flights...\nBest\n£123\nCheapest\n£111"
        quote, diagnostics = extract_page_quote_with_diagnostics(REGIONS["UK"], "https://example.com", page_text)
        
        self.assertEqual(quote.status, "page_text")
        self.assertEqual(quote.best_price, 123.0)
        self.assertEqual(quote.cheapest_price, 111.0)

if __name__ == "__main__":
    unittest.main()
