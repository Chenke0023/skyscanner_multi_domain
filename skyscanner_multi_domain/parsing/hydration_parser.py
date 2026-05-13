from __future__ import annotations

import json
import re
from typing import Any

from skyscanner_multi_domain.models import QuoteEvidence, RegionConfig
from skyscanner_multi_domain.parsing.network_parser import find_price_like_objects


def extract_json_blobs(text: str) -> list[Any]:
    blobs: list[Any] = []
    stripped = (text or "").strip()
    if not stripped:
        return blobs
    for candidate in (stripped, *_script_assignment_json_candidates(stripped)):
        try:
            blobs.append(json.loads(candidate))
        except (TypeError, json.JSONDecodeError):
            continue
    return blobs


def _script_assignment_json_candidates(text: str) -> list[str]:
    candidates: list[str] = []
    for match in re.finditer(r"(?:__NEXT_DATA__|__INITIAL_STATE__|apolloState|flight|itinerary|price)", text, re.I):
        start = text.find("{", match.start())
        if start == -1:
            continue
        depth = 0
        in_string = False
        escape = False
        for index in range(start, min(len(text), start + 2_000_000)):
            char = text[index]
            if escape:
                escape = False
                continue
            if char == "\\":
                escape = True
                continue
            if char == '"':
                in_string = not in_string
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


def parse_hydration_scripts(
    region: RegionConfig,
    source_url: str,
    scripts: list[dict[str, Any]],
) -> list[QuoteEvidence]:
    evidences: list[QuoteEvidence] = []
    for script_index, script in enumerate(scripts):
        for blob_index, blob in enumerate(extract_json_blobs(str(script.get("text") or ""))):
            for object_index, match in enumerate(find_price_like_objects(blob)):
                confidence = 0.8 if match["currency"] and match["has_context"] else 0.58
                evidences.append(
                    QuoteEvidence(
                        layer="hydration",
                        price=match["price"],
                        currency=match["currency"] or region.currency,
                        label="unknown",
                        source_url=source_url,
                        raw_ref=f"hydration:{script_index}:{blob_index}:{object_index}",
                        confidence=confidence,
                    )
                )
    return evidences
