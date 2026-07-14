from __future__ import annotations

import re
from typing import Any, Iterable

_TIME_RE = re.compile(r"(?<!\d)([01]?\d|2[0-3]):[0-5]\d(?:\s*[AP]M)?(?!\d)", re.IGNORECASE)
_PRICE_RE = re.compile(
    r"(?:HK\$|US\$|CA\$|A\$|S\$|¥|￥|£|€|\$|₩|₹|CNY|HKD|SGD|GBP|EUR|USD|JPY|KRW|INR)\s*"
    r"([\d,]+(?:\.\d+)?)",
    re.IGNORECASE,
)
_DIRECT_RE = re.compile(
    r"\b(?:non[\s-]?stop|direct)\b|直飞|直飛|不停站|不经停|不經停",
    re.IGNORECASE,
)
_STOP_RE = re.compile(
    r"(?<!\d)(\d+)\s*(?:stop(?:s)?|次\s*(?:经停|經停|中转|中轉|转机|轉機)|個?\s*(?:中转|中轉|转机|轉機))",
    re.IGNORECASE,
)
_DURATION_PATTERNS = (
    re.compile(r"(?<!\d)(\d+)\s*(?:hours|hour|hrs|hr|h)\s*(?:(\d+)\s*(?:minutes|minute|mins|min|m))?", re.IGNORECASE),
    re.compile(r"(?<!\d)(\d+)\s*(?:小时|小時|时)\s*(?:(\d+)\s*(?:分钟|分鐘|分))?"),
    re.compile(r"(?<!\d)(\d+)\s*(?:minutes|minute|mins|min|m)\b", re.IGNORECASE),
    re.compile(r"(?<!\d)(\d+)\s*(?:分钟|分鐘|分)(?!钟|鐘)"),
)


def _clean_time(value: str) -> str:
    return re.sub(r"\s+", " ", value.strip()).upper()


def _duration_minutes(text: str) -> int | None:
    for index, pattern in enumerate(_DURATION_PATTERNS):
        match = pattern.search(text)
        if not match:
            continue
        first = int(match.group(1))
        second = int(match.group(2) or 0) if match.lastindex and match.lastindex >= 2 else 0
        if index < 2:
            total = first * 60 + second
        else:
            total = first
        if 0 < total <= 72 * 60:
            return total
    return None


def _stop_count(text: str) -> int | None:
    if _DIRECT_RE.search(text):
        return 0
    match = _STOP_RE.search(text)
    if match:
        value = int(match.group(1))
        if 0 <= value <= 9:
            return value
    return None


def parse_itinerary_legs(card_text: str, *, round_trip: bool = False) -> list[dict[str, Any]]:
    """Extract up to two itinerary legs from a single flight-result card.

    The parser intentionally requires a departure/arrival time pair. Optional
    stop and duration values are only read from the text belonging to that leg;
    missing values remain ``None`` rather than being inferred.
    """

    text = str(card_text or "")
    time_matches = list(_TIME_RE.finditer(text))
    max_legs = 2 if round_trip else 1
    pair_count = min(len(time_matches) // 2, max_legs)
    legs: list[dict[str, Any]] = []
    for index in range(pair_count):
        departure = time_matches[index * 2]
        arrival = time_matches[index * 2 + 1]
        next_departure_index = (index + 1) * 2
        end = (
            time_matches[next_departure_index].start()
            if next_departure_index < len(time_matches) and index + 1 < pair_count
            else len(text)
        )
        context = text[departure.start():end]
        legs.append(
            {
                "direction": "outbound" if index == 0 else "return",
                "departure_time": _clean_time(departure.group(0)),
                "arrival_time": _clean_time(arrival.group(0)),
                "stop_count": _stop_count(context),
                "duration_minutes": _duration_minutes(context),
            }
        )
    return legs


def price_amount(text: str) -> float | None:
    match = _PRICE_RE.search(str(text or ""))
    if not match:
        return None
    try:
        return float(match.group(1).replace(",", ""))
    except ValueError:
        return None


def _leg_quality(legs: list[dict[str, Any]]) -> tuple[int, int]:
    populated = sum(
        1
        for leg in legs
        for key in ("departure_time", "arrival_time", "stop_count", "duration_minutes")
        if leg.get(key) is not None
    )
    return (len(legs), populated)


def match_itinerary_legs(
    cards: Iterable[dict[str, Any]],
    target_price: float | int | None,
    *,
    round_trip: bool = False,
) -> list[dict[str, Any]]:
    """Return the most complete itinerary whose card price matches target_price."""

    if not isinstance(target_price, (int, float)) or isinstance(target_price, bool):
        return []
    matches: list[list[dict[str, Any]]] = []
    for card in cards:
        card_price = price_amount(str(card.get("priceText") or card.get("cardText") or ""))
        if card_price is None or abs(card_price - float(target_price)) > 0.01:
            continue
        legs = parse_itinerary_legs(str(card.get("cardText") or ""), round_trip=round_trip)
        if legs:
            matches.append(legs)
    return max(matches, key=_leg_quality, default=[])
