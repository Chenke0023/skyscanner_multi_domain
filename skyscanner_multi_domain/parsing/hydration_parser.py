from __future__ import annotations

import json
import re
from typing import Any

from skyscanner_multi_domain.models import QuoteEvidence, RegionConfig
from skyscanner_multi_domain.parsing.structured_price_scanner import scan_price_like_objects


def extract_json_blobs(text: str, *, max_text_len: int = 200_000) -> list[Any]:
    blobs: list[Any] = []
    stripped = (text or "").strip()
    if not stripped:
        return blobs
    if len(stripped) > max_text_len and not stripped.startswith(("{", "[")):
        return blobs
    candidates = [stripped[:max_text_len]] if stripped.startswith(("{", "[")) else []
    candidates.extend(_script_assignment_json_candidates(stripped[:max_text_len]))
    for candidate in candidates:
        try:
            blobs.append(json.loads(candidate))
        except (TypeError, json.JSONDecodeError):
            continue
    return blobs


def _script_assignment_json_candidates(text: str) -> list[str]:
    candidates: list[str] = []
    for match in re.finditer(r"(?:__NEXT_DATA__|__INITIAL_STATE__|apolloState|flight|itinerary|price|marketingContext)", text, re.I):
        start = text.find("{", match.start())
        if start == -1:
            continue
        depth = 0
        in_string = False
        quote_char = ""
        escape = False
        for index in range(start, min(len(text), start + 200_000)):
            char = text[index]
            if escape:
                escape = False
                continue
            if char == "\\":
                escape = True
                continue
            if char in {'"', "'"}:
                if in_string and char == quote_char:
                    in_string = False
                    quote_char = ""
                elif not in_string:
                    in_string = True
                    quote_char = char
                continue
            if in_string:
                continue
            if char == "{":
                depth += 1
            elif char == "}":
                depth -= 1
                if depth == 0:
                    candidates.append(text[start:index + 1])
                    break
    return candidates[:10]


def enrich_hydration_candidates(scripts: list[dict[str, Any]]) -> list[dict[str, Any]]:
    enriched: list[dict[str, Any]] = []
    for script_index, script in enumerate(scripts[:20]):
        text = str(script.get("text") or "")
        if len(text) > 300_000 and not text.lstrip().startswith(("{", "[")):
            enriched.append(
                {
                    "layer": "hydration",
                    "script_index": script_index,
                    "accepted": False,
                    "reason": "script_too_large_non_json",
                    "text_length": len(text),
                }
            )
            continue
        for blob_index, blob in enumerate(extract_json_blobs(text)):
            for candidate in scan_price_like_objects(blob, max_candidates=80):
                item = candidate.to_dict()
                item["script_index"] = script_index
                item["blob_index"] = blob_index
                item["layer"] = "hydration"
                enriched.append(item)
    return enriched[:200]


def parse_hydration_scripts(
    region: RegionConfig,
    source_url: str,
    scripts: list[dict[str, Any]],
) -> list[QuoteEvidence]:
    evidences: list[QuoteEvidence] = []
    for script_index, script in enumerate(scripts):
        for blob_index, blob in enumerate(extract_json_blobs(str(script.get("text") or ""))):
            for candidate_index, candidate in enumerate(scan_price_like_objects(blob)):
                if not candidate.accepted or candidate.price is None:
                    continue
                confidence = min(max(candidate.confidence, 0.0), 0.84)
                evidences.append(
                    QuoteEvidence(
                        layer="hydration",
                        price=candidate.price,
                        currency=candidate.currency or region.currency,
                        label="unknown",
                        source_url=source_url,
                        raw_ref=f"hydration:{script_index}:{blob_index}:{candidate_index}:{candidate.path}",
                        confidence=confidence,
                    )
                )
    return evidences
