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

    # Structured metadata: phase, retryability, subprocess details, etc.
    metadata: dict[str, Any] = field(default_factory=dict)

    @property
    def ok(self) -> bool:
        return self.error is None and bool(self.page_text)

    @property
    def retryable(self) -> bool:
        return self.metadata.get("retryable", self.error is not None)


# ── AttemptAction ─────────────────────────────────────────────────────────────


class AttemptAction(Enum):
    ACCEPT = "accept"              # quote is good, stop trying
    ACCEPT_WITH_REVIEW = "accept_with_review"   # accept but mark for manual review
    FALLBACK_CDP = "fallback_cdp"
    FALLBACK_SCRAPLING = "fallback_scrapling"
    FALLBACK_GOOGLE_JUMP = "fallback_google_jump"
    MANUAL_REVIEW = "manual_review"  # requires user action (challenge manual mode)
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


def _enum_value(value: Any) -> str:
    """Coerce a config field that may be an Enum or plain string into its value."""
    return getattr(value, "value", str(value))


def apply_quote_trust_policy(
    quote: "FlightQuote",
    *,
    config: Any | None = None,
) -> "FlightQuote":
    """Annotate a quote with rankable / result_visibility / requires_manual_review.

    The annotations are derived from the quote's confidence and the user's
    low_confidence_policy.  They do NOT change the planner's action — that's
    the planner's job — but they DO control how the quote is displayed and
    whether it counts toward "best rankable" in reports.

    Idempotent: calling it twice with the same config produces the same result.
    """
    challenge_policy = "stop"
    rankable_threshold = 0.80
    review_threshold = 0.50
    low_conf_policy = "fallback"

    if config is not None:
        rankable_threshold = float(getattr(config, "rankable_confidence", 0.80))
        review_threshold = float(getattr(config, "review_confidence", 0.50))
        low_conf_policy = _enum_value(
            getattr(config, "low_confidence_policy", "fallback")
        )
        challenge_policy = _enum_value(
            getattr(config, "challenge_policy", "stop")
        )

    # Challenge quotes (regardless of price): manual policy surfaces them for
    # user action, stop policy hides them.
    if quote.status in ("page_challenge", "px_challenge", "captcha_solve_failed"):
        quote.rankable = False
        if challenge_policy == "manual":
            quote.result_visibility = "visible"
            quote.requires_manual_review = True
        else:
            quote.result_visibility = "hidden"
            quote.requires_manual_review = False
        return quote

    if quote.price is None:
        # Failed quotes carry no displayable price; leave defaults alone so
        # the report layer hides them by virtue of price=None.
        return quote

    conf = quote.confidence or 0.0

    if conf >= rankable_threshold:
        quote.rankable = True
        quote.result_visibility = "visible"
        quote.requires_manual_review = False
        return quote

    quote.rankable = False
    if low_conf_policy == "show":
        quote.result_visibility = "visible"
        quote.requires_manual_review = False
    elif low_conf_policy == "hide":
        quote.result_visibility = "debug_only"
        quote.requires_manual_review = False
    elif low_conf_policy == "accept-review":
        quote.result_visibility = "visible"
        quote.requires_manual_review = True
    else:  # "fallback" or unknown
        quote.result_visibility = "fallback_candidate"
        quote.requires_manual_review = conf < review_threshold

    return quote


class AttemptPlanner:
    """Centralized decision engine: given a FlightQuote, decide the next action.

    Replaces the scattered `if quote.price is not None: continue` guards
    and ad-hoc fallback routing that was previously in orchestrator.py.

    The planner always uses `decide_fallback()` from the fallback router,
    ensuring semantic_mismatch, challenge, no_flights etc. are handled
    consistently regardless of which transport produced the quote.

    If a ScanConfig is provided at construction time, all plan() calls
    automatically use it — no need to thread config through every call site.
    """

    def __init__(self, config: Any | None = None) -> None:
        self._config = config

    def plan(self, quote: "FlightQuote") -> AttemptPlan:
        from skyscanner_multi_domain.scan.fallback_router import (
            classify_quote_failure,
            decide_fallback,
        )

        cfg = self._config
        min_conf = 0.5
        challenge_policy = "stop"
        low_confidence_policy = "fallback"

        if cfg is not None:
            min_conf = float(getattr(cfg, "rankable_confidence", 0.5))
            challenge_policy = _enum_value(getattr(cfg, "challenge_policy", "stop"))
            low_confidence_policy = _enum_value(
                getattr(cfg, "low_confidence_policy", "fallback")
            )

        # Annotate the quote with trust attributes before deciding next action.
        # The plan action and quote attributes are decoupled: action says what to
        # do next, attributes say how to display/rank the price if it's accepted.
        apply_quote_trust_policy(quote, config=cfg)

        fc = classify_quote_failure(quote, min_parser_confidence=min_conf)
        decision = decide_fallback(
            quote,
            min_parser_confidence=min_conf,
            challenge_policy=challenge_policy,
        )

        base = AttemptPlan(
            action=AttemptAction.TERMINAL,
            failure_class=fc,
            reason=decision.reason,
            confidence=0.0,
            manual_review_required=decision.manual_review_required,
            max_attempts=decision.max_attempts,
        )

        # Low-confidence + non-fallback policies always accept the quote. The
        # quote's result_visibility/requires_manual_review (set by trust policy)
        # carry the show/hide/accept-review distinction.
        if fc == "low_confidence" and low_confidence_policy != "fallback":
            return AttemptPlan(
                action=AttemptAction.ACCEPT_WITH_REVIEW,
                failure_class=fc,
                reason=f"Low confidence price accepted (policy={low_confidence_policy})",
                confidence=quote.confidence or 0.0,
                manual_review_required=(low_confidence_policy == "accept-review"),
            )

        if not decision.should_fallback:
            if fc == "success":
                return AttemptPlan(
                    action=AttemptAction.ACCEPT,
                    failure_class=fc,
                    reason="Price found and passed sanity checks",
                    confidence=quote.confidence or 0.9,
                )
            if fc == "challenge" and challenge_policy == "manual":
                return AttemptPlan(
                    action=AttemptAction.MANUAL_REVIEW,
                    failure_class=fc,
                    reason=decision.reason,
                    manual_review_required=True,
                )
            return base

        if fc == "challenge" and challenge_policy == "manual":
            return AttemptPlan(
                action=AttemptAction.MANUAL_REVIEW,
                failure_class=fc,
                reason=decision.reason,
                manual_review_required=True,
            )

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
    quote.fetch_metadata = dict(attempt.metadata)

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
        quote.fetch_metadata = dict(attempt.metadata)

    # Apply readiness evidence from transport (opencli-specific classification)
    readiness = attempt.evidence.get("readiness")
    no_flights_conf = attempt.evidence.get("no_flights_confidence", 0.0)
    if readiness == "challenge" and quote.status not in ("px_challenge", "page_challenge"):
        quote.status = "page_challenge"
        quote.error = "OpenCLI page readiness classified the page as a challenge"
    elif readiness == "no_flights" and no_flights_conf >= 0.8:
        quote.status = "opencli_no_flights"
        quote.error = (
            f"OpenCLI page readiness classified the page as no flights "
            f"(confidence {no_flights_conf:.2f}, terminal)"
        )

    # Propagate transport error if no price (transport knows better than parser)
    if quote.price is None and attempt.error:
        quote.error = attempt.error[:200]

    return quote
