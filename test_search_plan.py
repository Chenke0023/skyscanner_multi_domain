from __future__ import annotations

from location_resolver import LocationRecord
from search_plan import (
    TripIntent,
    build_date_candidates,
    build_market_candidates,
    build_ordered_trip_dates,
    build_search_plan,
    flatten_plan_batches,
    rank_region_codes,
    rank_route_pairs,
    render_search_plan,
    scan_batch_region_codes,
)
from scan_history import build_plan_telemetry
from scan_orchestrator import quotes_to_dicts
from skyscanner_models import FlightQuote


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


def test_all_candidates_have_reason_and_score_breakdown() -> None:
    origins = [
        LocationRecord(name="Beijing", code="PEK", kind="airport", airport_type="large_airport"),
    ]
    destinations = [
        LocationRecord(name="Almaty", code="ALA", kind="airport", airport_type="large_airport"),
    ]
    plan = build_search_plan(
        TripIntent(
            origin_input="北京",
            destination_input="阿拉木图",
            depart_date="2026-05-20",
            return_date=None,
            origin_is_country=False,
            destination_is_country=False,
            date_window=1,
            user_regions=["HK"],
        ),
        origins,
        destinations,
        ["CN", "HK", "SG"],
        origin_country="CN",
    )

    candidates = [
        *plan.route_candidates,
        *plan.date_candidates,
        *plan.market_candidates,
    ]
    assert candidates
    assert all(candidate.reason for candidate in candidates)
    assert all(candidate.score_breakdown for candidate in candidates)


def test_score_breakdown_sums_to_score_for_route_and_market() -> None:
    origins = [
        LocationRecord(name="Beijing", code="PEK", kind="airport", airport_type="large_airport"),
    ]
    destinations = [
        LocationRecord(name="Almaty", code="ALA", kind="airport", airport_type="large_airport"),
    ]
    plan = build_search_plan(
        TripIntent(
            origin_input="北京",
            destination_input="阿拉木图",
            depart_date="2026-05-20",
            return_date=None,
            origin_is_country=False,
            destination_is_country=False,
            date_window=0,
            user_regions=[],
        ),
        origins,
        destinations,
        ["CN", "HK"],
        origin_country="CN",
    )

    for candidate in [*plan.route_candidates, *plan.market_candidates]:
        assert abs(sum(candidate.score_breakdown.values()) - candidate.score) < 0.001


def test_plan_task_count_unchanged_in_phase_two() -> None:
    origins = [
        LocationRecord(name="Beijing", code="PEK", kind="airport", airport_type="large_airport"),
        LocationRecord(name="Shanghai", code="PVG", kind="airport", airport_type="large_airport"),
    ]
    destinations = [
        LocationRecord(name="Almaty", code="ALA", kind="airport", airport_type="large_airport"),
        LocationRecord(name="Astana", code="NQZ", kind="airport", airport_type="large_airport"),
    ]
    plan = build_search_plan(
        TripIntent(
            origin_input="中国",
            destination_input="哈萨克斯坦",
            depart_date="2026-05-20",
            return_date=None,
            origin_is_country=True,
            destination_is_country=True,
            date_window=1,
            user_regions=[],
        ),
        origins,
        destinations,
        ["CN", "HK", "SG", "UK", "KZ"],
        origin_country="CN",
        destination_country="KZ",
    )

    assert len(plan.tasks) == 2 * 2 * 3 * 5
    assert sum(len(batch.tasks) for batch in plan.batches) == len(plan.tasks)


def test_scan_batches_preserve_all_tasks() -> None:
    origins = [
        LocationRecord(name="Beijing", code="PEK", kind="airport", airport_type="large_airport"),
        LocationRecord(name="Shanghai", code="PVG", kind="airport", airport_type="large_airport"),
    ]
    destinations = [
        LocationRecord(name="Almaty", code="ALA", kind="airport", airport_type="large_airport"),
    ]
    plan = build_search_plan(
        TripIntent(
            origin_input="中国",
            destination_input="哈萨克斯坦",
            depart_date="2026-05-20",
            return_date=None,
            origin_is_country=True,
            destination_is_country=False,
            date_window=1,
            user_regions=[],
        ),
        origins,
        destinations,
        ["CN", "HK", "SG", "UK", "KZ"],
        origin_country="CN",
        destination_country="KZ",
    )

    def task_key(task):
        return (
            task.route.origin_code,
            task.route.destination_code,
            task.date.depart_date,
            task.date.return_date,
            task.market.region_code,
        )

    flattened = flatten_plan_batches(plan.batches)
    assert len(flattened) == len(plan.tasks)
    assert {task_key(task) for task in flattened} == {task_key(task) for task in plan.tasks}


def test_scan_batches_do_not_drop_markets() -> None:
    origins = [LocationRecord(name="Beijing", code="PEK", kind="airport")]
    destinations = [LocationRecord(name="Almaty", code="ALA", kind="airport")]
    plan = build_search_plan(
        TripIntent("北京", "阿拉木图", "2026-05-20", None, False, False, 1, []),
        origins,
        destinations,
        ["CN", "HK", "SG", "UK", "KZ"],
    )

    market_codes_from_tasks = {task.market.region_code for task in plan.tasks}
    market_codes_from_batches = {
        task.market.region_code
        for batch in plan.batches
        for task in batch.tasks
    }
    assert market_codes_from_batches == market_codes_from_tasks


def test_batch_order_is_stable() -> None:
    origins = [LocationRecord(name="Beijing", code="PEK", kind="airport")]
    destinations = [LocationRecord(name="Almaty", code="ALA", kind="airport")]
    plan = build_search_plan(
        TripIntent("北京", "阿拉木图", "2026-05-20", None, False, False, 2, []),
        origins,
        destinations,
        ["CN", "HK", "SG", "UK", "KZ"],
    )

    expected_order = ["probe", "verify", "expand", "deep"]
    phases = [batch.phase for batch in plan.batches]
    assert phases == [phase for phase in expected_order if phase in phases]


def test_batch_region_codes_are_unique_and_ordered() -> None:
    origins = [LocationRecord(name="Beijing", code="PEK", kind="airport")]
    destinations = [LocationRecord(name="Almaty", code="ALA", kind="airport")]
    plan = build_search_plan(
        TripIntent("北京", "阿拉木图", "2026-05-20", None, False, False, 2, []),
        origins,
        destinations,
        ["CN", "HK", "SG", "UK", "KZ"],
    )

    for batch in plan.batches:
        codes = scan_batch_region_codes(batch)
        first_seen = []
        for task in batch.tasks:
            if task.market.region_code not in first_seen:
                first_seen.append(task.market.region_code)
        assert codes == first_seen
        assert len(codes) == len(set(codes))


def test_render_search_plan_includes_explanations() -> None:
    origins = [
        LocationRecord(name="Beijing", code="PEK", kind="airport", airport_type="large_airport"),
    ]
    destinations = [
        LocationRecord(name="Almaty", code="ALA", kind="airport", airport_type="large_airport"),
    ]
    plan = build_search_plan(
        TripIntent(
            origin_input="北京",
            destination_input="阿拉木图",
            depart_date="2026-05-20",
            return_date=None,
            origin_is_country=False,
            destination_is_country=False,
            date_window=0,
            user_regions=[],
        ),
        origins,
        destinations,
        ["CN"],
        origin_country="CN",
    )

    rendered = render_search_plan(plan)

    assert "扫描计划" in rendered
    assert "市场顺序" in rendered
    assert "日期顺序" in rendered
    assert "路线顺序" in rendered
    assert "批次" in rendered
    assert "reason=" in rendered


def test_quotes_to_dicts_preserves_plan_metadata() -> None:
    quote = FlightQuote(
        region="HK",
        domain="https://www.skyscanner.com.hk",
        price=1200.0,
        currency="HKD",
        source_url="https://example.test",
        status="ok",
        plan_rank=3,
        plan_score=0.72,
        plan_phase="probe",
        plan_reason="baseline 市场",
        route_rank=1,
        date_rank=2,
        market_rank=3,
    )

    payload = quotes_to_dicts([quote])[0]

    assert payload["plan_rank"] == 3
    assert payload["plan_score"] == 0.72
    assert payload["plan_phase"] == "probe"
    assert payload["plan_reason"] == "baseline 市场"
    assert payload["route_rank"] == 1
    assert payload["date_rank"] == 2
    assert payload["market_rank"] == 3


def test_build_plan_telemetry_tracks_first_valid_and_best_result() -> None:
    telemetry = build_plan_telemetry(
        [
            (
                "2026-05-20",
                [
                    {"region": "CN", "price": None, "plan_rank": 1, "market_rank": 1},
                    {
                        "region": "HK",
                        "price": 1800.0,
                        "cheapest_price": 1800.0,
                        "plan_rank": 2,
                        "market_rank": 2,
                        "date_rank": 1,
                        "route_rank": 1,
                    },
                    {
                        "region": "KZ",
                        "price": 1200.0,
                        "cheapest_price": 1200.0,
                        "plan_rank": 5,
                        "market_rank": 3,
                        "date_rank": 2,
                        "route_rank": 1,
                    },
                ],
            )
        ]
    )

    assert telemetry["total_tasks"] == 3
    assert telemetry["priced_tasks"] == 2
    assert telemetry["first_valid_task_index"] == 2
    assert telemetry["best_result_found_at_task_index"] == 5
    assert telemetry["best_result_market_rank"] == 3
    assert telemetry["best_result_date_rank"] == 2
    assert telemetry["best_result_route_rank"] == 1
