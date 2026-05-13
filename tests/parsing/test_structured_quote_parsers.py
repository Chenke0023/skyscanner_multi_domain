from __future__ import annotations

from skyscanner_multi_domain.models import QuoteEvidence, RegionConfig
from skyscanner_multi_domain.parsing.dom_parser import parse_dom_cards
from skyscanner_multi_domain.parsing.hydration_parser import parse_hydration_scripts
from skyscanner_multi_domain.parsing.network_parser import parse_network_json
from skyscanner_multi_domain.parsing.quote_merge import resolve_quote


def _region() -> RegionConfig:
    return RegionConfig(
        code="UK",
        name="United Kingdom",
        domain="https://www.skyscanner.co.uk",
        locale="en-GB",
        currency="GBP",
    )


def test_parse_network_price_like_object() -> None:
    evidences = parse_network_json(
        _region(),
        "https://example.test/search",
        [{"itinerary": {"price": {"amount": 123, "currency": "GBP"}}}],
    )

    assert evidences[0].layer == "network"
    assert evidences[0].price == 123
    assert evidences[0].currency == "GBP"
    assert evidences[0].confidence == 0.9


def test_parse_hydration_script_price() -> None:
    scripts = [
        {
            "index": 0,
            "text": '{"props":{"flight":{"price":{"amount":145,"currency":"HKD"}}}}',
        }
    ]

    evidences = parse_hydration_scripts(_region(), "https://example.test/search", scripts)

    assert evidences[0].layer == "hydration"
    assert evidences[0].price == 145
    assert evidences[0].currency == "HKD"


def test_parse_dom_card_price_and_label() -> None:
    cards = [{"priceText": "£123", "cardText": "Best flight £123 Non-stop"}]

    evidences = parse_dom_cards(_region(), "https://example.test/search", cards)

    assert evidences[0].layer == "dom"
    assert evidences[0].label == "best"
    assert evidences[0].price == 123


def test_resolve_network_dom_agree_high() -> None:
    region = _region()
    result = resolve_quote(
        region,
        "https://example.test/search",
        [
            QuoteEvidence("network", 123, "GBP", "unknown", "https://example.test/search", confidence=0.9),
            QuoteEvidence("dom", 124, "GBP", "cheapest", "https://example.test/search", confidence=0.75),
        ],
    )

    assert result.confidence == "high"
    assert result.final_quote.price == 123
    assert result.final_quote.source_kind == "cdp_structured"


def test_resolve_network_dom_conflict_medium() -> None:
    result = resolve_quote(
        _region(),
        "https://example.test/search",
        [
            QuoteEvidence("network", 123, "GBP", "unknown", "https://example.test/search", confidence=0.9),
            QuoteEvidence("dom", 145, "GBP", "cheapest", "https://example.test/search", confidence=0.75),
        ],
    )

    assert result.confidence == "medium"
    assert result.conflict_reason == "network_dom_conflict"
    assert result.final_quote.error == "network_dom_conflict"
