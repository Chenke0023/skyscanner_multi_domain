from __future__ import annotations

from dataclasses import dataclass, field
import re
from typing import Any, Iterable


@dataclass
class PriceCandidate:
    amount: float
    currency: str
    source: str
    confidence: str
    evidence_text: str
    marker: str | None = None
    marker_distance: int | None = None
    provider: str | None = None
    rank_score: float = 0.0
    warning_flags: list[str] = field(default_factory=list)


CURRENCY_TOKENS = (
    "HK$",
    "US$",
    "CA$",
    "A$",
    "S$",
    "€",
    "£",
    "¥",
    "$",
    "CHF",
    "SEK",
    "NOK",
    "DKK",
    "JPY",
    "CNY",
    "HKD",
    "USD",
    "GBP",
    "SGD",
    "KRW",
    "EUR",
    "AUD",
    "CAD",
    "INR",
    "KZT",
)

PRICE_MARKERS = {
    "cheapest": "near_cheapest_marker",
    "best": "near_best_marker",
    "direct": "near_direct_marker",
    "最便宜": "near_cheapest_marker",
    "最佳": "near_best_marker",
    "直飞": "near_direct_marker",
}

CONTEXT_KEYS = (
    "itinerary",
    "itineraries",
    "leg",
    "legs",
    "segment",
    "segments",
    "agent",
    "provider",
    "deeplink",
    "booking",
    "fare",
    "flight",
)

PRICE_KEYS = (
    "amount",
    "price",
    "formattedPrice",
    "rawPrice",
    "totalPrice",
    "currency",
    "currencyCode",
)

TOKEN_PATTERN = "|".join(re.escape(token) for token in sorted(CURRENCY_TOKENS, key=len, reverse=True))
AMOUNT_PATTERN = r"\d[\d\s,.]*"
PRICE_RE = re.compile(
    rf"(?P<prefix>{TOKEN_PATTERN})\s*(?P<prefix_amount>{AMOUNT_PATTERN})|"
    rf"(?P<suffix_amount>{AMOUNT_PATTERN})\s*(?P<suffix>{TOKEN_PATTERN})",
    re.IGNORECASE,
)
JSON_AMOUNT_RE = re.compile(
    r'"(?:amount|price|rawPrice|totalPrice)"\s*:\s*"?(?P<amount>\d[\d,.]*)"?',
    re.IGNORECASE,
)
JSON_FORMATTED_RE = re.compile(
    r'"(?:formattedPrice|displayPrice)"\s*:\s*"(?P<value>[^"]{1,80})"',
    re.IGNORECASE,
)
JSON_CURRENCY_RE = re.compile(
    r'"(?:currency|currencyCode)"\s*:\s*"(?P<currency>[A-Z]{3}|HK\$|US\$|CA\$|A\$|S\$|[$€£¥])"',
    re.IGNORECASE,
)


def parse_amount(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    if not isinstance(value, str):
        return None
    cleaned = re.sub(r"[^\d,.]", "", value)
    if not cleaned:
        return None
    if "," in cleaned and "." in cleaned:
        if cleaned.rfind(",") > cleaned.rfind("."):
            cleaned = cleaned.replace(".", "").replace(",", ".")
        else:
            cleaned = cleaned.replace(",", "")
    elif "," in cleaned:
        parts = cleaned.split(",")
        cleaned = "".join(parts) if len(parts[-1]) != 2 else "".join(parts[:-1]) + "." + parts[-1]
    elif "." in cleaned:
        parts = cleaned.split(".")
        if len(parts) > 2 or (len(parts) == 2 and len(parts[-1]) not in {1, 2}):
            cleaned = "".join(parts)
    try:
        return float(cleaned)
    except ValueError:
        return None


def parse_price_text(text: str) -> tuple[str, float, str] | None:
    match = PRICE_RE.search(text)
    if not match:
        return None
    currency = match.group("prefix") or match.group("suffix") or ""
    amount_text = match.group("prefix_amount") or match.group("suffix_amount") or ""
    amount = parse_amount(amount_text)
    if amount is None:
        return None
    return currency.upper(), amount, match.group(0)


def collect_price_candidates(page_text: str, expected_currency: str | None = None) -> list[PriceCandidate]:
    candidates: list[PriceCandidate] = []
    candidates.extend(extract_visible_price_candidates(page_text, expected_currency))
    candidates.extend(extract_embedded_price_candidates(page_text, expected_currency))
    return rank_price_candidates(candidates, expected_currency)


def extract_visible_price_candidates(
    page_text: str,
    expected_currency: str | None = None,
) -> list[PriceCandidate]:
    text = str(page_text or "")
    lower = text.lower()
    candidates: list[PriceCandidate] = []
    for match in PRICE_RE.finditer(text):
        parsed = parse_price_text(match.group(0))
        if parsed is None:
            continue
        currency, amount, raw = parsed
        start = match.start()
        context = text[max(0, start - 220) : min(len(text), match.end() + 220)]
        marker, marker_distance, source = _nearest_marker(lower, start)
        source = source or "visible_text_price"
        if not candidates:
            source = source if source != "visible_text_price" else "first_price_fallback"
        candidate = PriceCandidate(
            amount=amount,
            currency=currency,
            source=source,
            confidence="unknown",
            evidence_text=" ".join(context.split())[:300],
            marker=marker,
            marker_distance=marker_distance,
        )
        _apply_basic_warnings(candidate, expected_currency)
        candidates.append(candidate)
    return candidates[:30]


def extract_embedded_price_candidates(
    raw_text: str,
    expected_currency: str | None = None,
) -> list[PriceCandidate]:
    text = str(raw_text or "")
    lower = text.lower()
    candidates: list[PriceCandidate] = []
    for key in PRICE_KEYS:
        for match in re.finditer(re.escape(key), text, flags=re.IGNORECASE):
            window = text[max(0, match.start() - 500) : min(len(text), match.end() + 700)]
            lower_window = window.lower()
            if not any(context_key in lower_window for context_key in CONTEXT_KEYS):
                continue
            parsed = parse_price_text(window)
            if parsed is None:
                formatted = JSON_FORMATTED_RE.search(window)
                parsed = parse_price_text(formatted.group("value")) if formatted else None
            if parsed is not None:
                currency, amount, raw = parsed
            else:
                amount_match = JSON_AMOUNT_RE.search(window)
                currency_match = JSON_CURRENCY_RE.search(window)
                if not amount_match or not currency_match:
                    continue
                amount = parse_amount(amount_match.group("amount"))
                if amount is None:
                    continue
                currency = currency_match.group("currency").upper()
                raw = f"{currency} {amount:g}"
            source = "script_state_price" if "__NEXT_DATA__" in window or "hydration" in lower_window else "embedded_json_price"
            candidate = PriceCandidate(
                amount=amount,
                currency=currency,
                source=source,
                confidence="unknown",
                evidence_text=" ".join(window.split())[:300],
            )
            _apply_basic_warnings(candidate, expected_currency)
            candidates.append(candidate)
    return _dedupe_candidates(candidates)[:20]


def rank_price_candidates(
    candidates: Iterable[PriceCandidate],
    expected_currency: str | None = None,
) -> list[PriceCandidate]:
    weighted: list[PriceCandidate] = []
    for candidate in _dedupe_candidates(candidates):
        score = _source_weight(candidate.source)
        if expected_currency and _normalize_currency(candidate.currency) == _normalize_currency(expected_currency):
            score += 12
        if candidate.marker_distance is not None:
            score += max(0, 12 - min(candidate.marker_distance, 12))
        if candidate.evidence_text and len(candidate.evidence_text) >= 30:
            score += 4
        score -= 8 * len(candidate.warning_flags)
        if candidate.source == "first_price_fallback":
            score = min(score, 42)
        candidate.rank_score = score
        candidate.confidence = _confidence_for_score(score, candidate.source)
        weighted.append(candidate)
    weighted.sort(key=lambda item: (-item.rank_score, item.amount, item.source))
    return weighted


def selected_candidate_to_metadata(
    candidates: list[PriceCandidate],
) -> tuple[PriceCandidate | None, list[str], int | None]:
    if not candidates:
        return None, [], None
    sources = []
    for candidate in candidates:
        if candidate.source not in sources:
            sources.append(candidate.source)
    return candidates[0], sources, 1


def confidence_to_float(confidence: str) -> float:
    return {
        "high": 0.9,
        "medium": 0.72,
        "low": 0.45,
        "unknown": 0.0,
    }.get(confidence, 0.0)


def _nearest_marker(lower_text: str, price_index: int) -> tuple[str | None, int | None, str | None]:
    best: tuple[str | None, int | None, str | None] = (None, None, None)
    for marker, source in PRICE_MARKERS.items():
        marker_index = lower_text.rfind(marker.lower(), 0, price_index)
        if marker_index < 0:
            continue
        distance = len(lower_text[marker_index:price_index].split())
        if best[1] is None or distance < best[1]:
            best = (marker, distance, source)
    if best[1] is not None and best[1] > 50:
        return None, None, None
    return best


def _source_weight(source: str) -> float:
    return {
        "cheapest_block": 90,
        "near_cheapest_marker": 82,
        "best_block": 75,
        "near_best_marker": 70,
        "near_direct_marker": 62,
        "embedded_json_price": 60,
        "script_state_price": 60,
        "dom_card_price": 58,
        "visible_text_price": 45,
        "first_price_fallback": 25,
        "manual_confirmed": 100,
    }.get(source, 35)


def _confidence_for_score(score: float, source: str) -> str:
    if source == "first_price_fallback":
        return "low"
    if score >= 88:
        return "high"
    if score >= 58:
        return "medium"
    if score > 0:
        return "low"
    return "unknown"


def _apply_basic_warnings(candidate: PriceCandidate, expected_currency: str | None) -> None:
    if expected_currency and _normalize_currency(candidate.currency) != _normalize_currency(expected_currency):
        candidate.warning_flags.append("currency_mismatch")
    if candidate.amount < 20:
        candidate.warning_flags.append("suspicious_low_price")
    if candidate.amount > 200000:
        candidate.warning_flags.append("suspicious_high_price")
    lower = candidate.evidence_text.lower()
    if any(word in lower for word in ("hotel", "car rental", "calendar", "month", "广告", "酒店", "租车")):
        candidate.warning_flags.append("non_itinerary_context")


def _normalize_currency(currency: str | None) -> str:
    value = str(currency or "").strip().upper()
    return {
        "¥": "CNY",
        "$": "USD",
        "US$": "USD",
        "HK$": "HKD",
        "S$": "SGD",
        "A$": "AUD",
        "CA$": "CAD",
        "£": "GBP",
        "€": "EUR",
    }.get(value, value)


def _dedupe_candidates(candidates: Iterable[PriceCandidate]) -> list[PriceCandidate]:
    seen: set[tuple[float, str, str]] = set()
    result: list[PriceCandidate] = []
    for candidate in candidates:
        key = (round(candidate.amount, 2), _normalize_currency(candidate.currency), candidate.source)
        if key in seen:
            continue
        seen.add(key)
        result.append(candidate)
    return result
