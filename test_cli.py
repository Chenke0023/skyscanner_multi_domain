import unittest
from argparse import Namespace
from contextlib import redirect_stdout
from io import StringIO
from unittest.mock import patch

from cli import SimpleCLI, build_parser, run_failure_replay_command


class CliParserTests(unittest.TestCase):
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


if __name__ == "__main__":
    unittest.main()
