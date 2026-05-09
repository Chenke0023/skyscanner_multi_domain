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
    run_page_scan,
)
from skyscanner_models import FlightQuote


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

    def test_challenge_cf_yields_manual_session_action(self) -> None:
        assert failure_action("challenge_cf") == FailureAction.MANUAL_SESSION

    def test_network_yields_retry_browser_action(self) -> None:
        assert failure_action("network") == FailureAction.RETRY_BROWSER

    def test_parse_yields_retry_browser_action(self) -> None:
        assert failure_action("parse") == FailureAction.RETRY_BROWSER


class CanFallbackToBrowserTests(unittest.TestCase):
    def test_page_loading_is_false(self) -> None:
        assert can_fallback_to_browser("page_loading") is False

    def test_px_challenge_is_false(self) -> None:
        assert can_fallback_to_browser("px_challenge") is False

    def test_page_challenge_is_false(self) -> None:
        assert can_fallback_to_browser("page_challenge") is False

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


class OpenCliBatchProgressTests(unittest.IsolatedAsyncioTestCase):
    async def test_opencli_emits_search_plan_batch_progress_without_dropping_regions(self) -> None:
        calls: list[list[str]] = []
        progress_events: list[dict] = []

        async def fake_compare_via_opencli(args, regions, **kwargs):
            region_codes = [region.code for region in regions]
            calls.append(region_codes)
            return [
                FlightQuote(
                    region=region.code,
                    domain=region.domain,
                    price=1000.0 + index,
                    currency=region.currency,
                    source_url=f"https://example.test/{region.code}",
                    status="ok",
                )
                for index, region in enumerate(regions)
            ]

        async def on_progress(payload: dict) -> None:
            progress_events.append(payload)

        with patch(
            "skyscanner_multi_domain.transports.opencli.compare_via_opencli",
            side_effect=fake_compare_via_opencli,
        ):
            quotes = await run_page_scan(
                origin="BJSA",
                destination="ALA",
                date="2026-05-20",
                region_codes=["CN", "HK", "SG", "UK", "KZ"],
                transport="opencli",
                allow_browser_fallback=False,
                on_progress=on_progress,
                query_payload={
                    "identity": {
                        "date": "2026-05-20",
                        "date_window_days": 0,
                        "origin_country": "CN",
                        "destination_country": "KZ",
                    }
                },
            )

        scanned_regions = [code for call in calls for code in call]
        assert set(scanned_regions) == {"CN", "HK", "SG", "UK", "KZ"}
        assert len(scanned_regions) == len(set(scanned_regions))
        assert {quote.region for quote in quotes} == {"CN", "HK", "SG", "UK", "KZ"}

        starts = [event for event in progress_events if event["stage"] == "plan_batch_start"]
        completes = [event for event in progress_events if event["stage"] == "plan_batch_complete"]
        assert starts
        assert completes
        assert len(starts) == len(completes) == len(calls)
        assert progress_events[-1]["stage"] == "final"
        for event in [*starts, *completes]:
            assert event["active_plan_phase"]
            assert event["plan_batch_id"] is not None
            assert event["plan_batch_count"] is not None
            assert event["plan_batch_reason"]
            assert event["plan_tasks_total"] is not None
            assert event["plan_tasks_in_batch"] is not None


if __name__ == "__main__":
    unittest.main()


# ── P6: Integration trace tests ───────────────────────────────────────────────

class TraceFallbackChainTests(unittest.IsolatedAsyncioTestCase):
    """Verify JSONL trace output for full fallback chains."""

    async def test_full_fallback_chain_produces_three_trace_lines(self) -> None:
        """OpenCLI network error → CDP parse failed → Scrapling success."""
        trace_events: list[dict] = []

        async def fake_opencli(args, regions, **kwargs):
            return [
                FlightQuote(
                    region=region.code, domain=region.domain,
                    price=None, currency=region.currency,
                    source_url=f"https://example.test/{region.code}",
                    status="opencli_error",
                    fetch_metadata={"elapsed_ms": 5000, "phase": "wait_interactive", "retryable": True},
                )
                for region in regions
            ]

        async def fake_cdp(args, regions, **kwargs):
            return [
                FlightQuote(
                    region=region.code, domain=region.domain,
                    price=None, currency=region.currency,
                    source_url=f"https://example.test/{region.code}",
                    status="page_parse_failed",
                    fetch_metadata={"elapsed_ms": 8000, "phase": "extract", "retryable": False},
                )
                for region in regions
            ]

        async def fake_scrapling(args, regions, **kwargs):
            return [
                FlightQuote(
                    region=region.code, domain=region.domain,
                    price=410.0, currency=region.currency,
                    source_url=f"https://example.test/{region.code}",
                    status="ok", confidence=0.88,
                    fetch_metadata={"elapsed_ms": 3000, "phase": "extract"},
                )
                for region in regions
            ]

        with patch(
            "skyscanner_multi_domain.transports.opencli.compare_via_opencli",
            side_effect=fake_opencli,
        ), patch(
            "skyscanner_multi_domain.transports.cdp.compare_via_pages",
            side_effect=fake_cdp,
        ), patch(
            "skyscanner_multi_domain.transports.scrapling.compare_via_scrapling",
            side_effect=fake_scrapling,
        ), patch(
            "skyscanner_multi_domain.scan.trace.ScanTraceWriter.write",
            side_effect=lambda event: trace_events.append(event.to_json_dict()),
        ):
            quotes = await run_page_scan(
                origin="BJS", destination="ALA", date="2026-06-10",
                region_codes=["SG"], transport="opencli",
                allow_browser_fallback=True,
            )

        # Should have 3 trace events: opencli, cdp, scrapling
        assert len(trace_events) == 3, f"Expected 3 trace events, got {len(trace_events)}"

        e1 = trace_events[0]
        assert e1["transport"] == "opencli"
        assert e1["attempt_index"] == 1
        # network failures route to google_jump first, then CDP, then scrapling
        assert e1["action"] in ("fallback_google_jump", "fallback_cdp")
        assert e1["failure_class"] == "network"

        e2 = trace_events[1]
        assert e2["transport"] == "cdp"
        assert e2["attempt_index"] == 2
        assert e2["action"] == "fallback_scrapling"
        assert e2["failure_class"] == "parse"

        e3 = trace_events[2]
        assert e3["transport"] == "scrapling"
        assert e3["attempt_index"] == 3
        assert e3["action"] == "accept"
        assert e3["price"] == 410.0

        # All share the same scan_id and route_id
        scan_ids = {e["scan_id"] for e in trace_events}
        assert len(scan_ids) == 1
        route_ids = {e["route_id"] for e in trace_events}
        assert len(route_ids) == 1

        # Final quote has attempt_history with 3 entries
        sg_quote = next(q for q in quotes if q.region == "SG")
        assert len(sg_quote.attempt_history) == 3
        assert sg_quote.attempt_history[0]["transport"] == "opencli"
        assert sg_quote.attempt_history[1]["transport"] == "cdp"
        assert sg_quote.attempt_history[2]["transport"] == "scrapling"

    async def test_no_flights_produces_terminal_trace(self) -> None:
        """CDP returns page_no_flights — terminal, no further fallback."""
        trace_events: list[dict] = []

        async def fake_cdp(args, regions, **kwargs):
            return [
                FlightQuote(
                    region=region.code, domain=region.domain,
                    price=None, currency=region.currency,
                    source_url=f"https://example.test/{region.code}",
                    status="page_no_flights",
                    fetch_metadata={"elapsed_ms": 2000},
                )
                for region in regions
            ]

        with patch(
            "skyscanner_multi_domain.transports.cdp.compare_via_pages",
            side_effect=fake_cdp,
        ), patch(
            "skyscanner_multi_domain.scan.trace.ScanTraceWriter.write",
            side_effect=lambda event: trace_events.append(event.to_json_dict()),
        ):
            await run_page_scan(
                origin="BJS", destination="ALA", date="2026-06-10",
                region_codes=["KR"], transport="page",
            )

        assert len(trace_events) == 1
        e = trace_events[0]
        assert e["transport"] == "cdp"
        assert e["action"] == "terminal"
        assert e["failure_class"] == "no_flights"
        assert e["attempt_index"] == 1

    async def test_low_confidence_triggers_fallback_trace(self) -> None:
        """OpenCLI returns low confidence price — fallback to CDP."""
        trace_events: list[dict] = []

        async def fake_opencli(args, regions, **kwargs):
            return [
                FlightQuote(
                    region=region.code, domain=region.domain,
                    price=312.0, currency=region.currency,
                    source_url=f"https://example.test/{region.code}",
                    status="ok", confidence=0.45,
                    price_source="first_price_fallback",
                    fetch_metadata={"elapsed_ms": 3000},
                )
                for region in regions
            ]

        async def fake_cdp(args, regions, **kwargs):
            return [
                FlightQuote(
                    region=region.code, domain=region.domain,
                    price=305.0, currency=region.currency,
                    source_url=f"https://example.test/{region.code}",
                    status="ok", confidence=0.91,
                    fetch_metadata={"elapsed_ms": 6000},
                )
                for region in regions
            ]

        with patch(
            "skyscanner_multi_domain.transports.opencli.compare_via_opencli",
            side_effect=fake_opencli,
        ), patch(
            "skyscanner_multi_domain.transports.cdp.compare_via_pages",
            side_effect=fake_cdp,
        ), patch(
            "skyscanner_multi_domain.scan.trace.ScanTraceWriter.write",
            side_effect=lambda event: trace_events.append(event.to_json_dict()),
        ):
            await run_page_scan(
                origin="BJS", destination="ALA", date="2026-06-10",
                region_codes=["CN"], transport="opencli",
                allow_browser_fallback=True,
            )

        assert len(trace_events) == 2
        e1 = trace_events[0]
        assert e1["transport"] == "opencli"
        assert e1["confidence"] == 0.45
        assert e1["action"] == "fallback_cdp"
        assert "confidence" in (e1.get("reason") or "").lower()

        e2 = trace_events[1]
        assert e2["transport"] == "cdp"
        assert e2["confidence"] == 0.91
        assert e2["action"] == "accept"

    async def test_dual_region_attempt_indices_independent(self) -> None:
        """CN opencli success, SG opencli fail → CDP fail → scrapling success."""
        trace_events: list[dict] = []

        async def fake_opencli(args, regions, **kwargs):
            results = []
            for region in regions:
                if region.code == "CN":
                    results.append(FlightQuote(
                        region="CN", domain=region.domain,
                        price=2200.0, currency="CNY",
                        source_url=f"https://example.test/CN",
                        status="ok", confidence=0.91,
                        fetch_metadata={"elapsed_ms": 2000},
                    ))
                else:
                    results.append(FlightQuote(
                        region=region.code, domain=region.domain,
                        price=None, currency=region.currency,
                        source_url=f"https://example.test/{region.code}",
                        status="opencli_error",
                        fetch_metadata={"elapsed_ms": 5000, "phase": "wait_interactive"},
                    ))
            return results

        async def fake_cdp(args, regions, **kwargs):
            return [
                FlightQuote(
                    region=region.code, domain=region.domain,
                    price=None, currency=region.currency,
                    source_url=f"https://example.test/{region.code}",
                    status="page_parse_failed",
                    fetch_metadata={"elapsed_ms": 4000},
                )
                for region in regions
            ]

        async def fake_scrapling(args, regions, **kwargs):
            return [
                FlightQuote(
                    region=region.code, domain=region.domain,
                    price=410.0, currency=region.currency,
                    source_url=f"https://example.test/{region.code}",
                    status="ok", confidence=0.88,
                    fetch_metadata={"elapsed_ms": 3000},
                )
                for region in regions
            ]

        with patch(
            "skyscanner_multi_domain.transports.opencli.compare_via_opencli",
            side_effect=fake_opencli,
        ), patch(
            "skyscanner_multi_domain.transports.cdp.compare_via_pages",
            side_effect=fake_cdp,
        ), patch(
            "skyscanner_multi_domain.transports.scrapling.compare_via_scrapling",
            side_effect=fake_scrapling,
        ), patch(
            "skyscanner_multi_domain.scan.trace.ScanTraceWriter.write",
            side_effect=lambda event: trace_events.append(event.to_json_dict()),
        ):
            await run_page_scan(
                origin="BJS", destination="ALA", date="2026-06-10",
                region_codes=["CN", "SG"], transport="opencli",
                allow_browser_fallback=True,
            )

        # CN: 1 event (opencli success)
        cn_events = [e for e in trace_events if e["region"] == "CN"]
        assert len(cn_events) == 1
        assert cn_events[0]["attempt_index"] == 1
        assert cn_events[0]["action"] == "accept"

        # SG: 3 events (opencli → cdp → scrapling)
        sg_events = [e for e in trace_events if e["region"] == "SG"]
        assert len(sg_events) == 3
        assert sg_events[0]["attempt_index"] == 1
        assert sg_events[1]["attempt_index"] == 2
        assert sg_events[2]["attempt_index"] == 3

    async def test_metadata_propagation_in_trace(self) -> None:
        """FetchAttempt metadata is preserved in trace event."""
        trace_events: list[dict] = []

        async def fake_opencli(args, regions, **kwargs):
            return [
                FlightQuote(
                    region=region.code, domain=region.domain,
                    price=None, currency=region.currency,
                    source_url=f"https://example.test/{region.code}",
                    status="opencli_error",
                    fetch_metadata={
                        "phase": "extract",
                        "exit_code": 1,
                        "stderr_tail": "boom",
                        "retryable": True,
                        "elapsed_ms": 12345,
                    },
                )
                for region in regions
            ]

        with patch(
            "skyscanner_multi_domain.transports.opencli.compare_via_opencli",
            side_effect=fake_opencli,
        ), patch(
            "skyscanner_multi_domain.scan.trace.ScanTraceWriter.write",
            side_effect=lambda event: trace_events.append(event.to_json_dict()),
        ):
            await run_page_scan(
                origin="BJS", destination="ALA", date="2026-06-10",
                region_codes=["SG"], transport="opencli",
                allow_browser_fallback=False,
            )

        assert len(trace_events) == 1
        e = trace_events[0]
        assert e["phase"] == "extract"
        assert e["elapsed_ms"] == 12345
        assert e["retryable"] is True
        assert e["metadata"]["exit_code"] == 1
        assert e["metadata"]["stderr_tail"] == "boom"

    async def test_page_transport_traces_all_regions(self) -> None:
        """Page/CDP-only transport traces every region."""
        trace_events: list[dict] = []

        async def fake_cdp(args, regions, **kwargs):
            return [
                FlightQuote(
                    region=region.code, domain=region.domain,
                    price=100.0 + i, currency=region.currency,
                    source_url=f"https://example.test/{region.code}",
                    status="ok", confidence=0.9,
                    fetch_metadata={"elapsed_ms": 1000},
                )
                for i, region in enumerate(regions)
            ]

        with patch(
            "skyscanner_multi_domain.transports.cdp.compare_via_pages",
            side_effect=fake_cdp,
        ), patch(
            "skyscanner_multi_domain.scan.trace.ScanTraceWriter.write",
            side_effect=lambda event: trace_events.append(event.to_json_dict()),
        ):
            await run_page_scan(
                origin="BJS", destination="ALA", date="2026-06-10",
                region_codes=["CN", "HK", "SG"], transport="page",
            )

        assert len(trace_events) == 3
        regions = {e["region"] for e in trace_events}
        assert regions == {"CN", "HK", "SG"}
        for e in trace_events:
            assert e["transport"] == "cdp"
            assert e["attempt_index"] == 1
