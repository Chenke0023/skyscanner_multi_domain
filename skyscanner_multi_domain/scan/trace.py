"""Scan trace event model and JSONL writer.

Each transport attempt produces one ScanTraceEvent written as one JSONL line.
The writer is per-scan (not a singleton) so trace files are scoped to a
specific scan run and can be placed in a configurable directory.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from threading import Lock
from typing import Any


# ── ScanTraceEvent ────────────────────────────────────────────────────────────


@dataclass
class ScanTraceEvent:
    """One structured trace record per transport attempt.

    Written as a single JSONL line.  Designed to be greppable / jq-able
    for post-hoc analysis of fallback chains, confidence gating, and
    transport reliability.
    """

    scan_id: str
    route_id: str
    origin: str
    destination: str
    depart_date: str

    region: str
    domain: str | None

    attempt_index: int
    transport: str
    status: str

    action: str
    failure_class: str | None = None
    reason: str | None = None

    price: float | None = None
    currency: str | None = None
    confidence: float | None = None
    rankable: bool | None = None
    result_visibility: str | None = None
    requires_manual_review: bool | None = None

    elapsed_ms: int | None = None
    retryable: bool | None = None

    url: str | None = None
    phase: str | None = None

    failure_log_path: str | None = None

    metadata: dict[str, Any] = field(default_factory=dict)

    timestamp: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )

    schema_version: int = 1

    def to_json_dict(self) -> dict[str, Any]:
        return asdict(self)


# ── ScanTraceWriter ───────────────────────────────────────────────────────────


class ScanTraceWriter:
    """Per-scan JSONL writer — not a singleton.

    Each scan run gets its own writer pointed at a specific file path.
    Writes are buffered and flushed every 50 lines or on explicit flush().
    """

    def __init__(self, path: Path | None) -> None:
        self.path = path
        self._lock = Lock()
        self._buf: list[str] = []

        if self.path is not None:
            self.path.parent.mkdir(parents=True, exist_ok=True)

    def write(self, event: ScanTraceEvent) -> None:
        if self.path is None:
            return

        line = json.dumps(
            event.to_json_dict(),
            ensure_ascii=False,
            sort_keys=True,
            default=str,
        )

        with self._lock:
            self._buf.append(line)
            if len(self._buf) >= 50:
                self._flush_unlocked()

    def flush(self) -> None:
        if self.path is None:
            return
        with self._lock:
            self._flush_unlocked()

    def _flush_unlocked(self) -> None:
        if not self._buf:
            return
        batch = self._buf
        self._buf = []
        with self.path.open("a", encoding="utf-8") as f:  # type: ignore[union-attr]
            f.write("\n".join(batch) + "\n")


# ── ScanTraceContext ──────────────────────────────────────────────────────────


@dataclass
class ScanTraceContext:
    """Lightweight context carried through a single scan run.

    Bundles the identifiers that every trace event needs plus the writer
    so we don't thread a dozen individual parameters through the orchestrator.
    """

    scan_id: str
    route_id: str
    origin: str
    destination: str
    depart_date: str
    writer: ScanTraceWriter


# ── Trace emission helpers ────────────────────────────────────────────────────


def emit_attempt_trace(
    *,
    trace_ctx: ScanTraceContext | None,
    quote: Any,  # FlightQuote — lazy import to avoid circular deps
    plan: Any,  # AttemptPlan
    region: str,
    domain: str | None,
    transport: str,
    attempt_index: int,
    url: str | None = None,
) -> None:
    """Emit a single trace event from orchestrator context.

    Transport code should NOT call this directly — only the orchestrator
    has the full picture (attempt_index, region, planner action, etc.).
    """
    if trace_ctx is None:
        return

    metadata = dict(getattr(quote, "fetch_metadata", None) or {})

    event = ScanTraceEvent(
        scan_id=trace_ctx.scan_id,
        route_id=trace_ctx.route_id,
        origin=trace_ctx.origin,
        destination=trace_ctx.destination,
        depart_date=trace_ctx.depart_date,

        region=region,
        domain=domain,

        attempt_index=attempt_index,
        transport=transport,
        status=getattr(quote, "status", ""),

        action=getattr(plan, "action", None) and plan.action.value,
        failure_class=getattr(plan, "failure_class", None),
        reason=getattr(plan, "reason", None),

        price=getattr(quote, "price", None),
        currency=getattr(quote, "currency", None),
        confidence=getattr(quote, "confidence", None),
        rankable=getattr(quote, "rankable", None),
        result_visibility=getattr(quote, "result_visibility", None),
        requires_manual_review=getattr(plan, "manual_review_required", None),

        elapsed_ms=metadata.get("elapsed_ms"),
        retryable=metadata.get("retryable"),
        url=url or getattr(quote, "source_url", None),
        phase=metadata.get("phase"),

        failure_log_path=getattr(quote, "debug_log_path", None),

        metadata=metadata,
    )

    trace_ctx.writer.write(event)


def append_attempt_history(
    quote: Any,  # FlightQuote
    *,
    transport: str,
    attempt_index: int,
    plan: Any,  # AttemptPlan
) -> None:
    """Append a lightweight attempt summary to the quote for UI display.

    Does NOT include large fields like page_text.  Only stores enough for
    the UI to render a fallback chain summary.
    """
    metadata = dict(getattr(quote, "fetch_metadata", None) or {})

    entry: dict[str, Any] = {
        "attempt_index": attempt_index,
        "transport": transport,
        "status": getattr(quote, "status", ""),
        "action": getattr(plan, "action", None) and plan.action.value,
        "failure_class": getattr(plan, "failure_class", None),
        "reason": getattr(plan, "reason", None),
        "confidence": getattr(quote, "confidence", None),
        "rankable": getattr(quote, "rankable", None),
        "elapsed_ms": metadata.get("elapsed_ms"),
        "phase": metadata.get("phase"),
        "retryable": metadata.get("retryable"),
    }

    history = getattr(quote, "attempt_history", None)
    if history is None:
        quote.attempt_history = []
    quote.attempt_history.append(entry)


def merge_attempt_history(
    source_quote: Any,  # FlightQuote
    target_quote: Any,  # FlightQuote
) -> None:
    """Merge attempt history from source into target.

    When a fallback transport succeeds, the successful quote should
    inherit the failure history of prior attempts so the UI can show
    the full chain.
    """
    source_history = list(getattr(source_quote, "attempt_history", []) or [])
    target_history = list(getattr(target_quote, "attempt_history", []) or [])

    target_quote.attempt_history = source_history + target_history