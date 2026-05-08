from __future__ import annotations

import re
from typing import Literal, Tuple


OpenCLIReadiness = Literal[
    "price_ready",
    "still_loading",
    "challenge",
    "empty_shell",
    "no_flights",
    "region_redirect",
    "unsupported_route",
    "unknown_parse_surface",
]


CHALLENGE_MARKERS = (
    "px",
    "captcha",
    "verify you are human",
    "verify you're human",
    "access denied",
    "unusual traffic",
    "security check",
    "complete the challenge",
    "are you a robot",
    "press and hold",
    "cloudflare",
    "perimeterx",
    "人机验证",
    "验证你是人类",
    "安全检查",
)

LOADING_MARKERS = (
    "loading",
    "searching flights",
    "searching for flights",
    "searching for the best flights",
    "finding flights",
    "please wait",
    "spinner",
    "skeleton",
    "正在搜索",
    "搜索中",
    "请稍候",
    "正在查找",
)

REDIRECT_MARKERS = (
    "go to skyscanner",
    "take me to",
    "we've found a better",
    "redirecting",
    "前往",
    "带我去",
    "找到更好的",
    "正在重定向",
    "switch to",
)

UNSUPPORTED_MARKERS = (
    "we don't fly",
    "no routes",
    "route not supported",
    "try another route",
    "不提供",
    "没有航线",
    "不支持此航线",
    "尝试其他航线",
    "sorry, we don't",
)

NO_FLIGHTS_MARKERS = (
    "no flights found",
    "no results",
    "no result",
    "try different dates",
    "try another date",
    "unavailable",
    "no flight results",
    "没有找到航班",
    "无结果",
    "未找到航班",
    "0 results",
)

PRICE_CONTEXT_MARKERS = (
    "cheapest",
    "best",
    "direct",
    "travel providers",
    "provider",
    "flight",
    "itinerary",
    "booking",
    "agent",
    "最便宜",
    "最佳",
    "直飞",
    "航班",
    "供应商",
)

CURRENCY_PATTERN = re.compile(
    r"(?:HK\$|US\$|CA\$|A\$|S\$|[$€£¥]|"
    r"\b(?:USD|HKD|CNY|JPY|SGD|GBP|EUR|AUD|CAD|KRW|KZT|INR|CHF|SEK|NOK|DKK)\b)"
    r"\s*\d[\d\s,.]*|\d[\d\s,.]*\s*"
    r"(?:HK\$|US\$|CA\$|A\$|S\$|[$€£¥]|\b(?:USD|HKD|CNY|JPY|SGD|GBP|EUR|AUD|CAD|KRW|KZT|INR|CHF|SEK|NOK|DKK)\b)",
    re.IGNORECASE,
)


def classify_opencli_page_readiness(page_text: str) -> OpenCLIReadiness:
    readiness, _ = classify_opencli_page_readiness_with_confidence(page_text)
    return readiness


# Confidence scores range from 0.0 to 1.0; 1.0 = most confident in the classification.
def classify_opencli_page_readiness_with_confidence(
    page_text: str,
) -> Tuple[OpenCLIReadiness, float]:
    """Classify page readiness with a confidence score.

    Returns (readiness_state, confidence).
    Confidence is a float 0.0–1.0 indicating how certain the classification is.
    """
    text = " ".join(str(page_text or "").split())
    lower = text.lower()
    if not text:
        return "empty_shell", 1.0

    has_price = CURRENCY_PATTERN.search(text) is not None
    has_price_context = any(marker in lower for marker in PRICE_CONTEXT_MARKERS)

    if any(marker in lower for marker in CHALLENGE_MARKERS) and not has_price:
        return "challenge", 0.95
    if any(marker in lower for marker in REDIRECT_MARKERS) and not has_price:
        return "region_redirect", 0.90
    if any(marker in lower for marker in UNSUPPORTED_MARKERS) and not has_price:
        return "unsupported_route", 0.85

    # no_flights: checked BEFORE short-text fallback so genuine no-flights pages
    # (including short ones like "No result available") are not misclassified
    no_flights_hit = next((m for m in NO_FLIGHTS_MARKERS if m in lower), None)
    if no_flights_hit:
        high_specific = (
            "no flights found",
            "no flight results",
            "未找到航班",
            "没有航班",
            "没有找到航班",
            "0 results",
            "0 flights",
            "无结果",
        )
        generic = (
            "no results",
            "no result",
            "unavailable",
            "try different dates",
            "try another date",
        )
        if any(hs in lower for hs in high_specific):
            conf = 0.90 if not has_price else 0.55
            return "no_flights", conf
        elif any(g in lower for g in generic):
            if has_price or has_price_context:
                return "no_flights", 0.50
            return "no_flights", 0.65
        else:
            return "no_flights", 0.65

    if has_price and has_price_context:
        return "price_ready", 0.95
    if any(marker in lower for marker in LOADING_MARKERS) and not has_price:
        return "still_loading", 0.80
    # Short text: only triggers when none of the above matched AND text is tiny
    if len(text) < 40 and not has_price_context and not has_price:
        return "empty_shell", 0.90
    if has_price:
        return "price_ready", 0.70
    return "unknown_parse_surface", 0.50
