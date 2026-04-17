from __future__ import annotations

from pathlib import Path

from desktop_ui_service import DesktopUIService
from scan_history import ScanHistoryStore


def build_service(tmp_path: Path) -> DesktopUIService:
    service = DesktopUIService()
    service._state_path = tmp_path / "gui_last_query.json"
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
            "notificationsEnabled": True,
            "notifyOnRecovery": True,
            "notifyOnNewLow": True,
        }
    )

    assert "目标价" in str(result["summary"])
    assert result["config"] is not None
    assert result["config"]["auto_refresh_minutes"] == 30


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
