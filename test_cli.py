import unittest
from argparse import Namespace
from contextlib import redirect_stdout
from io import StringIO
from unittest.mock import patch

from cli import (
    SimpleCLI,
    _build_args_from_saved_query,
    _is_ac_power_connected,
    _build_decision_summary,
    _build_warning_detail_section,
    _confidence_label,
    _failed_reason_counts,
    _price_source_label,
    _row_cny_value,
    _warnings_summary,
    build_parser,
    run_failure_replay_command,
)


class CliParserTests(unittest.TestCase):
    def test_background_auto_refresh_commands_parse(self) -> None:
        parser = build_parser()

        once = parser.parse_args(["auto-refresh-once", "--limit", "2", "--dry-run", "--only-on-ac-power"])
        install = parser.parse_args(["install-auto-refresh", "--interval-minutes", "30", "--only-on-ac-power"])
        uninstall = parser.parse_args(["uninstall-auto-refresh"])

        self.assertEqual(once.command, "auto-refresh-once")
        self.assertEqual(once.limit, 2)
        self.assertTrue(once.dry_run)
        self.assertTrue(once.only_on_ac_power)
        self.assertEqual(install.command, "install-auto-refresh")
        self.assertEqual(install.interval_minutes, 30)
        self.assertTrue(install.only_on_ac_power)
        self.assertEqual(uninstall.command, "uninstall-auto-refresh")

    def test_ac_power_detection_parses_pmset_output(self) -> None:
        with patch("cli.subprocess.run") as run:
            run.return_value = Namespace(returncode=0, stdout="Now drawing from 'AC Power'\\n", stderr="")
            self.assertTrue(_is_ac_power_connected())

            run.return_value = Namespace(returncode=0, stdout="Now drawing from 'Battery Power'\\n", stderr="")
            self.assertFalse(_is_ac_power_connected())

    def test_page_command_accepts_return_date(self) -> None:
        parser = build_parser()

        args = parser.parse_args(
            [
                "page",
                "-o",
                "上海",
                "-d",
                "香港",
                "-t",
                "2026-05-20",
                "--return-date",
                "2026-05-25",
            ]
        )

        self.assertEqual(args.return_date, "2026-05-25")

    def test_page_command_accepts_country_mode_arguments(self) -> None:
        parser = build_parser()

        args = parser.parse_args(
            [
                "page",
                "--origin-country",
                "中国",
                "--destination-country",
                "乌兹别克斯坦",
                "-t",
                "2026-05-20",
            ]
        )

        self.assertEqual(args.origin_country, "中国")
        self.assertEqual(args.destination_country, "乌兹别克斯坦")

    def test_page_command_accepts_mixed_location_country_arguments(self) -> None:
        parser = build_parser()

        args = parser.parse_args(
            [
                "page",
                "-o",
                "北京",
                "--destination-country",
                "乌兹别克斯坦",
                "-t",
                "2026-05-20",
            ]
        )

        self.assertEqual(args.origin, "北京")
        self.assertEqual(args.destination_country, "乌兹别克斯坦")

    def test_page_command_accepts_preview_and_rerun_flags(self) -> None:
        parser = build_parser()

        args = parser.parse_args(
            [
                "page",
                "-o",
                "北京",
                "-d",
                "阿拉木图",
                "-t",
                "2026-05-20",
                "--preview-only",
                "--rerun-failed",
                "--show-delta",
            ]
        )

        self.assertTrue(args.preview_only)
        self.assertTrue(args.rerun_failed)
        self.assertTrue(args.show_delta)

    def test_replay_failures_command_accepts_custom_directory(self) -> None:
        parser = build_parser()

        args = parser.parse_args(
            [
                "replay-failures",
                "--failure-dir",
                "/tmp/failures",
                "--no-show-samples",
            ]
        )

        self.assertEqual(args.failure_dir, "/tmp/failures")
        self.assertFalse(args.show_samples)

    def test_build_args_from_saved_query_supports_background_point_route(self) -> None:
        args = _build_args_from_saved_query(
            {
                "identity": {
                    "mode": "point_to_point",
                    "origin_input": "北京",
                    "destination_input": "香港",
                    "date": "2026-06-01",
                    "return_date": None,
                    "date_window_days": 2,
                    "manual_regions": ["hk", "sg"],
                    "exact_airport": False,
                }
            },
            Namespace(wait=8, timeout=20, transport="opencli", fetch_pipeline="balanced", save=False),
        )

        self.assertEqual(args.origin, "北京")
        self.assertEqual(args.destination, "香港")
        self.assertEqual(args.date, "2026-06-01")
        self.assertEqual(args.date_window, 2)
        self.assertEqual(args.regions, "HK,SG")
        self.assertFalse(args.save)


class MarkdownFormattingTests(unittest.TestCase):
    def test_window_markdown_shows_round_trip_range(self) -> None:
        cli = SimpleCLI()

        payload = cli.build_window_markdown_table(
            rows_by_date=[],
            origin="SHAA",
            destination="HKG",
            start_date="2026-05-20",
            end_date="2026-05-22",
            start_return_date="2026-05-25",
            end_return_date="2026-05-27",
        )

        self.assertIn("- 行程: `往返`", payload)
        self.assertIn("- 出发窗口: `2026-05-20` ~ `2026-05-22`", payload)
        self.assertIn("- 返程窗口: `2026-05-25` ~ `2026-05-27`", payload)


class RoutePlanTests(unittest.TestCase):
    def test_build_expanded_route_plan_supports_location_to_country(self) -> None:
        cli = SimpleCLI()

        (
            origin_label,
            destination_label,
            origin_file_token,
            destination_file_token,
            origin_points,
            destination_points,
            regions,
        ) = cli.build_expanded_route_plan(
            origin_value="北京",
            destination_value="乌兹别克斯坦",
            origin_is_country=False,
            destination_is_country=True,
            prefer_origin_metro=True,
        )

        self.assertEqual(origin_label, "北京")
        self.assertEqual(destination_label, "乌兹别克斯坦")
        self.assertEqual(origin_file_token, "BJSA")
        self.assertEqual(destination_file_token, "UZ_ANY")
        self.assertEqual(len(origin_points), 1)
        self.assertEqual(origin_points[0].code, "BJSA")
        self.assertGreaterEqual(len(destination_points), 1)
        self.assertEqual(destination_points[0].code, "TAS")
        self.assertIn("CN", regions)


class FailureReplayCommandTests(unittest.TestCase):
    def test_run_failure_replay_command_prints_summary(self) -> None:
        output = StringIO()

        with (
            patch("cli.build_failure_replay_report") as build_report,
            redirect_stdout(output),
        ):
            build_report.return_value = type(
                "Report",
                (),
                {
                    "total_samples": 3,
                },
            )()
            with patch(
                "cli.render_failure_replay_report",
                return_value="# 失败样本回放集\n",
            ):
                exit_code = run_failure_replay_command(
                    Namespace(
                        failure_dir="/tmp/failures",
                        show_samples=False,
                    )
                )

        self.assertEqual(exit_code, 0)
        self.assertIn("失败样本回放集", output.getvalue())


class RunPageCommandTests(unittest.IsolatedAsyncioTestCase):
    async def test_invalid_round_trip_dates_return_cli_error(self) -> None:
        cli = SimpleCLI()
        args = Namespace(
            origin="上海",
            destination="香港",
            date="2026-05-20",
            return_date="2026-05-19",
            regions="",
            wait=10,
            timeout=30,
            save=False,
            date_window=0,
            exact_airport=False,
            transport="scrapling",
        )
        output = StringIO()

        with (
            patch.object(
                cli,
                "build_effective_regions",
                return_value=(object(), object(), ["CN"]),
            ),
            redirect_stdout(output),
        ):
            exit_code = await cli.run_page_command(args)

        self.assertEqual(exit_code, 2)
        self.assertIn("日期参数错误", output.getvalue())


def _make_simplified_row(**overrides: object) -> dict[str, object]:
    base: dict[str, object] = {
        "region_code": "CN",
        "region_name": "中国",
        "best_display_price": "2,500.00 CNY",
        "best_cny_price": 2500.0,
        "cheapest_display_price": "2,438.00 CNY",
        "cheapest_cny_price": 2438.0,
        "link": "https://www.skyscanner.com/transport/flights/bjsa/alt/2026-05-20",
        "status": "ok",
        "error": "-",
        "route": "北京 -> 阿拉木图",
        "source_kind": "page_render",
        "source_label": "页面渲染",
        "delta_vs_last_scan": None,
        "delta_label": "-",
        "updated_at": None,
        "failure_category": None,
        "failure_action": None,
        "can_reuse_page": True,
        "plan_rank": 1,
        "plan_score": 99.0,
        "plan_phase": "core_route",
        "plan_reason": "primary",
        "route_rank": 1,
        "date_rank": 1,
        "market_rank": 1,
        "confidence": 0.9,
        "price_source": "cheapest_block",
        "evidence_text": "Cheapest 2,438 CNY direct",
        "parser_warnings": [],
    }
    base.update(overrides)
    return base


class TrustHelperTests(unittest.TestCase):
    def test_confidence_label_buckets(self) -> None:
        self.assertEqual(_confidence_label(0.95), "高")
        self.assertEqual(_confidence_label(0.85), "高")
        self.assertEqual(_confidence_label(0.7), "中")
        self.assertEqual(_confidence_label(0.45), "低")
        self.assertEqual(_confidence_label(0.1), "极低")
        self.assertEqual(_confidence_label(None), "未知")
        self.assertEqual(_confidence_label("0.9"), "未知")

    def test_price_source_label_known_and_unknown(self) -> None:
        self.assertEqual(_price_source_label("cheapest_block"), "Cheapest 区块")
        self.assertEqual(_price_source_label("first_price_fallback"), "首个价格 fallback")
        self.assertEqual(_price_source_label("unpriced"), "未取价")
        self.assertEqual(_price_source_label(None), "未知")
        self.assertEqual(_price_source_label(""), "未知")
        self.assertEqual(_price_source_label("custom_source"), "custom_source")

    def test_warnings_summary_handles_empty_one_or_many(self) -> None:
        self.assertEqual(_warnings_summary(None), "-")
        self.assertEqual(_warnings_summary([]), "-")
        self.assertEqual(_warnings_summary(["仅 best"]), "仅 best")
        self.assertEqual(_warnings_summary(["a", "b"]), "2 项警告")
        self.assertEqual(_warnings_summary(["", "  "]), "-")

    def test_failed_reason_counts_groups_by_failure_category(self) -> None:
        rows = [
            _make_simplified_row(),
            _make_simplified_row(
                cheapest_cny_price=None,
                best_cny_price=None,
                failure_category="challenge",
            ),
            _make_simplified_row(
                cheapest_cny_price=None,
                best_cny_price=None,
                failure_category="parse_failed",
            ),
            _make_simplified_row(
                cheapest_cny_price=None,
                best_cny_price=None,
                failure_category="challenge",
            ),
        ]
        counts = _failed_reason_counts(rows)
        self.assertEqual(counts, {"challenge": 2, "parse_failed": 1})

    def test_row_cny_value_prefers_cheapest_then_best(self) -> None:
        self.assertEqual(_row_cny_value(_make_simplified_row()), 2438.0)
        self.assertEqual(
            _row_cny_value(
                _make_simplified_row(cheapest_cny_price=None, best_cny_price=2700.0)
            ),
            2700.0,
        )
        self.assertIsNone(
            _row_cny_value(
                _make_simplified_row(cheapest_cny_price=None, best_cny_price=None)
            )
        )


class DecisionSummaryTests(unittest.TestCase):
    def test_decision_summary_lists_primary_and_runner_with_spread(self) -> None:
        rows = [
            _make_simplified_row(
                region_name="中国",
                cheapest_cny_price=2438.0,
                best_cny_price=2500.0,
            ),
            _make_simplified_row(
                region_name="香港",
                cheapest_cny_price=2610.0,
                best_cny_price=2700.0,
                confidence=0.78,
                price_source="best_block",
            ),
            _make_simplified_row(
                region_name="新加坡",
                cheapest_cny_price=None,
                best_cny_price=None,
                failure_category="challenge",
            ),
        ]
        text = "\n".join(_build_decision_summary(rows))

        self.assertIn("## 扫描结论", text)
        self.assertIn("### 推荐先验证", text)
        self.assertIn("¥2,438.00", text)
        self.assertIn("市场：中国", text)
        self.assertIn("可信度：高", text)
        self.assertIn("### 备选结果", text)
        self.assertIn("¥2,610.00", text)
        self.assertIn("价差：¥172.00", text)
        self.assertIn("challenge×1", text)

    def test_decision_summary_marks_low_confidence_primary_with_runner_advantage(
        self,
    ) -> None:
        rows = [
            _make_simplified_row(
                region_name="中国",
                cheapest_cny_price=2438.0,
                best_cny_price=2500.0,
                confidence=0.45,
                price_source="first_price_fallback",
            ),
            _make_simplified_row(
                region_name="香港",
                cheapest_cny_price=2480.0,
                best_cny_price=2520.0,
                confidence=0.9,
                price_source="cheapest_block",
            ),
        ]
        text = "\n".join(_build_decision_summary(rows))

        self.assertIn("最低价需复核；第二低价可信度更高且价差较小。", text)
        self.assertIn("最低价来自首个价格 fallback，必须人工确认。", text)

    def test_decision_summary_emits_parser_warning_hint(self) -> None:
        rows = [
            _make_simplified_row(
                parser_warnings=["Best 与 Cheapest 价格不一致，请点开页面确认票价条件。"],
            ),
        ]
        text = "\n".join(_build_decision_summary(rows))

        self.assertIn(
            "解析警告：Best 与 Cheapest 价格不一致，请点开页面确认票价条件。",
            text,
        )

    def test_decision_summary_handles_no_priced_rows(self) -> None:
        rows = [
            _make_simplified_row(
                cheapest_cny_price=None,
                best_cny_price=None,
                failure_category="challenge",
            ),
            _make_simplified_row(
                cheapest_cny_price=None,
                best_cny_price=None,
                failure_category="parse_failed",
            ),
        ]
        text = "\n".join(_build_decision_summary(rows))

        self.assertIn("本次未抓取到任何有效价格", text)
        self.assertIn("- challenge: 1", text)
        self.assertIn("- parse_failed: 1", text)

    def test_decision_summary_warns_when_only_fallback_sources_priced(self) -> None:
        rows = [
            _make_simplified_row(
                region_name="中国",
                cheapest_cny_price=2438.0,
                price_source="first_price_fallback",
                confidence=0.45,
            ),
            _make_simplified_row(
                region_name="香港",
                cheapest_cny_price=2520.0,
                price_source="recovered_best",
                confidence=0.6,
            ),
        ]
        text = "\n".join(_build_decision_summary(rows))

        self.assertIn(
            "所有有效价格均来自 fallback 解析，作为初筛结果，需人工复核。",
            text,
        )

    def test_decision_summary_includes_date_when_requested(self) -> None:
        row = _make_simplified_row(date="2026-05-20")
        text = "\n".join(_build_decision_summary([row], show_dates=True))

        self.assertIn("日期：2026-05-20", text)


class WarningDetailSectionTests(unittest.TestCase):
    def test_section_lists_warnings_and_evidence(self) -> None:
        rows = [
            _make_simplified_row(
                region_name="中国",
                parser_warnings=["仅解析到 Best 区块，未解析到 Cheapest 对照。"],
                evidence_text="Best 2,500 CNY",
            ),
            _make_simplified_row(region_name="香港", parser_warnings=[]),
        ]
        text = "\n".join(_build_warning_detail_section(rows))

        self.assertIn("## 解析警告与证据", text)
        self.assertIn("**中国 · 北京 -> 阿拉木图**", text)
        self.assertIn("- 仅解析到 Best 区块，未解析到 Cheapest 对照。", text)
        self.assertIn("证据片段：Best 2,500 CNY", text)
        self.assertNotIn("香港", text)

    def test_section_returns_empty_when_no_warnings(self) -> None:
        rows = [_make_simplified_row(parser_warnings=[])]
        self.assertEqual(_build_warning_detail_section(rows), [])


class MarkdownReportTrustTests(unittest.TestCase):
    def test_build_markdown_table_includes_trust_columns_and_decision(self) -> None:
        cli = SimpleCLI()
        rows = [
            _make_simplified_row(
                region_name="中国",
                cheapest_cny_price=2438.0,
                price_source="cheapest_block",
                confidence=0.9,
            ),
            _make_simplified_row(
                region_name="香港",
                cheapest_cny_price=2610.0,
                confidence=0.78,
                price_source="best_block",
                parser_warnings=["仅解析到 Best 区块，未解析到 Cheapest 对照。"],
                evidence_text="Best 2,610 CNY direct",
            ),
        ]

        payload = cli.build_markdown_table(
            rows=rows,
            origin="北京",
            destination="阿拉木图",
            date="2026-05-20",
        )

        self.assertIn("| 可信度 | 价格来源 | 警告 |", payload)
        self.assertIn("## 扫描结论", payload)
        self.assertIn("## 价格明细", payload)
        self.assertIn("Cheapest 区块", payload)
        self.assertIn("Best 区块", payload)
        self.assertIn("仅解析到 Best 区块", payload)
        self.assertIn("## 解析警告与证据", payload)
        self.assertIn("证据片段：Best 2,610 CNY direct", payload)

    def test_build_markdown_table_tolerates_rows_without_trust_metadata(self) -> None:
        cli = SimpleCLI()
        legacy_row = _make_simplified_row()
        for key in ("confidence", "price_source", "evidence_text", "parser_warnings"):
            legacy_row.pop(key, None)

        payload = cli.build_markdown_table(
            rows=[legacy_row],
            origin="北京",
            destination="阿拉木图",
            date="2026-05-20",
        )

        self.assertIn("未知", payload)
        self.assertNotIn("证据片段：", payload)

    def test_build_window_markdown_table_includes_decision_section(self) -> None:
        cli = SimpleCLI()
        rows_a = [
            _make_simplified_row(
                region_name="中国",
                cheapest_cny_price=2438.0,
                price_source="cheapest_block",
                confidence=0.9,
            ),
        ]
        rows_b = [
            _make_simplified_row(
                region_name="香港",
                cheapest_cny_price=2610.0,
                confidence=0.78,
                price_source="best_block",
            ),
        ]

        payload = cli.build_window_markdown_table(
            rows_by_date=[("2026-05-20", rows_a), ("2026-05-21", rows_b)],
            origin="北京",
            destination="阿拉木图",
            start_date="2026-05-20",
            end_date="2026-05-21",
        )

        self.assertIn("## 扫描结论", payload)
        self.assertIn("¥2,438.00", payload)
        self.assertIn("¥2,610.00", payload)
        self.assertIn("日期：2026-05-20", payload)
        self.assertIn("| 可信度 | 价格来源 | 警告 |", payload)


if __name__ == "__main__":
    unittest.main()
