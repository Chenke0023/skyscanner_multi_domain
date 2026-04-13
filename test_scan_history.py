from __future__ import annotations

from pathlib import Path

from scan_history import (
    ScanHistoryStore,
    build_delta_summary_lines,
    build_history_series,
    get_failed_region_codes,
    annotate_rows_with_history,
    override_rows_source_kind,
    select_preview_region_batches,
    summarize_query_history,
)


def test_override_rows_source_kind_preserves_existing_delta() -> None:
    rows_by_date = [
        (
            "2026-05-20",
            [
                {
                    "region_code": "HK",
                    "region_name": "香港",
                    "route": "PEK -> ALA",
                    "cheapest_cny_price": 888.0,
                    "delta_label": "降 ¥100.00",
                    "updated_at": "2026-04-10T10:00:00",
                }
            ],
        )
    ]

    overridden = override_rows_source_kind(rows_by_date, "cached")

    row = overridden[0][1][0]
    assert row["source_kind"] == "cached"
    assert row["delta_label"] == "降 ¥100.00"
    assert row["updated_at"] == "2026-04-10T10:00:00"


def test_select_preview_region_batches_prioritizes_previous_winner() -> None:
    quick, remaining = select_preview_region_batches(
        ["CN", "HK", "SG"],
        [
            (
                "2026-05-20",
                [
                    {
                        "region_code": "SG",
                        "region_name": "新加坡",
                        "route": "PEK -> ALA",
                        "cheapest_cny_price": 820.0,
                    }
                ],
            )
        ],
        first_batch_size=2,
    )

    assert quick == ["SG", "CN"]
    assert remaining == ["HK"]


def test_build_delta_summary_lines_only_returns_changed_rows() -> None:
    lines = build_delta_summary_lines(
        [
            (
                "2026-05-20",
                [
                    {
                        "region_name": "香港",
                        "route": "PEK -> ALA",
                        "delta_label": "降 ¥50.00",
                        "cheapest_cny_price": 900.0,
                    },
                    {
                        "region_name": "英国",
                        "route": "PEK -> ALA",
                        "delta_label": "持平",
                        "cheapest_cny_price": 1200.0,
                    },
                ],
            )
        ]
    )

    assert lines == ["2026-05-20 | PEK -> ALA | 香港 | 降 ¥50.00"]


def test_scan_history_store_round_trip(tmp_path: Path) -> None:
    store = ScanHistoryStore(tmp_path / "scan_history.sqlite3")
    query_payload = {
        "identity": {
            "mode": "point_to_point",
            "origin_code": "PEK",
            "destination_code": "ALA",
            "date": "2026-05-20",
            "return_date": None,
        },
        "display": {"title": "北京 -> 阿拉木图"},
    }
    rows_by_date = [
        (
            "2026-05-20",
            [
                {
                    "region_code": "HK",
                    "region_name": "香港",
                    "route": "PEK -> ALA",
                    "cheapest_cny_price": 888.0,
                }
            ],
        )
    ]
    quotes_by_date = [
        (
            "2026-05-20",
            [
                {
                    "region": "HK",
                    "price": 888.0,
                    "best_price": 920.0,
                    "cheapest_price": 888.0,
                }
            ],
        )
    ]

    store.record_scan(query_payload, rows_by_date, quotes_by_date, scan_mode="full_scan")

    latest = store.get_latest_scan(query_payload)
    preview = store.get_cached_preview(query_payload)

    assert latest is not None
    assert preview is not None
    assert latest.rows_by_date == rows_by_date
    assert get_failed_region_codes(latest.quotes_by_date) == []


def test_annotate_rows_marks_cdp_reuse_failures_as_reusable() -> None:
    annotated = annotate_rows_with_history(
        [
            (
                "2026-05-20",
                [
                    {
                        "region_code": "HK",
                        "region_name": "香港",
                        "route": "PEK -> ALA",
                        "source_kind": "cdp_reuse",
                        "status": "page_loading",
                        "error": "still loading",
                    }
                ],
            )
        ]
    )

    row = annotated[0][1][0]
    assert row["can_reuse_page"] is True
    assert row["failure_action"] == "复用已打开的页面后重试"


def test_query_history_summary_tracks_low_price_and_market_distribution(tmp_path: Path) -> None:
    store = ScanHistoryStore(tmp_path / "scan_history.sqlite3")
    query_payload = {
        "identity": {
            "mode": "point_to_point",
            "origin_code": "PEK",
            "destination_code": "ALA",
            "date": "2026-05-20",
            "return_date": None,
        },
        "display": {"title": "北京 -> 阿拉木图"},
    }
    store.record_scan(
        query_payload,
        [("2026-05-20", [{"region_name": "香港", "route": "PEK -> ALA", "cheapest_cny_price": 920.0}])],
        [("2026-05-20", [{"region": "HK", "price": 920.0}])],
        scan_mode="preview_first",
    )
    store.record_scan(
        query_payload,
        [("2026-05-20", [{"region_name": "新加坡", "route": "PEK -> ALA", "cheapest_cny_price": 860.0}])],
        [("2026-05-20", [{"region": "SG", "price": 860.0}])],
        scan_mode="preview_first",
    )

    history = store.get_query_history(query_payload, limit=10)
    summary = summarize_query_history(history)
    series = build_history_series(history)

    assert len(history) == 2
    assert summary.history_low_price == 860.0
    assert summary.market_win_counts["香港"] == 1
    assert summary.market_win_counts["新加坡"] == 1
    assert len(series) == 2
    assert series[-1].cheapest_cny_price == 920.0 or series[-1].cheapest_cny_price == 860.0
