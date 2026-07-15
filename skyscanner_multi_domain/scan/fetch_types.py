"""Trust policy and trace decision for one direct-CDP attempt."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from enum import Enum
from typing import TYPE_CHECKING, Any

from skyscanner_multi_domain.route_validation import clear_quote_prices, is_semantic_mismatch

if TYPE_CHECKING:
    from skyscanner_multi_domain.models import FlightQuote


UNRANKABLE_PRICE_SOURCES = frozenset({"first_price_fallback"})


class AttemptAction(Enum):
    ACCEPT = "accept"
    ACCEPT_WITH_REVIEW = "accept_with_review"
    MANUAL_REVIEW = "manual_review"
    TERMINAL = "terminal"


@dataclass
class AttemptPlan:
    action: AttemptAction
    failure_class: str = ""
    reason: str = ""
    confidence: float = 1.0
    manual_review_required: bool = False
    max_attempts: int = 1


def _value(value: Any) -> str:
    return str(getattr(value, "value", value))


def is_decision_eligible(row: Mapping[str, object]) -> bool:
    """Keep weak or explicitly unrankable prices out of price decisions."""
    if is_semantic_mismatch(row):
        return False
    if row.get("decision_eligible") is False or row.get("rankable") is False:
        return False
    if str(row.get("price_source") or "").strip() in UNRANKABLE_PRICE_SOURCES:
        return False
    confidence = row.get("confidence")
    return not (
        row.get("rankable") is None
        and isinstance(confidence, (int, float))
        and not isinstance(confidence, bool)
        and float(confidence) < 0.80
    )


def apply_quote_trust_policy(quote: "FlightQuote", *, config: Any | None = None) -> "FlightQuote":
    rankable_threshold = float(getattr(config, "rankable_confidence", 0.80))
    policy = _value(getattr(config, "low_confidence_policy", "accept-review"))
    challenge_policy = _value(getattr(config, "challenge_policy", "stop"))

    if is_semantic_mismatch(quote):
        clear_quote_prices(quote)
        quote.rankable = False
        quote.result_visibility = "hidden"
        quote.requires_manual_review = False
    elif quote.status in {"page_challenge", "px_challenge"}:
        quote.rankable = False
        quote.result_visibility = "visible" if challenge_policy == "manual" else "hidden"
        quote.requires_manual_review = challenge_policy == "manual"
    elif quote.price is None:
        quote.rankable = False
        quote.result_visibility = "hidden"
        quote.requires_manual_review = False
    elif quote.price_source in UNRANKABLE_PRICE_SOURCES:
        quote.rankable = False
        quote.result_visibility = "hidden" if policy == "hide" else "visible"
        quote.requires_manual_review = policy == "accept-review"
    elif (quote.confidence if quote.confidence is not None else 1.0) >= rankable_threshold:
        quote.rankable = True
        quote.result_visibility = "visible"
        quote.requires_manual_review = False
    else:
        quote.rankable = False
        quote.result_visibility = "hidden" if policy == "hide" else "visible"
        quote.requires_manual_review = policy == "accept-review"
    return quote


class AttemptPlanner:
    def __init__(self, config: Any | None = None) -> None:
        self.config = config

    def plan(self, quote: "FlightQuote") -> AttemptPlan:
        apply_quote_trust_policy(quote, config=self.config)
        confidence = quote.confidence if quote.confidence is not None else (1.0 if quote.price is not None else 0.0)
        if quote.status in {"page_challenge", "px_challenge"}:
            manual = quote.requires_manual_review
            return AttemptPlan(
                AttemptAction.MANUAL_REVIEW if manual else AttemptAction.TERMINAL,
                "challenge",
                quote.error or "Browser challenge requires user action",
                confidence,
                manual,
            )
        if is_semantic_mismatch(quote):
            return AttemptPlan(
                AttemptAction.TERMINAL,
                "semantic_mismatch",
                quote.error or "Route/date validation failed",
                confidence,
            )
        if quote.price is None:
            return AttemptPlan(AttemptAction.TERMINAL, "failure", quote.error or quote.status, confidence)
        if quote.rankable:
            return AttemptPlan(AttemptAction.ACCEPT, "success", "Price found", confidence)
        return AttemptPlan(
            AttemptAction.ACCEPT_WITH_REVIEW,
            "low_confidence",
            "Low-confidence price retained from direct CDP",
            confidence,
            quote.requires_manual_review,
        )
