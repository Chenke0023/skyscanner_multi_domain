from __future__ import annotations

import re
from dataclasses import asdict, dataclass
from typing import Any

PRICE_KEYS = {
    "price",
    "amount",
    "rawamount",
    "rawprice",
    "formattedprice",
    "totalprice",
    "minprice",
    "cheapestprice",
    "total",
    "value",
}
CURRENCY_KEYS = {"currency", "currencycode", "currencysymbol", "currency_code"}
FLIGHT_HINT_KEYS = {
    "itinerary",
    "itineraries",
    "leg",
    "legs",
    "segment",
    "segments",
    "carrier",
    "agent",
    "quote",
    "flight",
    "flights",
}
PRICE_TEXT_RE = re.compile(
    r"(?P<currency>HK\$|US\$|CA\$|A\$|S\$|¥|￥|£|€|\$|₩|₹|CNY|HKD|SGD|GBP|EUR|USD|JPY|KRW|INR)\s?(?P<amount>[\d,]+(?:\.\d+)?)",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class StructuredPriceCandidate:
    price: float | None
    currency: str
    path: str
    price_key: str
    confidence: float
    has_currency: bool
    has_flight_context: bool
    accepted: bool
    reason: str
    keys: list[str]
    raw_value: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _norm_key(key: str) -> str:
    return key.replace("_", "").replace("-", "").lower()


def _number(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        match = PRICE_TEXT_RE.search(value)
        if match:
            try:
                return float(match.group("amount").replace(",", ""))
            except ValueError:
                return None
        cleaned = value.replace(",", "").strip()
        try:
            return float(cleaned)
        except ValueError:
            return None
    return None


def _currency_from_value(value: Any) -> str:
    if isinstance(value, str):
        match = PRICE_TEXT_RE.search(value)
        if match:
            return match.group("currency")
    return ""


def _find_currency(obj: dict[str, Any]) -> str:
    for key, value in obj.items():
        if _norm_key(str(key)) in CURRENCY_KEYS and value:
            return str(value)
    for value in obj.values():
        currency = _currency_from_value(value)
        if currency:
            return currency
    return ""


def _has_flight_context(obj: dict[str, Any], inherited_context: bool) -> bool:
    if inherited_context:
        return True
    joined = " ".join(str(key).lower() for key in obj.keys())
    return any(key in joined for key in FLIGHT_HINT_KEYS)


def _confidence(has_currency: bool, has_context: bool, price_key: str) -> float:
    score = 0.35
    if has_currency:
        score += 0.20
    if has_context:
        score += 0.25
    if _norm_key(price_key) in {"totalprice", "cheapestprice", "minprice", "amount", "price"}:
        score += 0.10
    return min(score, 0.92)


def _candidate_reason(price: float | None, has_currency: bool, has_context: bool) -> tuple[bool, str]:
    if price is None:
        return False, "price_not_numeric"
    if price <= 0:
        return False, "price_not_positive"
    if not has_currency and not has_context:
        return False, "weak_context"
    return True, "usable"


def scan_price_like_objects(payload: Any, *, max_candidates: int = 200) -> list[StructuredPriceCandidate]:
    candidates: list[StructuredPriceCandidate] = []

    def walk(value: Any, path: str, inherited_context: bool = False) -> None:
        if len(candidates) >= max_candidates:
            return
        if isinstance(value, dict):
            has_context = _has_flight_context(value, inherited_context)
            currency = _find_currency(value)
            for key, raw in value.items():
                key_norm = _norm_key(str(key))
                if key_norm not in PRICE_KEYS:
                    continue
                price = _number(raw)
                raw_currency = currency or _currency_from_value(raw)
                accepted, reason = _candidate_reason(price, bool(raw_currency), has_context)
                candidates.append(
                    StructuredPriceCandidate(
                        price=price,
                        currency=raw_currency,
                        path=f"{path}.{key}" if path else str(key),
                        price_key=str(key),
                        confidence=_confidence(bool(raw_currency), has_context, str(key)),
                        has_currency=bool(raw_currency),
                        has_flight_context=has_context,
                        accepted=accepted,
                        reason=reason,
                        keys=sorted(str(k) for k in value.keys())[:30],
                        raw_value=str(raw)[:200],
                    )
                )
            if len(candidates) >= max_candidates:
                return
            for key, child in value.items():
                child_path = f"{path}.{key}" if path else str(key)
                key_text = _norm_key(str(key))
                key_has_context = any(hint in key_text for hint in FLIGHT_HINT_KEYS)
                walk(child, child_path, inherited_context or key_has_context)
                if len(candidates) >= max_candidates:
                    return
        elif isinstance(value, list):
            for index, child in enumerate(value):
                walk(child, f"{path}[{index}]", inherited_context)

    walk(payload, "$", False)
    candidates.sort(key=lambda item: (item.accepted, item.confidence, item.price or 0), reverse=True)
    return candidates
