from __future__ import annotations

from location_resolver import LocationRecord
from search_plan import (
    build_date_candidates,
    build_market_candidates,
    build_ordered_trip_dates,
    rank_region_codes,
    rank_route_pairs,
)


def test_build_date_candidates_orders_anchor_edges_nearby_remaining() -> None:
    candidates = build_date_candidates("2026-05-20", None, 3)

    assert [(item.depart_date, item.offset, item.phase) for item in candidates] == [
        ("2026-05-20", 0, "anchor"),
        ("2026-05-17", -3, "edge"),
        ("2026-05-23", 3, "edge"),
        ("2026-05-19", -1, "nearby"),
        ("2026-05-21", 1, "nearby"),
        ("2026-05-18", -2, "full"),
        ("2026-05-22", 2, "full"),
    ]


def test_build_ordered_trip_dates_keeps_round_trip_stay_length() -> None:
    dates = build_ordered_trip_dates("2026-05-20", "2026-05-27", 1)

    assert dates == [
        ("2026-05-20", "2026-05-27"),
        ("2026-05-19", "2026-05-26"),
        ("2026-05-21", "2026-05-28"),
    ]


def test_rank_region_codes_promotes_route_relevant_market() -> None:
    ranked = rank_region_codes(
        ["CN", "HK", "SG", "UK", "KZ"],
        origin_country="CN",
        destination_country="KZ",
    )

    assert ranked[:2] == ["CN", "KZ"]
    assert set(ranked) == {"CN", "HK", "SG", "UK", "KZ"}


def test_build_market_candidates_uses_history_wins() -> None:
    rows_by_date = [
        (
            "2026-05-20",
            [
                {"region_code": "CN", "cheapest_cny_price": 2100.0, "route": "PEK -> ALA"},
                {"region_code": "HK", "cheapest_cny_price": 1200.0, "route": "PEK -> ALA"},
            ],
        )
    ]

    candidates = build_market_candidates(["CN", "HK", "SG"], rows_by_date)

    hk = next(candidate for candidate in candidates if candidate.region_code == "HK")
    assert hk.historical_win_rate > 0
    assert candidates[0].region_code == "HK"


def test_rank_route_pairs_prefers_earlier_airport_candidates() -> None:
    origins = [
        LocationRecord(name="Beijing", code="PEK", kind="airport", airport_type="large_airport"),
        LocationRecord(name="Shanghai", code="PVG", kind="airport", airport_type="large_airport"),
    ]
    destinations = [
        LocationRecord(name="Almaty", code="ALA", kind="airport", airport_type="large_airport"),
        LocationRecord(name="Astana", code="NQZ", kind="airport", airport_type="large_airport"),
    ]

    ranked = rank_route_pairs(origins, destinations)

    assert [(origin.code, destination.code) for origin, destination in ranked] == [
        ("PEK", "ALA"),
        ("PEK", "NQZ"),
        ("PVG", "ALA"),
        ("PVG", "NQZ"),
    ]
