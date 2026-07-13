from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

import desktop_ui_service
from desktop_ui_service import DesktopUIService
from skyscanner_multi_domain.geo.location_resolver import LocationRecord


def _run_point_worker(service: DesktopUIService) -> None:
    service._run_scan_worker(
        "BJSA",
        "ALA",
        "2026-05-20",
        None,
        ["CN"],
        1,
        0,
        False,
        {"identity": {"date": "2026-05-20"}},
        "all",
        [],
    )


def _run_expanded_worker(service: DesktopUIService) -> None:
    service._run_expanded_scan_worker(
        "北京",
        "阿拉木图",
        "beijing",
        "almaty",
        [LocationRecord("北京", "BJSA", "city", country="CN")],
        [LocationRecord("阿拉木图", "ALA", "airport", country="KZ")],
        "2026-05-20",
        None,
        ["CN"],
        1,
        0,
        False,
        {"identity": {"date": "2026-05-20"}},
        "all",
        [],
    )


def test_point_scan_worker_reports_boundary_error(monkeypatch: pytest.MonkeyPatch) -> None:
    service = DesktopUIService()
    errors: list[str] = []

    def fail_before_scan(*_: Any, **__: Any) -> list[tuple[str, str | None]]:
        raise RuntimeError("scan setup broke")

    monkeypatch.setattr(desktop_ui_service, "build_ordered_trip_dates", fail_before_scan)
    monkeypatch.setattr(service, "_handle_scan_error", errors.append)

    _run_point_worker(service)

    assert errors == ["scan setup broke"]


def test_point_scan_worker_normalizes_browser_launch_error(monkeypatch: pytest.MonkeyPatch) -> None:
    service = DesktopUIService()

    def fail_before_scan(*_: Any, **__: Any) -> list[tuple[str, str | None]]:
        raise RuntimeError("browser-unavailable: no launchable browser")

    monkeypatch.setattr(desktop_ui_service, "build_ordered_trip_dates", fail_before_scan)

    _run_point_worker(service)

    status = service.get_ui_state()["status"]
    message = str(status["message"])
    error = str(status["error"])
    assert "browser-unavailable" in message
    assert "browser-unavailable" in error


def test_point_scan_worker_normalizes_browser_window_protocol_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = DesktopUIService()

    def fail_before_scan(*_: Any, **__: Any) -> list[tuple[str, str | None]]:
        raise RuntimeError("browser-unavailable: Browser window not found")

    monkeypatch.setattr(desktop_ui_service, "build_ordered_trip_dates", fail_before_scan)

    _run_point_worker(service)

    status = service.get_ui_state()["status"]
    message = str(status["message"])
    assert "browser-unavailable" in message
    assert "Chrome、Edge 或 Comet" in message


def test_point_scan_worker_uses_runtime_trace_dir_when_cwd_is_read_only(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    service = DesktopUIService()
    runtime_traces = tmp_path / "app-home" / "traces"
    readonly_cwd = tmp_path / "readonly-cwd"
    readonly_cwd.mkdir()
    readonly_cwd.chmod(0o555)
    captured_trace_dirs: list[Path] = []

    async def fake_run_page_scan(**kwargs: Any) -> list[Any]:
        config = kwargs["config"]
        trace_dir = Path(str(config.trace_dir))
        trace_dir.mkdir(parents=True, exist_ok=True)
        (trace_dir / "probe.txt").write_text("ok", encoding="utf-8")
        captured_trace_dirs.append(trace_dir)
        return []

    monkeypatch.setattr(desktop_ui_service, "get_traces_dir", lambda: runtime_traces)
    monkeypatch.setattr(desktop_ui_service, "run_page_scan", fake_run_page_scan)
    try:
        monkeypatch.chdir(readonly_cwd)
        _run_point_worker(service)
    finally:
        readonly_cwd.chmod(0o755)

    assert captured_trace_dirs == [runtime_traces]
    assert (runtime_traces / "probe.txt").read_text(encoding="utf-8") == "ok"
    assert not (readonly_cwd / "traces").exists()


def test_point_scan_worker_prefers_cancel_when_cancelled(monkeypatch: pytest.MonkeyPatch) -> None:
    service = DesktopUIService()
    cancelled: list[bool] = []
    errors: list[str] = []

    def fail_before_scan(*_: Any, **__: Any) -> list[tuple[str, str | None]]:
        raise RuntimeError("scan setup broke")

    service._cancel_event.set()
    monkeypatch.setattr(desktop_ui_service, "build_ordered_trip_dates", fail_before_scan)
    monkeypatch.setattr(service, "_handle_cancelled", lambda: cancelled.append(True))
    monkeypatch.setattr(service, "_handle_scan_error", errors.append)

    _run_point_worker(service)

    assert cancelled == [True]
    assert errors == []


def test_expanded_scan_worker_reports_boundary_error(monkeypatch: pytest.MonkeyPatch) -> None:
    service = DesktopUIService()
    errors: list[str] = []

    def fail_before_scan(*_: Any, **__: Any) -> list[tuple[str, str | None]]:
        raise RuntimeError("expanded setup broke")

    monkeypatch.setattr(desktop_ui_service, "build_ordered_trip_dates", fail_before_scan)
    monkeypatch.setattr(service, "_handle_scan_error", errors.append)

    _run_expanded_worker(service)

    assert errors == ["expanded setup broke"]


def test_expanded_scan_worker_prefers_cancel_when_cancelled(monkeypatch: pytest.MonkeyPatch) -> None:
    service = DesktopUIService()
    cancelled: list[bool] = []
    errors: list[str] = []

    def fail_before_scan(*_: Any, **__: Any) -> list[tuple[str, str | None]]:
        raise RuntimeError("expanded setup broke")

    service._cancel_event.set()
    monkeypatch.setattr(desktop_ui_service, "build_ordered_trip_dates", fail_before_scan)
    monkeypatch.setattr(service, "_handle_cancelled", lambda: cancelled.append(True))
    monkeypatch.setattr(service, "_handle_scan_error", errors.append)

    _run_expanded_worker(service)

    assert cancelled == [True]
    assert errors == []
