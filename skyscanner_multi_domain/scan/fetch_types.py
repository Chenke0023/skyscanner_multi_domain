"""Trust policy and trace decision for one direct-CDP attempt."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from skyscanner_multi_domain.models import FlightQuote


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


def apply_quote_trust_policy(quote: "FlightQuote", *, config: Any | None = None) -> "FlightQuote":
    rankable_threshold = float(getattr(config, "rankable_confidence", 0.80))
    policy = _value(getattr(config, "low_confidence_policy", "accept-review"))
    challenge_policy = _value(getattr(config, "challenge_policy", "stop"))

    if quote.status in {"page_challenge", "px_challenge"}:
        quote.rankable = False
        quote.result_visibility = "visible" if challenge_policy == "manual" else "hidden"
        quote.requires_manual_review = challenge_policy == "manual"
    elif quote.price is None:
        quote.rankable = False
        quote.result_visibility = "hidden"
        quote.requires_manual_review = False
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
