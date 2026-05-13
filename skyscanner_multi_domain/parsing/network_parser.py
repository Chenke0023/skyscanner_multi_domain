from __future__ import annotations

from typing import Any, Iterable

from skyscanner_multi_domain.models import QuoteEvidence, RegionConfig

PRICE_KEYS = {"amount", "price", "rawprice", "total", "value"}
CURRENCY_KEYS = {"currency", "currencycode", "currency_code"}
CONTEXT_KEYS = {"itinerary", "itineraries", "leg", "legs", "quote", "quotes", "flight", "flights"}


def _iter_objects(value: Any, *, context: bool = False) -> Iterable[tuple[dict[str, Any], bool]]:
    if isinstance(value, dict):
        current_context = context or _has_context(value)
        yield value, current_context
        for nested in value.values():
            yield from _iter_objects(nested, context=current_context)
    elif isinstance(value, list):
        for item in value:
            yield from _iter_objects(item, context=context)


def _number(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        cleaned = value.replace(",", "").strip()
        try:
            return float(cleaned)
        except ValueError:
            return None
    return None


def _find_value(obj: dict[str, Any], keys: set[str]) -> Any:
    for key, value in obj.items():
        if key.replace("_", "").lower() in keys:
            return value
    return None


def _has_context(obj: dict[str, Any]) -> bool:
    joined = " ".join(str(key).lower() for key in obj.keys())
    return any(key in joined for key in CONTEXT_KEYS)


def find_price_like_objects(payload: Any) -> list[dict[str, Any]]:
    matches: list[dict[str, Any]] = []
    for obj, has_context in _iter_objects(payload):
        raw_price = _find_value(obj, PRICE_KEYS)
        price = _number(raw_price)
        if price is None or price <= 0:
            continue
        currency = _find_value(obj, CURRENCY_KEYS)
        matches.append(
            {
                "price": price,
                "currency": str(currency or ""),
                "has_context": has_context,
                "keys": sorted(str(key) for key in obj.keys())[:20],
            }
        )
    return matches


def parse_network_json(
    region: RegionConfig,
    source_url: str,
    payloads: list[Any],
) -> list[QuoteEvidence]:
    evidences: list[QuoteEvidence] = []
    for payload_index, payload in enumerate(payloads):
        for object_index, match in enumerate(find_price_like_objects(payload)):
            confidence = 0.9 if match["currency"] and match["has_context"] else 0.62
            evidences.append(
                QuoteEvidence(
                    layer="network",
                    price=match["price"],
                    currency=match["currency"] or region.currency,
                    label="unknown",
                    source_url=source_url,
                    raw_ref=f"network:{payload_index}:{object_index}:{','.join(match['keys'])}",
                    confidence=confidence,
                )
            )
    return evidences
