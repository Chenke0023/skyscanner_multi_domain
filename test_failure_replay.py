import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from failure_replay import (
    build_failure_replay_report,
    load_failure_sample,
    replay_failure_sample,
)


def _write_failure_sample(
    root: Path,
    name: str,
    *,
    region: str,
    status: str,
    transport: str = "scrapling",
    page_text_excerpt: str = "(empty)",
) -> Path:
    path = root / name
    path.write_text(
        f"""timestamp: 2026-04-08T10:49:33
transport: {transport}
route: BJSA_DPS_20260502
region: {region}
domain: https://www.skyscanner.com
status: {status}
error: sample failure
source_url: https://www.skyscanner.com/transport/flights/bjsa/dps/260502/
extra: {{}}

--- page_text_excerpt ---
{page_text_excerpt}
""",
        encoding="utf-8",
    )
    return path


class FailureReplayTests(unittest.TestCase):
    def test_load_failure_sample_parses_headers_and_excerpt(self) -> None:
        with TemporaryDirectory() as temp_dir:
            sample_path = _write_failure_sample(
                Path(temp_dir),
                "20260408_104933_BJSA_DPS_20260502_ID_scrapling_page_parse_failed.log",
                region="ID",
                status="page_parse_failed",
            )

            sample = load_failure_sample(sample_path)

        self.assertEqual(sample.region, "ID")
        self.assertEqual(sample.transport, "scrapling")
        self.assertEqual(sample.status, "page_parse_failed")
        self.assertEqual(sample.page_text_excerpt, "")

    def test_replay_failure_sample_handles_parser_failures(self) -> None:
        with TemporaryDirectory() as temp_dir:
            sample_path = _write_failure_sample(
                Path(temp_dir),
                "20260408_104933_BJSA_DPS_20260502_US_scrapling_page_parse_failed.log",
                region="US",
                status="page_parse_failed",
            )
            sample = load_failure_sample(sample_path)

        result = replay_failure_sample(sample)

        self.assertTrue(result.replayable)
        self.assertIsNotNone(result.quote)
        self.assertEqual(result.quote.status, "page_empty_shell")
        self.assertFalse(result.matched_expected_status)
        self.assertEqual(result.failure_stage, "page_state_recognition")

    def test_build_failure_replay_report_groups_markets(self) -> None:
        with TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            _write_failure_sample(
                temp_path,
                "20260313_104335_BJSA_ALA_20260429_CN_invalid_invalid_transport.log",
                region="CN",
                status="invalid_transport",
                transport="invalid",
            )
            _write_failure_sample(
                temp_path,
                "20260408_104933_BJSA_DPS_20260502_ID_scrapling_page_parse_failed.log",
                region="ID",
                status="page_parse_failed",
            )
            _write_failure_sample(
                temp_path,
                "20260408_104933_BJSA_DPS_20260502_US_scrapling_page_parse_failed.log",
                region="US",
                status="page_parse_failed",
            )

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
