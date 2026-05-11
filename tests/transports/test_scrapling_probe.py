"""CDP / Playwright probe priority tests for Scrapling transport."""

from __future__ import annotations

import argparse
import asyncio
import types
import unittest
from unittest.mock import patch

from transport_scrapling import compare_via_scrapling
from skyscanner_models import RegionConfig


class ScraplingProbePriorityTests(unittest.TestCase):
    def test_compare_via_scrapling_reuses_existing_cdp_page_before_playwright(self) -> None:
        args = argparse.Namespace(
            origin="BJSA", destination="ALA",
            date="2026-04-29", timeout=30, page_wait=8,
        )
        region = RegionConfig(
            code="HK", name="香港",
            domain="https://www.skyscanner.com.hk",
            currency="HKD", locale="zh-HK",
        )

        async def run_case() -> None:
            with (
                patch(
                    "transport_scrapling._probe_existing_cdp_page",
                    return_value=types.SimpleNamespace(
                        quote=types.SimpleNamespace(
                            region="HK",
                            domain="https://www.skyscanner.com.hk",
                            price=3305.0, currency="HKD",
                            source_url="https://www.skyscanner.com.hk/transport/flights/bjsa/ala/260429/",
                            status="page_text", error=None,
                            cheapest_price=3072.0, best_price=3305.0,
                            price_path="cheapest_price", best_price_path="best_price",
                            cheapest_price_path="cheapest_price", debug_log_path=None,
                        ),
                        page_text="最優 HK$3,305 最便宜 HK$3,072",
                    ),
                ),
                patch("transport_scrapling._probe_page_with_playwright") as playwright_probe,
                patch.dict(__import__("sys").modules, {"scrapling": None}),
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

            playwright_probe.assert_not_called()
            self.assertEqual(len(quotes), 1)
            self.assertEqual(quotes[0].price, 3305.0)
            self.assertEqual(quotes[0].cheapest_price, 3072.0)

        asyncio.run(run_case())

    def test_compare_via_scrapling_returns_probe_quote_without_scrapling(self) -> None:
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
                patch(
                    "transport_scrapling._probe_existing_cdp_page",
                    return_value=None,
                ),
                patch(
                    "transport_scrapling._probe_page_with_playwright",
                    return_value=types.SimpleNamespace(
                        region="SG", domain="https://www.skyscanner.sg",
                        price=None, currency="SGD",
                        source_url="https://www.skyscanner.sg/sttc/px/captcha-v2/index.html",
                        status="px_challenge",
                        error="Playwright 预探测命中 PX 验证页",
                        debug_log_path=None,
                    ),
                ),
                patch.dict(__import__("sys").modules, {"scrapling": None}),
                patch("transport_scrapling.emit_trace", lambda **k: None),
            ):
                quotes = await compare_via_scrapling(
                    args, [region],
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

    def test_compare_via_scrapling_forwards_probe_page_text_to_failure_log(self) -> None:
        args = argparse.Namespace(
            origin="BJSA", destination="TBS",
            date="2026-04-28", timeout=30, page_wait=8,
        )
        region = RegionConfig(
            code="SG", name="Singapore",
            domain="https://www.skyscanner.sg",
            currency="SGD", locale="en-SG",
        )
        logged: list[tuple[tuple[object, ...], dict[str, object]]] = []

        async def run_case() -> None:
            with (
                patch("transport_scrapling._probe_existing_cdp_page", return_value=None),
                patch(
                    "transport_scrapling._probe_page_with_playwright",
                    return_value=types.SimpleNamespace(
                        region="SG", domain="https://www.skyscanner.sg",
                        price=None, currency="SGD",
                        source_url="https://www.skyscanner.sg/transport/flights/bjsa/tbs/260428/",
                        status="page_parse_failed",
                        error="页面正文未识别到 Best/Cheapest 价格",
                        page_text="Show results by\nBest\n£123",
                    ),
                ),
                patch("transport_scrapling.emit_trace", lambda **k: None),
            ):
                quotes = await compare_via_scrapling(
                    args, [region],
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


if __name__ == "__main__":
    unittest.main()