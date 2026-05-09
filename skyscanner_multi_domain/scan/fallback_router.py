"""Failure-aware fallback router — routes failed quotes to appropriate recovery transports.

Principle: not all failures benefit from automatic retry. This module classifies
failures and decides which fallback transports to try, if any, without creating
redundant request chains.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from skyscanner_multi_domain.models import FlightQuote

# ── Failure classification taxonomy ──────────────────────────────────────────

FailureClass = str  # one of the keys in FAILURE_CLASS_MAP

# Prices with confidence below this threshold are treated as unreliable
# and routed to fallback transports for verification.
MIN_PARSER_CONFIDENCE = 0.5

FAILURE_CLASS_MAP: dict[str, str] = {
    # Success — no fallback needed
    "price_found": "success",
    # Terminal — no fallback, accept result
    "px_challenge": "challenge",
    "page_challenge": "challenge",
    "opencli_no_flights": "no_flights",
    "page_no_flights": "no_flights",
    "page_unsupported_route": "unsupported",
    "page_region_redirect": "redirect",
    "page_missing": "browser_missing",
    "page_missing_ws": "browser_missing",
    # Recoverable — can fallback
    "opencli_timeout": "timeout",
    "opencli_error": "network",
    "scrapling_failed": "network",
    "opencli_failed": "network",
    "page_parse_failed": "parse",
    "scrapling_parse_failed": "parse",
    "page_loading": "loading",
    "page_eval_error": "transport_error",
    "scrapling_unavailable": "transport_error",
    "captcha_solve_failed": "challenge",
    "page_empty_shell": "empty_shell",
    "opencli_not_attempted": "not_attempted",
    # Confidence-gated recoverable
    "low_confidence": "low_confidence",
    # Fallback
    "request_error": "network",
    "invalid_transport": "transport_error",
}

# ── Fallback decision data ───────────────────────────────────────────────────


@dataclass
class FallbackDecision:
    should_fallback: bool
    transports: list[str] = field(default_factory=list)
    reason: str = ""
    max_attempts: int = 1
    manual_review_required: bool = False


# ── Decision table ──────────────────────────────────────────────────────────

# Per failure_class, decide what to do
_DECISION_TABLE: dict[str, FallbackDecision] = {
    "success": FallbackDecision(
        should_fallback=False,
        reason="Price already found",
    ),
    "semantic_mismatch": FallbackDecision(
        should_fallback=True,
        transports=["cdp", "scrapling"],
        reason="Parsed price failed route/date/currency sanity check — try CDP and Scrapling",
        max_attempts=2,
    ),
    "no_flights": FallbackDecision(
        should_fallback=False,
        reason="Search completed with no itinerary results — terminal",
    ),
    "challenge": FallbackDecision(
        should_fallback=False,
        reason="Captcha/challenge detected — requires manual review, not automatic retry",
        manual_review_required=True,
    ),
    "unsupported": FallbackDecision(
        should_fallback=False,
        reason="Route not supported by this market — terminal",
    ),
    "redirect": FallbackDecision(
        should_fallback=False,
        reason="Region redirect detected — terminal for this market",
    ),
    "browser_missing": FallbackDecision(
        should_fallback=False,
        reason="No browser tab available — environment issue, not recoverable via retry",
    ),
    "timeout": FallbackDecision(
        should_fallback=True,
        transports=["cdp"],
        reason="Page load timeout — fallback to CDP page read",
        max_attempts=1,
    ),
    "network": FallbackDecision(
        should_fallback=True,
        transports=["google_jump", "cdp", "scrapling"],
        reason="Network/transport error — try Google jump then CDP then Scrapling",
        max_attempts=3,
    ),
    "loading": FallbackDecision(
        should_fallback=True,
        transports=["cdp"],
        reason="Page still rendering — try reading via CDP",
        max_attempts=1,
    ),
    "parse": FallbackDecision(
        should_fallback=True,
        transports=["cdp", "scrapling"],
        reason="Price not found in parsed text — try CDP DOM read then Scrapling",
        max_attempts=2,
    ),
    "empty_shell": FallbackDecision(
        should_fallback=True,
        transports=["cdp", "scrapling"],
        reason="Near-empty page shell — try CDP and Scrapling",
        max_attempts=2,
    ),
    "transport_error": FallbackDecision(
        should_fallback=True,
        transports=["cdp", "scrapling"],
        reason="Transport internal error — try CDP and Scrapling",
        max_attempts=2,
    ),
    "not_attempted": FallbackDecision(
        should_fallback=True,
        transports=["cdp", "scrapling"],
        reason="Region was never attempted — try CDP and Scrapling",
        max_attempts=2,
    ),
    "low_confidence": FallbackDecision(
        should_fallback=True,
        transports=["cdp", "scrapling"],
        reason="Parsed price confidence below threshold — verify with alternative transports",
        max_attempts=2,
    ),
}

_DEFAULT_DECISION = FallbackDecision(
    should_fallback=False,
    reason="Unknown failure class — no automatic fallback",
    manual_review_required=True,
)


# ── Public API ───────────────────────────────────────────────────────────────


def classify_quote_failure(quote: FlightQuote) -> FailureClass:
    """Map a FlightQuote's status to a failure class."""
    if quote.status == "page_semantic_mismatch":
        return "semantic_mismatch"
    if quote.price is not None:
        if (quote.confidence or 0.0) < MIN_PARSER_CONFIDENCE:
            return "low_confidence"
        return "success"
    return FAILURE_CLASS_MAP.get(quote.status, "other")


def decide_fallback(quote: FlightQuote) -> FallbackDecision:
    """Given a failed quote, decide whether and how to fallback."""
    fc = classify_quote_failure(quote)
    decision = _DECISION_TABLE.get(fc, _DEFAULT_DECISION)

    # If we've already exhausted the transports in prior attempts, don't re-try
    tried_transports = {
        attempt.get("transport", "")
        for attempt in (quote.fallback_attempts or [])
    }
    remaining = [t for t in decision.transports if t not in tried_transports]

    if not remaining and decision.should_fallback:
        return FallbackDecision(
            should_fallback=False,
            reason=f"All fallback transports already attempted: {decision.transports}",
            manual_review_required=decision.manual_review_required,
        )

    return FallbackDecision(
        should_fallback=decision.should_fallback and bool(remaining),
        transports=remaining,
        reason=decision.reason,
        max_attempts=min(decision.max_attempts, len(remaining)),
        manual_review_required=decision.manual_review_required,
    )


def should_skip_automatic_retry(quote: FlightQuote) -> bool:
    """Quick check: should we skip all automatic retries for this quote?"""
    decision = decide_fallback(quote)
    return not decision.should_fallback


def build_fallback_telemetry(quotes: list[FlightQuote]) -> dict[str, Any]:
    """Build telemetry summary about fallback decisions across a batch of quotes."""
    skipped_challenge = 0
    skipped_no_flights = 0
    skipped_other = 0
    routed_to_cdp = 0
    routed_to_scrapling = 0
    manual_review = 0
    low_confidence_count = 0

    for quote in quotes:
        decision = decide_fallback(quote)
        fc = classify_quote_failure(quote)

        if not decision.should_fallback:
            if fc == "challenge":
                skipped_challenge += 1
            elif fc == "no_flights":
                skipped_no_flights += 1
            else:
                skipped_other += 1
        else:
            if "cdp" in decision.transports:
                routed_to_cdp += 1
            if "scrapling" in decision.transports:
                routed_to_scrapling += 1

        if fc == "low_confidence":
            low_confidence_count += 1

        if decision.manual_review_required:
            manual_review += 1

    return {
        "fallback_skipped_challenge_count": skipped_challenge,
        "fallback_skipped_no_flights_count": skipped_no_flights,
        "fallback_skipped_other_count": skipped_other,
        "fallback_routed_to_cdp_count": routed_to_cdp,
        "fallback_routed_to_scrapling_count": routed_to_scrapling,
        "fallback_manual_review_required_count": manual_review,
        "fallback_low_confidence_count": low_confidence_count,
    }
