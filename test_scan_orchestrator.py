"""Tests for scan_orchestrator FailureClass/Action split and WAIT_RENDER."""

import argparse
import asyncio
import unittest

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


if __name__ == "__main__":
    unittest.main()
