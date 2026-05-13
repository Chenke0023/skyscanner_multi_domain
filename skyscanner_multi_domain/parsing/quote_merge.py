from __future__ import annotations

from dataclasses import asdict
from typing import Literal

from skyscanner_multi_domain.models import (
    FlightQuote,
    QuoteEvidence,
    RegionConfig,
    StructuredQuoteResult,
)


def price_close(left: float, right: float) -> bool:
    return abs(left - right) <= max(2.0, min(left, right) * 0.02)


def best_evidence(
    evidences: list[QuoteEvidence],
    layer: str | None = None,
) -> QuoteEvidence | None:
    candidates = [e for e in evidences if e.price is not None]
    if layer is not None:
        candidates = [e for e in candidates if e.layer == layer]
    if not candidates:
        return None
    return max(candidates, key=lambda e: e.confidence)


def _quote_from_evidence(
    region: RegionConfig,
    source_url: str,
    evidence: QuoteEvidence | None,
    *,
    status: str,
    confidence: float,
    error: str | None = None,
) -> FlightQuote:
    quote = FlightQuote(
        region=region.code,
        domain=region.domain,
        price=evidence.price if evidence is not None else None,
        currency=evidence.currency if evidence is not None else region.currency,
        source_url=source_url,
        status=status,
        error=error,
        confidence=confidence,
    )
    quote.source_kind = "cdp_structured"
    if evidence is not None:
        quote.price_source = f"{evidence.layer}:{evidence.label or 'unknown'}"
        quote.evidence_text = evidence.raw_ref
        quote.best_price = evidence.price if evidence.label == "best" else None
        quote.cheapest_price = evidence.price if evidence.label == "cheapest" else evidence.price
    quote.fetch_metadata = {
        "structured_confidence": status,
        "evidence_count": 0,
    }
    return quote


def resolve_quote(
    region: RegionConfig,
    source_url: str,
    evidences: list[QuoteEvidence],
) -> StructuredQuoteResult:
    network = best_evidence(evidences, "network")
    hydration = best_evidence(evidences, "hydration")
    dom = best_evidence(evidences, "dom")
    text = best_evidence(evidences, "text")
    decision_trace = [
        f"network: found {sum(1 for e in evidences if e.layer == 'network' and e.price is not None)} usable candidates",
        f"hydration: found {sum(1 for e in evidences if e.layer == 'hydration' and e.price is not None)} usable candidates",
        f"dom: found {sum(1 for e in evidences if e.layer == 'dom' and e.price is not None)} usable candidates",
        f"text: found {sum(1 for e in evidences if e.layer == 'text' and e.price is not None)} usable candidates",
    ]

    selected: QuoteEvidence | None = None
    confidence: Literal["high", "medium", "low", "failed"] = "failed"
    conflict_reason: str | None = None

    if network and dom and price_close(network.price or 0, dom.price or 0):
        selected = max([network, dom], key=lambda e: e.confidence)
        confidence = "high"
        decision_trace.append("merge: network and dom agree within tolerance")
    elif hydration and dom and price_close(hydration.price or 0, dom.price or 0):
        selected = max([hydration, dom], key=lambda e: e.confidence)
        confidence = "high"
        decision_trace.append("merge: hydration and dom agree within tolerance")
    elif network and dom:
        selected = max([network, dom], key=lambda e: e.confidence)
        confidence = "medium"
        conflict_reason = "network_dom_conflict"
        decision_trace.append("merge: network and dom conflict")
    elif network:
        selected = network
        confidence = "medium"
        conflict_reason = "network_only"
        decision_trace.append("merge: selected network evidence only")
    elif hydration:
        selected = hydration
        confidence = "medium"
        conflict_reason = "hydration_only"
        decision_trace.append("merge: selected hydration evidence only")
    elif dom:
        selected = dom
        confidence = "medium"
        conflict_reason = "dom_only"
        decision_trace.append("merge: selected dom evidence only")
    elif text:
        selected = text
        confidence = "low"
        conflict_reason = "text_fallback"
        decision_trace.append("merge: selected text fallback evidence")

    if selected is None:
        decision_trace.append("final: no usable evidence, confidence=failed")
        quote = _quote_from_evidence(
            region,
            source_url,
            None,
            status="cdp_structured_parse_failed",
            confidence=0.0,
            error="No structured price evidence found",
        )
    else:
        decision_trace.append(
            f"final: selected {selected.layer} evidence, confidence={confidence}"
        )
        quote = _quote_from_evidence(
            region,
            source_url,
            selected,
            status="price_found",
            confidence=selected.confidence,
            error=conflict_reason,
        )
    quote.fetch_metadata = {
        "structured_confidence": confidence,
        "conflict_reason": conflict_reason,
        "evidence_count": len(evidences),
        "evidences": [asdict(e) for e in evidences[:20]],
        "decision_trace": decision_trace,
    }
    return StructuredQuoteResult(
        final_quote=quote,
        evidences=evidences,
        confidence=confidence,
        conflict_reason=conflict_reason,
        decision_trace=decision_trace,
    )
