from __future__ import annotations

import json
from pathlib import Path

import gui


def test_normalize_query_state_uses_defaults_for_invalid_payload() -> None:
    state = gui._normalize_query_state(
        {
            "origin": "上海",
            "trip_type": "invalid",
            "date": "2026/05/01",
            "wait": 12,
            "combined_summary": False,
        },
        default_departure="2026-05-09",
        default_return="2026-05-16",
    )

    assert state["origin"] == "上海"
    assert state["trip_type"] == gui._TRIP_TYPE_ONE_WAY
    assert state["date"] == "2026-05-09"
    assert state["return_date"] == "2026-05-16"
    assert state["wait"] == "12"
    assert state["combined_summary"] is False


def test_load_and_write_query_state_round_trip(tmp_path: Path) -> None:
    target = tmp_path / "gui_last_query.json"
    payload = {
        "origin": "东京",
        "destination": "伦敦",
        "trip_type": gui._TRIP_TYPE_ROUND_TRIP,
        "date": "2026-06-01",
        "return_date": "2026-06-10",
        "regions": "JP,UK",
        "wait": "15",
        "date_window": "2",
        "exact_airport": True,
        "origin_country": False,
        "destination_country": False,
        "combined_summary": True,
    }

    gui._write_query_state(target, payload)
    loaded = gui._load_query_state(
        target,
        default_departure="2026-05-09",
        default_return="2026-05-16",
    )

    assert json.loads(target.read_text(encoding="utf-8"))["origin"] == "东京"
    assert loaded == payload


def test_build_cheapest_conclusion_highlights_winner_and_delta() -> None:
    summary = gui._build_cheapest_conclusion(
        [
            {
                "date": "2026-05-01",
                "route": "PEK -> HKG",
                "region_name": "香港",
                "cheapest_display_price": "HK$900",
                "cheapest_cny_price": 840.0,
                "status": "page_text",
                "link": "https://example.com/hk",
            },
            {
                "date": "2026-05-01",
                "route": "PEK -> HKG",
                "region_name": "新加坡",
                "cheapest_display_price": "SGD 170",
                "cheapest_cny_price": 910.0,
                "status": "page_text",
                "link": "https://example.com/sg",
            },
        ]
    )

    assert summary["headline"] == "当前最低价来自 香港"
    assert summary["price"] == "¥840.00"
    assert summary["supporting"] == "HK$900"
    assert summary["link"] == "https://example.com/hk"
    assert "¥70.00" in str(summary["insight"])


def test_build_cheapest_conclusion_handles_missing_prices() -> None:
    summary = gui._build_cheapest_conclusion(
        [
            {
                "date": "2026-05-01",
                "route": "PEK -> HKG",
                "region_name": "香港",
                "cheapest_display_price": "-",
                "cheapest_cny_price": None,
                "status": "page_loading",
                "link": "https://example.com/hk",
            }
        ]
    )

    assert summary["headline"] == "暂无最低价结论"
    assert summary["link"] is None


def test_find_cheapest_highlight_signatures_marks_all_min_rows() -> None:
    rows = [
        {
            "date": "2026-05-01",
            "route": "PEK -> HKG",
            "region_name": "香港",
            "cheapest_cny_price": 840.0,
            "link": "https://example.com/hk",
            "status": "page_text",
        },
        {
            "date": "2026-05-01",
            "route": "PEK -> HKG",
            "region_name": "新加坡",
            "cheapest_cny_price": 840.0,
            "link": "https://example.com/sg",
            "status": "page_text",
        },
        {
            "date": "2026-05-01",
            "route": "PEK -> HKG",
            "region_name": "英国",
            "cheapest_cny_price": 910.0,
            "link": "https://example.com/uk",
            "status": "page_text",
        },
    ]

    signatures = gui._find_cheapest_highlight_signatures(rows)

    assert len(signatures) == 2
    assert gui._row_signature(rows[0]) in signatures
    assert gui._row_signature(rows[1]) in signatures
    assert gui._row_signature(rows[2]) not in signatures
