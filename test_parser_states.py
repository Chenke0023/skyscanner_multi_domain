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

    # ── Fixture-driven regression tests ────────────────────────────────────────

    def test_best_and_cheapest_high_confidence(self) -> None:
        page_text = "Best\n£123\nCheapest\n£111"
        quote, diagnostics = extract_page_quote_with_diagnostics(REGIONS["UK"], "https://example.com", page_text)

        self.assertEqual(quote.status, "page_text")
        self.assertEqual(quote.price, 111.0)
        self.assertEqual(quote.best_price, 123.0)
        self.assertEqual(quote.cheapest_price, 111.0)
        self.assertGreater(quote.confidence or 0, 0.7)
        self.assertEqual(quote.price_source, "cheapest_block")
        self.assertTrue(len(diagnostics.best_candidates) > 0)
        self.assertTrue(len(diagnostics.cheapest_candidates) > 0)

    def test_best_only_medium_confidence_with_warning(self) -> None:
        page_text = "Best\n£123"
        quote, diagnostics = extract_page_quote_with_diagnostics(REGIONS["UK"], "https://example.com", page_text)

        self.assertEqual(quote.status, "page_text_best_only")
        self.assertEqual(quote.price, 123.0)
        self.assertIsNone(quote.cheapest_price)
        self.assertLess(quote.confidence or 1.0, 0.8)
        self.assertTrue(any("Best" in w for w in quote.parser_warnings))

    def test_cheapest_only_medium_confidence_with_warning(self) -> None:
        page_text = "Cheapest\n£111"
        quote, diagnostics = extract_page_quote_with_diagnostics(REGIONS["UK"], "https://example.com", page_text)

        self.assertEqual(quote.status, "page_text_cheapest_only")
        self.assertEqual(quote.price, 111.0)
        self.assertIsNone(quote.best_price)
        self.assertLess(quote.confidence or 1.0, 0.8)

    def test_first_price_fallback_low_confidence(self) -> None:
        page_text = "Some results here\n£99"
        quote, diagnostics = extract_page_quote_with_diagnostics(REGIONS["UK"], "https://example.com", page_text)

        self.assertEqual(quote.status, "page_text_fallback")
        self.assertEqual(quote.price, 99.0)
        self.assertLess(quote.confidence or 1.0, 0.5)
        self.assertEqual(quote.price_source, "first_price_fallback")

    def test_fallback_not_ranked_when_labeled_candidates_exist(self) -> None:
        # When Best/Cheapest labels exist, fallback should not appear in candidates
        page_text = "Best\n£123\nCheapest\n£111\nAlso £99 somewhere"
        quote, diagnostics = extract_page_quote_with_diagnostics(REGIONS["UK"], "https://example.com", page_text)

        self.assertEqual(quote.status, "page_text")
        sources = quote.candidate_sources
        self.assertNotIn("first_price_fallback", sources)

    def test_challenge_page_terminal(self) -> None:
        page_text = "Please verify you are human. Complete the security check."
        quote, diagnostics = extract_page_quote_with_diagnostics(REGIONS["UK"], "https://example.com", page_text)

        self.assertEqual(quote.status, "page_challenge")
        self.assertIsNone(quote.price)
        self.assertIsNotNone(diagnostics.state.challenge_hint)

    def test_loading_page(self) -> None:
        page_text = "Searching for the best flights..."
        quote, diagnostics = extract_page_quote_with_diagnostics(REGIONS["UK"], "https://example.com", page_text)

        self.assertEqual(quote.status, "page_loading")
        self.assertIsNone(quote.price)
        self.assertIsNotNone(diagnostics.state.loading_hint)

    def test_evidence_block_on_parse_failure(self) -> None:
        page_text = (
            "Some random text without prices or labels. "
            "This is definitely not an empty shell because it has enough content. "
            "But there are no sorting markers or prices to parse."
        )
        quote, diagnostics = extract_page_quote_with_diagnostics(REGIONS["UK"], "https://example.com", page_text)

        self.assertEqual(quote.status, "page_parse_failed")
        self.assertIsNone(quote.price)
        # Evidence block should still be present
        self.assertIsNotNone(diagnostics.failure_stage)
        self.assertIsNotNone(diagnostics.failure_reason)
        self.assertIsNotNone(quote.evidence_text)

if __name__ == "__main__":
    unittest.main()
