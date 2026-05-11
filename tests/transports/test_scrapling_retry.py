"""Scrapling retry / serialization tests."""

from __future__ import annotations

import argparse
import asyncio
import types
import unittest
from unittest.mock import patch

from transport_scrapling import compare_via_scrapling
from skyscanner_models import RegionConfig


class FullPricePage:
    url = "https://www.skyscanner.com.hk/transport/flights/bjsa/ala/260429/"
    html = """
    <html><body>
      <div>搜尋結果顯示方式</div>
      <div>最佳</div><div>HK$3,305</div>
      <div>最便宜</div><div>HK$3,072</div>
    </body></html>
    """


class ShellPage:
    url = "https://www.skyscanner.com.hk/transport/flights/bjsa/ala/260429/"
    html = "<html><body><h1>Skyscanner 上從北京到阿拉木圖的便宜機票</h1></body></html>"


class ScraplingRetryTests(unittest.TestCase):
    def test_compare_via_scrapling_serializes_shared_profile_dir_usage(self) -> None:
        active_calls = 0
        max_active_calls = 0

        def fake_fetch(url: str, **kwargs):
            nonlocal active_calls, max_active_calls
            self.assertEqual(kwargs.get("user_data_dir"), "/tmp/shared-profile")
            active_calls += 1
            max_active_calls = max(max_active_calls, active_calls)
            import time
            time.sleep(0.05)
            return FullPricePage()

        fake_scrapling = types.SimpleNamespace(
            Fetcher=types.SimpleNamespace(get=lambda *a, **k: FullPricePage()),
            StealthyFetcher=types.SimpleNamespace(fetch=fake_fetch),
        )
        fake_captcha_solver = types.SimpleNamespace(
            CaptchaSolverClient=None, CaptchaSolverError=Exception,
        )

        args = argparse.Namespace(
            origin="BJSA", destination="ALA", date="2026-04-29",
            timeout=20, page_wait=5,
        )
        regions = [
            RegionConfig(code="HK", name="香港", domain="https://www.skyscanner.com.hk",
                         currency="HKD", locale="zh-HK"),
            RegionConfig(code="SG", name="Singapore", domain="https://www.skyscanner.sg",
                         currency="SGD", locale="en-SG"),
        ]

        async def run_case() -> None:
            with (
                patch.dict(__import__("sys").modules, {
                    "scrapling": fake_scrapling,
                    "captcha_solver": fake_captcha_solver,
                }),
                patch("transport_scrapling._probe_existing_cdp_page", return_value=None),
                patch("transport_scrapling._probe_page_with_playwright", return_value=None),
                patch(
                    "transport_scrapling._resolve_scrapling_state_overrides",
                    return_value={"user_data_dir": "/tmp/shared-profile"},
                ),
                patch("transport_scrapling.emit_trace", lambda **k: None),
            ):
                quotes = await compare_via_scrapling(
                    args, regions,
                    persist_failures=False,
                    region_concurrency=2,
                    build_search_url=lambda region, *_args: (
                        f"{region.domain}/transport/flights/bjsa/ala/260429/"
                    ),
                    persist_failure_log=lambda *a, **k: a[0],
                )

            self.assertEqual(len(quotes), 2)
            self.assertGreaterEqual(max_active_calls, 1)
            self.assertTrue(all(quote.price is not None for quote in quotes))

        asyncio.run(run_case())

    def test_compare_via_scrapling_retries_shell_page_with_dom_loading(self) -> None:
        calls: list[dict[str, object]] = []

        def fake_fetch(url: str, **kwargs):
            calls.append(kwargs)
            if len(calls) == 1:
                return ShellPage()
            return FullPricePage()

        fake_scrapling = types.SimpleNamespace(
            Fetcher=types.SimpleNamespace(get=lambda *a, **k: ShellPage()),
            StealthyFetcher=types.SimpleNamespace(fetch=fake_fetch),
        )
        fake_captcha_solver = types.SimpleNamespace(
            CaptchaSolverClient=None, CaptchaSolverError=Exception,
        )

        args = argparse.Namespace(
            origin="BJSA", destination="ALA", date="2026-04-29",
            timeout=20, page_wait=5,
        )
        region = RegionConfig(
            code="HK", name="香港", domain="https://www.skyscanner.com.hk",
            currency="HKD", locale="zh-HK",
        )

        async def run_case() -> None:
            with (
                patch.dict(__import__("sys").modules, {
                    "scrapling": fake_scrapling,
                    "captcha_solver": fake_captcha_solver,
                }),
                patch("transport_scrapling._probe_existing_cdp_page", return_value=None),
                patch("transport_scrapling._probe_page_with_playwright", return_value=None),
                patch(
                    "transport_scrapling._resolve_scrapling_state_overrides",
                    return_value={"cookies": {"_px3": "token"}},
                ),
                patch("transport_scrapling.emit_trace", lambda **k: None),
            ):
                quotes = await compare_via_scrapling(
                    args, [region],
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

    def test_compare_via_scrapling_skips_cloudflare_retries_for_px_pages(self) -> None:
        calls: list[dict[str, object]] = []

        class FakePage:
            url = "https://www.skyscanner.com.sg/sttc/px/captcha-v2/index.html"
            html = """
            <html><body>
              <h1>Verify you are human</h1>
              <div>captcha-v2</div>
              <div>Press and hold</div>
            </body></html>
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
            CaptchaSolverClient=None, CaptchaSolverError=Exception,
        )

        args = argparse.Namespace(
            origin="BJSA", destination="TBS",
            date="2026-04-28", timeout=30, page_wait=8,
        )
        region = RegionConfig(
            code="SG", name="Singapore",
            domain="https://www.skyscanner.sg",
            currency="SGD", locale="en-SG",
        )

        async def run_case() -> None:
            with (
                patch.dict(__import__("sys").modules, {
                    "scrapling": fake_scrapling,
                    "captcha_solver": fake_captcha_solver,
                }),
                patch("transport_scrapling._probe_existing_cdp_page", return_value=None),
                patch("transport_scrapling._probe_page_with_playwright", return_value=None),
                patch("transport_scrapling._resolve_scrapling_state_overrides", return_value={}),
            ):
                quotes = await compare_via_scrapling(
                    args, [region],
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
            <html><body>
              <h1>Verify you are human</h1>
              <div>captcha-v2</div>
              <div>Press and hold</div>
            </body></html>
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
            CaptchaSolverClient=None, CaptchaSolverError=Exception,
        )

        args = argparse.Namespace(
            origin="BJSA", destination="TBS",
            date="2026-04-28", timeout=30, page_wait=8,
        )
        region = RegionConfig(
            code="SG", name="Singapore",
            domain="https://www.skyscanner.sg",
            currency="SGD", locale="en-SG",
        )

        async def run_case() -> None:
            with (
                patch.dict(__import__("sys").modules, {
                    "scrapling": fake_scrapling,
                    "captcha_solver": fake_captcha_solver,
                }),
                patch("transport_scrapling._probe_existing_cdp_page", return_value=None),
                patch("transport_scrapling._probe_page_with_playwright", return_value=None),
                patch("transport_scrapling._resolve_scrapling_state_overrides", return_value={}),
                patch("transport_scrapling.emit_trace", lambda **k: None),
            ):
                quotes = await compare_via_scrapling(
                    args, [region],
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


if __name__ == "__main__":
    unittest.main()