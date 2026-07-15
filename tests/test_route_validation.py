from __future__ import annotations

from skyscanner_multi_domain.models import FlightQuote
from skyscanner_multi_domain.route_validation import (
    clear_quote_prices,
    is_semantic_mismatch,
    parse_search_route_url,
    reject_quote_route_mismatch,
    search_route_matches_requested_url,
)


def test_parse_search_route_supports_one_way_and_round_trip() -> None:
    one_way = parse_search_route_url(
        "https://www.skyscanner.es/transport/flights/BCN/TBS/260726/?adultsv2=1"
    )
    round_trip = parse_search_route_url(
        "https://www.skyscanner.es/transport/flights/bcn/tbs/260726/260802/?rtn=1"
    )

    assert one_way is not None
    assert (one_way.origin, one_way.destination, one_way.outbound_date, one_way.return_date) == (
        "bcn",
        "tbs",
        "260726",
        None,
    )
    assert round_trip is not None
    assert round_trip.return_date == "260802"


def test_route_match_ignores_host_and_query_but_not_return_date() -> None:
    requested = (
        "https://www.skyscanner.com/transport/flights/bcn/tbs/260726/260802/"
        "?market=GE&currency=GEL"
    )

    assert search_route_matches_requested_url(
        "https://www.skyscanner.ro/transport/flights/bcn/tbs/260726/260802/?adultsv2=1",
        requested,
    )
    assert not search_route_matches_requested_url(
        "https://www.skyscanner.ro/transport/flights/bcn/tbs/260726/260803/",
        requested,
    )


def test_reject_route_mismatch_clears_every_price_field() -> None:
    quote = FlightQuote(
        region="ES",
        domain="https://www.skyscanner.es",
        price=99.0,
        currency="EUR",
        source_url="https://www.skyscanner.es/",
        status="page_text",
        price_path="text[0]",
        best_price=120.0,
        best_price_path="text[1]",
        cheapest_price=99.0,
        cheapest_price_path="text[2]",
        confidence=0.95,
        rankable=True,
        result_visibility="visible",
        itinerary_legs=[{"departure_time": "08:00"}],
    )

    reject_quote_route_mismatch(
        quote,
        requested_url="https://www.skyscanner.es/transport/flights/bcn/tbs/260726/",
    )

    assert quote.status == "page_semantic_mismatch"
    assert quote.route_mismatch is True
    assert quote.price is None
    assert quote.best_price is None
    assert quote.cheapest_price is None
    assert quote.price_path is None
    assert quote.best_price_path is None
    assert quote.cheapest_price_path is None
    assert quote.itinerary_legs == []
    assert quote.confidence == 0.0
    assert quote.rankable is False
    assert quote.result_visibility == "hidden"
    assert quote.fetch_metadata["discarded_price"]["price"] == 99.0
    assert is_semantic_mismatch(quote)


def test_date_only_mismatch_is_marked_precisely() -> None:
    quote = FlightQuote(
        region="ES",
        domain="https://www.skyscanner.es",
        price=275.0,
        currency="EUR",
        source_url="https://www.skyscanner.es/transport/flights/bcn/tbs/260727/",
        status="page_text",
    )

    reject_quote_route_mismatch(
        quote,
        requested_url="https://www.skyscanner.es/transport/flights/bcn/tbs/260726/",
    )

    assert quote.route_mismatch is False
    assert quote.date_mismatch is True
    assert "日期不匹配" in (quote.error or "")


def test_clear_quote_prices_is_idempotent() -> None:
    quote = FlightQuote(
        region="ES",
        domain="https://www.skyscanner.es",
        price=275.0,
        currency="EUR",
        source_url="https://www.skyscanner.es/transport/flights/bcn/tbs/260726/",
        status="page_text",
    )

    clear_quote_prices(quote)
    clear_quote_prices(quote)

    assert quote.price is None
    assert quote.fetch_metadata["discarded_price"]["price"] == 275.0
