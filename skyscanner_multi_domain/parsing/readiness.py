from __future__ import annotations

import re
from typing import Literal


OpenCLIReadiness = Literal[
    "price_ready",
    "still_loading",
    "challenge",
    "empty_shell",
    "no_flights",
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

NO_FLIGHTS_MARKERS = (
    "no flights found",
    "no results",
    "try different dates",
    "try another date",
    "unavailable",
    "no flight results",
    "没有找到航班",
    "无结果",
    "未找到航班",
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
    text = " ".join(str(page_text or "").split())
    lower = text.lower()
    if not text:
        return "empty_shell"

    has_price = CURRENCY_PATTERN.search(text) is not None
    has_price_context = any(marker in lower for marker in PRICE_CONTEXT_MARKERS)

    if any(marker in lower for marker in CHALLENGE_MARKERS) and not has_price:
        return "challenge"
    if any(marker in lower for marker in NO_FLIGHTS_MARKERS):
        return "no_flights"
    if has_price and has_price_context:
        return "price_ready"
    if any(marker in lower for marker in LOADING_MARKERS) and not has_price:
        return "still_loading"
    if len(text) < 40 and not has_price_context and not has_price:
        return "empty_shell"
    if has_price:
        return "price_ready"
    return "unknown_parse_surface"
