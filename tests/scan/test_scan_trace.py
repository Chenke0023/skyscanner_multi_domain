"""Tests for scan trace event model, JSONL writer, and helpers."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from skyscanner_multi_domain.scan.trace import (
    ScanTraceContext,
    ScanTraceEvent,
    ScanTraceWriter,
    append_attempt_history,
    emit_attempt_trace,
    merge_attempt_history,
)
from skyscanner_multi_domain.scan.fetch_types import AttemptAction, AttemptPlan


# ── Helpers ───────────────────────────────────────────────────────────────────

def _fake_quote(**overrides):
    """Minimal FlightQuote-alike for trace tests."""
    from skyscanner_multi_domain.models import FlightQuote

    defaults = dict(
        region="SG",
        domain="https://www.skyscanner.com.sg",
        price=None,
        currency="SGD",
        source_url="https://www.skyscanner.com.sg/test",
        status="opencli_error",
        confidence=0.0,
        rankable=False,
        result_visibility=None,
        fetch_metadata={"elapsed_ms": 18021, "phase": "wait_interactive", "retryable": True},
        debug_log_path="/tmp/fail.log",
    )
    defaults.update(overrides)
    return FlightQuote(**{k: v for k, v in defaults.items() if k in FlightQuote.__dataclass_fields__})


def _fake_plan(**overrides):
    defaults = dict(
        action=AttemptAction.FALLBACK_CDP,
        failure_class="network",
        reason="Network timeout — fallback to CDP",
        transports_remaining=["cdp", "scrapling"],
        confidence=0.0,
        manual_review_required=False,
        max_attempts=2,
    )
    defaults.update(overrides)
    return AttemptPlan(**defaults)


def _make_trace_ctx(writer):
    return ScanTraceContext(
        scan_id="abc123",
        route_id="BJS-ALA-20260610",
        origin="BJS",
        destination="ALA",
        depart_date="2026-06-10",
        writer=writer,
    )


# ── ScanTraceEvent ────────────────────────────────────────────────────────────


class ScanTraceEventTests(unittest.TestCase):
    def test_full_event_serializes_all_fields(self) -> None:
        event = ScanTraceEvent(
            scan_id="abc123",
            route_id="BJS-ALA-20260610",
            origin="BJS",
            destination="ALA",
            depart_date="2026-06-10",
            region="SG",
            domain="https://www.skyscanner.com.sg",
            attempt_index=1,
            transport="opencli",
            status="opencli_error",
            action="fallback_cdp",
            failure_class="network",
            reason="timeout",
            price=None,
            currency="SGD",
            confidence=0.0,
            rankable=False,
            result_visibility="fallback_candidate",
            requires_manual_review=False,
            elapsed_ms=18021,
            retryable=True,
            url="https://www.skyscanner.com.sg/test",
            phase="wait_interactive",
            failure_log_path="/tmp/fail.log",
            metadata={"exit_code": 1, "stderr_tail": "boom"},
        )

        d = event.to_json_dict()
        assert d["scan_id"] == "abc123"
        assert d["route_id"] == "BJS-ALA-20260610"
        assert d["region"] == "SG"
        assert d["transport"] == "opencli"
        assert d["attempt_index"] == 1
        assert d["action"] == "fallback_cdp"
        assert d["failure_class"] == "network"
        assert d["elapsed_ms"] == 18021
        assert d["phase"] == "wait_interactive"
        assert d["retryable"] is True
        assert d["metadata"]["exit_code"] == 1
        assert d["schema_version"] == 1
        assert "timestamp" in d

    def test_minimal_event_defaults(self) -> None:
        event = ScanTraceEvent(
            scan_id="x",
            route_id="y",
            origin="A",
            destination="B",
            depart_date="2026-01-01",
            region="CN",
            domain=None,
            attempt_index=0,
            transport="opencli",
            status="ok",
            action="accept",
        )
        d = event.to_json_dict()
        assert d["failure_class"] is None
        assert d["price"] is None
        assert d["confidence"] is None
        assert d["rankable"] is None
        assert d["metadata"] == {}

    def test_json_serializable(self) -> None:
        event = ScanTraceEvent(
            scan_id="abc",
            route_id="r1",
            origin="A",
            destination="B",
            depart_date="2026-01-01",
            region="CN",
            domain="https://example.com",
            attempt_index=0,
            transport="opencli",
            status="ok",
            action="accept",
            metadata={"nested": {"key": "value"}},
        )
        line = json.dumps(event.to_json_dict(), ensure_ascii=False, sort_keys=True)
        parsed = json.loads(line)
        assert parsed["metadata"]["nested"]["key"] == "value"


# ── ScanTraceWriter ───────────────────────────────────────────────────────────


class ScanTraceWriterTests(unittest.TestCase):
    def test_write_and_flush_produces_jsonl(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "trace.jsonl"
            writer = ScanTraceWriter(path)

            event = ScanTraceEvent(
                scan_id="s1", route_id="r1", origin="A", destination="B",
                depart_date="2026-01-01", region="CN", domain="example.com",
                attempt_index=0, transport="opencli", status="ok", action="accept",
            )
            writer.write(event)
            writer.flush()

            lines = path.read_text().strip().split("\n")
            assert len(lines) == 1
            record = json.loads(lines[0])
            assert record["scan_id"] == "s1"

    def test_auto_flush_at_50(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "trace.jsonl"
            writer = ScanTraceWriter(path)

            for i in range(50):
                writer.write(ScanTraceEvent(
                    scan_id="s1", route_id="r1", origin="A", destination="B",
                    depart_date="2026-01-01", region="CN", domain="x.com",
                    attempt_index=i, transport="opencli", status="ok", action="accept",
                ))

            # Should have auto-flushed at 50 lines
            lines = path.read_text().strip().split("\n")
            assert len(lines) == 50

    def test_multiple_writes_accumulate(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "trace.jsonl"
            writer = ScanTraceWriter(path)

            for i in range(3):
                writer.write(ScanTraceEvent(
                    scan_id="s1", route_id="r1", origin="A", destination="B",
                    depart_date="2026-01-01", region="CN", domain="x.com",
                    attempt_index=i, transport="opencli", status="ok", action="accept",
                ))
            writer.flush()

            lines = path.read_text().strip().split("\n")
            assert len(lines) == 3
            for i, line in enumerate(lines):
                assert json.loads(line)["attempt_index"] == i

    def test_none_path_is_noop(self) -> None:
        writer = ScanTraceWriter(None)
        event = ScanTraceEvent(
            scan_id="s1", route_id="r1", origin="A", destination="B",
            depart_date="2026-01-01", region="CN", domain="x.com",
            attempt_index=0, transport="opencli", status="ok", action="accept",
        )
        writer.write(event)  # should not raise
        writer.flush()       # should not raise

    def test_creates_parent_dirs(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "deep" / "nested" / "trace.jsonl"
            writer = ScanTraceWriter(path)
            writer.write(ScanTraceEvent(
                scan_id="s1", route_id="r1", origin="A", destination="B",
                depart_date="2026-01-01", region="CN", domain="x.com",
                attempt_index=0, transport="opencli", status="ok", action="accept",
            ))
            writer.flush()
            assert path.exists()

    def test_flush_empty_noop(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "trace.jsonl"
            writer = ScanTraceWriter(path)
            writer.flush()  # should not raise or create file
            assert not path.exists()


# ── emit_attempt_trace ────────────────────────────────────────────────────────


class EmitAttemptTraceTests(unittest.TestCase):
    def test_emits_event_to_writer(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "trace.jsonl"
            writer = ScanTraceWriter(path)
            ctx = _make_trace_ctx(writer)

            quote = _fake_quote(region="SG", status="opencli_error", price=None)
            plan = _fake_plan(action=AttemptAction.FALLBACK_CDP, failure_class="network")

            emit_attempt_trace(
                trace_ctx=ctx,
                quote=quote,
                plan=plan,
                region="SG",
                domain="https://www.skyscanner.com.sg",
                transport="opencli",
                attempt_index=1,
            )
            writer.flush()

            lines = path.read_text().strip().split("\n")
            assert len(lines) == 1
            record = json.loads(lines[0])
            assert record["region"] == "SG"
            assert record["transport"] == "opencli"
            assert record["attempt_index"] == 1
            assert record["action"] == "fallback_cdp"
            assert record["failure_class"] == "network"
            assert record["elapsed_ms"] == 18021
            assert record["phase"] == "wait_interactive"
            assert record["retryable"] is True

    def test_none_context_is_noop(self) -> None:
        quote = _fake_quote()
        plan = _fake_plan()
        # should not raise
        emit_attempt_trace(
            trace_ctx=None,
            quote=quote,
            plan=plan,
            region="SG",
            domain="x.com",
            transport="opencli",
            attempt_index=1,
        )

    def test_metadata_without_fetch_metadata(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "trace.jsonl"
            writer = ScanTraceWriter(path)
            ctx = _make_trace_ctx(writer)

            quote = _fake_quote(fetch_metadata={})
            plan = _fake_plan(action=AttemptAction.ACCEPT)

            emit_attempt_trace(
                trace_ctx=ctx, quote=quote, plan=plan,
                region="CN", domain="x.com", transport="opencli", attempt_index=0,
            )
            writer.flush()

            record = json.loads(path.read_text().strip().split("\n")[0])
            assert record["elapsed_ms"] is None
            assert record["metadata"] == {}


# ── append_attempt_history ────────────────────────────────────────────────────


class AppendAttemptHistoryTests(unittest.TestCase):
    def test_appends_to_empty_history(self) -> None:
        quote = _fake_quote(region="SG", status="opencli_error", confidence=0.0)
        plan = _fake_plan(action=AttemptAction.FALLBACK_CDP, failure_class="network")

        append_attempt_history(quote, transport="opencli", attempt_index=1, plan=plan)

        assert len(quote.attempt_history) == 1
        entry = quote.attempt_history[0]
        assert entry["attempt_index"] == 1
        assert entry["transport"] == "opencli"
        assert entry["status"] == "opencli_error"
        assert entry["action"] == "fallback_cdp"
        assert entry["failure_class"] == "network"
        assert entry["elapsed_ms"] == 18021
        assert entry["phase"] == "wait_interactive"

    def test_appends_multiple_attempts(self) -> None:
        quote = _fake_quote(region="SG", status="ok", price=410.0, confidence=0.88)
        plan1 = _fake_plan(action=AttemptAction.FALLBACK_CDP)
        plan2 = _fake_plan(action=AttemptAction.FALLBACK_SCRAPLING)
        plan3 = _fake_plan(action=AttemptAction.ACCEPT)

        append_attempt_history(quote, transport="opencli", attempt_index=1, plan=plan1)
        append_attempt_history(quote, transport="cdp", attempt_index=2, plan=plan2)
        append_attempt_history(quote, transport="scrapling", attempt_index=3, plan=plan3)

        assert len(quote.attempt_history) == 3
        assert quote.attempt_history[0]["transport"] == "opencli"
        assert quote.attempt_history[1]["transport"] == "cdp"
        assert quote.attempt_history[2]["transport"] == "scrapling"


# ── merge_attempt_history ─────────────────────────────────────────────────────


class MergeAttemptHistoryTests(unittest.TestCase):
    def test_merge_prepends_source_history(self) -> None:
        source = _fake_quote(region="SG")
        source.attempt_history = [
            {"attempt_index": 1, "transport": "opencli", "action": "fallback_cdp"},
        ]
        target = _fake_quote(region="SG", price=410.0, status="ok")
        target.attempt_history = [
            {"attempt_index": 2, "transport": "cdp", "action": "accept"},
        ]

        merge_attempt_history(source, target)

        assert len(target.attempt_history) == 2
        assert target.attempt_history[0]["transport"] == "opencli"
        assert target.attempt_history[1]["transport"] == "cdp"

    def test_merge_with_empty_source(self) -> None:
        source = _fake_quote()
        source.attempt_history = []
        target = _fake_quote(price=410.0)
        target.attempt_history = [{"attempt_index": 1, "transport": "cdp", "action": "accept"}]

        merge_attempt_history(source, target)
        assert len(target.attempt_history) == 1

    def test_merge_with_empty_target(self) -> None:
        source = _fake_quote()
        source.attempt_history = [{"attempt_index": 1, "transport": "opencli", "action": "fallback_cdp"}]
        target = _fake_quote()

        merge_attempt_history(source, target)
        assert len(target.attempt_history) == 1


if __name__ == "__main__":
    unittest.main()