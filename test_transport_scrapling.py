import argparse
import asyncio
from pathlib import Path
import sys
from tempfile import TemporaryDirectory
import types
import unittest
from unittest.mock import patch

from transport_scrapling import (
    _build_cookie_scope_urls,
    _check_captcha_in_page,
    _get_persistent_probe_candidates,
    _get_matching_cdp_page_ws_urls,
    _resolve_scrapling_state_overrides,
    _state_usage,
    compare_via_scrapling,
)
from skyscanner_models import RegionConfig


class CaptchaDetectionTests(unittest.TestCase):
    def test_check_captcha_in_page_detects_px_challenge(self) -> None:
        class FakePage:
            url = "https://www.skyscanner.com.sg/sttc/px/captcha-v2/index.html"

        has_captcha, captcha_type = _check_captcha_in_page(
            "Verify you are human\ncaptcha-v2\nPress and hold", FakePage()
        )

        self.assertTrue(has_captcha)
        self.assertEqual(captcha_type, "px")

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

    def test_build_cookie_scope_urls_includes_host_aliases(self) -> None:
        region = RegionConfig(
            code="CN",
            name="中国",
            domain="https://www.skyscanner.cn",
            currency="CNY",
            locale="zh-CN",
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

    def test_get_matching_cdp_page_ws_urls_filters_by_path_and_region_aliases(self) -> None:
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

    def test_resolve_scrapling_state_overrides_prefers_live_cookies(self) -> None:
        region = RegionConfig(
            code="HK",
            name="香港",
            domain="https://www.skyscanner.com.hk",
            currency="HKD",
            locale="zh-HK",
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
            code="HK",
            name="香港",
            domain="https://www.skyscanner.com.hk",
            currency="HKD",
            locale="zh-HK",
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
            code="SG",
            name="新加坡",
            domain="https://www.skyscanner.sg",
            currency="SGD",
            locale="en-SG",
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


class ScraplingRetryTests(unittest.TestCase):
    def test_compare_via_scrapling_serializes_shared_profile_dir_usage(self) -> None:
        active_calls = 0
        max_active_calls = 0

        class FullPage:
            url = "https://www.skyscanner.com.hk/transport/flights/bjsa/ala/260429/"
            html = """
            <html>
              <body>
                <div>搜尋結果顯示方式</div>
                <div>最佳</div>
                <div>HK$3,305</div>
                <div>最便宜</div>
                <div>HK$3,072</div>
              </body>
            </html>
            """

        def fake_fetch(url: str, **kwargs):
            nonlocal active_calls, max_active_calls
            self.assertEqual(kwargs.get("user_data_dir"), "/tmp/shared-profile")
            active_calls += 1
            max_active_calls = max(max_active_calls, active_calls)
            try:
                import time

                time.sleep(0.05)
                return FullPage()
            finally:
                active_calls -= 1

        fake_scrapling = types.SimpleNamespace(
            Fetcher=types.SimpleNamespace(get=lambda *a, **k: FullPage()),
            StealthyFetcher=types.SimpleNamespace(fetch=fake_fetch),
        )
        fake_captcha_solver = types.SimpleNamespace(
            CaptchaSolverClient=None,
            CaptchaSolverError=Exception,
        )

        args = argparse.Namespace(
            origin="BJSA",
            destination="ALA",
            date="2026-04-29",
            timeout=20,
            page_wait=5,
        )
        regions = [
            RegionConfig(
                code="HK",
                name="香港",
                domain="https://www.skyscanner.com.hk",
                currency="HKD",
                locale="zh-HK",
            ),
            RegionConfig(
                code="SG",
                name="Singapore",
                domain="https://www.skyscanner.sg",
                currency="SGD",
                locale="en-SG",
            ),
        ]

        async def run_case() -> None:
            with (
                patch.dict(
                    sys.modules,
                    {
                        "scrapling": fake_scrapling,
                        "captcha_solver": fake_captcha_solver,
                    },
                ),
                patch("transport_scrapling._probe_existing_cdp_page", return_value=None),
                patch("transport_scrapling._probe_page_with_playwright", return_value=None),
                patch(
                    "transport_scrapling._resolve_scrapling_state_overrides",
                    return_value={"user_data_dir": "/tmp/shared-profile"},
                ),
                patch("transport_scrapling.emit_trace", lambda **k: None),
            ):
                quotes = await compare_via_scrapling(
                    args,
                    regions,
                    persist_failures=False,
                    region_concurrency=2,
                    build_search_url=lambda region, *_args: (
                        f"{region.domain}/transport/flights/bjsa/ala/260429/"
                    ),
                    persist_failure_log=lambda *a, **k: a[0],
                )

            self.assertEqual(len(quotes), 2)
            self.assertEqual(max_active_calls, 1)
            self.assertTrue(all(quote.price is not None for quote in quotes))

        asyncio.run(run_case())

    def test_compare_via_scrapling_retries_shell_page_with_dom_loading(self) -> None:
        calls: list[dict[str, object]] = []

        class ShellPage:
            url = "https://www.skyscanner.com.hk/transport/flights/bjsa/ala/260429/"
            html = "<html><body><h1>Skyscanner 上從北京到阿拉木圖的便宜機票</h1></body></html>"

        class FullPage:
            url = "https://www.skyscanner.com.hk/transport/flights/bjsa/ala/260429/"
            html = """
            <html>
              <body>
                <div>搜尋結果顯示方式</div>
                <div>最佳</div>
                <div>HK$3,305</div>
                <div>最便宜</div>
                <div>HK$3,072</div>
              </body>
            </html>
            """

        def fake_fetch(url: str, **kwargs):
            calls.append(kwargs)
            if len(calls) == 1:
                return ShellPage()
            return FullPage()

        fake_scrapling = types.SimpleNamespace(
            Fetcher=types.SimpleNamespace(get=lambda *a, **k: ShellPage()),
            StealthyFetcher=types.SimpleNamespace(fetch=fake_fetch),
        )
        fake_captcha_solver = types.SimpleNamespace(
            CaptchaSolverClient=None,
            CaptchaSolverError=Exception,
        )

        args = argparse.Namespace(
            origin="BJSA",
            destination="ALA",
            date="2026-04-29",
            timeout=20,
            page_wait=5,
        )
        region = RegionConfig(
            code="HK",
            name="香港",
            domain="https://www.skyscanner.com.hk",
            currency="HKD",
            locale="zh-HK",
        )

        async def run_case() -> None:
            with (
                patch.dict(
                    sys.modules,
                    {
                        "scrapling": fake_scrapling,
                        "captcha_solver": fake_captcha_solver,
                    },
                ),
                patch("transport_scrapling._probe_existing_cdp_page", return_value=None),
                patch("transport_scrapling._probe_page_with_playwright", return_value=None),
                patch(
                    "transport_scrapling._resolve_scrapling_state_overrides",
                    return_value={"cookies": {"_px3": "token"}},
                ),
                patch("transport_scrapling.emit_trace", lambda **k: None),
            ):
                quotes = await compare_via_scrapling(
                    args,
                    [region],
                    persist_failures=False,
                    build_search_url=lambda *_args: (
                        "https://www.skyscanner.com.hk/transport/flights/bjsa/ala/260429/"
                    ),
                    persist_failure_log=lambda *a, **k: a[0],
                )

            self.assertEqual(len(calls), 2)
            self.assertFalse(calls[0]["load_dom"])
            self.assertTrue(calls[1]["load_dom"])
            self.assertTrue(calls[1]["network_idle"])
            self.assertEqual(quotes[0].status, "page_text")
            self.assertEqual(quotes[0].cheapest_price, 3072.0)

        asyncio.run(run_case())

    def test_compare_via_scrapling_forwards_probe_page_text_to_failure_log(self) -> None:
        args = argparse.Namespace(
            origin="BJSA",
            destination="TBS",
            date="2026-04-28",
            timeout=30,
            page_wait=8,
        )
        region = RegionConfig(
            code="SG",
            name="Singapore",
            domain="https://www.skyscanner.sg",
            currency="SGD",
            locale="en-SG",
        )
        logged: list[tuple[tuple[object, ...], dict[str, object]]] = []

        async def run_case() -> None:
            with patch(
                "transport_scrapling._probe_existing_cdp_page",
                return_value=None,
            ), patch(
                "transport_scrapling._probe_page_with_playwright",
                return_value=types.SimpleNamespace(
                    region="SG",
                    domain="https://www.skyscanner.sg",
                    price=None,
                    currency="SGD",
                    source_url="https://www.skyscanner.sg/transport/flights/bjsa/tbs/260428/",
                    status="page_parse_failed",
                    error="页面正文未识别到 Best/Cheapest 价格",
                    page_text="Show results by\nBest\n£123",
                ),
            ), patch("transport_scrapling.emit_trace", lambda **k: None):
                quotes = await compare_via_scrapling(
                    args,
                    [region],
                    persist_failures=True,
                    build_search_url=lambda *_args: (
                        "https://www.skyscanner.sg/transport/flights/bjsa/tbs/260428/"
                    ),
                    persist_failure_log=lambda *a, **k: logged.append((a, k)) or a[0],
                    fetch_pipeline="session_heavy",
                )

            self.assertEqual(len(quotes), 1)
            self.assertEqual(len(logged), 1)
            self.assertEqual(logged[0][1]["page_text"], "Show results by\nBest\n£123")

        asyncio.run(run_case())

    def test_compare_via_scrapling_returns_probe_quote_without_scrapling(self) -> None:
        args = argparse.Namespace(
            origin="BJSA",
            destination="TBS",
            date="2026-04-28",
            timeout=30,
            page_wait=8,
        )
        region = RegionConfig(
            code="SG",
            name="Singapore",
            domain="https://www.skyscanner.sg",
            currency="SGD",
            locale="en-SG",
        )

        async def run_case() -> None:
            with patch(
                "transport_scrapling._probe_existing_cdp_page",
                return_value=None,
            ), patch(
                "transport_scrapling._probe_page_with_playwright",
                return_value=types.SimpleNamespace(
                    region="SG",
                    domain="https://www.skyscanner.sg",
                    price=None,
                    currency="SGD",
                    source_url="https://www.skyscanner.sg/sttc/px/captcha-v2/index.html",
                    status="px_challenge",
                    error="Playwright 预探测命中 PX 验证页",
                    debug_log_path=None,
                ),
            ), patch.dict(sys.modules, {"scrapling": None}), patch("transport_scrapling.emit_trace", lambda **k: None):
                quotes = await compare_via_scrapling(
                    args,
                    [region],
                    persist_failures=False,
                    build_search_url=lambda *_args: (
                        "https://www.skyscanner.sg/transport/flights/bjsa/tbs/260428/"
                    ),
                    persist_failure_log=lambda *a, **k: a[0],
                    fetch_pipeline="session_heavy",
                )

            self.assertEqual(len(quotes), 1)
            self.assertEqual(quotes[0].status, "px_challenge")
            self.assertIn("PX", quotes[0].error)

        asyncio.run(run_case())

    def test_compare_via_scrapling_reuses_existing_cdp_page_before_playwright(self) -> None:
        args = argparse.Namespace(
            origin="BJSA",
            destination="ALA",
            date="2026-04-29",
            timeout=30,
            page_wait=8,
        )
        region = RegionConfig(
            code="HK",
            name="香港",
            domain="https://www.skyscanner.com.hk",
            currency="HKD",
            locale="zh-HK",
        )

        async def run_case() -> None:
            with (
                patch(
                    "transport_scrapling._probe_existing_cdp_page",
                    return_value=types.SimpleNamespace(
                        quote=types.SimpleNamespace(
                            region="HK",
                            domain="https://www.skyscanner.com.hk",
                            price=3305.0,
                            currency="HKD",
                            source_url="https://www.skyscanner.com.hk/transport/flights/bjsa/ala/260429/",
                            status="page_text",
                            error=None,
                            cheapest_price=3072.0,
                            best_price=3305.0,
                            price_path="cheapest_price",
                            best_price_path="best_price",
                            cheapest_price_path="cheapest_price",
                            debug_log_path=None,
                        ),
                        page_text="最優 HK$3,305 最便宜 HK$3,072",
                    ),
                ),
                patch("transport_scrapling._probe_page_with_playwright") as playwright_probe,
                patch.dict(sys.modules, {"scrapling": None}),
                patch("transport_scrapling.emit_trace", lambda **k: None),
            ):
                quotes = await compare_via_scrapling(
                    args,
                    [region],
                    persist_failures=False,
                    build_search_url=lambda *_args: (
                        "https://www.skyscanner.com.hk/transport/flights/bjsa/ala/260429/"
                    ),
                    persist_failure_log=lambda *a, **k: a[0],
                )

            playwright_probe.assert_not_called()
            self.assertEqual(len(quotes), 1)
            self.assertEqual(quotes[0].price, 3305.0)
            self.assertEqual(quotes[0].cheapest_price, 3072.0)

        asyncio.run(run_case())

    def test_compare_via_scrapling_skips_cloudflare_retries_for_px_pages(self) -> None:
        calls: list[dict[str, object]] = []

        class FakePage:
            url = "https://www.skyscanner.com.sg/sttc/px/captcha-v2/index.html"
            html = """
            <html>
              <body>
                <h1>Verify you are human</h1>
                <div>captcha-v2</div>
                <div>Press and hold</div>
              </body>
            </html>
            """

        def fake_fetch(url: str, **kwargs):
            calls.append(kwargs)
            return FakePage()

        def fake_get(url: str, **kwargs):
            return FakePage()

        fake_scrapling = types.SimpleNamespace(
            Fetcher=types.SimpleNamespace(get=fake_get),
            StealthyFetcher=types.SimpleNamespace(fetch=fake_fetch),
        )
        fake_captcha_solver = types.SimpleNamespace(
            CaptchaSolverClient=None,
            CaptchaSolverError=Exception,
        )

        args = argparse.Namespace(
            origin="BJSA",
            destination="TBS",
            date="2026-04-28",
            timeout=30,
            page_wait=8,
        )
        region = RegionConfig(
            code="SG",
            name="Singapore",
            domain="https://www.skyscanner.sg",
            currency="SGD",
            locale="en-SG",
        )

        async def run_case() -> None:
            with patch.dict(
                sys.modules,
                {
                    "scrapling": fake_scrapling,
                    "captcha_solver": fake_captcha_solver,
                },
            ), patch(
                "transport_scrapling._probe_existing_cdp_page",
                return_value=None,
            ), patch(
                "transport_scrapling._probe_page_with_playwright",
                return_value=None,
            ), patch(
                "transport_scrapling._resolve_scrapling_state_overrides",
                return_value={},
            ):
                quotes = await compare_via_scrapling(
                    args,
                    [region],
                    persist_failures=False,
                    build_search_url=lambda *_args: (
                        "https://www.skyscanner.sg/transport/flights/bjsa/tbs/260428/"
                    ),
                    persist_failure_log=lambda *a, **k: a[0],
                )

            self.assertEqual(len(calls), 1)
            self.assertFalse(calls[0]["solve_cloudflare"])
            self.assertFalse(calls[0]["network_idle"])
            self.assertFalse(calls[0]["load_dom"])
            self.assertEqual(quotes[0].status, "px_challenge")

        asyncio.run(run_case())

    def test_compare_via_scrapling_classifies_px_in_fetcher_fallback(self) -> None:
        calls: list[dict[str, object]] = []

        class EmptyPage:
            url = "https://www.skyscanner.com.sg/transport/flights/bjsa/tbs/260428/"
            html = "<html><body></body></html>"

        class PxPage:
            url = "https://www.skyscanner.com.sg/sttc/px/captcha-v2/index.html"
            html = """
            <html>
              <body>
                <h1>Verify you are human</h1>
                <div>captcha-v2</div>
                <div>Press and hold</div>
              </body>
            </html>
            """

        def fake_fetch(url: str, **kwargs):
            calls.append(kwargs)
            return EmptyPage()

        def fake_get(url: str, **kwargs):
            return PxPage()

        fake_scrapling = types.SimpleNamespace(
            Fetcher=types.SimpleNamespace(get=fake_get),
            StealthyFetcher=types.SimpleNamespace(fetch=fake_fetch),
        )
        fake_captcha_solver = types.SimpleNamespace(
            CaptchaSolverClient=None,
            CaptchaSolverError=Exception,
        )

        args = argparse.Namespace(
            origin="BJSA",
            destination="TBS",
            date="2026-04-28",
            timeout=30,
            page_wait=8,
        )
        region = RegionConfig(
            code="SG",
            name="Singapore",
            domain="https://www.skyscanner.sg",
            currency="SGD",
            locale="en-SG",
        )

        async def run_case() -> None:
            with patch.dict(
                sys.modules,
                {
                    "scrapling": fake_scrapling,
                    "captcha_solver": fake_captcha_solver,
                },
            ), patch(
                "transport_scrapling._probe_existing_cdp_page",
                return_value=None,
            ), patch(
                "transport_scrapling._probe_page_with_playwright",
                return_value=None,
            ), patch(
                "transport_scrapling._resolve_scrapling_state_overrides",
                return_value={},
            ), patch("transport_scrapling.emit_trace", lambda **k: None):
                quotes = await compare_via_scrapling(
                    args,
                    [region],
                    persist_failures=False,
                    build_search_url=lambda *_args: (
                        "https://www.skyscanner.sg/transport/flights/bjsa/tbs/260428/"
                    ),
                    persist_failure_log=lambda *a, **k: a[0],
                )

            self.assertEqual(len(calls), 3)
            self.assertEqual(len(quotes), 1)
            self.assertEqual(quotes[0].status, "px_challenge")
            self.assertIn("/sttc/px/captcha-v2/", quotes[0].source_url)

        asyncio.run(run_case())


class ScraplingTraceTests(unittest.TestCase):
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

    def test_compare_via_scrapling_emits_correct_state_usage_with_cookies(self) -> None:
        traces: list[dict[str, object]] = []

        class FullPage:
            url = "https://www.skyscanner.com.hk/transport/flights/bjsa/ala/260429/"
            html = """
            <html><body>
              <div>最佳</div><div>HK$3,305</div>
              <div>最便宜</div><div>HK$3,072</div>
            </body></html>
            """

        def fake_fetch(url: str, **kwargs):
            return FullPage()

        fake_scrapling = types.SimpleNamespace(
            Fetcher=types.SimpleNamespace(get=lambda *a, **k: FullPage()),
            StealthyFetcher=types.SimpleNamespace(fetch=fake_fetch),
        )
        fake_captcha_solver = types.SimpleNamespace(
            CaptchaSolverClient=None,
            CaptchaSolverError=Exception,
        )

        args = argparse.Namespace(
            origin="BJSA", destination="ALA", date="2026-04-29",
            timeout=20, page_wait=5,
        )
        region = RegionConfig(
            code="HK", name="香港",
            domain="https://www.skyscanner.com.hk",
            currency="HKD", locale="zh-HK",
        )

        async def run_case() -> None:
            with (
                patch.dict(sys.modules, {
                    "scrapling": fake_scrapling,
                    "captcha_solver": fake_captcha_solver,
                }),
                patch("transport_scrapling._probe_existing_cdp_page", return_value=None),
                patch("transport_scrapling._probe_page_with_playwright", return_value=None),
                patch(
                    "transport_scrapling._resolve_scrapling_state_overrides",
                    return_value={"cookies": {"_px3": "token"}},
                ),
                patch("transport_scrapling.emit_trace", side_effect=lambda **k: traces.append(k)),
            ):
                await compare_via_scrapling(
                    args, [region],
                    persist_failures=False,
                    build_search_url=lambda *_args: (
                        "https://www.skyscanner.com.hk/transport/flights/bjsa/ala/260429/"
                    ),
                    persist_failure_log=lambda *a, **k: a[0],
                )

            self.assertGreater(len(traces), 0, "No traces emitted")
            for trace in traces:
                self.assertTrue(trace["used_cdp_cookies"], f"Expected used_cdp_cookies=True, got {trace}")
                self.assertFalse(trace["used_profile_dir"], f"Expected used_profile_dir=False, got {trace}")

        asyncio.run(run_case())

    def test_compare_via_scrapling_emits_correct_state_usage_with_profile_dir(self) -> None:
        traces: list[dict[str, object]] = []

        class FullPage:
            url = "https://www.skyscanner.com.hk/transport/flights/bjsa/ala/260429/"
            html = """
            <html><body>
              <div>最佳</div><div>HK$3,305</div>
              <div>最便宜</div><div>HK$3,072</div>
            </body></html>
            """

        def fake_fetch(url: str, **kwargs):
            return FullPage()

        fake_scrapling = types.SimpleNamespace(
            Fetcher=types.SimpleNamespace(get=lambda *a, **k: FullPage()),
            StealthyFetcher=types.SimpleNamespace(fetch=fake_fetch),
        )
        fake_captcha_solver = types.SimpleNamespace(
            CaptchaSolverClient=None,
            CaptchaSolverError=Exception,
        )

        args = argparse.Namespace(
            origin="BJSA", destination="ALA", date="2026-04-29",
            timeout=20, page_wait=5,
        )
        region = RegionConfig(
            code="HK", name="香港",
            domain="https://www.skyscanner.com.hk",
            currency="HKD", locale="zh-HK",
        )

        async def run_case() -> None:
            with (
                patch.dict(sys.modules, {
                    "scrapling": fake_scrapling,
                    "captcha_solver": fake_captcha_solver,
                }),
                patch("transport_scrapling._probe_existing_cdp_page", return_value=None),
                patch("transport_scrapling._probe_page_with_playwright", return_value=None),
                patch(
                    "transport_scrapling._resolve_scrapling_state_overrides",
                    return_value={"user_data_dir": "/tmp/edge-profile"},
                ),
                patch("transport_scrapling.emit_trace", side_effect=lambda **k: traces.append(k)),
            ):
                await compare_via_scrapling(
                    args, [region],
                    persist_failures=False,
                    build_search_url=lambda *_args: (
                        "https://www.skyscanner.com.hk/transport/flights/bjsa/ala/260429/"
                    ),
                    persist_failure_log=lambda *a, **k: a[0],
                )

            self.assertGreater(len(traces), 0, "No traces emitted")
            for trace in traces:
                self.assertFalse(trace["used_cdp_cookies"], f"Expected used_cdp_cookies=False, got {trace}")
                self.assertTrue(trace["used_profile_dir"], f"Expected used_profile_dir=True, got {trace}")

        asyncio.run(run_case())

    def test_compare_via_scrapling_attempt_index_monotonically_increasing(self) -> None:
        traces: list[dict[str, object]] = []

        class EmptyPage:
            url = "https://www.skyscanner.com.hk/transport/flights/bjsa/ala/260429/"
            html = "<html><body></body></html>"

        def fake_fetch(url: str, **kwargs):
            return EmptyPage()

        def fake_get(url: str, **kwargs):
            return EmptyPage()

        fake_scrapling = types.SimpleNamespace(
            Fetcher=types.SimpleNamespace(get=fake_get),
            StealthyFetcher=types.SimpleNamespace(fetch=fake_fetch),
        )
        fake_captcha_solver = types.SimpleNamespace(
            CaptchaSolverClient=None,
            CaptchaSolverError=Exception,
        )

        args = argparse.Namespace(
            origin="BJSA", destination="ALA", date="2026-04-29",
            timeout=20, page_wait=5,
        )
        region = RegionConfig(
            code="HK", name="香港",
            domain="https://www.skyscanner.com.hk",
            currency="HKD", locale="zh-HK",
        )

        async def run_case() -> None:
            with (
                patch.dict(sys.modules, {
                    "scrapling": fake_scrapling,
                    "captcha_solver": fake_captcha_solver,
                }),
                patch("transport_scrapling._probe_existing_cdp_page", return_value=None),
                patch("transport_scrapling._probe_page_with_playwright", return_value=None),
                patch(
                    "transport_scrapling._resolve_scrapling_state_overrides",
                    return_value={},
                ),
                patch("transport_scrapling.emit_trace", side_effect=lambda **k: traces.append(k)),
            ):
                await compare_via_scrapling(
                    args, [region],
                    persist_failures=False,
                    build_search_url=lambda *_args: (
                        "https://www.skyscanner.com.hk/transport/flights/bjsa/ala/260429/"
                    ),
                    persist_failure_log=lambda *a, **k: a[0],
                )

            self.assertGreater(len(traces), 1, "Expected multiple trace stages")
            prev = -1
            for trace in traces:
                idx = int(trace["attempt_index"])
                self.assertGreater(
                    idx, prev,
                    f"attempt_index not increasing: {prev} → {idx} in {trace['source_kind']}",
                )
                prev = idx

        asyncio.run(run_case())

    def test_compare_via_scrapling_trace_includes_per_region_page_info(self) -> None:
        traces: list[dict[str, object]] = []

        class FullPage:
            url = "https://www.skyscanner.sg/transport/flights/bjsa/ala/260429/"
            html = "<html><body><div>Best</div><div>S$500</div><div>Cheapest</div><div>S$400</div></body></html>"

        def fake_fetch(url: str, **kwargs):
            return FullPage()

        fake_scrapling = types.SimpleNamespace(
            Fetcher=types.SimpleNamespace(get=lambda *a, **k: FullPage()),
            StealthyFetcher=types.SimpleNamespace(fetch=fake_fetch),
        )
        fake_captcha_solver = types.SimpleNamespace(
            CaptchaSolverClient=None,
            CaptchaSolverError=Exception,
        )

        args = argparse.Namespace(
            origin="BJSA", destination="ALA", date="2026-04-29",
            timeout=20, page_wait=5,
        )
        region = RegionConfig(
            code="SG", name="Singapore",
            domain="https://www.skyscanner.sg",
            currency="SGD", locale="en-SG",
        )

        async def run_case() -> None:
            with (
                patch.dict(sys.modules, {
                    "scrapling": fake_scrapling,
                    "captcha_solver": fake_captcha_solver,
                }),
                patch("transport_scrapling._probe_existing_cdp_page", return_value=None),
                patch("transport_scrapling._probe_page_with_playwright", return_value=None),
                patch(
                    "transport_scrapling._resolve_scrapling_state_overrides",
                    return_value={},
                ),
                patch("transport_scrapling.emit_trace", side_effect=lambda **k: traces.append(k)),
            ):
                await compare_via_scrapling(
                    args, [region],
                    persist_failures=False,
                    build_search_url=lambda *_args: (
                        "https://www.skyscanner.sg/transport/flights/bjsa/ala/260429/"
                    ),
                    persist_failure_log=lambda *a, **k: a[0],
                )

            self.assertGreater(len(traces), 0, "No traces emitted")
            for trace in traces:
                self.assertEqual(trace["region"], "SG")
                self.assertIn("page_text_len", trace)
                self.assertIn("page_url", trace)
            self.assertTrue(
                any(int(t["page_text_len"]) > 0 for t in traces),
                "At least one trace should have page_text_len > 0",
            )

        asyncio.run(run_case())


if __name__ == "__main__":
    unittest.main()
