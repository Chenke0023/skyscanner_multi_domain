from __future__ import annotations

from datetime import datetime
import json
import re
from pathlib import Path
from typing import Any

from skyscanner_multi_domain.models import FlightQuote, RegionConfig
from skyscanner_multi_domain.runtime.paths import LOGS_DIR, ensure_runtime_dirs


SNAPSHOT_SCHEMA_VERSION = 1
OPENCLI_SNAPSHOT_DIR = LOGS_DIR / "snapshots" / "opencli"
SNAPSHOT_TEXT_LIMIT = 1800
SNAPSHOT_CONTEXT_LIMIT = 360
SNAPSHOT_MARKERS = (
    "Cheapest",
    "Best",
    "Direct",
    "Travel Providers",
    "最便宜",
    "最佳",
    "直飞",
    "航班",
)
SNAPSHOT_STATUSES = {
    "page_parse_failed",
    "opencli_parse_failed",
    "unknown_parse_surface",
    "empty_shell",
    "opencli_no_flights",
    "page_no_flights",
}


def should_save_opencli_snapshot(quote: FlightQuote) -> bool:
    if quote.status in SNAPSHOT_STATUSES:
        return True
    if quote.price is None and quote.price_candidates_count > 0:
        return True
    if quote.confidence is not None and quote.confidence < 0.65 and quote.price is not None:
        return True
    return any("不一致" in warning or "disagreement" in warning.lower() for warning in quote.parser_warnings)


def save_opencli_snapshot(
    *,
    route: dict[str, Any],
    region: RegionConfig,
    quote: FlightQuote,
    page_text: str,
    snapshot_dir: Path | None = None,
) -> Path:
    ensure_runtime_dirs()
    target_dir = snapshot_dir or OPENCLI_SNAPSHOT_DIR
    target_dir.mkdir(parents=True, exist_ok=True)
    payload = build_opencli_snapshot_payload(
        route=route,
        region=region,
        quote=quote,
        page_text=page_text,
    )
    path = target_dir / _snapshot_filename(route, region, quote)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
    return path


def build_opencli_snapshot_payload(
    *,
    route: dict[str, Any],
    region: RegionConfig,
    quote: FlightQuote,
    page_text: str,
) -> dict[str, Any]:
    clean_text = _sanitize_text(page_text)
    return {
        "schema_version": SNAPSHOT_SCHEMA_VERSION,
        "created_at": datetime.now().isoformat(timespec="seconds"),
        "route": dict(route),
        "region": region.code,
        "domain": region.domain,
        "url": quote.source_url,
        "status": quote.status,
        "failure_class": _snapshot_failure_class(quote),
        "readiness": quote.readiness,
        "chunk_size": quote.max_chunk_size_used,
        "extract_attempt_count": quote.extract_attempt_count,
        "confidence": quote.confidence,
        "price_source": quote.price_source,
        "parser_warnings": list(quote.parser_warnings or []),
        "page_text_head": clean_text[:SNAPSHOT_TEXT_LIMIT],
        "page_text_tail": clean_text[-SNAPSHOT_TEXT_LIMIT:] if clean_text else "",
        "marker_contexts": _marker_contexts(clean_text),
        "candidate_sources": list(quote.candidate_sources or []),
    }


def _snapshot_filename(route: dict[str, Any], region: RegionConfig, quote: FlightQuote) -> str:
    date = re.sub(r"\D", "", str(route.get("date") or "")) or "unknown_date"
    origin = _safe_token(route.get("origin") or "origin")
    destination = _safe_token(route.get("destination") or "destination")
    status = _safe_token(quote.status or "unknown")
    return f"{date}_{region.code}_{origin}_{destination}_{status}.json"


def _safe_token(value: Any) -> str:
    token = re.sub(r"[^A-Za-z0-9]+", "", str(value or ""))[:24]
    return token or "unknown"


def _sanitize_text(page_text: str) -> str:
    text = str(page_text or "")
    text = re.sub(r"(?i)(token|cookie|session|auth)[=:][^\s&]+", r"\1=<redacted>", text)
    return "\n".join(line.strip() for line in text.splitlines() if line.strip())


def _marker_contexts(text: str) -> list[str]:
    contexts: list[str] = []
    lower = text.lower()
    for marker in SNAPSHOT_MARKERS:
        start = lower.find(marker.lower())
        if start < 0:
            continue
        begin = max(start - SNAPSHOT_CONTEXT_LIMIT, 0)
        end = min(start + len(marker) + SNAPSHOT_CONTEXT_LIMIT, len(text))
        contexts.append(" ".join(text[begin:end].split())[:800])
    return contexts[:8]


def _snapshot_failure_class(quote: FlightQuote) -> str:
    status = str(quote.status or "").lower()
    if "challenge" in status:
        return "challenge"
    if "loading" in status:
        return "still_loading"
    if "timeout" in status:
        return "timeout"
    if "no_flights" in status:
        return "no_flights"
    if "not_attempted" in status:
        return "not_attempted"
    if "parse" in status:
        return "parse_failed"
    if quote.readiness:
        return quote.readiness
    return "unknown_parse_surface"
