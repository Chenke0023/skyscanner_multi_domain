from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

from desktop_ui_service import DesktopUIService
from skyscanner_multi_domain.scan.confirmation import PriceConfirmationStore
from skyscanner_multi_domain.scan.history import ScanHistoryStore


def build_service(tmp_path: Path) -> DesktopUIService:
    service = DesktopUIService()
    service._state_path = tmp_path / "gui_last_query.json"
    service.confirmations = PriceConfirmationStore(tmp_path / "price_confirmations.jsonl")
    service.history_store = ScanHistoryStore(tmp_path / "scan_history.sqlite3")
    service._refresh_history_lists()
    return service


def test_update_query_state_persists_form_and_hints(tmp_path: Path) -> None:
    service = build_service(tmp_path)

    state = service.update_query_state(
        {
            "origin": "上海",
            "destination": "东京",
            "trip_type": "one_way",
            "date": "2026-06-01",
            "return_date": "",
            "regions": "JP",
            "wait": "12",
            "date_window": "2",
            "exact_airport": False,
            "origin_country": False,
            "destination_country": False,
            "combined_summary": True,
        }
    )

    assert state["form"]["origin"] == "上海"
    assert state["form"]["destination"] == "东京"
    assert "JP" in state["hints"]["regions"]
    assert service._state_path.exists()


def test_save_alert_config_returns_serializable_summary(tmp_path: Path) -> None:
    service = build_service(tmp_path)
    service.update_query_state(
        {
            "origin": "北京",
            "destination": "香港",
            "trip_type": "one_way",
            "date": "2026-06-01",
            "return_date": "",
            "regions": "",
            "wait": "10",
            "date_window": "1",
            "exact_airport": False,
            "origin_country": False,
            "destination_country": False,
            "combined_summary": True,
        }
    )

    result = service.save_alert_config(
        {
            "targetPrice": "900",
            "dropAmount": "50",
            "autoRefreshMinutes": "30",
            "autoRefreshMode": "background",
            "notificationsEnabled": True,
            "notifyOnRecovery": True,
            "notifyOnNewLow": True,
        }
    )

    assert "目标价" in str(result["summary"])
    assert result["config"] is not None
    assert result["config"]["auto_refresh_minutes"] == 30
    assert result["config"]["auto_refresh_mode"] == "background"
    assert "后台自动复扫" in str(result["summary"])


def test_install_background_auto_refresh_uses_ui_interval(tmp_path: Path) -> None:
    service = build_service(tmp_path)

    with patch("desktop_ui_service.install_auto_refresh_launchd", return_value=0) as install:
        result = service.install_background_auto_refresh(
            {
                "intervalMinutes": "600",
                "limit": "1",
                "onlyOnAcPower": True,
            }
        )

    assert result["ok"] is True
    assert result["intervalMinutes"] == 600
    args = install.call_args.args[0]
    assert args.interval_minutes == 600
    assert args.only_on_ac_power is True


def test_queue_failure_region_updates_retry_queue(tmp_path: Path) -> None:
    service = build_service(tmp_path)

    payload = service.queue_failure_region(
        {
            "date": "2026-06-01",
            "route": "PEK -> HKG",
            "regionCode": "HK",
            "regionName": "香港",
        }
    )

    assert payload["queuedRegions"] == ["HK"]
    state = service.get_ui_state()
    assert state["alerts"]["pendingRetryRegions"] == ["HK"]


def test_apply_repair_action_queues_matching_failure_class(tmp_path: Path) -> None:
    service = build_service(tmp_path)
    service._quote_snapshots_by_date = [
        (
            "2026-06-01",
            [
                {"region": "HK", "status": "page_parse_failed", "source_url": "https://example.test/hk"},
                {"region": "SG", "status": "opencli_timeout", "source_url": "https://example.test/sg"},
            ],
        )
    ]

    payload = service.apply_repair_action({"action": "queue_retry", "failureClass": "parse_failed"})

    assert payload["matched"] == 1
    assert payload["queuedRegions"] == ["HK"]
    assert service.get_ui_state()["alerts"]["pendingRetryRegions"] == ["HK"]


def test_apply_repair_action_skip_removes_current_repair_task(tmp_path: Path) -> None:
    service = build_service(tmp_path)
    service._quote_snapshots_by_date = [
        (
            "2026-06-01",
            [{"region": "HK", "status": "page_parse_failed", "source_url": "https://example.test/hk"}],
        )
    ]

    before = service.get_ui_state()["results"]["trust"]["repairPlan"]["summary"]
    payload = service.apply_repair_action({"action": "skip", "failureClass": "parse_failed"})
    after = service.get_ui_state()["results"]["trust"]["repairPlan"]["summary"]

    assert before["total_repair_tasks"] == 1
    assert payload["skipped"] == 1
    assert after["total_repair_tasks"] == 0


def test_apply_repair_action_extend_wait_starts_selected_region_scan(tmp_path: Path) -> None:
    service = build_service(tmp_path)
    service._form_state["wait"] = "10"
    service._quote_snapshots_by_date = [
        (
            "2026-06-01",
            [{"region": "SG", "status": "page_loading", "source_url": "https://example.test/sg"}],
        )
    ]

    with patch.object(service, "start_scan", return_value={"ok": True}) as start_scan:
        payload = service.apply_repair_action({"action": "extend_wait", "failureClass": "still_loading"})

    assert payload["action"] == "extend_wait"
    assert service._form_state["wait"] == "18"
    start_payload = start_scan.call_args.args[0]
    assert start_payload["rerunScopeOverride"] == "selected_regions"
    assert start_payload["selectedRegionCodes"] == ["SG"]
    assert start_payload["allowBrowserFallback"] is True


def test_record_price_confirmation_persists_sample_and_updates_trust_summary(tmp_path: Path) -> None:
    service = build_service(tmp_path)
    row = {
        "date": "2026-06-01",
        "route": "PEK -> HKG",
        "region_code": "HK",
        "region_name": "香港",
        "link": "https://example.test/hk",
        "cheapest_cny_price": 900.0,
        "confidence": 0.9,
        "price_source": "cheapest_block",
    }

    result = service.record_price_confirmation({"row": row, "status": "confirmed"})
    state = service.get_ui_state()

    assert result["summary"]["confirmed"] == 1
    assert state["results"]["trust"]["priceConfirmationSummary"]["total"] == 1
    samples = service.confirmations.load()
    assert samples[0].region_code == "HK"
    assert samples[0].status == "confirmed"


def test_desktop_progress_includes_active_plan_phase(tmp_path: Path) -> None:
    service = build_service(tmp_path)

    service._update_partial_scan(
        rows_by_date=[],
        status="正在扫描阶段 probe",
        log_message="",
        plan_progress={
            "active_plan_phase": "probe",
            "plan_batch_id": 1,
            "plan_batch_count": 3,
            "plan_batch_reason": "核心路线、核心日期和高优先级市场",
            "plan_batch_completed": False,
        },
    )

    assert service._progress["active_plan_phase"] == "probe"
    assert service._progress["plan_batch_id"] == 1
    assert service._progress["plan_batch_count"] == 3
    assert service._progress["plan_batch_reason"] == "核心路线、核心日期和高优先级市场"


def test_history_detail_includes_plan_telemetry_and_trust_summary(tmp_path: Path) -> None:
    service = build_service(tmp_path)
    record = type(
        "_Record",
        (),
        {
            "query_payload": {
                "plan_telemetry": {
                    "total_tasks": 3,
                    "priced_tasks": 2,
                    "first_valid_price_task_index": 1,
                    "best_price_task_index": 2,
                    "best_market_rank": 1,
                    "failed_tasks_by_failure_class": {"parse_failed": 1},
                }
            },
            "rows_by_date": [
                (
                    "2026-06-01",
                    [
                        {
                            "region_name": "中国",
                            "cheapest_cny_price": 1200.0,
                            "confidence": 0.9,
                            "price_source": "cheapest_block",
                            "parser_warnings": [],
                        },
                        {
                            "region_name": "香港",
                            "cheapest_cny_price": 1250.0,
                            "confidence": 0.45,
                            "price_source": "first_price_fallback",
                            "parser_warnings": ["只解析到一侧价格。"],
                        },
                    ],
                )
            ],
            "quotes_by_date": [],
            "created_at": "2026-05-07T10:00:00",
            "id": 1,
        },
    )()

    detail = service._format_history_detail_locked([record])

    assert "SearchPlan 复盘" in detail
    assert "2/3 个拿到价格" in detail
    assert "parse_failed×1" in detail
    assert "解析可信度" in detail
    assert "first_price_fallback×1" in detail
    assert "低可信度结果: 1" in detail
    assert "Parser warnings: 1" in detail
