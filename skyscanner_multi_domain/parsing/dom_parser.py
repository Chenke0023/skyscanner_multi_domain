from __future__ import annotations

import re
from typing import Any

from skyscanner_multi_domain.models import QuoteEvidence, RegionConfig
from skyscanner_multi_domain.parsing.page_parser import extract_page_quote

PRICE_RE = re.compile(
    r"(?P<currency>HK\$|US\$|CA\$|A\$|S\$|¥|￥|£|€|\$|₩|₹|CNY|HKD|SGD|GBP|EUR|USD|JPY|KRW|INR)\s?(?P<amount>[\d,]+(?:\.\d+)?)",
    re.IGNORECASE,
)


def _parse_price(text: str) -> tuple[str, float] | None:
    match = PRICE_RE.search(text or "")
    if not match:
        return None
    try:
        return match.group("currency"), float(match.group("amount").replace(",", ""))
    except ValueError:
        return None


def _infer_label(text: str) -> str | None:
    lowered = (text or "").lower()
    if "best" in lowered or "最佳" in text:
        return "best"
    if "cheapest" in lowered or "lowest" in lowered or "最便宜" in text:
        return "cheapest"
    return None


def _label_source(card: dict[str, Any], label: str | None) -> str | None:
    if label is None:
        return None
    for key in ("cardText", "priceText", "aria", "role"):
        if label in str(card.get(key) or "").lower() or (
            label == "best" and "最佳" in str(card.get(key) or "")
        ) or (
            label == "cheapest" and "最便宜" in str(card.get(key) or "")
        ):
            return key
    return "inferred"


def _raw_ref(card: dict[str, Any], index: int, label: str | None) -> str:
    context = str(card.get("cardText") or card.get("priceText") or "")[:500]
    geometry = {
        "x": card.get("x"),
        "y": card.get("y"),
        "w": card.get("w"),
        "h": card.get("h"),
    }
    return repr(
        {
            "index": index,
            "label": label,
            "label_source": _label_source(card, label),
            "geometry": geometry,
            "text": context,
        }
    )


def parse_dom_cards(
    region: RegionConfig,
    source_url: str,
    cards: list[dict[str, Any]],
) -> list[QuoteEvidence]:
    evidences: list[QuoteEvidence] = []
    for index, card in enumerate(cards):
        price_text = str(card.get("priceText") or "")
        card_text = str(card.get("cardText") or "")
        parsed = _parse_price(price_text) or _parse_price(card_text)
        if parsed is None:
            continue
        currency, price = parsed
        label = _infer_label(card_text) or _infer_label(price_text)
        evidences.append(
            QuoteEvidence(
                layer="dom",
                price=price,
                currency=currency or region.currency,
                label=label,
                source_url=source_url,
                raw_ref=_raw_ref(card, index, label),
                confidence=0.75,
            )
        )
    return evidences


def parse_text_fallback(
    region: RegionConfig,
    source_url: str,
    page_text: str,
) -> list[QuoteEvidence]:
    quote = extract_page_quote(region, source_url, page_text)
    if quote.price is None:
        return []
    return [
        QuoteEvidence(
            layer="text",
            price=quote.price,
            currency=quote.currency or region.currency,
            label=quote.price_source,
            source_url=source_url,
            raw_ref=quote.evidence_text or page_text[:500],
            confidence=quote.confidence or 0.45,
            error=quote.error,
        )
    ]
