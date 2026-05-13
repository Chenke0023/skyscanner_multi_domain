from __future__ import annotations

from typing import Any

from skyscanner_multi_domain.models import QuoteEvidence, RegionConfig
from skyscanner_multi_domain.parsing.structured_price_scanner import (
    StructuredPriceCandidate,
    scan_price_like_objects,
)


def find_price_like_objects(payload: Any) -> list[dict[str, Any]]:
    return [candidate.to_dict() for candidate in scan_price_like_objects(payload)]


def enrich_network_candidates(payloads: list[Any]) -> list[dict[str, Any]]:
    enriched: list[dict[str, Any]] = []
    for payload_index, payload in enumerate(payloads):
        for candidate in scan_price_like_objects(payload):
            item = candidate.to_dict()
            item["payload_index"] = payload_index
            item["layer"] = "network"
            enriched.append(item)
    return enriched


def _candidate_to_evidence(
    region: RegionConfig,
    source_url: str,
    candidate: StructuredPriceCandidate,
    payload_index: int,
    candidate_index: int,
) -> QuoteEvidence | None:
    if not candidate.accepted or candidate.price is None:
        return None
    confidence = min(max(candidate.confidence + 0.08, 0.0), 0.92)
    return QuoteEvidence(
        layer="network",
        price=candidate.price,
        currency=candidate.currency or region.currency,
        label="unknown",
        source_url=source_url,
        raw_ref=f"network:{payload_index}:{candidate_index}:{candidate.path}",
        confidence=confidence,
    )


def parse_network_json(
    region: RegionConfig,
    source_url: str,
    payloads: list[Any],
) -> list[QuoteEvidence]:
    evidences: list[QuoteEvidence] = []
    for payload_index, payload in enumerate(payloads):
        for candidate_index, candidate in enumerate(scan_price_like_objects(payload)):
            evidence = _candidate_to_evidence(region, source_url, candidate, payload_index, candidate_index)
            if evidence is not None:
                evidences.append(evidence)
    return evidences
