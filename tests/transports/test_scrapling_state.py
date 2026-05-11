"""Cookie / profile state resolution tests for Scrapling transport."""

from __future__ import annotations

import asyncio
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from transport_scrapling import (
    _build_cookie_scope_urls,
    _get_persistent_probe_candidates,
    _get_matching_cdp_page_ws_urls,
    _resolve_scrapling_state_overrides,
    _state_usage,
)
from skyscanner_models import RegionConfig


class PersistentProbeCandidatesTests(unittest.TestCase):
    def test_get_persistent_probe_candidates_prefers_non_empty_profiles(self) -> None:
        with TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            edge_binary = temp_path / "Microsoft Edge"
            chrome_binary = temp_path / "Google Chrome"
            edge_binary.write_text("", encoding="utf-8")
            chrome_binary.write_text("", encoding="utf-8")

            edge_profile = temp_path / "runtime" / "edge-cdp-profile"
            chrome_profile = temp_path / "runtime" / "chrome-cdp-profile"
            (edge_profile / "Default").mkdir(parents=True)
            chrome_profile.mkdir(parents=True)

            def fake_get_browser_profile_dir(browser_name: str) -> Path:
                return {
                    "edge": edge_profile,
                    "chrome": chrome_profile,
                }[browser_name]

            with (
                patch(
                    "transport_scrapling._detect_local_browsers",
                    return_value={
                        "edge": edge_binary,
                        "chrome": chrome_binary,
                    },
                ),
                patch(
                    "transport_scrapling.get_browser_profile_dir",
                    side_effect=fake_get_browser_profile_dir,
                ),
            ):
                candidates = _get_persistent_probe_candidates()

        self.assertEqual(candidates, (("edge", edge_binary, edge_profile),))


class CookieScopeTests(unittest.TestCase):
    def test_build_cookie_scope_urls_includes_host_aliases(self) -> None:
        region = RegionConfig(
            code="CN", name="中国",
            domain="https://www.skyscanner.cn",
            currency="CNY", locale="zh-CN",
        )

        urls = _build_cookie_scope_urls(
            region,
            "https://www.skyscanner.cn/transport/flights/bjsa/ala/260429/?adultsv2=1",
        )

        self.assertIn("https://www.skyscanner.cn", urls)
        self.assertIn(
            "https://www.tianxun.com/transport/flights/bjsa/ala/260429/?adultsv2=1",
            urls,
        )


class StateOverridesTests(unittest.TestCase):
    def test_resolve_scrapling_state_overrides_prefers_live_cookies(self) -> None:
        region = RegionConfig(
            code="HK", name="香港",
            domain="https://www.skyscanner.com.hk",
            currency="HKD", locale="zh-HK",
        )

        async def run_case() -> None:
            with (
                patch(
                    "transport_scrapling._cdp_get_cookie_jar",
                    return_value=[
                        {"name": "_px3", "value": "token", "domain": "www.skyscanner.com.hk"},
                        {"name": "scanner", "value": "abc", "domain": "www.skyscanner.com.hk"},
                    ],
                ),
                patch(
                    "transport_scrapling._get_persistent_profile_dirs",
                    return_value=(Path("/tmp/edge-cdp-profile"),),
                ),
            ):
                overrides = await _resolve_scrapling_state_overrides(
                    region,
                    "https://www.skyscanner.com.hk/transport/flights/bjsa/ala/260429/",
                    for_stealth=True,
                )

            self.assertEqual(
                overrides,
                {
                    "cookies": [
                        {"name": "_px3", "value": "token", "domain": "www.skyscanner.com.hk"},
                        {"name": "scanner", "value": "abc", "domain": "www.skyscanner.com.hk"},
                    ]
                },
            )

        asyncio.run(run_case())

    def test_resolve_scrapling_state_overrides_converts_live_cookies_for_http(self) -> None:
        region = RegionConfig(
            code="HK", name="香港",
            domain="https://www.skyscanner.com.hk",
            currency="HKD", locale="zh-HK",
        )

        async def run_case() -> None:
            with (
                patch(
                    "transport_scrapling._cdp_get_cookie_jar",
                    return_value=[
                        {"name": "_px3", "value": "token"},
                        {"name": "scanner", "value": "abc"},
                    ],
                ),
                patch(
                    "transport_scrapling._get_persistent_profile_dirs",
                    return_value=(Path("/tmp/edge-cdp-profile"),),
                ),
            ):
                overrides = await _resolve_scrapling_state_overrides(
                    region,
                    "https://www.skyscanner.com.hk/transport/flights/bjsa/ala/260429/",
                    for_stealth=False,
                )

            self.assertEqual(
                overrides,
                {"cookies": {"_px3": "token", "scanner": "abc"}},
            )

        asyncio.run(run_case())

    def test_resolve_scrapling_state_overrides_falls_back_to_profile_dir(self) -> None:
        region = RegionConfig(
            code="SG", name="Singapore",
            domain="https://www.skyscanner.sg",
            currency="SGD", locale="en-SG",
        )

        async def run_case() -> None:
            with (
                patch(
                    "transport_scrapling._cdp_get_cookie_jar",
                    return_value=[],
                ),
                patch(
                    "transport_scrapling._get_persistent_profile_dirs",
                    return_value=(Path("/tmp/edge-cdp-profile"),),
                ),
            ):
                overrides = await _resolve_scrapling_state_overrides(
                    region,
                    "https://www.skyscanner.sg/transport/flights/bjsa/ala/260429/",
                    for_stealth=True,
                )

            self.assertEqual(
                overrides,
                {"user_data_dir": "/tmp/edge-cdp-profile"},
            )

        asyncio.run(run_case())


class CDPProbeTests(unittest.TestCase):
    def test_get_matching_cdp_page_ws_urls_filters_by_path_and_region_aliases(self) -> None:
        region = RegionConfig(
            code="CN", name="中国",
            domain="https://www.skyscanner.cn",
            currency="CNY", locale="zh-CN",
        )

        tabs = [
            {
                "type": "page",
                "url": "https://www.tianxun.com/transport/flights/bjsa/ala/260429/",
                "webSocketDebuggerUrl": "ws://match-cn",
            },
            {
                "type": "page",
                "url": "https://www.tianxun.com/transport/flights/bjsa/tbs/260429/",
                "webSocketDebuggerUrl": "ws://wrong-path",
            },
            {
                "type": "page",
                "url": "https://www.skyscanner.net/transport/flights/bjsa/ala/260429/",
                "webSocketDebuggerUrl": "ws://wrong-region",
            },
        ]

        with patch("transport_scrapling._cdp_get_json", return_value=tabs):
            ws_urls = _get_matching_cdp_page_ws_urls(
                region,
                "https://www.skyscanner.cn/transport/flights/bjsa/ala/260429/?adultsv2=1",
            )

        self.assertEqual(ws_urls, ("ws://match-cn",))


class StateUsageTests(unittest.TestCase):
    def test_state_usage_distinguishes_cookies_from_profile(self) -> None:
        self.assertEqual(_state_usage(None), (False, False))
        self.assertEqual(_state_usage({}), (False, False))
        self.assertEqual(
            _state_usage({"cookies": {"_px3": "token"}}),
            (True, False),
        )
        self.assertEqual(
            _state_usage({"user_data_dir": "/tmp/profile"}),
            (False, True),
        )
        self.assertEqual(
            _state_usage({"cookies": {"a": "1"}, "user_data_dir": "/p"}),
            (True, True),
        )


if __name__ == "__main__":
    unittest.main()