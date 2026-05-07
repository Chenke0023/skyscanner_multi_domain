from __future__ import annotations

from skyscanner_multi_domain.models import RegionConfig
from skyscanner_multi_domain.parsing.page_parser import extract_page_quote
from skyscanner_multi_domain.parsing.price_candidates import (
    collect_price_candidates,
    extract_embedded_price_candidates,
)


def test_first_price_fallback_is_never_high_confidence() -> None:
    candidates = collect_price_candidates("Flight result card from provider Example Air. USD 480", "USD")

    assert candidates
    assert candidates[0].source == "first_price_fallback"
    assert candidates[0].confidence == "low"


def test_cheapest_marker_candidate_ranks_above_first_price() -> None:
    candidates = collect_price_candidates(
        "Header USD 999\nCheapest\nExample Air\nUSD 480\nBest\nExample Air\nUSD 520",
        "USD",
    )

    assert candidates[0].source == "near_cheapest_marker"
    assert candidates[0].amount == 480.0


def test_embedded_json_candidate_requires_booking_context() -> None:
    no_context = '"price": 123, "currency": "USD"'
    with_context = (
        '{"itinerary":{"legs":[],"agent":"Example Air","price":{"amount":480,'
        '"currency":"USD"},"deeplink":"https://example.test"}}'
    )

    assert extract_embedded_price_candidates(no_context, "USD") == []
    candidates = extract_embedded_price_candidates(with_context, "USD")
    assert candidates
    assert candidates[0].source == "embedded_json_price"
    assert candidates[0].amount == 480.0


def test_page_parser_recovers_embedded_json_price_candidate() -> None:
    region = RegionConfig("US", "United States", "skyscanner.com", "en-US", "USD")
    text = (
        "Results loaded but visible card text is sparse. "
        '{"itinerary":{"legs":[{"origin":"PEK","destination":"ALA"}],'
        '"provider":"Example Air","totalPrice":{"amount":480,"currency":"USD"},'
        '"deeplink":"https://example.test/book"}}'
    )

    quote = extract_page_quote(region, "https://example.test", text)

    assert quote.price == 480.0
    assert quote.price_source == "embedded_json_price"
    assert quote.price_candidates_count >= 1
    assert "embedded_json_price" in quote.candidate_sources
