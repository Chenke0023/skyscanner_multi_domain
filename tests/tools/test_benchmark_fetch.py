from __future__ import annotations

import argparse
import asyncio

import pytest

from skyscanner_multi_domain.geo.location_resolver import LocationRecord
from skyscanner_multi_domain.models import RegionConfig
from tools import benchmark_fetch


def _args() -> argparse.Namespace:
    return argparse.Namespace(
        date="2026-05-20",
        return_date=None,
        wait=1,
        timeout=1,
        transport="opencli",
        fetch_pipeline="balanced",
        max_run_seconds=1,
    )


def _location(code: str) -> LocationRecord:
    return LocationRecord(name=code, code=code, kind="airport", municipality=code, country=code)


async def _run() -> dict[str, object]:
    return await benchmark_fetch._single_run(
        _args(),
        _location("BJSA"),
        _location("ALA"),
        ["CN"],
        [RegionConfig("CN", "China", "cn.example", "zh-CN", "CNY")],
        0,
    )


def test_single_run_returns_error_row_for_scan_exception(monkeypatch: pytest.MonkeyPatch) -> None:
    async def broken_scan(**_: object) -> list[object]:
        raise RuntimeError("scan broke")

    monkeypatch.setattr(benchmark_fetch, "run_page_scan", broken_scan)

    run = asyncio.run(_run())

    assert run["error"] == "scan broke"
    assert run["fetch_price_found_count"] == 0
    assert run["fetch_total_regions"] == 1


def test_single_run_returns_timeout_row(monkeypatch: pytest.MonkeyPatch) -> None:
    async def slow_scan(**_: object) -> list[object]:
        await asyncio.sleep(2)
        return []

    monkeypatch.setattr(benchmark_fetch, "run_page_scan", slow_scan)

    run = asyncio.run(_run())

    assert run["error"] == "run_timeout_after_1s"
    assert run["fetch_price_found_rate"] == 0.0
