from __future__ import annotations

from types import SimpleNamespace

from desktop_logic import (
    _build_cheapest_conclusion,
    _build_compare_rows,
    _build_recommendation_payload,
)
from skyscanner_multi_domain.models import FlightQuote
from skyscanner_multi_domain.scan.fetch_types import apply_quote_trust_policy
from skyscanner_multi_domain.scan.orchestrator import quotes_to_dicts
from skyscanner_multi_domain.scan.result_service import ResultService


def _fallback_row() -> dict[str, object]:
    return {
        "date": "2026-07-26",
        "route": "ALC -> BUS",
        "region_code": "GE",
        "region_name": "格鲁吉亚",
        "cheapest_cny_price": 589.99,
        "cheapest_display_price": "229.00 GEL",
        "link": "https://example.test/ge",
        "status": "page_text_fallback",
        "price_source": "first_price_fallback",
        "confidence": 0.45,
    }


def test_unrankable_fallback_is_not_converted_to_a_comparable_price() -> None:
    quote = FlightQuote(
        region="GE",
        domain="skyscanner.ge",
        price=229.0,
        currency="GEL",
        source_url="https://example.test/ge",
        status="page_text_fallback",
        cheapest_price=229.0,
        confidence=0.45,
        price_source="first_price_fallback",
        rankable=False,
        result_visibility="visible",
        requires_manual_review=True,
    )

    serialized = quotes_to_dicts([quote])[0]
    row = ResultService().simplify_quotes([serialized], route_label="ALC -> BUS")[0]

    assert serialized["rankable"] is False
    assert row["cheapest_cny_price"] is None
    assert row["cheapest_display_price"] is None
    assert row["excluded_price_display"] == "229.00 GEL"
    assert row["decision_eligible"] is False
    assert row["failure_category"] == "未能读取价格"


def test_fallback_cannot_be_made_rankable_by_a_low_threshold() -> None:
    quote = FlightQuote(
        region="GE",
        domain="skyscanner.ge",
        price=229.0,
        currency="GEL",
        source_url="https://example.test/ge",
        status="page_text_fallback",
        confidence=0.45,
        price_source="first_price_fallback",
    )

    apply_quote_trust_policy(
        quote,
        config=SimpleNamespace(
            rankable_confidence=0.10,
            low_confidence_policy="accept-review",
            challenge_policy="stop",
        ),
    )

    assert quote.rankable is False
    assert quote.requires_manual_review is True


def test_legacy_first_price_fallback_cannot_become_current_minimum() -> None:
    trusted = {
        "date": "2026-07-26",
        "route": "ALC -> BUS",
        "region_code": "TR",
        "region_name": "土耳其",
        "cheapest_cny_price": 1589.0,
        "cheapest_display_price": "9,300.00 TRY",
        "link": "https://example.test/tr",
        "status": "page_text",
        "price_source": "cheapest_block",
        "confidence": 0.9,
        "rankable": True,
        "itinerary_legs": [
            {
                "direction": "outbound",
                "departure_time": "08:10",
                "arrival_time": "11:25",
                "stop_count": 1,
                "duration_minutes": 315,
            }
        ],
    }

    conclusion = _build_cheapest_conclusion([_fallback_row(), trusted])

    assert conclusion["headline"] == "当前最低价来自 土耳其"
    assert conclusion["price"] == "¥1,589.00"
    assert conclusion["link"] == "https://example.test/tr"
    assert conclusion["itinerary_legs"] == trusted["itinerary_legs"]

    recommendation = _build_recommendation_payload([_fallback_row(), trusted])
    assert recommendation["itinerary_legs"] == trusted["itinerary_legs"]


def test_weak_fallback_is_not_reported_as_recovered_success() -> None:
    comparison = _build_compare_rows([_fallback_row()], [])

    assert comparison[0]["current"] == "-"
    assert comparison[0]["change"] == "仍无有效价格"


def test_only_weak_fallbacks_produce_no_clickable_minimum() -> None:
    conclusion = _build_cheapest_conclusion([_fallback_row()])

    assert conclusion["headline"] == "暂无可信最低价"
    assert conclusion["link"] is None
    assert conclusion["button_text"] == "等待可信结果"
