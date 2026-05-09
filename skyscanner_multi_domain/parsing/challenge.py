"""Captcha/challenge detection and quote builders.

This module is transport-neutral: both scrapling, cdp, and opencli
use these helpers to detect challenges and build challenge quotes.
"""

from __future__ import annotations

from typing import Any

from skyscanner_multi_domain.models import FlightQuote, RegionConfig


def coerce_page_snippet(value: Any) -> str:
    if callable(value):
        try:
            value = value()
        except Exception:
            value = None
    if isinstance(value, (bytes, bytearray)):
        value = value.decode("utf-8", errors="ignore")
    if isinstance(value, str):
        return value
    return ""


def check_captcha_in_page(page_text: str, page: Any | None = None) -> tuple[bool, str]:
    """Check if page contains captcha indicators.

    Returns:
        (has_captcha, captcha_type)
    """
    captcha_indicators = {
        "px": [
            "captcha-v2",
            "/sttc/px/",
            "perimeterx",
            "px-captcha",
            "press and hold",
        ],
        "cloudflare": ["cf-turnstile", "cloudflare", "turnstile", "cf.challenge"],
        "recaptcha": ["g-recaptcha", "recaptcha", "google recaptcha"],
        "hcaptcha": ["h-captcha", "hcaptcha"],
        "generic": ["captcha", "verify you are human", "human verification"],
    }

    text_parts = [page_text]
    if page is not None:
        for attr_name in ("url", "current_url", "html", "content", "body", "text"):
            text_parts.append(coerce_page_snippet(getattr(page, attr_name, None)))
    text_lower = "\n".join(part for part in text_parts if part).lower()
    for captcha_type, indicators in captcha_indicators.items():
        for indicator in indicators:
            if indicator in text_lower:
                return True, captcha_type
    return False, ""


def build_captcha_quote(
    region: RegionConfig,
    source_url: str,
    captcha_type: str,
    *,
    source_label: str,
) -> FlightQuote:
    normalized_type = (captcha_type or "").strip().lower()
    if normalized_type == "px":
        return FlightQuote(
            region=region.code,
            domain=region.domain,
            price=None,
            currency=region.currency,
            source_url=source_url,
            status="px_challenge",
            error=(
                f"{source_label} 命中 PX captcha-v2 验证页。"
                "当前不会自动解此类验证；如已打开浏览器结果页，请在页面中手动完成验证后等待页面模式继续轮询。"
            ),
        )

    challenge_label = {
        "cloudflare": "Cloudflare",
        "recaptcha": "reCAPTCHA",
        "hcaptcha": "hCaptcha",
        "generic": "通用验证码",
    }.get(normalized_type, normalized_type.upper() or "验证码")
    return FlightQuote(
        region=region.code,
        domain=region.domain,
        price=None,
        currency=region.currency,
        source_url=source_url,
        status="page_challenge",
        error=f"{source_label} 命中 {challenge_label} 验证页",
    )
