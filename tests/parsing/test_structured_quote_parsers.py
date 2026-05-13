from __future__ import annotations

from skyscanner_multi_domain.models import QuoteEvidence, RegionConfig
from skyscanner_multi_domain.parsing.dom_parser import parse_dom_cards
from skyscanner_multi_domain.parsing.hydration_parser import parse_hydration_scripts
from skyscanner_multi_domain.parsing.network_parser import enrich_network_candidates, parse_network_json
from skyscanner_multi_domain.parsing.structured_price_scanner import scan_price_like_objects
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
    assert evidences[0].confidence == 0.92


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
    cards = [{"priceText": "£123", "cardText": "Best flight £123 Non-stop", "x": 1, "y": 2, "w": 300, "h": 120}]

    evidences = parse_dom_cards(_region(), "https://example.test/search", cards)

    assert evidences[0].layer == "dom"
    assert evidences[0].label == "best"
    assert evidences[0].price == 123
    assert "label_source" in (evidences[0].raw_ref or "")
    assert "geometry" in (evidences[0].raw_ref or "")


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
    assert "merge: network and dom agree within tolerance" in result.decision_trace
    assert result.final_quote.fetch_metadata["decision_trace"] == result.decision_trace


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
    assert "merge: network and dom conflict" in result.decision_trace


def test_structured_price_scanner_ranks_and_rejects_weak_candidates() -> None:
    payload = {
        "itineraries": [
            {"pricing": {"totalPrice": {"amount": 234, "currencyCode": "GBP"}}}
        ],
        "metadata": {"value": 999},
    }

    candidates = scan_price_like_objects(payload)

    assert candidates[0].accepted is True
    assert candidates[0].price == 234
    assert candidates[0].currency == "GBP"
    assert "itineraries" in candidates[0].path
    assert any(candidate.accepted is False and candidate.reason == "weak_context" for candidate in candidates)


def test_enrich_network_candidates_preserves_paths() -> None:
    enriched = enrich_network_candidates(
        [{"quote": {"cheapestPrice": "HK$1,234", "currency": "HKD"}}]
    )

    assert enriched[0]["layer"] == "network"
    assert enriched[0]["path"].endswith("cheapestPrice")
    assert enriched[0]["price"] == 1234


def test_resolve_quote_records_ranking_and_rejected_candidates() -> None:
    result = resolve_quote(
        _region(),
        "https://example.test/search",
        [
            QuoteEvidence("dom", 123, "GBP", "cheapest", "https://example.test/search", confidence=0.75),
            QuoteEvidence("hydration", 150, "GBP", "unknown", "https://example.test/search", confidence=0.70),
        ],
    )

    metadata = result.final_quote.fetch_metadata
    assert metadata["evidence_ranking"][0]["selected"] is True
    assert metadata["rejected_candidates"][0]["reason"] == "price_conflict"
    assert any(line.startswith("ranking:") for line in result.decision_trace)


def test_real_smoke_fixture_does_not_emit_false_positive_evidence() -> None:
    import json
    from pathlib import Path

    fixture = Path("tests/fixtures/cdp_structured/real_smoke_pek_hkg")
    region = RegionConfig(
        code="CN",
        name="China",
        domain="https://www.tianxun.com",
        locale="zh-CN",
        currency="CNY",
    )

    dom_cards = json.loads((fixture / "dom_cards.json").read_text(encoding="utf-8"))
    network_payloads = json.loads((fixture / "network_candidates_sample.json").read_text(encoding="utf-8"))
    hydration_scripts = json.loads((fixture / "hydration_candidates_sample.json").read_text(encoding="utf-8"))

    evidences = []
    evidences.extend(parse_dom_cards(region, "https://example.test/search", dom_cards))
    evidences.extend(parse_network_json(region, "https://example.test/search", network_payloads))
    evidences.extend(parse_hydration_scripts(region, "https://example.test/search", hydration_scripts))

    assert evidences == []
    assert json.loads((fixture / "page_health.json").read_text(encoding="utf-8"))["hasBody"] is True
