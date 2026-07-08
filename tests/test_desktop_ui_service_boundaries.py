from __future__ import annotations

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
        False,
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
        False,
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
