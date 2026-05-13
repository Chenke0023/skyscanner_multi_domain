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
from skyscanner_multi_domain.parsing.hydration_parser import enrich_hydration_candidates, parse_hydration_scripts
from skyscanner_multi_domain.parsing.network_parser import enrich_network_candidates, parse_network_json
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


class CdpStageError(RuntimeError):
    def __init__(self, stage: str, cause: Exception):
        self.stage = stage
        self.cause = cause
        super().__init__(f"{stage}: {cause}")


def _is_transient_eval_error(error: Exception) -> bool:
    text = str(error).lower()
    transient_markers = (
        "execution context was destroyed",
        "cannot find context",
        "context with specified id",
        "inspected target navigated",
        "target closed",
        "session closed",
        "websocket",
    )
    return any(marker in text for marker in transient_markers)


async def _safe_eval(ws_url: str, stage: str, expression: str, *, retries: int = 2) -> Any:
    last_error: Exception | None = None
    for attempt in range(retries + 1):
        try:
            return await cdp_eval(ws_url, expression, max_retries=0)
        except Exception as exc:  # noqa: BLE001
            last_error = exc
            if attempt < retries and _is_transient_eval_error(exc):
                await asyncio.sleep(0.75 * (attempt + 1))
                continue
            raise CdpStageError(stage, exc) from exc
    raise CdpStageError(stage, last_error or RuntimeError("unknown CDP eval failure"))


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


def _page_health_expression() -> str:
    return r"""
(() => ({
  url: location.href,
  title: document.title,
  readyState: document.readyState,
  hasBody: !!document.body,
  bodyTextLength: document.body ? (document.body.innerText || "").length : 0,
  scripts: document.scripts ? document.scripts.length : 0
}))()
""".strip()


def _page_text_expression() -> str:
    return r"""
(() => document.body ? (document.body.innerText || "").slice(0, 80000) : "")()
""".strip()


def _dom_cards_expression() -> str:
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
    for (let depth = 0; cur && depth < 12; depth++, cur = cur.parentElement) {
      const text = cur.innerText || "";
      if (text.length < 5000 && /best|cheapest|lowest|最佳|最便宜|price|flight|航班|价格|票价/i.test(text)) {
        return cur;
      }
    }
    return el;
  }
  const candidateSelector = "body *, button, a, [role=button], [data-testid]";
  const seen = new Set();
  return [...document.querySelectorAll(candidateSelector)]
    .filter(visible)
    .filter(el => {
      const text = el.innerText || el.textContent || el.getAttribute("aria-label") || "";
      const attrs = `${el.getAttribute("aria-label") || ""} ${el.getAttribute("data-testid") || ""}`;
      return priceRegex.test(text) || priceRegex.test(attrs);
    })
    .filter(el => {
      const card = nearestCard(el);
      const key = `${card.tagName}:${(card.innerText || "").slice(0, 200)}`;
      if (seen.has(key)) return false;
      seen.add(key);
      return true;
    })
    .slice(0, 120)
    .map((el, idx) => {
      const card = nearestCard(el);
      const rect = card.getBoundingClientRect();
      return {
        idx,
        priceText: (el.innerText || el.textContent || el.getAttribute("aria-label") || "").slice(0, 800),
        cardText: (card.innerText || card.textContent || "").slice(0, 2500),
        role: el.getAttribute("role"),
        testId: el.getAttribute("data-testid"),
        cardTestId: card.getAttribute("data-testid"),
        aria: el.getAttribute("aria-label"),
        tag: el.tagName,
        cardTag: card.tagName,
        x: rect.x,
        y: rect.y,
        w: rect.width,
        h: rect.height
      };
    });
})()
""".strip()


def _hydration_scripts_expression() -> str:
    return r"""
(() => [...document.querySelectorAll("script")]
  .map((s, i) => ({
    index: i,
    type: s.type || "",
    id: s.id || "",
    text: (s.textContent || "").slice(0, 120000)
  }))
  .filter(x => /price|itinerary|flight|currency/i.test(x.text))
  .slice(0, 20)
)()
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
    enriched_network_candidates: list[Any] | None = None,
    enriched_hydration_candidates: list[Any] | None = None,
    screenshot_png: bytes | None = None,
    failure_stage: str | None = None,
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
                "failure_stage": failure_stage or capture.get("failureStage"),
                "stage_errors": capture.get("stageErrors", []),
                "captcha_detected": "challenge" in (final_quote.status or "").lower(),
                "timestamp": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    (base / "page_health.json").write_text(
        json.dumps(capture.get("pageHealth", {}), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    (base / "network_candidates.json").write_text(
        json.dumps(network_candidates or [], ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    (base / "network_candidates_enriched.json").write_text(
        json.dumps(enriched_network_candidates or [], ensure_ascii=False, indent=2),
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
    (base / "hydration_candidates_enriched.json").write_text(
        json.dumps(enriched_hydration_candidates or [], ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    (base / "page_text.txt").write_text(str(capture.get("pageText", "")), encoding="utf-8")
    (base / "final_decision.json").write_text(
        json.dumps(
            {
                "confidence": result.confidence,
                "conflict_reason": result.conflict_reason,
                "failure_stage": failure_stage or capture.get("failureStage"),
                "stage_errors": capture.get("stageErrors", []),
                "page_health": capture.get("pageHealth", {}),
                "final_quote": asdict(result.final_quote),
                "evidences": [asdict(e) for e in result.evidences],
                "evidence_ranking": result.final_quote.fetch_metadata.get("evidence_ranking", []),
                "rejected_candidates": result.final_quote.fetch_metadata.get("rejected_candidates", []),
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


async def _capture_structured_artifacts(ws_url: str, url: str, trace: list[str]) -> tuple[dict[str, Any], str | None]:
    capture: dict[str, Any] = {
        "url": url,
        "pageHealth": {},
        "pageText": "",
        "domCards": [],
        "hydrationScripts": [],
        "stageErrors": [],
    }
    failure_stage: str | None = None

    async def run_stage(stage: str, expression: str, default: Any) -> Any:
        nonlocal failure_stage
        trace.append(f"{stage}:start")
        try:
            value = await _safe_eval(ws_url, stage, expression)
            count = len(value) if isinstance(value, (list, str)) else "ok"
            trace.append(f"{stage}:ok count={count}")
            return value
        except CdpStageError as exc:
            failure_stage = failure_stage or exc.stage
            message = str(exc.cause)
            capture["stageErrors"].append({"stage": exc.stage, "error": message})
            trace.append(f"{stage}:failed {message}")
            return default

    health = await run_stage("health_eval", _page_health_expression(), {})
    if isinstance(health, dict):
        capture["pageHealth"] = health
        capture["url"] = str(health.get("url") or url)

    page_text = await run_stage("text_eval", _page_text_expression(), "")
    if isinstance(page_text, str):
        capture["pageText"] = page_text

    dom_cards = await run_stage("dom_eval", _dom_cards_expression(), [])
    if isinstance(dom_cards, list):
        capture["domCards"] = dom_cards

    hydration_scripts = await run_stage("hydration_eval", _hydration_scripts_expression(), [])
    if isinstance(hydration_scripts, list):
        capture["hydrationScripts"] = hydration_scripts

    capture["failureStage"] = failure_stage
    return capture, failure_stage


async def _capture_screenshot(ws_url: str) -> bytes | None:
    try:
        result = await asyncio.wait_for(
            _cdp_command(
                ws_url,
                "Page.captureScreenshot",
                {"format": "png", "captureBeyondViewport": False},
            ),
            timeout=8.0,
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
                started = time.monotonic()
                trace: list[str] = []
                domain_host = _domain_host(region)
                tab_id = domain_tabs.get(domain_host)
                ws_url = ""
                failure_stage: str | None = None
                capture: dict[str, Any] = {
                    "url": url,
                    "pageHealth": {},
                    "pageText": "",
                    "domCards": [],
                    "hydrationScripts": [],
                    "stageErrors": [],
                }
                network_candidates: list[Any] = []
                enriched_network_candidates: list[Any] = []
                enriched_hydration_candidates: list[Any] = []

                try:
                    trace.append("target_select:start")
                    if tab_id:
                        trace.append("page_navigate:start")
                        ws_url = await cdp_navigate_tab(session, tab_id, url)
                        trace.append("page_navigate:ok reused_tab=true")
                    elif cdp_mode != "manual":
                        tab = await cdp_open_tab(session, url)
                        tab_id = str(tab.get("id", ""))
                        ws_url = str(tab.get("webSocketDebuggerUrl", ""))
                        domain_tabs[domain_host] = tab_id
                        owned_tab_ids.add(tab_id)
                        trace.append("target_select:ok opened_tab=true")
                    else:
                        raise RuntimeError("Manual mode: no tab provided for this domain")
                except TabNotFoundError as exc:
                    failure_stage = "target_select"
                    capture["stageErrors"].append({"stage": failure_stage, "error": str(exc)})
                    if cdp_mode == "manual":
                        trace.append("target_select:failed manual_tab_missing")
                    else:
                        try:
                            tab = await cdp_open_tab(session, url)
                            tab_id = str(tab.get("id", ""))
                            ws_url = str(tab.get("webSocketDebuggerUrl", ""))
                            domain_tabs[domain_host] = tab_id
                            owned_tab_ids.add(tab_id)
                            trace.append("target_select:ok opened_replacement_tab=true")
                            failure_stage = None
                        except Exception as open_exc:  # noqa: BLE001
                            capture["stageErrors"].append({"stage": "target_select", "error": str(open_exc)})
                except Exception as exc:  # noqa: BLE001
                    failure_stage = "target_select"
                    capture["stageErrors"].append({"stage": failure_stage, "error": str(exc)})
                    trace.append(f"target_select:failed {exc}")

                if not ws_url and tab_id:
                    try:
                        tabs = await cdp_list_tabs(session)
                        tab = next((item for item in tabs if item.get("id") == tab_id), None)
                        ws_url = str((tab or {}).get("webSocketDebuggerUrl", ""))
                    except Exception as exc:  # noqa: BLE001
                        failure_stage = failure_stage or "target_select"
                        capture["stageErrors"].append({"stage": "target_select", "error": str(exc)})

                if not ws_url:
                    failure_stage = failure_stage or "target_select"
                    capture["failureStage"] = failure_stage
                    capture["stageErrors"].append({"stage": failure_stage, "error": "CDP tab has no webSocketDebuggerUrl"})
                else:
                    trace.append(f"wait_result:start seconds={args.page_wait}")
                    await asyncio.sleep(min(args.page_wait, 5))
                    trace.append("wait_result:ok")
                    capture, eval_failure_stage = await _capture_structured_artifacts(ws_url, url, trace)
                    failure_stage = failure_stage or eval_failure_stage

                source_url = str(capture.get("url") or url)
                evidences = []
                enriched_network_candidates = enrich_network_candidates(network_candidates)
                enriched_hydration_candidates = enrich_hydration_candidates(list(capture.get("hydrationScripts") or []))
                trace.append(f"network_capture:metadata_only candidates={len(enriched_network_candidates)}")
                trace.append(f"hydration_scan:candidates={len(enriched_hydration_candidates)}")
                evidences.extend(parse_network_json(region, source_url, network_candidates))
                evidences.extend(parse_hydration_scripts(region, source_url, list(capture.get("hydrationScripts") or [])))
                evidences.extend(parse_dom_cards(region, source_url, list(capture.get("domCards") or [])))
                if not evidences:
                    evidences.extend(parse_text_fallback(region, source_url, str(capture.get("pageText") or "")))
                result = resolve_quote(region, source_url, evidences)
                result.decision_trace[:0] = trace
                result.final_quote.fetch_metadata["decision_trace"] = result.decision_trace
                quote = result.final_quote
                if failure_stage and quote.price is None:
                    quote.error = f"CDP structured failed at {failure_stage}"
                    quote.status = f"cdp_structured_{failure_stage}_failed"
                quote.extract_attempt_count = 1
                quote.progressive_wait_used = args.page_wait
                quote.fetch_metadata["elapsed_ms"] = int((time.monotonic() - started) * 1000)
                quote.fetch_metadata["failure_stage"] = failure_stage
                quote.fetch_metadata["stage_errors"] = capture.get("stageErrors", [])
                needs_artifacts = quote.price is None or result.conflict_reason is not None or failure_stage is not None
                if needs_artifacts:
                    trace.append("screenshot_capture:start")
                    screenshot_png = await _capture_screenshot(ws_url) if ws_url else None
                    trace.append("screenshot_capture:ok" if screenshot_png else "screenshot_capture:empty")
                    diagnostic_dir = _write_diagnostics(
                        args=args,
                        region=region,
                        capture=capture,
                        result=result,
                        network_candidates=network_candidates,
                        enriched_network_candidates=enriched_network_candidates,
                        enriched_hydration_candidates=enriched_hydration_candidates,
                        screenshot_png=screenshot_png,
                        failure_stage=failure_stage,
                    )
                    quote.debug_log_path = str(Path(diagnostic_dir) / "final_decision.json")
                    if quote.price is None and persist_failure_log is not None:
                        persist_failure_log(
                            quote,
                            transport="cdp_structured",
                            route_key=_route_key(args),
                            page_text=str(capture.get("pageText") or ""),
                            extra={"diagnostic_dir": diagnostic_dir, "failure_stage": failure_stage},
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
