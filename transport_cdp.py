"""Browser CDP (Chrome DevTools Protocol) transport."""

from __future__ import annotations

import argparse
import asyncio
import http.client
import json
import shutil
import subprocess
import time
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Optional
from urllib.parse import urlparse

import aiohttp

from app_paths import get_browser_profile_dir
from skyscanner_models import FlightQuote, RegionConfig
from skyscanner_page_parser import (
    PAGE_TEXT_CAPTURE_CONTEXT,
    PAGE_TEXT_CAPTURE_LIMIT,
    SORT_LABELS,
    SORT_SECTION_HINTS,
    extract_page_quote,
)
from skyscanner_regions import REGION_HOST_ALIASES
from transport_scrapling import _build_captcha_quote, _check_captcha_in_page

CDP_HTTP = "http://localhost:9222"
CDP_HOST_CANDIDATES = ("localhost", "::1", "127.0.0.1")
PROFILE_CACHE_PATHS = (
    "BrowserMetrics",
    "component_crx_cache",
    "GraphiteDawnCache",
    "GrShaderCache",
    "ShaderCache",
    "Default/Cache",
    "Default/Code Cache",
    "Default/GPUCache",
    "Default/DawnGraphiteCache",
    "Default/DawnWebGPUCache",
    "Default/blob_storage",
)


# ---------------------------------------------------------------------------
# Browser detection & profile management
# ---------------------------------------------------------------------------


def detect_browsers() -> dict[str, Path]:
    candidates = {
        "comet": Path("/Applications/Comet.app/Contents/MacOS/Comet"),
        "chrome": Path("/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"),
        "edge": Path("/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge"),
    }
    return {name: path for name, path in candidates.items() if path.exists()}


def profile_dir_for(browser_name: str) -> Path:
    return get_browser_profile_dir(browser_name)


def detect_cdp_version(port: int = 9222) -> Optional[dict[str, Any]]:
    for host in CDP_HOST_CANDIDATES:
        try:
            connection = http.client.HTTPConnection(host, port, timeout=2)
            connection.request("GET", "/json/version")
            response = connection.getresponse()
            if response.status != 200:
                response.read()
                continue
            payload = json.loads(response.read().decode("utf-8"))
        except (OSError, http.client.HTTPException, json.JSONDecodeError, TimeoutError):
            continue
        finally:
            try:
                connection.close()
            except Exception:
                pass

        if isinstance(payload, dict) and payload.get("Browser"):
            return payload
    return None


def wait_for_cdp(
    port: int = 9222, timeout: float = 12.0, interval: float = 0.5
) -> Optional[dict[str, Any]]:
    deadline = time.time() + timeout
    while time.time() < deadline:
        info = detect_cdp_version(port)
        if info:
            return info
        time.sleep(interval)
    return None


def prune_browser_profile(profile_dir: Path) -> tuple[int, list[str]]:
    removed: list[str] = []
    for rel_path in PROFILE_CACHE_PATHS:
        target = profile_dir / rel_path
        if not target.exists():
            continue
        try:
            if target.is_dir():
                shutil.rmtree(target)
            else:
                target.unlink()
            removed.append(rel_path)
        except OSError:
            continue
    return len(removed), removed


def launch_browser_with_cdp(
    port: int = 9222, start_url: str = "https://www.skyscanner.co.uk"
) -> str:
    browsers = detect_browsers()
    for browser_name in ("comet", "edge", "chrome"):
        binary = browsers.get(browser_name)
        if not binary:
            continue

        profile_dir = profile_dir_for(browser_name)
        profile_dir.mkdir(parents=True, exist_ok=True)
        removed_count, _ = prune_browser_profile(profile_dir)
        command = [
            str(binary),
            f"--remote-debugging-port={port}",
            f"--user-data-dir={profile_dir}",
            "--no-first-run",
            "--no-default-browser-check",
            "--new-window",
            start_url,
        ]
        try:
            subprocess.Popen(
                command,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                stdin=subprocess.DEVNULL,
                start_new_session=True,
            )
            if removed_count:
                return f"已清理 {removed_count} 处缓存并自动启动 {browser_name.capitalize()}，调试端口 {port}"
            return f"已尝试自动启动 {browser_name.capitalize()}，调试端口 {port}"
        except OSError as exc:
            return f"找到 {browser_name.capitalize()}，但启动失败: {exc}"

    return "没有找到可自动启动的 Comet、Edge 或 Chrome"


def ensure_cdp_ready(
    port: int = 9222,
    auto_launch: bool = True,
    wait_timeout: float = 12.0,
    start_url: str = "https://www.skyscanner.co.uk",
) -> dict[str, Any]:
    cdp_info = detect_cdp_version(port)
    if cdp_info:
        return cdp_info

    launch_note = None
    if auto_launch:
        launch_note = launch_browser_with_cdp(port=port, start_url=start_url)
        cdp_info = wait_for_cdp(port=port, timeout=wait_timeout)
        if cdp_info:
            return cdp_info

    raise RuntimeError(
        "未检测到浏览器调试端口 9222。"
        + (f" {launch_note}。" if launch_note else "")
        + " 请关闭已打开的浏览器后重试，或手动启动带 --remote-debugging-port=9222 的 Comet / Edge / Chrome。"
    )


# ---------------------------------------------------------------------------
# CDP low-level helpers
# ---------------------------------------------------------------------------


async def cdp_open_tab(session: aiohttp.ClientSession, url: str) -> dict[str, Any]:
    from urllib.parse import quote
    target_url = f"{CDP_HTTP}/json/new?{quote(url, safe=':/?&=%')}"
    async with session.put(target_url) as response:
        response.raise_for_status()
        return await response.json()


async def cdp_navigate_tab(
    session: aiohttp.ClientSession, tab_id: str, url: str
) -> str:
    """Navigate an existing tab to a new URL via CDP, returns new WS URL."""
    tabs = await cdp_list_tabs(session)
    seul_tab = None
    for t in tabs:
        if t.get("id") == tab_id:
            selected_tab = t
            break
    if not selected_tab:
        raise RuntimeError(f"Tab {tab_id} not found")

    ws_url = selected_tab.get("webSocketDebuggerUrl", "")
    if not ws_url:
        raise RuntimeError(f"No WebSocket URL for tab {tab_id}")

    timeout = aiohttp.ClientTimeout(total=15)
    async with aiohttp.ClientSession(timeout=timeout) as ws_session:
        async with ws_session.ws_connect(ws_url) as ws:
            await ws.send_json({
                "id": 1,
                "method": "Page.navigate",
                "params": {"url": url},
            })
            async for message in ws:
                if message.type != aiohttp.WSMsgType.TEXT:
                    continue
                payload = json.loads(message.data)
                if payload.get("id") != 1:
                    continue
                result = payload.get("result", {})
                if "error" in result:
                    raise RuntimeError(
                        f"Navigation failed: {json.dumps(result['error'])}"
                    )
                return ws_url


async def cdp_list_tabs(session: aiohttp.ClientSession) -> list[dict[str, Any]]:
    async with session.get(f"{CDP_HTTP}/json/list") as response:
        response.raise_for_status()
        return await response.json()


async def cdp_eval(ws_url: str, expression: str) -> Any:
    timeout = aiohttp.ClientTimeout(total=20)
    async with aiohttp.ClientSession(timeout=timeout) as session:
        async with session.ws_connect(ws_url) as ws:
            await ws.send_json(
                {
                    "id": 1,
                    "method": "Runtime.evaluate",
                    "params": {
                        "expression": expression,
                        "returnByValue": True,
                        "awaitPromise": True,
                    },
                }
            )
            async for message in ws:
                if message.type != aiohttp.WSMsgType.TEXT:
                    continue
                payload = json.loads(message.data)
                if payload.get("id") != 1:
                    continue
                result = payload.get("result", {}).get("result", {})
                if "value" in result:
                    return result["value"]
                raise RuntimeError(json.dumps(payload, ensure_ascii=False))
    raise RuntimeError("CDP evaluate failed")


def build_page_text_capture_expression(
    *,
    max_chars: int = PAGE_TEXT_CAPTURE_LIMIT,
    context_chars: int = PAGE_TEXT_CAPTURE_CONTEXT,
) -> str:
    markers = tuple(dict.fromkeys((*SORT_SECTION_HINTS, *SORT_LABELS)))
    return (
        "(() => {"
        "const text = document.body ? document.body.innerText : '';"
        f"const maxChars = {max_chars};"
        f"const contextChars = {context_chars};"
        "const title = document.title;"
        "const url = location.href;"
        "if (text.length <= maxChars) { return {title, url, text}; }"
        f"const markers = {json.dumps(markers, ensure_ascii=False)};"
        "const lower = text.toLowerCase();"
        "let index = -1;"
        "for (const marker of markers) {"
        "  const markerIndex = lower.indexOf(String(marker).toLowerCase());"
        "  if (markerIndex !== -1) { index = markerIndex; break; }"
        "}"
        "const start = index === -1 ? 0 : Math.max(0, index - contextChars);"
        "return {title, url, text: text.slice(start, start + maxChars)};"
        "})()"
    )


def _quote_from_cdp_payload(
    region: RegionConfig,
    payload: dict[str, Any],
    fallback_url: str,
) -> FlightQuote:
    page_url = str(payload.get("url", fallback_url))
    page_text = str(payload.get("text", ""))
    quote = extract_page_quote(region, page_url, page_text)
    quote.source_kind = "browser_fallback"
    if quote.price is not None:
        return quote

    has_captcha, captcha_type = _check_captcha_in_page(
        page_text,
        SimpleNamespace(url=page_url),
    )
    if has_captcha:
        quote = _build_captcha_quote(
            region,
            page_url,
            captcha_type,
            source_label="页面模式",
        )
        quote.source_kind = "browser_fallback"
        return quote
    return quote


def _get_matching_cdp_tabs(
    tabs: list[dict[str, Any]],
    region: RegionConfig,
    target_url: str,
) -> list[dict[str, Any]]:
    expected_path = urlparse(target_url).path
    allowed_hosts = REGION_HOST_ALIASES.get(region.code, {urlparse(region.domain).netloc})
    return [
        tab
        for tab in tabs
        if tab.get("type") == "page"
        and urlparse(str(tab.get("url", ""))).netloc in allowed_hosts
        and urlparse(str(tab.get("url", ""))).path == expected_path
    ]


def _get_domain_host(region: RegionConfig) -> str:
    """Return the host portion of a region's Skyscanner domain."""
    return urlparse(region.domain).netloc


def _any_tab_for_domain(
    tabs: list[dict[str, Any]], region: RegionConfig
) -> Optional[dict[str, Any]]:
    """Return a tab belonging to the given region's domain (non-captcha, type=page)."""
    allowed_hosts = REGION_HOST_ALIASES.get(region.code, {urlparse(region.domain).netloc})
    for tab in tabs:
        if tab.get("type") != "page":
            continue
        tab_host = urlparse(str(tab.get("url", ""))).netloc
        if tab_host in allowed_hosts and "captcha" not in str(tab.get("url", "")).lower():
            return tab
    return None


# ---------------------------------------------------------------------------
# compare_via_pages — CDP page transport
# ---------------------------------------------------------------------------

async def compare_via_pages(
    args: argparse.Namespace,
    selected_regions: list[RegionConfig],
    *,
    persist_failures: bool = True,
    build_search_url: Any = None,
    persist_failure_log: Any = None,
) -> list[FlightQuote]:
    if build_search_url is None:
        from scan_orchestrator import build_search_url as _bsu
        build_search_url = _bsu
    if persist_failure_log is None:
        from scan_orchestrator import _persist_failure_log as _pfl
        persist_failure_log = _pfl

    return_date = getattr(args, "return_date", None)
    route_key = f"{args.origin}_{args.destination}_{args.date.replace('-', '')}"
    if return_date:
        route_key = f"{route_key}_rt{return_date.replace('-', '')}"
    total_wait = max(args.timeout, args.page_wait + 60, 45)
    timeout = aiohttp.ClientTimeout(total=total_wait + 15)
    async with aiohttp.ClientSession(timeout=timeout) as session:
        requested_urls = {
            region.code: build_search_url(
                region, args.origin, args.destination, args.date, return_date
            )
            for region in selected_regions
        }

        # One tab per domain — navigate existing, only open new if needed
        domain_tabs: dict[str, str] = {}  # domain_host -> tab_id
        existing_tabs = await cdp_list_tabs(session)

        for region in selected_regions:
            url = requested_urls[region.code]
            domain_host = _get_domain_host(region)

            if domain_host in domain_tabs:
                # Already have a tab for this domain, navigate it
                await cdp_navigate_tab(session, domain_tabs[domain_host], url)
                continue

            existing = _any_tab_for_domain(existing_tabs, region)
            if existing:
                tab_id = existing.get("id", "")
                if tab_id:
                    await cdp_navigate_tab(session, tab_id, url)
                domain_tabs[domain_host] = tab_id
            else:
                new_tab = await cdp_open_tab(session, url)
                tab_id = new_tab.get("id", "")
                domain_tabs[domain_host] = tab_id

        await asyncio.sleep(args.page_wait)
        deadline = time.monotonic() + max(total_wait - args.page_wait, 10)
        poll_interval = 2.0
        latest_quotes: dict[str, FlightQuote] = {}
        pending_regions = {region.code: region for region in selected_regions}

        while pending_regions:
            tabs = await cdp_list_tabs(session)
            next_pending: dict[str, RegionConfig] = {}

            for region in pending_regions.values():
                target_url = requested_urls[region.code]
                expected_path = urlparse(target_url).path
                domain_host = _get_domain_host(region)
                domain_tab = _any_tab_for_domain(tabs, region)

                if not domain_tab:
                    latest_quotes[region.code] = FlightQuote(
                        region=region.code,
                        domain=region.domain,
                        price=None,
                        currency=region.currency,
                        source_url=target_url,
                        status="page_missing",
                        error="CDP tabs missing for domain",
                    )
                    if time.monotonic() < deadline:
                        next_pending[region.code] = region
                    continue

                ws_url = str(domain_tab.get("webSocketDebuggerUrl", ""))
                if not ws_url:
                    latest_quotes[region.code] = FlightQuote(
                        region=region.code,
                        domain=region.domain,
                        price=None,
                        currency=region.currency,
                        source_url=str(domain_tab.get("url", "")),
                        status="page_missing_ws",
                        error="No webSocketDebuggerUrl",
                    )
                    if time.monotonic() < deadline:
                        next_pending[region.code] = region
                    continue

                payload = await cdp_eval(
                    ws_url,
                    build_page_text_capture_expression(),
                )
                quote = _quote_from_cdp_payload(
                    region,
                    payload,
                    str(domain_tab.get("url", "")),
                )
                page_text = str(payload.get("text", ""))

                final_quote = quote or FlightQuote(
                    region=region.code,
                    domain=region.domain,
                    price=None,
                    currency=region.currency,
                    source_url=target_url,
                    status="page_parse_failed",
                    error="No price found",
                )

                if persist_failures and final_quote.price is None:
                    persist_failure_log(
                        final_quote,
                        transport="page",
                        route_key=route_key,
                        page_text=page_text,
                        extra={"expected_path": expected_path, "domain_host": domain_host},
                    )

                latest_quotes[region.code] = final_quote
                if final_quote.price is None and time.monotonic() < deadline:
                    next_pending[region.code] = region

            if not next_pending:
                break
            pending_regions = next_pending
            await asyncio.sleep(poll_interval)

    ordered_quotes: list[FlightQuote] = []
    for region in selected_regions:
        quote = latest_quotes.get(region.code)
        if quote is not None:
            ordered_quotes.append(quote)
    return ordered_quotes
