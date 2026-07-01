from __future__ import annotations

import json
from pathlib import Path
import types
from unittest.mock import patch

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


def test_build_recommendation_payload_uses_lowest_candidate() -> None:
    payload = gui._build_recommendation_payload(
        [
            {
                "date": "2026-05-01",
                "route": "PEK -> HKG",
                "region_name": "香港",
                "cheapest_cny_price": 840.0,
                "source_label": "实时直连",
                "stability_label": "近期稳定",
                "market_reliability_label": "稳定可下单",
                "link": "https://example.com/hk",
            },
            {
                "date": "2026-05-02",
                "route": "PEK -> HKG",
                "region_name": "新加坡",
                "cheapest_cny_price": 900.0,
                "source_label": "实时直连",
                "stability_label": "近期稳定",
                "market_reliability_label": "稳定可下单",
                "link": "https://example.com/sg",
            },
        ]
    )

    assert payload["headline"] == "推荐优先打开 香港"
    assert payload["price"] == "¥840.00"
    assert payload["link"] == "https://example.com/hk"
    assert "¥60.00" in str(payload["insight"])


def test_build_calendar_summary_supports_round_trip_matrix() -> None:
    summary = gui._build_calendar_summary(
        [
            {
                "date": "2026-05-01 -> 2026-05-05",
                "route": "PEK -> HKG",
                "region_name": "香港",
                "cheapest_cny_price": 840.0,
            },
            {
                "date": "2026-05-02 -> 2026-05-06",
                "route": "PEK -> HKG",
                "region_name": "新加坡",
                "cheapest_cny_price": 900.0,
            },
        ]
    )

    assert summary["2026-05-01"]["2026-05-05"]["region_name"] == "香港"
    assert summary["2026-05-02"]["2026-05-06"]["region_name"] == "新加坡"


def test_build_compare_rows_identifies_failure_recovery() -> None:
    rows = gui._build_compare_rows(
        [
            {
                "date": "2026-05-01",
                "route": "PEK -> HKG",
                "region_name": "香港",
                "cheapest_cny_price": 840.0,
            }
        ],
        [
            {
                "date": "2026-05-01",
                "route": "PEK -> HKG",
                "region_name": "香港",
                "cheapest_cny_price": None,
            }
        ],
    )

    assert rows[0]["change"] == "由失败变成功"


def test_build_window_summary_text_mentions_winner_and_spread() -> None:
    text = gui._build_window_summary_text(
        [
            {
                "date": "2026-05-01",
                "route": "PEK -> HKG",
                "region_name": "香港",
                "cheapest_cny_price": 840.0,
            },
            {
                "date": "2026-05-02",
                "route": "PEK -> HKG",
                "region_name": "新加坡",
                "cheapest_cny_price": 920.0,
            },
        ],
        [],
    )

    assert "窗口最低价" in text
    assert "香港" in text
    assert "¥80.00" in text


def test_build_market_delta_explanation_mentions_history_wins() -> None:
    history_records = [
        types.SimpleNamespace(
            scan_count=0,
            created_at="2026-04-10T10:00:00",
            id=1,
            rows_by_date=[
                (
                    "2026-05-01",
                    [
                        {
                            "region_name": "香港",
                            "route": "PEK -> HKG",
                            "cheapest_cny_price": 800.0,
                        }
                    ],
                )
            ],
        )
    ]

    text = gui._build_market_delta_explanation(
        [
            {
                "date": "2026-05-01",
                "route": "PEK -> HKG",
                "region_name": "香港",
                "cheapest_cny_price": 840.0,
            },
            {
                "date": "2026-05-01",
                "route": "PEK -> HKG",
                "region_name": "新加坡",
                "cheapest_cny_price": 920.0,
            },
        ],
        history_records,
    )

    assert "香港 比 新加坡 低" in text
    assert "赢过 1 次" in text


def test_upsert_rows_by_date_replaces_existing_trip() -> None:
    updated = gui._upsert_rows_by_date(
        [("2026-05-20", [{"region_name": "香港"}])],
        "2026-05-20",
        [{"region_name": "新加坡"}],
    )

    assert updated == [("2026-05-20", [{"region_name": "新加坡"}])]


def test_upsert_quotes_by_date_appends_new_trip() -> None:
    updated = gui._upsert_quotes_by_date(
        [("2026-05-20", [{"region": "HK"}])],
        "2026-05-21",
        [{"region": "SG"}],
    )

    assert updated == [
        ("2026-05-20", [{"region": "HK"}]),
        ("2026-05-21", [{"region": "SG"}]),
    ]


def test_retry_queue_deduplicates_by_trip_route_and_region() -> None:
    logs: list[str] = []
    app = types.SimpleNamespace(
        _pending_retry_targets={},
        _selected_failure_row=lambda: {
            "date": "2026-05-20",
            "route": "PEK -> HKG",
            "region_code": "HK",
            "region_name": "香港",
        },
        log=lambda line: logs.append(line),
    )

    gui.App._queue_selected_failure_market(app)
    gui.App._queue_selected_failure_market(app)

    assert len(app._pending_retry_targets) == 1
    assert any("当前待补扫 1 个" in line for line in logs)


def test_check_environment_only_detects_cdp_without_auto_launching_browser() -> None:
    status_updates: list[str] = []
    log_lines: list[str] = []
    app = types.SimpleNamespace(
        cli=types.SimpleNamespace(project_root=Path("/tmp/skyscanner")),
        status_var=types.SimpleNamespace(set=lambda value: status_updates.append(value)),
        log=lambda line: log_lines.append(line),
    )

    with (
        patch("gui.importlib.util.find_spec", return_value=object()),
        patch("gui.NeoCli", return_value=types.SimpleNamespace(available=True)),
        patch("gui.detect_cdp_version", return_value=None) as detect_cdp_mock,
        patch("gui.ensure_cdp_ready") as ensure_cdp_ready_mock,
        patch("gui.messagebox.showinfo") as showinfo_mock,
        patch("gui.messagebox.showwarning"),
    ):
        gui.App.check_environment(app)

    detect_cdp_mock.assert_called_once()
    ensure_cdp_ready_mock.assert_not_called()
    showinfo_mock.assert_called_once()
    assert status_updates == ["Scrapling 主抓取: 已安装"]
    assert any("浏览器/CDP 回退: 未连接" in line for line in log_lines)
