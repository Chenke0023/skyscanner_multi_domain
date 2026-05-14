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


def _page_state_expression() -> str:
    return r"""
(() => {
  const body = document.body;
  const text = body ? (body.innerText || "") : "";
  const lowerText = text.toLowerCase();

  const cookieSelectors = '[class*="cookie"], [class*="consent"], [class*="gdpr"], [id*="cookie"], [id*="consent"], [data-testid*="cookie"]';
  const hasCookieBanner = /cookie|consent|gdpr|accept all|同意|接受|隐私|privacy|we value your/i.test(text) &&
    document.querySelector(cookieSelectors) !== null;

  const hasChallenge = /captcha|challenge|recaptcha|hcaptcha|turnstile|robot|机器人|验证|verify you are human|security check/i.test(text) ||
    document.querySelector('iframe[src*="captcha"], iframe[src*="challenge"], iframe[src*="recaptcha"], #captcha, .captcha, [class*="captcha"]') !== null;

  const searchInputs = document.querySelectorAll('input[placeholder*="From" i], input[placeholder*="To" i], input[placeholder*="Depart" i], input[placeholder*="Origin" i], input[placeholder*="Destination" i]');
  const hasSearchForm = searchInputs.length > 0 &&
    document.querySelectorAll('[data-testid*="result"], [class*="result-card"], [class*="itinerary"]').length === 0;

  const hasLoadingSkeleton = document.querySelector('[class*="skeleton"], [class*="shimmer"], [class*="loading"], [data-testid*="loading"], [aria-busy="true"], [class*="placeholder"]') !== null;

  const hasSortControls = /sort by|排序| cheapest|best|fastest|price|recommend/i.test(text) &&
    document.querySelectorAll('button, select, [role="tab"], [data-testid*="sort"]').length > 0;

  const hasBestLabel = /best|最佳|推荐|top pick/i.test(text);
  const hasCheapestLabel = /cheapest|最便宜|最低|lowest/i.test(text);

  const hasCurrencyText = /(?:¥|￥|HK\$|S\$|US\$|A\$|CA\$|£|€|\$|₩|₹)\s?[\d,]+(?:\.\d+)?/i.test(text);

  const hasNoResults = /no results|no flights|没找到|无结果|no matching|抱歉|sorry|we couldn.t find|couldn.t find any/i.test(text);

  const resultSelectors = '[data-testid*="result"], [class*="result-card"], [class*="itinerary"], [class*="flight-card"], [class*="flight-list"]';
  const resultCards = document.querySelectorAll(resultSelectors);
  const hasResultCards = resultCards.length > 0;

  const hasPriceDisplay = document.querySelectorAll('[class*="price"], [data-testid*="price"]').length > 0;

  return {
    final_url: location.href,
    title: document.title,
    ready_state: document.readyState,
    body_text_length: text.length,
    has_cookie_banner: hasCookieBanner,
    has_challenge: hasChallenge,
    has_search_form: hasSearchForm,
    has_loading_skeleton: hasLoadingSkeleton,
    has_sort_controls: hasSortControls,
    has_best_label: hasBestLabel,
    has_cheapest_label: hasCheapestLabel,
    has_currency_text: hasCurrencyText,
    has_no_results: hasNoResults,
    has_result_cards: hasResultCards,
    result_card_count: resultCards.length,
    has_price_display: hasPriceDisplay,
  };
})()
""".strip()


def _classify_page_state(state: dict[str, Any]) -> str:
    if state.get("has_challenge"):
        return "challenge"
    if state.get("has_loading_skeleton") and not state.get("has_result_cards") and not state.get("has_currency_text"):
        return "loading_skeleton"
    if state.get("has_search_form") and not state.get("has_result_cards"):
        return "search_form_or_incomplete_params"
    if state.get("has_no_results") and not state.get("has_currency_text") and not state.get("has_result_cards"):
        return "no_results"
    if state.get("has_result_cards") or state.get("has_sort_controls") or state.get("has_currency_text"):
        return "results_visible"
    if state.get("ready_state") != "complete":
        return "incomplete_load"
    if state.get("body_text_length", 0) < 500:
        return "nearly_empty"
    return "unknown"


def _classify_failure_reason(
    failure_stage: str | None,
    page_state: dict[str, Any] | None,
    capture: dict[str, Any],
    result: Any,
) -> str:
    """Classify the specific reason why no quote was extracted."""
    if failure_stage in ("target_select", "wait_result"):
        return "navigation_failed"

    state = _classify_page_state(page_state or {}) if page_state else "unknown"

    if state == "challenge":
        return "failed_challenge"
    if state in ("search_form_or_incomplete_params", "nearly_empty"):
        return "failed_invalid_search_page"
    if state == "loading_skeleton":
        return "failed_wait_timeout_before_results"
    if state == "no_results":
        return "failed_no_results"
    if state == "results_visible":
        dom_cards = capture.get("domCards", []) if capture else []
        if not dom_cards:
            return "failed_no_price_candidates_after_results"
        return "failed_merge_conservative"
    if state == "incomplete_load":
        if page_state and page_state.get("has_cookie_banner"):
            return "failed_cookie_interstitial"
        return "failed_incomplete_load"

    if failure_stage in ("dom_eval", "hydration_eval", "network_eval"):
        return "failed_eval_error"

    if page_state and page_state.get("has_cookie_banner"):
        return "failed_cookie_interstitial"

    return "failed_unknown"


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


def _navigation_trace_expression(requested_url: str) -> str:
    return f"""
(() => {{
  const nav = performance.getEntriesByType("navigation")[0] || {{}};
  return {{
    final_url: location.href,
    title: document.title,
    history_length: history.length,
    ready_state: document.readyState,
    navigation_type: nav.type || "unknown",
    redirect_count: nav.redirectCount || 0,
    load_duration_ms: Math.round(nav.duration || 0),
    dom_complete_ms: Math.round(nav.domComplete || 0),
  }};
}})()
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
    screenshot_initial: bytes | None = None,
    screenshot_final: bytes | None = None,
    failure_stage: str | None = None,
    page_state: dict[str, Any] | None = None,
    navigation_trace: dict[str, Any] | None = None,
    state_timeline: list[dict[str, Any]] | None = None,
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
    if page_state:
        (base / "page_state.json").write_text(
            json.dumps(page_state, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
    if navigation_trace:
        (base / "navigation_trace.json").write_text(
            json.dumps(navigation_trace, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
    (base / "final_decision.json").write_text(
        json.dumps(
            {
                "confidence": result.confidence,
                "conflict_reason": result.conflict_reason,
                "failure_stage": failure_stage or capture.get("failureStage"),
                "failure_reason": _classify_failure_reason(
                    failure_stage, page_state, capture, result
                ),
                "stage_errors": capture.get("stageErrors", []),
                "page_health": capture.get("pageHealth", {}),
                "page_state": page_state,
                "navigation_trace": navigation_trace,
                "state_timeline": state_timeline or [],
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
    if screenshot_initial:
        (base / "screenshot_initial.png").write_bytes(screenshot_initial)
    if screenshot_final:
        (base / "screenshot_final.png").write_bytes(screenshot_final)
    elif screenshot_initial:
        (base / "screenshot.png").write_bytes(screenshot_initial)
    return str(base)


async def wait_for_result_state(
    ws_url: str,
    timeout_seconds: float,
    poll_interval: float = 1.0,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """Poll page state until a terminal state is reached or timeout.

    Returns (final_state, state_timeline) where state_timeline contains
    snapshots at each poll iteration.
    """
    deadline = time.monotonic() + timeout_seconds
    snapshots: list[dict[str, Any]] = []
    terminal_states = {
        "results_visible",
        "no_results",
        "challenge",
        "search_form_or_incomplete_params",
    }

    while time.monotonic() < deadline:
        try:
            state = await _safe_eval(ws_url, "page_state_eval", _page_state_expression(), default=None)
            if state is None:
                await asyncio.sleep(poll_interval)
                continue

            if isinstance(state, dict):
                state["_eval_timestamp"] = time.monotonic()
                snapshots.append(state)
                classified = _classify_page_state(state)
                if classified in terminal_states:
                    return state, snapshots

        except CdpStageError:
            pass
        except Exception:
            pass

        await asyncio.sleep(poll_interval)

    if snapshots:
        return snapshots[-1], snapshots
    return {"state": "timeout", "final_url": "", "ready_state": "unknown", "body_text_length": 0}, snapshots


async def _capture_structured_artifacts(ws_url: str, url: str, trace: list[str]) -> tuple[dict[str, Any], str | None, dict[str, Any] | None]:
    capture: dict[str, Any] = {
        "url": url,
        "pageHealth": {},
        "pageText": "",
        "domCards": [],
        "hydrationScripts": [],
        "stageErrors": [],
    }
    failure_stage: str | None = None
    page_state: dict[str, Any] | None = None

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

    page_state = await run_stage("page_state_eval", _page_state_expression(), None)
    if isinstance(page_state, dict):
        capture["pageState"] = page_state

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
    return capture, failure_stage, page_state


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

                screenshot_initial: bytes | None = None
                screenshot_final: bytes | None = None
                page_state: dict[str, Any] | None = None
                navigation_trace: dict[str, Any] | None = None
                state_timeline: list[dict[str, Any]] = []

                if not ws_url:
                    failure_stage = failure_stage or "target_select"
                    capture["failureStage"] = failure_stage
                    capture["stageErrors"].append({"stage": failure_stage, "error": "CDP tab has no webSocketDebuggerUrl"})
                else:
                    trace.append(f"screenshot_initial:start")
                    screenshot_initial = await _capture_screenshot(ws_url)
                    trace.append("screenshot_initial:ok" if screenshot_initial else "screenshot_initial:empty")

                    trace.append(f"wait_for_result_state:start timeout={args.page_wait}")
                    page_state, state_timeline = await wait_for_result_state(ws_url, args.page_wait)
                    trace.append(f"wait_for_result_state:ok state={_classify_page_state(page_state)}")

                    trace.append("artifact_capture:start")
                    capture, eval_failure_stage, _ = await _capture_structured_artifacts(ws_url, url, trace)
                    failure_stage = failure_stage or eval_failure_stage

                    if page_state is None:
                        page_state = capture.get("pageState")

                    nav_expr = _navigation_trace_expression(url)
                    try:
                        navigation_trace = await _safe_eval(ws_url, "nav_trace", nav_expr)
                        if not isinstance(navigation_trace, dict):
                            navigation_trace = None
                    except CdpStageError:
                        navigation_trace = None

                    trace.append("screenshot_final:start")
                    screenshot_final = await _capture_screenshot(ws_url)
                    trace.append("screenshot_final:ok" if screenshot_final else "screenshot_final:empty")

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
                failure_reason = _classify_failure_reason(failure_stage, page_state, capture, result)
                if failure_stage and quote.price is None:
                    quote.error = f"CDP structured failed at {failure_stage}: {failure_reason}"
                    quote.status = f"cdp_structured_{failure_reason}_failed"
                quote.extract_attempt_count = 1
                quote.progressive_wait_used = args.page_wait
                quote.fetch_metadata["elapsed_ms"] = int((time.monotonic() - started) * 1000)
                quote.fetch_metadata["failure_stage"] = failure_stage
                quote.fetch_metadata["failure_reason"] = failure_reason
                quote.fetch_metadata["stage_errors"] = capture.get("stageErrors", [])
                quote.fetch_metadata["page_state"] = page_state
                quote.fetch_metadata["navigation_trace"] = navigation_trace
                needs_artifacts = quote.price is None or result.conflict_reason is not None or failure_stage is not None
                if needs_artifacts:
                    diagnostic_dir = _write_diagnostics(
                        args=args,
                        region=region,
                        capture=capture,
                        result=result,
                        network_candidates=network_candidates,
                        enriched_network_candidates=enriched_network_candidates,
                        enriched_hydration_candidates=enriched_hydration_candidates,
                        screenshot_initial=screenshot_initial,
                        screenshot_final=screenshot_final,
                        failure_stage=failure_stage,
                        page_state=page_state,
                        navigation_trace=navigation_trace,
                        state_timeline=state_timeline,
                    )
                    quote.debug_log_path = str(Path(diagnostic_dir) / "final_decision.json")
                    if quote.price is None and persist_failure_log is not None:
                        persist_failure_log(
                            quote,
                            transport="cdp_structured",
                            route_key=_route_key(args),
                            page_text=str(capture.get("pageText") or ""),
                            extra={"diagnostic_dir": diagnostic_dir, "failure_stage": failure_stage, "failure_reason": failure_reason},
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
