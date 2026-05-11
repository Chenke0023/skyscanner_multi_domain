"""Trace emission tests for Scrapling transport."""

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
      <div>最佳</div><div>HK$3,305</div>
      <div>最便宜</div><div>HK$3,072</div>
    </body></html>
    """


class ScraplingTraceTests(unittest.TestCase):
    def test_compare_via_scrapling_emits_correct_state_usage_with_cookies(self) -> None:
        traces: list[dict[str, object]] = []

        def fake_fetch(url: str, **kwargs):
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
        region = RegionConfig(
            code="HK", name="香港",
            domain="https://www.skyscanner.com.hk",
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
                self.assertTrue(
                    trace["used_cdp_cookies"],
                    f"Expected used_cdp_cookies=True, got {trace}"
                )
                self.assertFalse(
                    trace["used_profile_dir"],
                    f"Expected used_profile_dir=False, got {trace}"
                )

        asyncio.run(run_case())

    def test_compare_via_scrapling_emits_correct_state_usage_with_profile_dir(self) -> None:
        traces: list[dict[str, object]] = []

        def fake_fetch(url: str, **kwargs):
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
        region = RegionConfig(
            code="HK", name="香港",
            domain="https://www.skyscanner.com.hk",
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
                self.assertFalse(
                    trace["used_cdp_cookies"],
                    f"Expected used_cdp_cookies=False, got {trace}"
                )
                self.assertTrue(
                    trace["used_profile_dir"],
                    f"Expected used_profile_dir=True, got {trace}"
                )

        asyncio.run(run_case())

    def test_compare_via_scrapling_attempt_index_monotonically_increasing(self) -> None:
        traces: list[dict[str, object]] = []

        class EmptyPage:
            url = "https://www.skyscanner.com.hk/transport/flights/bjsa/ala/260429/"
            html = "<html><body></body></html>"

        def fake_fetch(url: str, **kwargs):
            return EmptyPage()

        fake_scrapling = types.SimpleNamespace(
            Fetcher=types.SimpleNamespace(get=lambda *a, **k: EmptyPage()),
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
            code="HK", name="香港",
            domain="https://www.skyscanner.com.hk",
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
                patch("transport_scrapling._resolve_scrapling_state_overrides", return_value={}),
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
                    f"attempt_index not increasing: {prev} → {idx} in {trace['source_kind']}"
                )
                prev = idx

        asyncio.run(run_case())

    def test_compare_via_scrapling_trace_includes_per_region_page_info(self) -> None:
        traces: list[dict[str, object]] = []

        class FullPricePageSG:
            url = "https://www.skyscanner.sg/transport/flights/bjsa/ala/260429/"
            html = "<html><body><div>Best</div><div>S$500</div><div>Cheapest</div><div>S$400</div></body></html>"

        def fake_fetch(url: str, **kwargs):
            return FullPricePageSG()

        fake_scrapling = types.SimpleNamespace(
            Fetcher=types.SimpleNamespace(get=lambda *a, **k: FullPricePageSG()),
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