"""Unified fetch result type and attempt planner.

Transport layer (opencli / cdp / scrapling) returns FetchAttempt — a raw
fetch result with page_text and metadata but NO quote parsing.  The
orchestrator converts FetchAttempt → FlightQuote via page_parser, then
feeds the quote to AttemptPlanner to decide what happens next.

This decouples "fetch once" from "parse + decide next step", which were
previously tangled inside each transport.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Optional


# ── FetchAttempt ──────────────────────────────────────────────────────────────


@dataclass
class FetchAttempt:
    """Raw result of a single transport fetch — no quote parsing."""

    transport: str                           # "opencli" | "cdp" | "scrapling"
    region_code: str
    url: str
    page_text: str = ""
    error: Optional[str] = None
    elapsed_ms: int = 0

    # Transport-specific raw payload (e.g. opencli extract JSON, cdp eval result)
    raw_payload: Optional[dict[str, Any]] = None

    # Evidence block: what the transport observed about the page
    evidence: dict[str, Any] = field(default_factory=dict)

    @property
    def ok(self) -> bool:
        return self.error is None and bool(self.page_text)


# ── AttemptAction ─────────────────────────────────────────────────────────────


class AttemptAction(Enum):
    ACCEPT = "accept"              # quote is good, stop trying
    FALLBACK_CDP = "fallback_cdp"
    FALLBACK_SCRAPLING = "fallback_scrapling"
    FALLBACK_GOOGLE_JUMP = "fallback_google_jump"
    TERMINAL = "terminal"          # no price, but no point retrying (challenge, no_flights, etc.)


@dataclass
class AttemptPlan:
    """What to do after a fetch attempt."""

    action: AttemptAction
    failure_class: str = ""           # e.g. "success", "semantic_mismatch", "challenge"
    reason: str = ""
    transports_remaining: list[str] = field(default_factory=list)
    confidence: float = 1.0
    manual_review_required: bool = False
    max_attempts: int = 1


# ── AttemptPlanner ────────────────────────────────────────────────────────────


class AttemptPlanner:
    """Centralized decision engine: given a FlightQuote, decide the next action.

    Replaces the scattered `if quote.price is not None: continue` guards
    and ad-hoc fallback routing that was previously in orchestrator.py.

    The planner always uses `decide_fallback()` from the fallback router,
    ensuring semantic_mismatch, challenge, no_flights etc. are handled
    consistently regardless of which transport produced the quote.
    """

    def plan(self, quote: "FlightQuote") -> AttemptPlan:
        from skyscanner_multi_domain.scan.fallback_router import (
            classify_quote_failure,
            decide_fallback,
        )

        fc = classify_quote_failure(quote)
        decision = decide_fallback(quote)

        base = AttemptPlan(
            action=AttemptAction.TERMINAL,
            failure_class=fc,
            reason=decision.reason,
            confidence=0.0,
            manual_review_required=decision.manual_review_required,
            max_attempts=decision.max_attempts,
        )

        if not decision.should_fallback:
            if fc == "success":
                return AttemptPlan(
                    action=AttemptAction.ACCEPT,
                    failure_class=fc,
                    reason="Price found and passed sanity checks",
                    confidence=quote.confidence or 0.9,
                )
            return base

        # Map router transports to AttemptAction
        remaining = decision.transports
        if not remaining:
            return base

        primary = remaining[0]
        if primary == "google_jump":
            return AttemptPlan(
                action=AttemptAction.FALLBACK_GOOGLE_JUMP,
                failure_class=fc,
                reason=decision.reason,
                transports_remaining=remaining,
                manual_review_required=decision.manual_review_required,
                max_attempts=decision.max_attempts,
            )
        if primary == "cdp":
            return AttemptPlan(
                action=AttemptAction.FALLBACK_CDP,
                failure_class=fc,
                reason=decision.reason,
                transports_remaining=remaining,
                manual_review_required=decision.manual_review_required,
                max_attempts=decision.max_attempts,
            )
        if primary == "scrapling":
            return AttemptPlan(
                action=AttemptAction.FALLBACK_SCRAPLING,
                failure_class=fc,
                reason=decision.reason,
                transports_remaining=remaining,
                manual_review_required=decision.manual_review_required,
                max_attempts=decision.max_attempts,
            )

        return base


def fetch_attempt_to_quote(
    attempt: FetchAttempt,
    region: "RegionConfig",
    fallback_url: str = "",
) -> "FlightQuote":
    """Convert a FetchAttempt into a FlightQuote via page_parser.

    This is the single place where raw page text becomes a quote.
    Transport code should NOT call extract_page_quote directly.
    """
    from skyscanner_multi_domain.models import FlightQuote
    from skyscanner_multi_domain.parsing.page_parser import extract_page_quote

    url = attempt.url or fallback_url
    quote = extract_page_quote(region, url, attempt.page_text)
    quote.source_kind = attempt.transport

    if quote.price is not None:
        return quote

    # Captcha check
    from skyscanner_multi_domain.parsing.challenge import (
        build_captcha_quote,
        check_captcha_in_page,
    )
    from types import SimpleNamespace

    has_captcha, captcha_type = check_captcha_in_page(
        attempt.page_text,
        SimpleNamespace(url=url),
    )
    if has_captcha:
        quote = build_captcha_quote(
            region,
            url,
            captcha_type,
            source_label=attempt.transport,
        )
        quote.source_kind = attempt.transport

    # Propagate transport error if no price
    if quote.price is None and attempt.error and not quote.error:
        quote.error = attempt.error[:200]

    return quote
