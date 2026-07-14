from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

from skyscanner_multi_domain.models import FlightQuote
from skyscanner_multi_domain.scan.config import ScanConfig, TransportMode
from skyscanner_multi_domain.scan.orchestrator import run_page_scan


def quote(region: str = "CN", *, price: float | None = 1234.0) -> FlightQuote:
    return FlightQuote(
        region=region,
        domain="https://example.test",
        price=price,
        currency="CNY",
        source_url=f"https://example.test/{region.lower()}",
        status="ok" if price is not None else "page_parse_failed",
    )


def test_page_is_default_and_uses_direct_cdp() -> None:
    page = AsyncMock(return_value=[quote()])
    structured = AsyncMock()
    on_challenge = MagicMock()
    with (
        patch("skyscanner_multi_domain.transports.cdp.ensure_cdp_ready"),
        patch("skyscanner_multi_domain.transports.cdp.compare_via_pages", page),
        patch("skyscanner_multi_domain.transports.cdp_structured.compare_via_cdp_structured", structured),
    ):
        rows = asyncio.run(
            run_page_scan(
                "BJS",
                "ALA",
                "2026-06-10",
                ["CN"],
                config=ScanConfig(no_trace=True),
                on_challenge=on_challenge,
            )
        )

    assert rows[0].price == 1234.0
    assert rows[0].source_kind == "page"
    page.assert_awaited_once()
    assert page.await_args.kwargs["on_challenge"] is on_challenge
    assert page.await_args.kwargs["keep_challenge_tabs"] is True
    structured.assert_not_awaited()
    assert len(rows[0].attempt_history) == 1


def test_structured_mode_is_explicit() -> None:
    structured = AsyncMock(return_value=[quote()])
    with (
        patch("skyscanner_multi_domain.transports.cdp.ensure_cdp_ready"),
        patch("skyscanner_multi_domain.transports.cdp.compare_via_pages", AsyncMock()) as page,
        patch("skyscanner_multi_domain.transports.cdp_structured.compare_via_cdp_structured", structured),
    ):
        rows = asyncio.run(run_page_scan(
            "BJS", "ALA", "2026-06-10", ["CN"],
            config=ScanConfig(transport=TransportMode.CDP_STRUCTURED, no_trace=True),
        ))

    assert rows[0].source_kind == "cdp_structured"
    structured.assert_awaited_once()
    page.assert_not_awaited()


def test_browser_unavailable_fails_without_transport_retry(tmp_path) -> None:
    page = AsyncMock()
    structured = AsyncMock()
    config = ScanConfig(no_trace=True, failure_log_dir=str(tmp_path))
    with (
        patch("skyscanner_multi_domain.transports.cdp.ensure_cdp_ready", side_effect=RuntimeError("no launchable browser")),
        patch("skyscanner_multi_domain.transports.cdp.compare_via_pages", page),
        patch("skyscanner_multi_domain.transports.cdp_structured.compare_via_cdp_structured", structured),
        patch("skyscanner_multi_domain.scan.orchestrator._persist_failure_log", side_effect=lambda row, **_: row),
    ):
        rows = asyncio.run(run_page_scan("BJS", "ALA", "2026-06-10", ["CN"], config=config))

    assert rows[0].status == "browser_unavailable"
    assert "browser-unavailable" in (rows[0].error or "")
    page.assert_not_awaited()
    structured.assert_not_awaited()
    assert len(rows[0].attempt_history) == 1


def test_retired_transport_is_rejected_without_browser_call() -> None:
    with (
        patch("skyscanner_multi_domain.transports.cdp.ensure_cdp_ready") as ready,
        patch("skyscanner_multi_domain.scan.orchestrator._persist_failure_log", side_effect=lambda row, **_: row),
    ):
        rows = asyncio.run(run_page_scan("BJS", "ALA", "2026-06-10", ["CN"], transport="opencli"))

    assert rows[0].status == "invalid_transport"
    ready.assert_not_called()
