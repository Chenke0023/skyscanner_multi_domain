"""Scrapling-based page fetching transport."""

from __future__ import annotations

import asyncio
import argparse
import re
from typing import Any, Callable

from bs4 import BeautifulSoup

from skyscanner_models import FlightQuote, RegionConfig
from skyscanner_page_parser import extract_page_quote


PLAYWRIGHT_PROBE_TEXT_LIMIT = 12000


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


def _coerce_page_snippet(value: Any) -> str:
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


def _check_captcha_in_page(page_text: str, page: Any | None = None) -> tuple[bool, str]:
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
            text_parts.append(_coerce_page_snippet(getattr(page, attr_name, None)))
    text_lower = "\n".join(part for part in text_parts if part).lower()
    for captcha_type, indicators in captcha_indicators.items():
        for indicator in indicators:
            if indicator in text_lower:
                return True, captcha_type
    return False, ""


async def _probe_page_with_playwright(
    url: str, region: RegionConfig, timeout_ms: int
) -> FlightQuote | None:
    try:
        from playwright.async_api import TimeoutError as PlaywrightTimeoutError
        from playwright.async_api import async_playwright
    except ImportError:
        return None

    browser = None
    context = None
    page = None
    page_text = ""
    final_url = url
    timed_out = False

    try:
        async with async_playwright() as playwright:
            browser = await playwright.chromium.launch(headless=True)
            context = await browser.new_context(
                locale=region.locale,
                extra_http_headers={
                    "accept-language": region.locale,
                    "referer": region.domain,
                },
            )
            page = await context.new_page()
            try:
                await page.goto(
                    url,
                    wait_until="domcontentloaded",
                    timeout=min(timeout_ms, 12000),
                )
            except PlaywrightTimeoutError:
                timed_out = True

            final_url = page.url or url
            try:
                page_text = await page.evaluate(
                    f"""
                    () => {{
                        const text = document.body ? document.body.innerText : '';
                        return text.slice(0, {PLAYWRIGHT_PROBE_TEXT_LIMIT});
                    }}
                    """
                )
            except Exception:
                try:
                    page_text = await page.text_content("body") or ""
                except Exception:
                    page_text = ""

            quote = extract_page_quote(region, final_url, page_text)
            if quote.price is not None:
                return quote

            has_captcha, captcha_type = _check_captcha_in_page(page_text)
            if has_captcha:
                label = "PX" if captcha_type == "px" else captcha_type.upper()
                return FlightQuote(
                    region=region.code,
                    domain=region.domain,
                    price=None,
                    currency=region.currency,
                    source_url=final_url,
                    status="page_challenge",
                    error=f"Playwright 预探测命中 {label} 验证页",
                )

            if quote.status == "page_loading":
                quote.error = "Playwright 预探测显示结果页仍在加载"
                return quote

            if timed_out and page_text:
                return FlightQuote(
                    region=region.code,
                    domain=region.domain,
                    price=None,
                    currency=region.currency,
                    source_url=final_url,
                    status="page_loading",
                    error="Playwright 预探测在 domcontentloaded 前超时",
                )
    except Exception:
        return None
    finally:
        try:
            if page is not None:
                await page.close()
        except Exception:
            pass
        try:
            if context is not None:
                await context.close()
        except Exception:
            pass
        try:
            if browser is not None:
                await browser.close()
        except Exception:
            pass

    return None


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
            network_idle=False,
            load_dom=False,
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
        detected_captcha_type = ""

        probe_quote = await _probe_page_with_playwright(url, region, timeout_ms)
        if probe_quote is not None:
            if persist_failures and probe_quote.price is None:
                persist_failure_log(
                    probe_quote,
                    transport="scrapling",
                    route_key=route_key,
                    page_text=page_text,
                    extra={"locale": region.locale, "probe": "playwright"},
                )
            quotes.append(probe_quote)
            continue

        try:
            from scrapling import Fetcher, StealthyFetcher
        except ImportError:
            install_hint = '未安装 Scrapling，请先执行: pip install "scrapling[fetchers]"'
            quotes.append(
                FlightQuote(
                    region=region.code,
                    domain=region.domain,
                    price=None,
                    currency=region.currency,
                    source_url=url,
                    status="scrapling_unavailable",
                    error=install_hint,
                )
            )
            continue

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
            has_captcha, detected_captcha_type = _check_captcha_in_page(page_text, page)
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

            if has_captcha and detected_captcha_type != "cloudflare":
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
            and captcha_type in {"cloudflare", "recaptcha", "hcaptcha"}
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
