import argparse
import asyncio
import sys
import types
import unittest
from unittest.mock import patch

from transport_scrapling import _check_captcha_in_page, compare_via_scrapling
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


class ScraplingRetryTests(unittest.TestCase):
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
            ):
                quotes = await compare_via_scrapling(
                    args,
                    [region],
                    persist_failures=True,
                    build_search_url=lambda *_args: (
                        "https://www.skyscanner.sg/transport/flights/bjsa/tbs/260428/"
                    ),
                    persist_failure_log=lambda *a, **k: logged.append((a, k)) or a[0],
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
            ), patch.dict(sys.modules, {"scrapling": None}):
                quotes = await compare_via_scrapling(
                    args,
                    [region],
                    persist_failures=False,
                    build_search_url=lambda *_args: (
                        "https://www.skyscanner.sg/transport/flights/bjsa/tbs/260428/"
                    ),
                    persist_failure_log=lambda *a, **k: a[0],
                )

            self.assertEqual(len(quotes), 1)
            self.assertEqual(quotes[0].status, "px_challenge")
            self.assertIn("PX", quotes[0].error)

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
            ), patch("transport_scrapling._probe_page_with_playwright", return_value=None):
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
            ), patch("transport_scrapling._probe_page_with_playwright", return_value=None):
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


if __name__ == "__main__":
    unittest.main()
