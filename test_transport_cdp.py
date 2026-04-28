from __future__ import annotations

import argparse
import asyncio
import json
from unittest.mock import MagicMock, patch

from skyscanner_models import RegionConfig
from transport_cdp import (
    _get_matching_cdp_tabs,
    _verify_browser_session_persistence_async,
    _quote_from_cdp_payload,
    compare_via_pages,
    detect_cdp_version,
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

    with patch("transport_cdp.http.client.HTTPConnection", return_value=localhost):
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
        "transport_cdp.http.client.HTTPConnection",
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


def test_compare_via_pages_reuses_existing_matching_tabs_without_opening_new_ones() -> None:
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
    existing_tabs = [
        {
            "type": "page",
            "url": target_url,
            "webSocketDebuggerUrl": "ws://existing-hk",
        }
    ]
    opened_urls: list[str] = []

    class FakeSession:
        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return None

    async def run_case() -> None:
        with (
            patch("transport_cdp.aiohttp.ClientSession", return_value=FakeSession()),
            patch(
                "transport_cdp.cdp_list_tabs",
                side_effect=[existing_tabs, existing_tabs],
            ),
            patch(
                "transport_cdp.cdp_open_tab",
                side_effect=lambda _session, url: opened_urls.append(url),
            ),
            patch(
                "transport_cdp.cdp_eval",
                return_value={
                    "url": target_url,
                    "text": "最優\nHK$3,305\n最便宜\nHK$3,072",
                },
            ),
        ):
            quotes = await compare_via_pages(
                args,
                [region],
                persist_failures=False,
                build_search_url=lambda *_args: target_url,
            )

        assert opened_urls == []
        assert len(quotes) == 1
        assert quotes[0].status == "page_text"
        assert quotes[0].cheapest_price == 3072.0

    asyncio.run(run_case())


def test_launch_browser_with_cdp_restarts_running_comet() -> None:
    fake_process = MagicMock()
    fake_process.poll.return_value = None

    with (
        patch(
            "transport_cdp._select_browser_launch_target",
            return_value=("comet", MagicMock(), MagicMock()),
        ),
        patch("transport_cdp._comet_is_running", return_value=True),
        patch("transport_cdp._kill_comet") as kill_comet,
        patch("transport_cdp.subprocess.Popen", return_value=fake_process),
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
                "transport_cdp._launch_browser_process",
                side_effect=[first_process, second_process],
            ),
            patch(
                "transport_cdp.wait_for_cdp",
                return_value={"Browser": "Edg/146.0"},
            ),
            patch("transport_cdp.wait_for_cdp_shutdown", return_value=True),
            patch("transport_cdp._terminate_browser_process") as terminate_process,
            patch("transport_cdp.aiohttp.ClientSession", return_value=FakeSession()),
            patch("transport_cdp.cdp_navigate_tab"),
            patch(
                "transport_cdp._wait_for_page_tab",
                side_effect=[tab, tab, tab, tab],
            ),
            patch(
                "transport_cdp.cdp_eval",
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
