"""Failure log persistence tests (migrated from test_skyscanner_neo.py)."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from skyscanner_models import FlightQuote
from skyscanner_neo import _persist_failure_log


class FailureLogTests(unittest.TestCase):
    def test_persist_failure_log_writes_excerpt_and_path(self) -> None:
        quote = FlightQuote(
            region="CN",
            domain="https://www.skyscanner.cn",
            price=None,
            currency="CNY",
            source_url="https://www.skyscanner.cn/transport/flights/bjsa/ala/260429/",
            status="page_parse_failed",
            error="页面正文未识别到 Best/Cheapest 价格",
        )

        with tempfile.TemporaryDirectory() as temp_dir:
            target = Path(temp_dir) / "failure.log"
            _persist_failure_log(
                quote,
                transport="scrapling",
                route_key="BJSA_ALA_20260429",
                page_text="综合最佳\n¥3215\n最便宜\n¥2184",
                extra={"locale": "zh-CN"},
                log_path=target,
            )

            self.assertEqual(quote.debug_log_path, str(target))
            content = target.read_text(encoding="utf-8")
            self.assertIn("transport: scrapling", content)
            self.assertIn("route: BJSA_ALA_20260429", content)
            self.assertIn("locale", content)
            self.assertIn("parser_snapshot", content)
            self.assertIn("综合最佳", content)


if __name__ == "__main__":
    unittest.main()