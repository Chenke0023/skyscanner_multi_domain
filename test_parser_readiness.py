from __future__ import annotations

from skyscanner_multi_domain.parsing.readiness import classify_opencli_page_readiness


def test_readiness_classifies_challenge_without_retry_surface() -> None:
    assert classify_opencli_page_readiness("Security check captcha verify you are human") == "challenge"


def test_readiness_classifies_no_flights() -> None:
    assert classify_opencli_page_readiness("No flights found. Try different dates for this route.") == "no_flights"


def test_readiness_classifies_loading_before_price_ready() -> None:
    assert classify_opencli_page_readiness("Searching flights, please wait while we find flights") == "still_loading"


def test_readiness_classifies_price_ready_with_context() -> None:
    text = "Cheapest flight Direct Travel Providers USD 480 Book this itinerary"
    assert classify_opencli_page_readiness(text) == "price_ready"


def test_readiness_classifies_unknown_parse_surface() -> None:
    text = "Skyscanner results page with route information and filters but no recognizable fare yet"
    assert classify_opencli_page_readiness(text) == "unknown_parse_surface"


def test_readiness_classifies_redirect() -> None:
    assert classify_opencli_page_readiness("Go to Skyscanner United Kingdom") == "region_redirect"


def test_readiness_classifies_unsupported() -> None:
    assert classify_opencli_page_readiness("Sorry, we don't fly this route") == "unsupported_route"
