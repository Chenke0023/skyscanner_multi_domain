"""Tests for scan_orchestrator FailureClass/Action split, WAIT_RENDER, and trace flush."""

import argparse
import asyncio
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from scan_orchestrator import (
    FailureAction,
    FailureClass,
    can_fallback_to_browser,
    classify_failure,
    failure_action,
    should_retry_wait_render,
    SCRAPLING_FALLBACK_STATUSES,
)


class FailureClassTests(unittest.TestCase):
    def test_page_loading_maps_to_loading_class(self) -> None:
        assert classify_failure("page_loading") == "loading"

    def test_px_challenge_maps_to_challenge_px_class(self) -> None:
        assert classify_failure("px_challenge") == "challenge_px"

    def test_page_challenge_maps_to_challenge_cf_class(self) -> None:
        assert classify_failure("page_challenge") == "challenge_cf"


class FailureActionTests(unittest.TestCase):
    def test_loading_yields_wait_render_action(self) -> None:
        assert failure_action("loading") == FailureAction.WAIT_RENDER

    def test_challenge_px_yields_manual_session_action(self) -> None:
        assert failure_action("challenge_px") == FailureAction.MANUAL_SESSION

    def test_network_yields_retry_browser_action(self) -> None:
        assert failure_action("network") == FailureAction.RETRY_BROWSER

    def test_parse_yields_retry_browser_action(self) -> None:
        assert failure_action("parse") == FailureAction.RETRY_BROWSER


class CanFallbackToBrowserTests(unittest.TestCase):
    def test_page_loading_is_false(self) -> None:
        assert can_fallback_to_browser("page_loading") is False

    def test_px_challenge_is_false(self) -> None:
        assert can_fallback_to_browser("px_challenge") is False

    def test_scrapling_failed_is_true(self) -> None:
        assert can_fallback_to_browser("scrapling_failed") is True

    def test_page_parse_failed_is_true(self) -> None:
        assert can_fallback_to_browser("page_parse_failed") is True


class ShouldRetryWaitRenderTests(unittest.TestCase):
    def test_page_loading_is_true(self) -> None:
        assert should_retry_wait_render("page_loading") is True

    def test_px_challenge_is_false(self) -> None:
        assert should_retry_wait_render("px_challenge") is False

    def test_network_is_false(self) -> None:
        assert should_retry_wait_render("network") is False

    def test_parse_is_false(self) -> None:
        assert should_retry_wait_render("parse") is False


class LegacySCRAPLING_FALLBACK_STATUSESTests(unittest.TestCase):
    def test_excludes_loading(self) -> None:
        assert "page_loading" not in SCRAPLING_FALLBACK_STATUSES

    def test_includes_network(self) -> None:
        assert "scrapling_failed" in SCRAPLING_FALLBACK_STATUSES

    def test_includes_parse(self) -> None:
        assert "page_parse_failed" in SCRAPLING_FALLBACK_STATUSES


class AttemptTraceFlushTests(unittest.TestCase):
    def test_flush_emits_record_to_disk(self) -> None:
        """flush() writes buffered records to disk even when < 50 records."""
        import attempt_trace

        with tempfile.TemporaryDirectory() as tmp:
            log_dir = Path(tmp) / "logs" / "attempts"
            log_dir.mkdir(parents=True, exist_ok=True)
            test_path = log_dir / "test_flush.jsonl"

            writer = attempt_trace.AttemptTraceWriter.__new__(attempt_trace.AttemptTraceWriter)
            writer._today = "test_flush"
            writer._path = test_path
            writer._buf = []
            import threading
            writer._flush_lock = threading.Lock()

            with patch.object(attempt_trace.AttemptTraceWriter, "get", return_value=writer):
                attempt_trace.emit_trace(run_id="r1", route_key="BJSA_ALA", region="CN")
                attempt_trace.flush()

            assert test_path.exists(), "flush() did not create file"
            content = test_path.read_text().strip()
            assert content, "file is empty"
            record = json.loads(content.splitlines()[0])
            assert record["run_id"] == "r1"
            assert record["region"] == "CN"

    def test_flush_noop_when_empty(self) -> None:
        """flush() with empty buffer does not raise."""
        import attempt_trace
        attempt_trace.flush()  # should not raise


if __name__ == "__main__":
    unittest.main()
