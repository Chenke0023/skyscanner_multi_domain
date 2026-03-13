"""Scrapling-based page fetching transport."""

from __future__ import annotations

import asyncio
import argparse
import re
from typing import Any, Callable

from bs4 import BeautifulSoup

from skyscanner_models import FlightQuote, RegionConfig
from skyscanner_page_parser import extract_page_quote


def _extract_scrapling_page_text(page: Any) -> str:
    """Extract parser-friendly page text from Scrapling response."""
    html_value = None
    for attr_name in ("html", "content", "body"):
        value = getattr(page, attr_name, None)
        if callable(value):
            try:
                value = value()
            except Exception:
                value = None
        if isinstance(value, (bytes, bytearray)):
            value = value.decode("utf-8", errors="ignore")
        if isinstance(value, str) and value.strip():
            html_value = value
            break

    if html_value:
        try:
            soup = BeautifulSoup(html_value, "lxml")
            for tag in soup(["script", "style", "noscript", "svg", "template"]):
                tag.decompose()
            visible_lines = [
                line.strip() for line in soup.get_text("\n").splitlines() if line.strip()
            ]
            if visible_lines:
                return "\n".join(visible_lines)
        except Exception:
            pass

    css_method = getattr(page, "css", None)
    if callable(css_method):
        for selector in (
            "body :not(script):not(style):not(noscript):not(template)::text",
            "body *::text",
            "body::text",
        ):
            try:
                nodes = css_method(selector)
                getall = getattr(nodes, "getall", None)
                if callable(getall):
                    texts = [
                        str(item).strip() for item in getall() if str(item).strip()
                    ]
                    if texts:
                        return "\n".join(texts)
            except Exception:
                continue

    body = getattr(page, "body", None)
    if isinstance(body, (bytes, bytearray)):
        decoded = body.decode("utf-8", errors="ignore").strip()
        if decoded:
            return decoded

    for attr_name in ("text", "html", "content"):
        value = getattr(page, attr_name, None)
        if callable(value):
            try:
                value = value()
            except Exception:
                value = None
        if isinstance(value, (bytes, bytearray)):
            value = value.decode("utf-8", errors="ignore")
        if isinstance(value, str):
            normalized = value.strip()
            if normalized:
                return normalized

    return ""


def _check_captcha_in_page(page_text: str) -> tuple[bool, str]:
    """Check if page contains captcha indicators.

    Returns:
        (has_captcha, captcha_type)
    """
    captcha_indicators = {
        "cloudflare": ["cf-turnstile", "cloudflare", "turnstile", "cf.challenge"],
        "recaptcha": ["g-recaptcha", "recaptcha", "google recaptcha"],
        "hcaptcha": ["h-captcha", "hcaptcha"],
        "generic": ["captcha", "verify you are human", "human verification"],
    }

    text_lower = page_text.lower()
    for captcha_type, indicators in captcha_indicators.items():
        for indicator in indicators:
            if indicator in text_lower:
                return True, captcha_type
    return False, ""


async def compare_via_scrapling(
    args: argparse.Namespace,
    selected_regions: list[RegionConfig],
    *,
    persist_failures: bool = True,
    on_region_start: Callable[[RegionConfig], None] | None = None,
    build_search_url: Callable[..., str] | None = None,
    persist_failure_log: Callable[..., FlightQuote] | None = None,
) -> list[FlightQuote]:
    # Lazy import to avoid hard dependency at module level
    if build_search_url is None:
        from scan_orchestrator import build_search_url as _bsu
        build_search_url = _bsu
    if persist_failure_log is None:
        from scan_orchestrator import _persist_failure_log as _pfl
        persist_failure_log = _pfl

    try:
        from scrapling import Fetcher, StealthyFetcher
    except ImportError:
        install_hint = '未安装 Scrapling，请先执行: pip install "scrapling[fetchers]"'
        return [
            FlightQuote(
                region=region.code,
                domain=region.domain,
                price=None,
                currency=region.currency,
                source_url=build_search_url(
                    region, args.origin, args.destination, args.date
                ),
                status="scrapling_unavailable",
                error=install_hint,
            )
            for region in selected_regions
        ]

    try:
        from captcha_solver import CaptchaSolverClient, CaptchaSolverError
    except ImportError:
        CaptchaSolverClient = None
        CaptchaSolverError = Exception

    quotes: list[FlightQuote] = []
    timeout_ms = max(int(getattr(args, "timeout", 30) * 1000), 10000)
    wait_ms = max(int(getattr(args, "page_wait", 8) * 1000), 3000)
    timeout_seconds = max(int(getattr(args, "timeout", 30)), 10)
    route_key = f"{args.origin}_{args.destination}_{args.date.replace('-', '')}"

    async def fetch_with_stealth(
        url: str,
        region: RegionConfig,
        *,
        solve_cloudflare: bool,
        wait_override_ms: int | None = None,
    ) -> Any:
        return await asyncio.to_thread(
            StealthyFetcher.fetch,
            url,
            headless=True,
            network_idle=True,
            timeout=timeout_ms,
            wait=wait_override_ms or wait_ms,
            solve_cloudflare=solve_cloudflare,
            google_search=False,
            locale=region.locale,
            extra_headers={
                "accept-language": region.locale,
                "referer": region.domain,
            },
        )

    for region in selected_regions:
        if on_region_start is not None:
            on_region_start(region)
        url = build_search_url(region, args.origin, args.destination, args.date)
        page_text = ""
        latest_quote: FlightQuote | None = None
        latest_error: str | None = None
        stealth_attempts = (
            {"solve_cloudflare": False, "wait_ms": wait_ms},
            {"solve_cloudflare": True, "wait_ms": max(wait_ms, 8000)},
            {"solve_cloudflare": True, "wait_ms": max(wait_ms * 2, 15000)},
        )

        for attempt in stealth_attempts:
            try:
                page = await fetch_with_stealth(
                    url,
                    region,
                    solve_cloudflare=attempt["solve_cloudflare"],
                    wait_override_ms=attempt["wait_ms"],
                )
            except Exception as exc:
                latest_error = f"Scrapling 抓取失败: {exc}"
                continue

            page_text = _extract_scrapling_page_text(page)
            if not page_text:
                latest_quote = FlightQuote(
                    region=region.code,
                    domain=region.domain,
                    price=None,
                    currency=region.currency,
                    source_url=url,
                    status="scrapling_parse_failed",
                    error="Scrapling 返回内容为空，未提取到可解析文本",
                )
                continue

            latest_quote = extract_page_quote(region, url, page_text)
            if latest_quote.price is not None:
                break

            if latest_quote.status not in {
                "page_challenge",
                "page_loading",
                "page_parse_failed",
            }:
                break

        if latest_quote is not None and latest_quote.price is not None:
            quotes.append(latest_quote)
            continue

        # Check if page contains captcha and try to solve it
        has_captcha, captcha_type = _check_captcha_in_page(page_text)
        if (
            has_captcha
            and latest_quote is not None
            and latest_quote.price is None
            and CaptchaSolverClient is not None
        ):
            try:
                captcha_solver = CaptchaSolverClient()
                health = await captcha_solver.health_check()
                if health.get("status") == "healthy":
                    token = None
                    site_key_match = re.search(
                        r'data-sitekey=["\']([^"\']+)["\']', page_text
                    )
                    site_key = site_key_match.group(1) if site_key_match else ""
                    if site_key:
                        if captcha_type == "cloudflare":
                            token = await captcha_solver.solve_turnstile(url, site_key)
                        elif captcha_type == "recaptcha":
                            token = await captcha_solver.solve_recaptcha_v2(url, site_key)
                        elif captcha_type == "hcaptcha":
                            token = await captcha_solver.solve_hcaptcha(url, site_key)

                    if token:
                        page = await fetch_with_stealth(
                            url,
                            region,
                            solve_cloudflare=True,
                            wait_override_ms=max(wait_ms * 2, 15000),
                        )
                        page_text = _extract_scrapling_page_text(page)
                        if page_text:
                            latest_quote = extract_page_quote(region, url, page_text)
                await captcha_solver.close()
            except CaptchaSolverError as exc:
                latest_quote = FlightQuote(
                    region=region.code,
                    domain=region.domain,
                    price=None,
                    currency=region.currency,
                    source_url=url,
                    status="captcha_solve_failed",
                    error=f"Captcha解决失败 ({captcha_type}): {exc}",
                )
            except Exception:
                pass

        if latest_quote is None or latest_quote.price is None:
            try:
                page = await asyncio.to_thread(
                    Fetcher.get,
                    url,
                    timeout=timeout_seconds,
                    stealthy_headers=True,
                    follow_redirects=True,
                )
                page_text = _extract_scrapling_page_text(page)
                if page_text:
                    latest_quote = extract_page_quote(region, url, page_text)
                elif latest_quote is None:
                    latest_quote = FlightQuote(
                        region=region.code,
                        domain=region.domain,
                        price=None,
                        currency=region.currency,
                        source_url=url,
                        status="scrapling_parse_failed",
                        error="Scrapling 返回内容为空，未提取到可解析文本",
                    )
            except Exception as exc:
                latest_error = latest_error or f"Scrapling 抓取失败: {exc}"

        if latest_quote is None:
            latest_quote = FlightQuote(
                region=region.code,
                domain=region.domain,
                price=None,
                currency=region.currency,
                source_url=url,
                status="scrapling_failed",
                error=latest_error or "Scrapling 抓取失败",
            )
        elif latest_quote.price is None and latest_error and not latest_quote.error:
            latest_quote.error = latest_error

        if persist_failures and latest_quote.price is None:
            persist_failure_log(
                latest_quote,
                transport="scrapling",
                route_key=route_key,
                page_text=page_text,
                extra={"locale": region.locale},
            )

        quotes.append(latest_quote)

    return quotes
