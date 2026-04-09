import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from failure_replay import (
    DEFAULT_FAILURE_DIR,
    build_failure_replay_report,
    load_failure_sample,
    replay_failure_sample,
)


class FailureReplayTests(unittest.TestCase):
    def test_load_failure_sample_parses_headers_and_excerpt(self) -> None:
        sample_path = (
            DEFAULT_FAILURE_DIR
            / "20260408_104933_BJSA_DPS_20260502_ID_scrapling_page_parse_failed.log"
        )

        sample = load_failure_sample(sample_path)

        self.assertEqual(sample.region, "ID")
        self.assertEqual(sample.transport, "scrapling")
        self.assertEqual(sample.status, "page_parse_failed")
        self.assertEqual(sample.page_text_excerpt, "")

    def test_replay_failure_sample_handles_parser_failures(self) -> None:
        sample_path = (
            DEFAULT_FAILURE_DIR
            / "20260408_104933_BJSA_DPS_20260502_US_scrapling_page_parse_failed.log"
        )
        sample = load_failure_sample(sample_path)

        result = replay_failure_sample(sample)

        self.assertTrue(result.replayable)
        self.assertIsNotNone(result.quote)
        self.assertEqual(result.quote.status, "page_parse_failed")
        self.assertTrue(result.matched_expected_status)
        self.assertEqual(result.failure_stage, "page_state_recognition")

    def test_build_failure_replay_report_groups_markets(self) -> None:
        sample_names = [
            "20260313_104335_BJSA_ALA_20260429_CN_invalid_invalid_transport.log",
            "20260408_104933_BJSA_DPS_20260502_ID_scrapling_page_parse_failed.log",
            "20260408_104933_BJSA_DPS_20260502_US_scrapling_page_parse_failed.log",
        ]
        with TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            for sample_name in sample_names:
                source = DEFAULT_FAILURE_DIR / sample_name
                target = temp_path / sample_name
                target.write_text(source.read_text(encoding="utf-8"), encoding="utf-8")

            report = build_failure_replay_report(temp_path)
            stats_by_region = {stat.region: stat for stat in report.region_stats}

        self.assertEqual(report.total_samples, 3)
        self.assertIn("ID", stats_by_region)
        self.assertIn("US", stats_by_region)
        self.assertIn("CN", stats_by_region)
        self.assertEqual(stats_by_region["ID"].replayable_count, 1)
        self.assertEqual(stats_by_region["US"].replayable_count, 1)
        self.assertEqual(stats_by_region["CN"].non_replayable_count, 1)


if __name__ == "__main__":
    unittest.main()
