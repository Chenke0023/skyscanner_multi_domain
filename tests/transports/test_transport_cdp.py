from __future__ import annotations

import argparse
import asyncio
import json
from unittest.mock import AsyncMock, MagicMock, patch

from skyscanner_multi_domain.models import RegionConfig
from skyscanner_multi_domain.transports.cdp import (
    _get_matching_cdp_tabs,
    _verify_browser_session_persistence_async,
    _quote_from_cdp_payload,
    compare_via_pages,
    detect_cdp_version,
    ensure_cdp_ready,
    launch_browser_with_cdp,
)


def _build_connection(response_bodies: list[tuple[int, str]]) -> MagicMock:
    connection = MagicMock()
    responses = []
    for status, body in response_bodies:
        response = MagicMock()
        response.status = status
        response.read.return_value = body.encode("utf-8")
        responses.append(response)
    connection.getresponse.side_effect = responses
    return connection


def test_detect_cdp_version_accepts_first_valid_browser_payload() -> None:
    localhost = _build_connection(
        [(200, json.dumps({"Browser": "Edg/146.0.3856.84", "Protocol-Version": "1.3"}))]
    )

    with patch("skyscanner_multi_domain.transports.cdp.http.client.HTTPConnection", return_value=localhost):
        info = detect_cdp_version()

    assert info is not None
    assert info["Browser"] == "Edg/146.0.3856.84"


def test_detect_cdp_version_skips_404_and_tries_next_host() -> None:
    localhost = _build_connection([(404, "")])
    loopback_v6 = _build_connection(
        [(200, json.dumps({"Browser": "Edg/146.0.3856.84", "Protocol-Version": "1.3"}))]
    )
    loopback_v4 = _build_connection([(404, "")])

    with patch(
        "skyscanner_multi_domain.transports.cdp.http.client.HTTPConnection",
        side_effect=[localhost, loopback_v6, loopback_v4],
    ):
        info = detect_cdp_version()

    assert info is not None
    assert info["Browser"] == "Edg/146.0.3856.84"


def test_quote_from_cdp_payload_marks_px_challenge_from_url() -> None:
    region = RegionConfig(
        code="SG",
        name="Singapore",
        domain="https://www.skyscanner.sg",
        currency="SGD",
        locale="en-SG",
    )

    quote = _quote_from_cdp_payload(
        region,
        {
            "url": "https://www.skyscanner.com.sg/sttc/px/captcha-v2/index.html",
            "text": "",
        },
        "https://www.skyscanner.sg/transport/flights/bjsa/dps/260502/",
    )

    assert quote.status == "px_challenge"
    assert quote.price is None
    assert "PX" in (quote.error or "")



def test_quote_from_cdp_payload_attaches_itinerary_from_cheapest_card() -> None:
    region = RegionConfig(
        code="HK",
        name="香港",
        domain="https://www.skyscanner.com.hk",
        currency="HKD",
        locale="zh-HK",
    )
    quote = _quote_from_cdp_payload(
        region,
        {
            "url": "https://www.skyscanner.com.hk/transport/flights/pek/ala/260520/",
            "text": "Best\nHK$3,305\nCheapest\nHK$3,072",
            "cards": [
                {"priceText": "HK$3,305", "cardText": "09:00 15:00 1 stop 6h HK$3,305"},
                {"priceText": "HK$3,072", "cardText": "08:10 11:25 Non-stop 3h 15m HK$3,072"},
            ],
        },
        "https://www.skyscanner.com.hk/transport/flights/pek/ala/260520/",
    )

    assert quote.cheapest_price == 3072
    assert quote.itinerary_legs[0]["departure_time"] == "08:10"
    assert quote.itinerary_legs[0]["stop_count"] == 0

def test_quote_from_cdp_payload_detects_bot_check_copy() -> None:
    region = RegionConfig(
        code="SG",
        name="新加坡",
        domain="https://www.skyscanner.com.sg",
        currency="SGD",
        locale="en-SG",
    )
    quote = _quote_from_cdp_payload(
        region,
        {
            "url": "https://www.skyscanner.com.sg/transport/flights/bjs/ala/260429/",
            "text": "Let's confirm you are not a bot before continuing.",
        },
        region.domain,
    )

    assert quote.status == "page_challenge"
    assert quote.price is None
    assert "通用验证码" in (quote.error or "")


def test_get_matching_cdp_tabs_filters_by_path_and_region_aliases() -> None:
    region = RegionConfig(
        code="CN",
        name="中国",
        domain="https://www.skyscanner.cn",
        currency="CNY",
        locale="zh-CN",
    )
    tabs = [
        {
            "type": "page",
            "url": "https://www.tianxun.com/transport/flights/bjsa/ala/260429/",
            "webSocketDebuggerUrl": "ws://match",
        },
        {
            "type": "page",
            "url": "https://www.tianxun.com/transport/flights/bjsa/tbs/260429/",
            "webSocketDebuggerUrl": "ws://wrong-path",
        },
        {
            "type": "page",
            "url": "https://www.skyscanner.net/transport/flights/bjsa/ala/260429/",
            "webSocketDebuggerUrl": "ws://wrong-market",
        },
    ]

    matches = _get_matching_cdp_tabs(
        tabs,
        region,
        "https://www.skyscanner.cn/transport/flights/bjsa/ala/260429/?adultsv2=1",
    )

    assert matches == [tabs[0]]


def test_compare_via_pages_recovers_from_stale_tab_id() -> None:
    """If a domain_tabs entry references a tab that no longer exists, the
    scan should drop the stale id and create a fresh tab instead of dying.

    Regression test for "Tab xxxxxxxx not found" surfacing in the UI."""
    args = argparse.Namespace(
        origin="BJSA",
        destination="ALA",
        date="2026-04-29",
        return_date=None,
        page_wait=0,
        timeout=5,
    )
    region = RegionConfig(
        code="HK",
        name="香港",
        domain="https://www.skyscanner.com.hk",
        currency="HKD",
        locale="zh-HK",
    )
    target_url = "https://www.skyscanner.com.hk/transport/flights/bjsa/ala/260429/?adultsv2=1"
    fresh_tab = {
        "type": "page",
        "url": target_url,
        "webSocketDebuggerUrl": "ws://fresh-hk",
        "id": "fresh-tab-id",
    }

    class FakeSession:
        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return None

    from skyscanner_multi_domain.transports.cdp import TabNotFoundError

    async def run_case() -> None:
        with (
            patch("skyscanner_multi_domain.transports.cdp.aiohttp.ClientSession", return_value=FakeSession()),
            patch("skyscanner_multi_domain.transports.cdp.cdp_list_tabs", return_value=[fresh_tab]),
            patch(
                "skyscanner_multi_domain.transports.cdp.cdp_navigate_tab",
                side_effect=TabNotFoundError("Tab STALE not found"),
            ) as mock_navigate,
            patch("skyscanner_multi_domain.transports.cdp.cdp_open_tab", return_value=fresh_tab) as mock_open,
            patch("skyscanner_multi_domain.transports.cdp.cdp_close_tab"),
            patch(
                "skyscanner_multi_domain.transports.cdp.cdp_eval",
                return_value={
                    "url": target_url,
                    "text": "最優\nHK$3,305\n最便宜\nHK$3,072",
                },
            ),
            patch("skyscanner_multi_domain.transports.cdp.emit_trace", lambda **k: None),
        ):
            quotes = await compare_via_pages(
                args,
                [region],
                persist_failures=False,
                build_search_url=lambda *_args: target_url,
                manual_tabs={"www.skyscanner.com.hk": "STALE"},
            )

        mock_navigate.assert_called_once()
        mock_open.assert_called_once()
        assert len(quotes) == 1
        assert quotes[0].status == "page_text"
        assert quotes[0].cheapest_price == 3072.0

    asyncio.run(run_case())


def test_compare_via_pages_creates_owned_tabs_and_closes_them() -> None:
    args = argparse.Namespace(
        origin="BJSA",
        destination="ALA",
        date="2026-04-29",
        return_date=None,
        page_wait=0,
        timeout=5,
    )
    region = RegionConfig(
        code="HK",
        name="香港",
        domain="https://www.skyscanner.com.hk",
        currency="HKD",
        locale="zh-HK",
    )
    target_url = "https://www.skyscanner.com.hk/transport/flights/bjsa/ala/260429/?adultsv2=1"
    new_tab = {
        "type": "page",
        "url": target_url,
        "webSocketDebuggerUrl": "ws://new-hk",
        "id": "new-tab-1",
    }

    class FakeSession:
        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return None

    async def run_case() -> None:
        with (
            patch("skyscanner_multi_domain.transports.cdp.aiohttp.ClientSession", return_value=FakeSession()),
            patch("skyscanner_multi_domain.transports.cdp.cdp_list_tabs", return_value=[new_tab]),
            patch("skyscanner_multi_domain.transports.cdp.cdp_open_tab", return_value=new_tab) as mock_open,
            patch("skyscanner_multi_domain.transports.cdp.cdp_close_tab") as mock_close,
            patch(
                "skyscanner_multi_domain.transports.cdp.cdp_eval",
                return_value={
                    "url": target_url,
                    "text": "最優\nHK$3,305\n最便宜\nHK$3,072",
                },
            ),
            patch("skyscanner_multi_domain.transports.cdp.emit_trace", lambda **k: None),
        ):
            quotes = await compare_via_pages(
                args,
                [region],
                persist_failures=False,
                build_search_url=lambda *_args: target_url,
            )

        mock_open.assert_called_once()
        mock_close.assert_called_once()
        assert len(quotes) == 1
        assert quotes[0].status == "page_text"
        assert quotes[0].cheapest_price == 3072.0

    asyncio.run(run_case())


def test_launch_browser_with_cdp_restarts_running_comet() -> None:
    fake_process = MagicMock()
    fake_process.poll.return_value = None

    with (
        patch(
            "skyscanner_multi_domain.transports.cdp._select_browser_launch_target",
            return_value=("comet", MagicMock(), MagicMock()),
        ),
        patch("skyscanner_multi_domain.transports.cdp._comet_is_running", return_value=True),
        patch("skyscanner_multi_domain.transports.cdp._kill_comet") as kill_comet,
        patch("skyscanner_multi_domain.transports.cdp.subprocess.Popen", return_value=fake_process),
    ):
        message = launch_browser_with_cdp(preferred_browser="comet")

    kill_comet.assert_called_once()
    assert "已自动启动 Comet" in message


def test_verify_browser_session_persistence_async_restarts_browser_and_confirms_cookie() -> None:
    probe = {
        "cookie_name": "skyscanner_probe_session",
        "cookie_value": "token-123",
        "set_url": "http://127.0.0.1:43111/set",
        "echo_url": "http://127.0.0.1:43111/echo",
        "host": "127.0.0.1:43111",
    }
    tab = {
        "type": "page",
        "id": "probe-tab",
        "url": probe["echo_url"],
        "webSocketDebuggerUrl": "ws://probe",
    }

    class FakeSession:
        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return None

    async def run_case() -> None:
        first_process = MagicMock()
        first_process.poll.return_value = None
        second_process = MagicMock()
        second_process.poll.return_value = None

        with (
            patch(
                "skyscanner_multi_domain.transports.cdp._launch_browser_process",
                side_effect=[first_process, second_process],
            ),
            patch(
                "skyscanner_multi_domain.transports.cdp.wait_for_cdp",
                return_value={"Browser": "Edg/146.0"},
            ),
            patch("skyscanner_multi_domain.transports.cdp.wait_for_cdp_shutdown", return_value=True),
            patch("skyscanner_multi_domain.transports.cdp._terminate_browser_process") as terminate_process,
            patch("skyscanner_multi_domain.transports.cdp.aiohttp.ClientSession", return_value=FakeSession()),
            patch("skyscanner_multi_domain.transports.cdp.cdp_navigate_tab"),
            patch(
                "skyscanner_multi_domain.transports.cdp._wait_for_page_tab",
                side_effect=[tab, tab, tab, tab],
            ),
            patch(
                "skyscanner_multi_domain.transports.cdp.cdp_eval",
                side_effect=[
                    "skyscanner_probe_session=token-123",
                    "skyscanner_probe_session=token-123",
                ],
            ),
        ):
            ok, message = await _verify_browser_session_persistence_async(
                "edge",
                MagicMock(),
                MagicMock(),
                probe,
            )

        assert ok is True
        assert "保留了 probe cookie" in message
        assert terminate_process.call_count == 2

    asyncio.run(run_case())


def test_ensure_cdp_ready_uses_existing_endpoint_without_launch() -> None:
    endpoint = {"Browser": "Chrome/1"}
    with (
        patch("skyscanner_multi_domain.transports.cdp.detect_cdp_version", return_value=endpoint),
        patch("skyscanner_multi_domain.transports.cdp.launch_browser_with_cdp") as launch,
    ):
        assert ensure_cdp_ready() == endpoint

    launch.assert_not_called()


def test_ensure_cdp_ready_reports_browser_unavailable() -> None:
    with (
        patch("skyscanner_multi_domain.transports.cdp.detect_cdp_version", return_value=None),
        patch("skyscanner_multi_domain.transports.cdp.launch_browser_with_cdp", return_value="没有找到可自动启动的浏览器"),
        patch("skyscanner_multi_domain.transports.cdp.wait_for_cdp", return_value=None),
    ):
        try:
            ensure_cdp_ready()
        except RuntimeError as exc:
            message = str(exc)
        else:
            raise AssertionError("expected browser-unavailable error")

    assert message.startswith("browser-unavailable:")
    assert "Chrome、Edge 或 Comet" in message


def test_compare_via_pages_waits_for_manual_challenge_and_recovers_result() -> None:
    args = argparse.Namespace(
        origin="BJSA",
        destination="ALA",
        date="2026-04-29",
        return_date=None,
        page_wait=0,
        timeout=0,
    )
    region = RegionConfig(
        code="HK",
        name="香港",
        domain="https://www.skyscanner.com.hk",
        currency="HKD",
        locale="zh-HK",
    )
    target_url = "https://www.skyscanner.com.hk/transport/flights/bjsa/ala/260429/?adultsv2=1"
    tab = {
        "type": "page",
        "url": target_url,
        "webSocketDebuggerUrl": "ws://challenge-hk",
        "id": "challenge-tab-1",
    }
    challenge = {
        "url": "https://www.skyscanner.com.hk/sttc/px/captcha-v2/index.html",
        "text": "Press and hold to verify you are human",
    }
    result = {
        "url": target_url,
        "text": "最優\nHK$3,305\n最便宜\nHK$3,072",
    }

    class FakeSession:
        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return None

    async def run_case() -> None:
        on_challenge = MagicMock()
        on_waiting = MagicMock()
        on_resolved = MagicMock()
        with (
            patch("skyscanner_multi_domain.transports.cdp.aiohttp.ClientSession", return_value=FakeSession()),
            patch("skyscanner_multi_domain.transports.cdp.asyncio.sleep", new=AsyncMock()),
            patch("skyscanner_multi_domain.transports.cdp.time.monotonic", side_effect=[0, 61, 62]),
            patch("skyscanner_multi_domain.transports.cdp.cdp_list_tabs", return_value=[tab]),
            patch("skyscanner_multi_domain.transports.cdp.cdp_open_tab", return_value=tab),
            patch("skyscanner_multi_domain.transports.cdp.cdp_close_tab") as close_tab,
            patch("skyscanner_multi_domain.transports.cdp.cdp_eval", side_effect=[challenge, result]),
            patch("skyscanner_multi_domain.transports.cdp.emit_trace", lambda **k: None),
        ):
            quotes = await compare_via_pages(
                args,
                [region],
                persist_failures=False,
                build_search_url=lambda *_args: target_url,
                on_challenge=on_challenge,
                on_challenge_waiting=on_waiting,
                on_challenge_resolved=on_resolved,
            )

        assert len(quotes) == 1
        assert quotes[0].status == "page_text"
        assert quotes[0].cheapest_price == 3072.0
        on_challenge.assert_called_once()
        on_waiting.assert_called_once()
        on_resolved.assert_called_once()
        close_tab.assert_awaited_once()
        assert close_tab.await_args.args[1] == "challenge-tab-1"

    asyncio.run(run_case())


def test_compare_via_pages_retains_challenge_tab_when_manual_wait_is_cancelled() -> None:
    args = argparse.Namespace(
        origin="BJSA", destination="ALA", date="2026-04-29", return_date=None, page_wait=0, timeout=0
    )
    region = RegionConfig(
        code="HK", name="香港", domain="https://www.skyscanner.com.hk", currency="HKD", locale="zh-HK"
    )
    target_url = "https://www.skyscanner.com.hk/transport/flights/bjsa/ala/260429/?adultsv2=1"
    tab = {"type": "page", "url": target_url, "webSocketDebuggerUrl": "ws://challenge", "id": "challenge-tab"}

    class FakeSession:
        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return None

    async def cancel_wait(*_args) -> None:
        raise asyncio.CancelledError

    async def run_case() -> None:
        with (
            patch("skyscanner_multi_domain.transports.cdp.aiohttp.ClientSession", return_value=FakeSession()),
            patch("skyscanner_multi_domain.transports.cdp.asyncio.sleep", new=AsyncMock()),
            patch("skyscanner_multi_domain.transports.cdp.time.monotonic", side_effect=[0, 100]),
            patch("skyscanner_multi_domain.transports.cdp.cdp_list_tabs", return_value=[tab]),
            patch("skyscanner_multi_domain.transports.cdp.cdp_open_tab", return_value=tab),
            patch("skyscanner_multi_domain.transports.cdp.cdp_close_tab") as close_tab,
            patch(
                "skyscanner_multi_domain.transports.cdp.cdp_eval",
                return_value={"url": f"{region.domain}/captcha", "text": "Press and hold to verify you are human"},
            ),
            patch("skyscanner_multi_domain.transports.cdp.emit_trace", lambda **k: None),
        ):
            try:
                await compare_via_pages(
                    args,
                    [region],
                    persist_failures=False,
                    build_search_url=lambda *_args: target_url,
                    on_challenge_waiting=cancel_wait,
                )
            except asyncio.CancelledError:
                pass
            else:
                raise AssertionError("manual wait should have been cancelled")

        close_tab.assert_not_awaited()

    asyncio.run(run_case())
