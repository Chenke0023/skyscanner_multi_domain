"""Scrapling-based page fetching transport."""

from __future__ import annotations

import asyncio
import argparse
from contextlib import asynccontextmanager
from dataclasses import dataclass
import http.client
import json
from pathlib import Path
import re
from typing import Any, Callable
from urllib.parse import urlparse, urlunparse

import aiohttp
from bs4 import BeautifulSoup

from app_paths import get_browser_profile_dir
from attempt_trace import emit_trace
from skyscanner_models import FlightQuote, RegionConfig
from skyscanner_page_parser import extract_page_quote
from skyscanner_regions import REGION_HOST_ALIASES


PLAYWRIGHT_PROBE_TEXT_LIMIT = 12000
BROWSER_CDP_PORT = 9222
BROWSER_BINARY_CANDIDATES = {
    "comet": Path("/Applications/Comet.app/Contents/MacOS/Comet"),
    "chrome": Path("/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"),
    "edge": Path("/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge"),
}
CDP_HOST_CANDIDATES = ("localhost", "::1", "127.0.0.1")
_PROFILE_LOCKS: dict[str, asyncio.Lock] = {}

# ── Fetch pipeline stages ────────────────────────────────────────────────────
# Each stage is a named step in the Scrapling fetch chain. Pipeline presets
# control which stages run and in what order.

FETCH_STAGE_CDP_REUSE = "cdp_reuse"
FETCH_STAGE_PLAYWRIGHT = "playwright_probe"
FETCH_STAGE_STEALTH = "scrapling_stealth"
FETCH_STAGE_CAPTCHA = "captcha_solve"
FETCH_STAGE_HTTP = "scrapling_http"

FETCH_PIPELINES: dict[str, tuple[str, ...]] = {
    "fast": (
        FETCH_STAGE_STEALTH,
        FETCH_STAGE_HTTP,
    ),
    "balanced": (
        FETCH_STAGE_CDP_REUSE,
        FETCH_STAGE_STEALTH,
        FETCH_STAGE_CAPTCHA,
        FETCH_STAGE_HTTP,
    ),
    "session_heavy": (
        FETCH_STAGE_CDP_REUSE,
        FETCH_STAGE_PLAYWRIGHT,
        FETCH_STAGE_STEALTH,
        FETCH_STAGE_CAPTCHA,
        FETCH_STAGE_HTTP,
    ),
}

DEFAULT_FETCH_PIPELINE = "balanced"


@dataclass(frozen=True)
class ProbeOutcome:
    quote: FlightQuote
    page_text: str


def _detect_local_browsers() -> dict[str, Path]:
    return {
        name: path for name, path in BROWSER_BINARY_CANDIDATES.items() if path.exists()
    }


def _profile_has_state(profile_dir: Path) -> bool:
    try:
        return profile_dir.exists() and any(profile_dir.iterdir())
    except OSError:
        return False


def _get_persistent_probe_candidates() -> tuple[tuple[str, Path, Path], ...]:
    browsers = _detect_local_browsers()
    candidates: list[tuple[str, Path, Path]] = []
    for browser_name in ("comet", "edge", "chrome"):
        binary = browsers.get(browser_name)
        if binary is None:
            continue
        profile_dir = get_browser_profile_dir(browser_name)
        if _profile_has_state(profile_dir):
            candidates.append((browser_name, binary, profile_dir))
    return tuple(candidates)


def _get_persistent_profile_dirs() -> tuple[Path, ...]:
    profile_dirs: list[Path] = []
    for browser_name in ("comet", "edge", "chrome"):
        profile_dir = get_browser_profile_dir(browser_name)
        if _profile_has_state(profile_dir):
            profile_dirs.append(profile_dir)
    return tuple(profile_dirs)


def _build_cookie_scope_urls(region: RegionConfig, url: str) -> tuple[str, ...]:
    parsed = urlparse(url)
    hosts = REGION_HOST_ALIASES.get(region.code, {parsed.netloc})
    urls = {
        urlunparse(
            (
                parsed.scheme or "https",
                host,
                parsed.path or "/",
                parsed.params,
                parsed.query,
                parsed.fragment,
            )
        )
        for host in hosts
        if host
    }
    urls.add(region.domain)
    return tuple(sorted(urls))


def _build_request_headers(region: RegionConfig) -> dict[str, str]:
    headers = {
        "accept-language": region.locale,
        "referer": region.domain,
    }
    version_info = _cdp_get_json("/json/version")
    if isinstance(version_info, dict):
        user_agent = str(version_info.get("User-Agent", "")).strip()
        if user_agent:
            headers["user-agent"] = user_agent
    return headers


def _cdp_get_json(path: str, port: int = BROWSER_CDP_PORT) -> Any:
    for host in CDP_HOST_CANDIDATES:
        connection: http.client.HTTPConnection | None = None
        try:
            connection = http.client.HTTPConnection(host, port, timeout=2)
            connection.request("GET", path)
            response = connection.getresponse()
            if response.status != 200:
                response.read()
                continue
            return json.loads(response.read().decode("utf-8"))
        except (OSError, http.client.HTTPException, json.JSONDecodeError, TimeoutError):
            continue
        finally:
            try:
                if connection is not None:
                    connection.close()
            except Exception:
                pass
    return None


async def _cdp_send_command(
    ws_url: str,
    method: str,
    params: dict[str, Any] | None = None,
    *,
    timeout_seconds: int = 10,
) -> Any:
    request_id = 1
    timeout = aiohttp.ClientTimeout(total=timeout_seconds)
    async with aiohttp.ClientSession(timeout=timeout) as session:
        async with session.ws_connect(ws_url) as ws:
            await ws.send_json(
                {
                    "id": request_id,
                    "method": method,
                    "params": params or {},
                }
            )
            async for message in ws:
                if message.type != aiohttp.WSMsgType.TEXT:
                    continue
                payload = json.loads(message.data)
                if payload.get("id") != request_id:
                    continue
                if "error" in payload:
                    raise RuntimeError(json.dumps(payload["error"], ensure_ascii=False))
                return payload.get("result")
    return None


def _detect_cdp_page_ws_url(port: int = BROWSER_CDP_PORT) -> str | None:
    tabs = _cdp_get_json("/json/list", port=port)
    if isinstance(tabs, list):
        for tab in tabs:
            if tab.get("type") != "page":
                continue
            ws_url = str(tab.get("webSocketDebuggerUrl", "")).strip()
            if ws_url:
                return ws_url
    return None


async def _cdp_get_cookie_jar(
    region: RegionConfig,
    url: str,
    *,
    port: int = BROWSER_CDP_PORT,
) -> list[dict[str, Any]]:
    ws_url = _detect_cdp_page_ws_url(port=port)
    if not ws_url:
        return []

    try:
        result = await _cdp_send_command(
            ws_url,
            "Network.getCookies",
            {"urls": list(_build_cookie_scope_urls(region, url))},
        )
        cookies = (result or {}).get("cookies", [])
        if not isinstance(cookies, list):
            return []
        return [cookie for cookie in cookies if isinstance(cookie, dict)]
    except Exception:
        return []

    return []


def _get_matching_cdp_page_ws_urls(
    region: RegionConfig,
    url: str,
    *,
    port: int = BROWSER_CDP_PORT,
) -> tuple[str, ...]:
    tabs = _cdp_get_json("/json/list", port=port)
    if not isinstance(tabs, list):
        return ()

    parsed_url = urlparse(url)
    allowed_hosts = REGION_HOST_ALIASES.get(region.code, {parsed_url.netloc})
    expected_path = parsed_url.path
    matches: list[str] = []
    for tab in tabs:
        if tab.get("type") != "page":
            continue
        tab_url = str(tab.get("url", "")).strip()
        parsed_tab = urlparse(tab_url)
        if parsed_tab.netloc not in allowed_hosts or parsed_tab.path != expected_path:
            continue
        ws_url = str(tab.get("webSocketDebuggerUrl", "")).strip()
        if ws_url:
            matches.append(ws_url)
    return tuple(dict.fromkeys(matches))


def _build_cdp_page_probe_expression(text_limit: int = PLAYWRIGHT_PROBE_TEXT_LIMIT) -> str:
    return (
        "(() => ({"
        "url: location.href,"
        "title: document.title,"
        f"text: (document.body ? document.body.innerText : '').slice(0, {text_limit})"
        "}))()"
    )


async def _probe_existing_cdp_page(
    url: str,
    region: RegionConfig,
) -> ProbeOutcome | None:
    for ws_url in _get_matching_cdp_page_ws_urls(region, url):
        try:
            payload = await _cdp_send_command(
                ws_url,
                "Runtime.evaluate",
                {
                    "expression": _build_cdp_page_probe_expression(),
                    "returnByValue": True,
                    "awaitPromise": True,
                },
            )
        except Exception:
            continue

        if not isinstance(payload, dict):
            continue
        result = payload.get("result", {})
        if not isinstance(result, dict):
            continue

        page_payload = result.get("value")
        if not isinstance(page_payload, dict):
            continue

        page_url = str(page_payload.get("url", url))
        page_text = str(page_payload.get("text", "")).strip()
        if not page_text:
            continue

        quote = extract_page_quote(region, page_url, page_text)
        quote.source_kind = "cdp_reuse"
        if quote.price is not None:
            return ProbeOutcome(quote=quote, page_text=page_text)

        has_captcha, captcha_type = _check_captcha_in_page(page_text)
        if has_captcha:
            return ProbeOutcome(
                quote=_build_captcha_quote(
                    region,
                    page_url,
                    captcha_type,
                    source_label="CDP 已打开页面",
                ),
                page_text=page_text,
            )

    return None


async def _resolve_scrapling_state_overrides(
    region: RegionConfig,
    url: str,
    *,
    for_stealth: bool,
) -> dict[str, Any]:
    cdp_cookies = await _cdp_get_cookie_jar(region, url)
    if cdp_cookies:
        if for_stealth:
            return {"cookies": cdp_cookies}
        return {
            "cookies": {
                str(cookie.get("name", "")).strip(): str(cookie.get("value", ""))
                for cookie in cdp_cookies
                if str(cookie.get("name", "")).strip()
            }
        }

    for profile_dir in _get_persistent_profile_dirs():
        return {"user_data_dir": str(profile_dir)}
    return {}


def _get_profile_lock(profile_dir: str) -> asyncio.Lock:
    lock = _PROFILE_LOCKS.get(profile_dir)
    if lock is None:
        lock = asyncio.Lock()
        _PROFILE_LOCKS[profile_dir] = lock
    return lock


@asynccontextmanager
async def _acquire_profile_lock(state_overrides: dict[str, Any] | None):
    profile_dir = str((state_overrides or {}).get("user_data_dir") or "").strip()
    if not profile_dir:
        yield
        return
    lock = _get_profile_lock(profile_dir)
    async with lock:
        yield


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


def _looks_like_shell_page(page_text: str) -> bool:
    normalized_lines = [line.strip() for line in page_text.splitlines() if line.strip()]
    if not normalized_lines:
        return False
    if len(normalized_lines) <= 2 and len("\n".join(normalized_lines)) < 200:
        return True
    return False


def _state_usage(state_overrides: dict[str, Any] | None) -> tuple[bool, bool]:
    """Return (used_cdp_cookies, used_profile_dir) from state_overrides."""
    state_overrides = state_overrides or {}
    return "cookies" in state_overrides, "user_data_dir" in state_overrides


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


def _build_captcha_quote(
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


async def _probe_page_with_playwright(
    url: str, region: RegionConfig, timeout_ms: int
) -> ProbeOutcome | None:
    try:
        from playwright.async_api import TimeoutError as PlaywrightTimeoutError
        from playwright.async_api import async_playwright
    except ImportError:
        return None

    async def probe_page(page: Any) -> ProbeOutcome | None:
        page_text = ""
        final_url = url
        timed_out = False

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
        quote.source_kind = "browser_fallback"
        if quote.price is not None:
            return ProbeOutcome(quote=quote, page_text=page_text)

        has_captcha, captcha_type = _check_captcha_in_page(page_text)
        if has_captcha:
            return ProbeOutcome(
                quote=_build_captcha_quote(
                    region,
                    final_url,
                    captcha_type,
                    source_label="Playwright 预探测",
                ),
                page_text=page_text,
            )

        if quote.status == "page_loading":
            quote.error = "Playwright 预探测显示结果页仍在加载"
            quote.source_kind = "browser_fallback"
            return ProbeOutcome(quote=quote, page_text=page_text)

        if timed_out and page_text:
            return ProbeOutcome(
                quote=FlightQuote(
                    region=region.code,
                    domain=region.domain,
                    price=None,
                    currency=region.currency,
                    source_url=final_url,
                    status="page_loading",
                    error="Playwright 预探测在 domcontentloaded 前超时",
                ),
                page_text=page_text,
            )

        return None

    try:
        async with async_playwright() as playwright:
            headers = _build_request_headers(region)
            for _, binary, profile_dir in _get_persistent_probe_candidates():
                context = None
                try:
                    async with _acquire_profile_lock({"user_data_dir": str(profile_dir)}):
                        context = await playwright.chromium.launch_persistent_context(
                            str(profile_dir),
                            executable_path=str(binary),
                            headless=True,
                            locale=region.locale,
                            extra_http_headers=headers,
                        )
                        page = context.pages[0] if context.pages else await context.new_page()
                        outcome = await probe_page(page)
                        if outcome is not None:
                            return outcome
                except Exception:
                    pass
                finally:
                    try:
                        if context is not None:
                            await context.close()
                    except Exception:
                        pass

            browser = None
            context = None
            page = None
            try:
                browser = await playwright.chromium.launch(headless=True)
                context = await browser.new_context(
                    locale=region.locale,
                    extra_http_headers=headers,
                )
                page = await context.new_page()
                return await probe_page(page)
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
    except Exception:
        return None

    return None


async def compare_via_scrapling(
    args: argparse.Namespace,
    selected_regions: list[RegionConfig],
    *,
    persist_failures: bool = True,
    on_region_start: Callable[[RegionConfig], None] | None = None,
    on_region_complete: Callable[[RegionConfig, "FlightQuote"], None] | None = None,
    build_search_url: Callable[..., str] | None = None,
    persist_failure_log: Callable[..., FlightQuote] | None = None,
    region_concurrency: int = 1,
    run_id: str = "",
    fetch_pipeline: str = DEFAULT_FETCH_PIPELINE,
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

    timeout_ms = max(int(getattr(args, "timeout", 30) * 1000), 10000)
    wait_ms = max(int(getattr(args, "page_wait", 8) * 1000), 3000)
    timeout_seconds = max(int(getattr(args, "timeout", 30)), 10)
    return_date = getattr(args, "return_date", None)
    route_key = f"{args.origin}_{args.destination}_{args.date.replace('-', '')}"
    if return_date:
        route_key = f"{route_key}_rt{return_date.replace('-', '')}"

    async def scan_region(region: RegionConfig) -> FlightQuote:
        if on_region_start is not None:
            on_region_start(region)
        url = build_search_url(
            region, args.origin, args.destination, args.date, return_date
        )
        page_text = ""
        latest_quote: FlightQuote | None = None
        latest_error: str | None = None
        detected_captcha_type = ""
        _attempt_counter = [0]
        _last_state_overrides: dict[str, Any] = {}

        def _next_attempt() -> int:
            val = _attempt_counter[0]
            _attempt_counter[0] += 1
            return val

        pipeline_stages = FETCH_PIPELINES.get(
            fetch_pipeline, FETCH_PIPELINES[DEFAULT_FETCH_PIPELINE]
        )

        def _handle_probe_outcome(
            outcome: Any, source: str
        ) -> FlightQuote | None:
            """Process a probe outcome: emit trace, persist failures, notify
            callbacks, and return the quote if it's a terminal result."""
            nonlocal page_text
            if hasattr(outcome, "quote"):
                probe_quote = outcome.quote
                probe_page_text = getattr(outcome, "page_text", "")
            else:
                probe_quote = outcome
                probe_page_text = getattr(outcome, "page_text", "")
            page_text = probe_page_text
            if source == "playwright" and not getattr(probe_quote, "source_kind", None):
                probe_quote.source_kind = "browser_fallback"
            if persist_failures and probe_quote.price is None:
                persist_failure_log(
                    probe_quote,
                    transport="scrapling",
                    route_key=route_key,
                    page_text=probe_page_text,
                    extra={"locale": region.locale, "probe": source},
                )
            if on_region_complete is not None:
                on_region_complete(region, probe_quote)
            emit_trace(
                run_id=run_id,
                route_key=route_key,
                region=region.code,
                transport="scrapling",
                attempt_index=0,
                source_kind=getattr(probe_quote, "source_kind", None) or source,
                used_cdp_cookies=source == "cdp_existing_page",
                used_profile_dir=source == "playwright",
                wait_ms=timeout_ms,
                load_dom=False,
                network_idle=False,
                page_text_len=len(probe_page_text),
                page_url=url,
                status=probe_quote.status,
                failure_reason=probe_quote.error,
                price=probe_quote.price,
                currency=probe_quote.currency,
            )
            return probe_quote

        # Stage: CDP reuse — try an already-open browser tab
        if FETCH_STAGE_CDP_REUSE in pipeline_stages:
            probe_outcome = await _probe_existing_cdp_page(url, region)
            if probe_outcome is not None:
                return _handle_probe_outcome(probe_outcome, "cdp_existing_page")

        # Stage: Playwright probe — launch a persistent browser context
        if FETCH_STAGE_PLAYWRIGHT in pipeline_stages:
            probe_outcome = await _probe_page_with_playwright(url, region, timeout_ms)
            if probe_outcome is not None:
                return _handle_probe_outcome(probe_outcome, "playwright")

        try:
            from scrapling import Fetcher, StealthyFetcher
        except ImportError:
            install_hint = '未安装 Scrapling，请先执行: pip install "scrapling[fetchers]"'
            quote = FlightQuote(
                region=region.code,
                domain=region.domain,
                price=None,
                currency=region.currency,
                source_url=url,
                status="scrapling_unavailable",
                error=install_hint,
            )
            if on_region_complete is not None:
                on_region_complete(region, quote)
            return quote

        async def fetch_with_stealth(
            *,
            solve_cloudflare: bool,
            wait_override_ms: int | None = None,
            load_dom_override: bool | None = None,
            network_idle_override: bool | None = None,
            state_overrides: dict[str, Any] | None = None,
        ) -> Any:
            current_state_overrides = state_overrides
            if current_state_overrides is None:
                current_state_overrides = await _resolve_scrapling_state_overrides(
                    region,
                    url,
                    for_stealth=True,
                )
            async with _acquire_profile_lock(current_state_overrides):
                return await asyncio.to_thread(
                    StealthyFetcher.fetch,
                    url,
                    headless=True,
                    network_idle=(
                        network_idle_override
                        if network_idle_override is not None
                        else False
                    ),
                    load_dom=load_dom_override if load_dom_override is not None else False,
                    timeout=timeout_ms,
                    wait=wait_override_ms or wait_ms,
                    solve_cloudflare=solve_cloudflare,
                    google_search=False,
                    locale=region.locale,
                    extra_headers=_build_request_headers(region),
                    **current_state_overrides,
                )

        def _emit_scrapling_trace(
            source_kind: str,
            attempt_idx: int,
            *,
            used_cdp: bool = False,
            used_profile: bool = False,
            wait_override_ms: int | None = None,
            load_dom: bool = False,
            network_idle: bool = False,
            final_quote: "FlightQuote | None" = None,
            final_page_text: str = "",
        ) -> None:
            quote = final_quote or latest_quote
            emit_trace(
                run_id=run_id,
                route_key=route_key,
                region=region.code,
                transport="scrapling",
                attempt_index=attempt_idx,
                source_kind=source_kind,
                used_cdp_cookies=used_cdp,
                used_profile_dir=used_profile,
                wait_ms=wait_override_ms or wait_ms,
                load_dom=load_dom,
                network_idle=network_idle,
                page_text_len=len(final_page_text) if final_page_text else len(page_text),
                page_url=url,
                status=quote.status if quote else "unknown",
                failure_reason=quote.error if quote else None,
                price=quote.price if quote else None,
                currency=quote.currency if quote else region.currency,
            )

        stealth_attempts = (
            {"solve_cloudflare": False, "wait_ms": wait_ms},
            {"solve_cloudflare": True, "wait_ms": max(wait_ms, 8000)},
            {"solve_cloudflare": True, "wait_ms": max(wait_ms * 2, 15000)},
        )

        attempt_index = 0
        source_kind = "scrapling_stealth"
        for attempt in (stealth_attempts if FETCH_STAGE_STEALTH in pipeline_stages else ()):
            attempt_index = _next_attempt()
            cf_suffix = "_cf" if attempt["solve_cloudflare"] else ""
            source_kind = f"scrapling_stealth{cf_suffix}"
            state_overrides = await _resolve_scrapling_state_overrides(
                region,
                url,
                for_stealth=True,
            )
            _last_state_overrides = state_overrides
            used_cdp, used_profile = _state_usage(state_overrides)
            _emit_scrapling_trace(source_kind, attempt_index, used_cdp=used_cdp, used_profile=used_profile)
            try:
                page = await fetch_with_stealth(
                    solve_cloudflare=attempt["solve_cloudflare"],
                    wait_override_ms=attempt["wait_ms"],
                    state_overrides=state_overrides,
                )
            except Exception as exc:
                latest_error = f"Scrapling 抓取失败: {exc}"
                _emit_scrapling_trace(source_kind, attempt_index, used_cdp=used_cdp, used_profile=used_profile)
                continue

            page_text = _extract_scrapling_page_text(page)
            has_captcha, detected_captcha_type = _check_captcha_in_page(page_text, page)
            page_url = _coerce_page_snippet(getattr(page, "url", None)) or url
            if has_captcha and detected_captcha_type == "px":
                latest_quote = _build_captcha_quote(
                    region,
                    page_url,
                    detected_captcha_type,
                    source_label="Scrapling",
                )
                latest_quote.source_kind = "live"
                _emit_scrapling_trace(source_kind, attempt_index, used_cdp=used_cdp, used_profile=used_profile, final_quote=latest_quote, final_page_text=page_text)
                break
            if not page_text:
                latest_quote = FlightQuote(
                    region=region.code,
                    domain=region.domain,
                    price=None,
                    currency=region.currency,
                    source_url=url,
                    status="scrapling_parse_failed",
                    error="Scrapling 返回内容为空，未提取到可解析文本",
                    source_kind="live",
                )
                _emit_scrapling_trace(source_kind, attempt_index, used_cdp=used_cdp, used_profile=used_profile, final_quote=latest_quote, final_page_text=page_text)
                continue

            latest_quote = extract_page_quote(region, page_url, page_text)
            latest_quote.source_kind = latest_quote.source_kind or "live"
            if latest_quote.price is not None:
                _emit_scrapling_trace(source_kind, attempt_index, used_cdp=used_cdp, used_profile=used_profile, final_quote=latest_quote, final_page_text=page_text)
                break

            if (
                state_overrides
                and latest_quote.status == "page_parse_failed"
                and _looks_like_shell_page(page_text)
            ):
                try:
                    dom_page = await fetch_with_stealth(
                        solve_cloudflare=attempt["solve_cloudflare"],
                        wait_override_ms=max(attempt["wait_ms"], 12000),
                        load_dom_override=True,
                        network_idle_override=True,
                        state_overrides=state_overrides,
                    )
                    dom_page_text = _extract_scrapling_page_text(dom_page)
                    dom_page_url = (
                        _coerce_page_snippet(getattr(dom_page, "url", None)) or page_url
                    )
                    if dom_page_text:
                        page_text = dom_page_text
                        latest_quote = extract_page_quote(
                            region,
                            dom_page_url,
                            dom_page_text,
                        )
                        latest_quote.source_kind = latest_quote.source_kind or "live"
                        _emit_scrapling_trace(
                            "scrapling_dom_retry", attempt_index,
                            used_cdp=used_cdp, used_profile=used_profile,
                            load_dom=True,
                            network_idle=True,
                            wait_override_ms=max(attempt["wait_ms"], 12000),
                            final_quote=latest_quote,
                            final_page_text=dom_page_text,
                        )
                        if latest_quote.price is not None:
                            break
                except Exception:
                    pass

            if has_captcha and detected_captcha_type != "cloudflare":
                latest_quote = _build_captcha_quote(
                    region,
                    page_url,
                    detected_captcha_type,
                    source_label="Scrapling",
                )
                latest_quote.source_kind = "live"
                _emit_scrapling_trace(source_kind, attempt_index, used_cdp=used_cdp, used_profile=used_profile, final_quote=latest_quote, final_page_text=page_text)
                break

            if latest_quote.status not in {
                "page_challenge",
                "page_loading",
                "page_parse_failed",
            }:
                _emit_scrapling_trace(source_kind, attempt_index, used_cdp=used_cdp, used_profile=used_profile, final_quote=latest_quote, final_page_text=page_text)
                break

        if latest_quote is not None:
            if latest_quote.price is not None:
                return latest_quote
            if latest_quote.status == "px_challenge":
                return latest_quote

        # Check if page contains captcha and try to solve it
        has_captcha, captcha_type = _check_captcha_in_page(page_text)
        if (
            has_captcha
            and captcha_type in {"cloudflare", "recaptcha", "hcaptcha"}
            and latest_quote is not None
            and latest_quote.price is None
            and CaptchaSolverClient is not None
            and FETCH_STAGE_CAPTCHA in pipeline_stages
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
                        captcha_state = await _resolve_scrapling_state_overrides(
                            region, url, for_stealth=True
                        )
                        _last_state_overrides = captcha_state
                        _captcha_used_cdp, _captcha_used_profile = _state_usage(captcha_state)
                        page = await fetch_with_stealth(
                            solve_cloudflare=True,
                            wait_override_ms=max(wait_ms * 2, 15000),
                            state_overrides=captcha_state,
                        )
                        page_text = _extract_scrapling_page_text(page)
                        if page_text:
                            latest_quote = extract_page_quote(region, url, page_text)
                            latest_quote.source_kind = latest_quote.source_kind or "live"
                            _emit_scrapling_trace(
                                "captcha_solve", _next_attempt(),
                                used_cdp=_captcha_used_cdp, used_profile=_captcha_used_profile,
                                wait_override_ms=max(wait_ms * 2, 15000),
                                final_quote=latest_quote,
                                final_page_text=page_text,
                            )
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
                    source_kind="live",
                )
            except Exception:
                pass

        if (latest_quote is None or latest_quote.price is None) and FETCH_STAGE_HTTP in pipeline_stages:
            try:
                state_overrides = await _resolve_scrapling_state_overrides(
                    region,
                    url,
                    for_stealth=False,
                )
                async with _acquire_profile_lock(state_overrides):
                    page = await asyncio.to_thread(
                        Fetcher.get,
                        url,
                        timeout=timeout_seconds,
                        stealthy_headers=True,
                        follow_redirects=True,
                        headers=_build_request_headers(region),
                        **state_overrides,
                    )
                page_text = _extract_scrapling_page_text(page)
                page_url = _coerce_page_snippet(getattr(page, "url", None)) or url
                if page_text:
                    has_captcha, detected_captcha_type = _check_captcha_in_page(
                        page_text,
                        page,
                    )
                    if has_captcha:
                        latest_quote = _build_captcha_quote(
                            region,
                            page_url,
                            detected_captcha_type,
                            source_label="Scrapling fallback",
                        )
                        latest_quote.source_kind = "live"
                    else:
                        latest_quote = extract_page_quote(region, page_url, page_text)
                        latest_quote.source_kind = latest_quote.source_kind or "live"
                        _last_state_overrides = state_overrides
                        _http_used_cdp, _http_used_profile = _state_usage(state_overrides)
                        _emit_scrapling_trace(
                            "scrapling_http_fallback", _next_attempt(),
                            used_cdp=_http_used_cdp,
                            used_profile=_http_used_profile,
                            final_quote=latest_quote,
                            final_page_text=page_text,
                        )
                elif latest_quote is None:
                    latest_quote = FlightQuote(
                        region=region.code,
                        domain=region.domain,
                        price=None,
                        currency=region.currency,
                        source_url=url,
                        status="scrapling_parse_failed",
                        error="Scrapling 返回内容为空，未提取到可解析文本",
                        source_kind="live",
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
                source_kind="live",
            )
        elif latest_quote.price is None and latest_error and not latest_quote.error:
            latest_quote.error = latest_error
        if latest_quote is not None and not latest_quote.source_kind:
            latest_quote.source_kind = "live"

        if persist_failures and latest_quote.price is None:
            persist_failure_log(
                latest_quote,
                transport="scrapling",
                route_key=route_key,
                page_text=page_text,
                extra={"locale": region.locale},
            )

        if on_region_complete is not None:
            on_region_complete(region, latest_quote)

        _final_used_cdp, _final_used_profile = _state_usage(_last_state_overrides)
        emit_trace(
            run_id=run_id,
            route_key=route_key,
            region=region.code,
            transport="scrapling",
            attempt_index=_next_attempt(),
            source_kind=latest_quote.source_kind or "unknown",
            used_cdp_cookies=_final_used_cdp,
            used_profile_dir=_final_used_profile,
            wait_ms=wait_ms,
            load_dom=False,
            network_idle=False,
            page_text_len=len(page_text),
            page_url=url,
            status=latest_quote.status,
            failure_reason=latest_quote.error,
            price=latest_quote.price,
            currency=latest_quote.currency,
        )
        return latest_quote

    concurrency = max(int(region_concurrency), 1)
    semaphore = asyncio.Semaphore(concurrency)

    async def run_with_limit(index: int, region: RegionConfig) -> tuple[int, FlightQuote]:
        async with semaphore:
            return (index, await scan_region(region))

    ordered_results = await asyncio.gather(
        *(run_with_limit(index, region) for index, region in enumerate(selected_regions))
    )
    ordered_results.sort(key=lambda item: item[0])
    return [quote for _, quote in ordered_results]
