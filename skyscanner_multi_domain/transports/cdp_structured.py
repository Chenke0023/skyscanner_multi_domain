"""Structured CDP transport.

This transport is intentionally experimental: it keeps the public output as
FlightQuote while collecting structured evidence from DOM cards, hydration
scripts, and the existing text parser fallback.
"""

from __future__ import annotations

import argparse
import asyncio
import base64
import json
import time
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import aiohttp

from skyscanner_multi_domain.models import FlightQuote, RegionConfig
from skyscanner_multi_domain.parsing.dom_parser import parse_dom_cards, parse_text_fallback
from skyscanner_multi_domain.parsing.hydration_parser import parse_hydration_scripts
from skyscanner_multi_domain.parsing.network_parser import parse_network_json
from skyscanner_multi_domain.parsing.quote_merge import resolve_quote
from skyscanner_multi_domain.runtime.paths import get_failure_log_file
from skyscanner_multi_domain.transports.cdp import (
    cdp_close_tab,
    cdp_eval,
    cdp_list_tabs,
    cdp_navigate_tab,
    cdp_open_tab,
    TabNotFoundError,
)


async def _cdp_command(ws_url: str, method: str, params: dict[str, Any] | None = None) -> Any:
    timeout = aiohttp.ClientTimeout(total=20)
    async with aiohttp.ClientSession(timeout=timeout) as session:
        async with session.ws_connect(ws_url) as ws:
            await ws.send_json({"id": 1, "method": method, "params": params or {}})
            async for message in ws:
                if message.type != aiohttp.WSMsgType.TEXT:
                    continue
                payload = json.loads(message.data)
                if payload.get("id") != 1:
                    continue
                if "error" in payload:
                    raise RuntimeError(json.dumps(payload["error"], ensure_ascii=False))
                return payload.get("result")
    return None


def _capture_expression() -> str:
    return r"""
(() => {
  const priceRegex = /(?:¥|￥|HK\$|S\$|US\$|A\$|CA\$|£|€|\$|₩|₹|CNY|HKD|SGD|GBP|EUR|USD|JPY|KRW|INR)\s?[\d,]+(?:\.\d+)?/i;
  function visible(el) {
    const r = el.getBoundingClientRect();
    const s = getComputedStyle(el);
    return r.width > 0 && r.height > 0 && s.visibility !== "hidden" && s.display !== "none";
  }
  function nearestCard(el) {
    let cur = el;
    for (let depth = 0; cur && depth < 8; depth++, cur = cur.parentElement) {
      const text = cur.innerText || "";
      if (text.length < 3000 && /best|cheapest|lowest|最佳|最便宜|price|flight|航班/i.test(text)) {
        return cur;
      }
    }
    return el;
  }
  const domCards = [...document.querySelectorAll("body *")]
    .filter(visible)
    .filter(el => priceRegex.test(el.innerText || ""))
    .slice(0, 200)
    .map((el, idx) => {
      const card = nearestCard(el);
      const rect = card.getBoundingClientRect();
      return {
        idx,
        priceText: (el.innerText || "").slice(0, 500),
        cardText: (card.innerText || "").slice(0, 2500),
        role: el.getAttribute("role"),
        aria: el.getAttribute("aria-label"),
        tag: el.tagName,
        cardTag: card.tagName,
        x: rect.x,
        y: rect.y,
        w: rect.width,
        h: rect.height
      };
    });
  const hydrationScripts = [...document.querySelectorAll("script")]
    .map((s, i) => ({
      index: i,
      type: s.type || "",
      id: s.id || "",
      text: (s.textContent || "").slice(0, 2000000)
    }))
    .filter(x => /price|itinerary|flight|currency/i.test(x.text));
  return {
    title: document.title,
    url: location.href,
    pageText: document.body ? document.body.innerText.slice(0, 80000) : "",
    domCards,
    hydrationScripts
  };
})()
""".strip()


def _domain_host(region: RegionConfig) -> str:
    from urllib.parse import urlparse

    return urlparse(region.domain).netloc


def _route_key(args: argparse.Namespace) -> str:
    key = f"{args.origin}_{args.destination}_{args.date.replace('-', '')}"
    return_date = getattr(args, "return_date", None)
    if return_date:
        key = f"{key}_rt{return_date.replace('-', '')}"
    return key


def _write_diagnostics(
    *,
    args: argparse.Namespace,
    region: RegionConfig,
    capture: dict[str, Any],
    result: Any,
    network_candidates: list[Any] | None = None,
    screenshot_png: bytes | None = None,
) -> str:
    base = get_failure_log_file("cdp_structured") / _route_key(args) / region.code
    base.mkdir(parents=True, exist_ok=True)
    final_quote = result.final_quote
    (base / "meta.json").write_text(
        json.dumps(
            {
                "region": region.code,
                "url": final_quote.source_url,
                "status": final_quote.status,
                "price": final_quote.price,
                "currency": final_quote.currency,
                "confidence": result.confidence,
                "conflict_reason": result.conflict_reason,
                "captcha_detected": "challenge" in (final_quote.status or "").lower(),
                "timestamp": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    (base / "network_candidates.json").write_text(
        json.dumps(network_candidates or [], ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    (base / "dom_cards.json").write_text(
        json.dumps(capture.get("domCards", []), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    (base / "hydration_candidates.json").write_text(
        json.dumps(capture.get("hydrationScripts", []), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    (base / "page_text.txt").write_text(str(capture.get("pageText", "")), encoding="utf-8")
    (base / "final_decision.json").write_text(
        json.dumps(
            {
                "confidence": result.confidence,
                "conflict_reason": result.conflict_reason,
                "final_quote": asdict(result.final_quote),
                "evidences": [asdict(e) for e in result.evidences],
                "decision_trace": result.decision_trace,
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    screenshot_path = base / "screenshot.png"
    screenshot_path.write_bytes(screenshot_png or b"")
    return str(base)


async def _capture_screenshot(ws_url: str) -> bytes | None:
    try:
        result = await _cdp_command(
            ws_url,
            "Page.captureScreenshot",
            {"format": "png", "captureBeyondViewport": True},
        )
        data = str((result or {}).get("data") or "")
        if data:
            return base64.b64decode(data)
    except Exception:
        return None
    return None


async def compare_via_cdp_structured(
    args: argparse.Namespace,
    selected_regions: list[RegionConfig],
    *,
    build_search_url: Any,
    persist_failure_log: Any | None = None,
    run_id: str = "",
    cdp_mode: str = "attach",
    manual_tabs: dict[str, str] | None = None,
    keep_tabs: bool = False,
) -> list[FlightQuote]:
    timeout = aiohttp.ClientTimeout(total=max(args.timeout, args.page_wait + 30, 45))
    quotes: list[FlightQuote] = []
    owned_tab_ids: set[str] = set()
    domain_tabs = dict(manual_tabs or {})

    async with aiohttp.ClientSession(timeout=timeout) as session:
        try:
            for region in selected_regions:
                url = build_search_url(
                    region,
                    args.origin,
                    args.destination,
                    args.date,
                    getattr(args, "return_date", None),
                )
                domain_host = _domain_host(region)
                tab_id = domain_tabs.get(domain_host)
                ws_url = ""
                try:
                    if tab_id:
                        ws_url = await cdp_navigate_tab(session, tab_id, url)
                    elif cdp_mode != "manual":
                        tab = await cdp_open_tab(session, url)
                        tab_id = str(tab.get("id", ""))
                        ws_url = str(tab.get("webSocketDebuggerUrl", ""))
                        domain_tabs[domain_host] = tab_id
                        owned_tab_ids.add(tab_id)
                    else:
                        raise RuntimeError("Manual mode: no tab provided for this domain")
                except TabNotFoundError:
                    if cdp_mode == "manual":
                        raise RuntimeError("Manual mode: provided tab no longer exists")
                    tab = await cdp_open_tab(session, url)
                    tab_id = str(tab.get("id", ""))
                    ws_url = str(tab.get("webSocketDebuggerUrl", ""))
                    domain_tabs[domain_host] = tab_id
                    owned_tab_ids.add(tab_id)

                if not ws_url:
                    tabs = await cdp_list_tabs(session)
                    tab = next((item for item in tabs if item.get("id") == tab_id), None)
                    ws_url = str((tab or {}).get("webSocketDebuggerUrl", ""))
                if not ws_url:
                    raise RuntimeError("CDP tab has no webSocketDebuggerUrl")

                await asyncio.sleep(args.page_wait)
                started = time.monotonic()
                capture_error: str | None = None
                try:
                    capture = await cdp_eval(ws_url, _capture_expression())
                except Exception as exc:  # noqa: BLE001
                    capture_error = f"CDP structured capture failed: {exc}"
                    capture = {"url": url, "pageText": "", "domCards": [], "hydrationScripts": []}
                if not isinstance(capture, dict):
                    capture = {"pageText": "", "domCards": [], "hydrationScripts": []}
                source_url = str(capture.get("url") or url)
                network_candidates: list[Any] = []
                evidences = []
                evidences.extend(parse_network_json(region, source_url, network_candidates))
                evidences.extend(parse_hydration_scripts(region, source_url, list(capture.get("hydrationScripts") or [])))
                evidences.extend(parse_dom_cards(region, source_url, list(capture.get("domCards") or [])))
                if not evidences:
                    evidences.extend(parse_text_fallback(region, source_url, str(capture.get("pageText") or "")))
                result = resolve_quote(region, source_url, evidences)
                quote = result.final_quote
                if capture_error and quote.price is None:
                    quote.error = capture_error
                quote.extract_attempt_count = 1
                quote.progressive_wait_used = args.page_wait
                quote.fetch_metadata["elapsed_ms"] = int((time.monotonic() - started) * 1000)
                needs_artifacts = quote.price is None or result.conflict_reason is not None
                if needs_artifacts:
                    screenshot_png = await _capture_screenshot(ws_url)
                    diagnostic_dir = _write_diagnostics(
                        args=args,
                        region=region,
                        capture=capture,
                        result=result,
                        network_candidates=network_candidates,
                        screenshot_png=screenshot_png,
                    )
                    quote.debug_log_path = str(Path(diagnostic_dir) / "final_decision.json")
                    if quote.price is None and persist_failure_log is not None:
                        persist_failure_log(
                            quote,
                            transport="cdp_structured",
                            route_key=_route_key(args),
                            page_text=str(capture.get("pageText") or ""),
                            extra={"diagnostic_dir": diagnostic_dir},
                        )
                quotes.append(quote)
        finally:
            if not keep_tabs:
                for tab_id in owned_tab_ids:
                    try:
                        await cdp_close_tab(session, tab_id)
                    except Exception:
                        pass

    return quotes
