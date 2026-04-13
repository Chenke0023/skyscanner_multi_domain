"""
Tkinter GUI for non-technical users.

Launch:
  python gui.py
"""

from __future__ import annotations

import asyncio
import calendar
import csv
import importlib.util
import inspect
import json
import os
import queue
import re
import threading
import webbrowser
from datetime import date, datetime, timedelta
from pathlib import Path
import tkinter as tk
from tkinter import messagebox, ttk
from typing import Any

from app_paths import get_gui_state_file, get_reports_dir
from cli import CombinedQuoteRow, SimpleCLI
from date_window import (
    build_date_window,
    build_round_trip_date_window,
    format_trip_date_label,
)
from location_resolver import (
    AIRPORT_DATASET_PATH,
    COUNTRY_ROUTE_DEFAULT_AIRPORT_LIMIT,
    LOCATION_MAPPINGS_PATH,
    LocationRecord,
)
from scan_history import (
    annotate_rows_with_history,
    build_history_series,
    ScanHistoryStore,
    get_failed_region_codes,
    get_quotes_for_trip_label,
    get_rows_for_trip_label,
    merge_quotes_by_date,
    merge_rows_by_date,
    override_quotes_source_kind,
    override_rows_source_kind,
    prioritize_region_codes,
    select_preview_region_batches,
    source_kind_label,
    summarize_query_history,
)
from skyscanner_neo import (
    DEFAULT_REGIONS,
    NeoCli,
    build_effective_region_codes,
    detect_cdp_version,
    ensure_cdp_ready,
    quotes_to_dicts,
    run_page_scan,
)


MAX_LOCATION_SUGGESTIONS = 8

_COLUMN_LABELS = {
    "date": "日期",
    "route": "航段",
    "region": "地区",
    "source": "来源",
    "best_native": "最佳（原币）",
    "best_cny": "最佳（人民币）",
    "cheapest_native": "最低价（原币）",
    "cheapest_cny": "最低价（人民币）",
    "delta": "较上次变化",
    "updated_at": "更新时间",
    "link": "链接",
}
_PRICE_COLUMNS = {"best_native", "best_cny", "cheapest_native", "cheapest_cny"}
_FAILURE_COLUMN_LABELS = {
    "date": "日期",
    "route": "航段",
    "region": "地区",
    "source": "来源",
    "category": "失败分类",
    "action": "建议动作",
    "reuse": "可复用页面",
    "status": "状态",
    "error": "错误",
    "link": "链接",
}

# ── Classic Mac OS 8/9 Platinum Theme ──────────────────────────────────
_PLATINUM = "#EAE1D4"
_PLATINUM_DARK = "#B7AA97"
_PLATINUM_LIGHT = "#F6F0E4"
_BUTTON_FACE = "#E4D8C6"
_HIGHLIGHT = "#5F7FA4"
_PAPER_GRAIN = "#F3ECE0"
_PAPER_SHADOW = "#CABBA5"
_INK = "#2E261F"
_MUTED_INK = "#6C6257"
_RULE = "#CDBEAA"
_PANEL_BG = "#F8F2E7"
_PANEL_EDGE = "#9E927F"
_PRIMARY_BUTTON = "#6D87A6"
_PRIMARY_BUTTON_ACTIVE = "#7A94B4"
_PRIMARY_BUTTON_PRESSED = "#56708F"
_SUCCESS_TINT = "#E7EFE3"
_FAILURE_TINT = "#F5E7E1"
_CHEAPEST_TINT = "#F7EDBF"
_FONT_BODY = ("Lucida Grande", 11)
_FONT_TITLE = ("Lucida Grande", 22, "bold")
_FONT_BUTTON = ("Lucida Grande", 12, "bold")
_FONT_HEADING = ("Lucida Grande", 11, "bold")
_FONT_MONO = ("Monaco", 10)
_FONT_CARD_PRICE = ("Lucida Grande", 20, "bold")
_FONT_CARD_HEADLINE = ("Lucida Grande", 13, "bold")
_TRIP_TYPE_ONE_WAY = "one_way"
_TRIP_TYPE_ROUND_TRIP = "round_trip"
_CARD_BG = "#F5E7C9"
_CARD_BORDER = "#8F7C58"
_CARD_PRICE = "#264B2D"
_REQUIRED_APIFY_DATA_FILES = (
    "browser-helper-file.json",
    "fingerprint-network-definition.zip",
    "header-network-definition.zip",
    "headers-order.json",
    "input-network-definition.zip",
)
_GUI_REGION_CONCURRENCY = 3
_GUI_DATE_WINDOW_CONCURRENCY = 2
_GUI_AIRPORT_PAIR_CONCURRENCY = 2
_TOP_RECOMMENDATION_LIMIT = 5


def _split_trip_label(trip_label: str) -> tuple[str, str | None]:
    normalized = str(trip_label or "").strip()
    if "->" not in normalized:
        return (normalized, None)
    departure, return_part = [part.strip() for part in normalized.split("->", 1)]
    return (departure, return_part or None)


def _is_live_source_kind(source_kind: str | None) -> bool:
    normalized = str(source_kind or "").strip().lower()
    return normalized not in {"", "cached", "preview_cache"}


def _decision_price_key(row: CombinedQuoteRow, mode: str = "cheapest") -> tuple[float, float, str, str]:
    primary_key = "best_cny_price" if mode == "best" else "cheapest_cny_price"
    secondary_key = "cheapest_cny_price" if mode == "best" else "best_cny_price"
    primary_value = row.get(primary_key)
    secondary_value = row.get(secondary_key)
    return (
        float(primary_value) if isinstance(primary_value, (int, float)) else float("inf"),
        float(secondary_value) if isinstance(secondary_value, (int, float)) else float("inf"),
        str(row.get("date") or ""),
        str(row.get("region_name") or ""),
    )


def _compute_stability_label(
    row: CombinedQuoteRow,
    history_records: list[Any],
) -> tuple[str, str | None]:
    if not history_records:
        return ("首次低价", None)
    target_region = str(row.get("region_name") or row.get("region_code") or "")
    target_trip = str(row.get("date") or "")
    matching_rows: list[dict[str, Any]] = []
    for record in history_records:
        for current_trip_label, rows in getattr(record, "rows_by_date", []) or []:
            if current_trip_label != target_trip:
                continue
            for candidate in rows:
                if str(candidate.get("region_name") or candidate.get("region_code") or "") == target_region:
                    matching_rows.append(candidate)
    if not matching_rows:
        return ("首次低价", None)

    prices = [
        float(candidate["cheapest_cny_price"])
        for candidate in matching_rows
        if isinstance(candidate.get("cheapest_cny_price"), (int, float))
    ]
    current_price = row.get("cheapest_cny_price")
    if not isinstance(current_price, (int, float)):
        return ("等待价格", None)
    if prices:
        if len(prices) >= 2 and float(current_price) < min(prices):
            return ("刷新历史新低", "down")
        trailing = prices[-2:] if len(prices) >= 2 else prices
        if trailing and all(abs(float(current_price) - price) < 0.01 for price in trailing):
            return (f"连续 {len(trailing) + 1} 次低位", "flat")
        if prices[-1] - float(current_price) >= 0.01:
            return ("较近 24h 下降", "down")
        price_range = max(prices + [float(current_price)]) - min(prices + [float(current_price)])
        if price_range >= max(120.0, float(current_price) * 0.12):
            return ("波动较大，谨慎判断", "volatile")
    return ("近期稳定", "flat")


def _compute_market_reliability_label(
    row: CombinedQuoteRow,
    history_records: list[Any],
) -> str:
    target_region = str(row.get("region_name") or row.get("region_code") or "")
    total = 0
    success = 0
    challenge_like = 0
    browser_like = 0
    for record in history_records:
        for _trip_label, rows in getattr(record, "rows_by_date", []) or []:
            for candidate in rows:
                region_name = str(candidate.get("region_name") or candidate.get("region_code") or "")
                if region_name != target_region:
                    continue
                total += 1
                if any(
                    isinstance(candidate.get(key), (int, float))
                    for key in ("best_cny_price", "cheapest_cny_price")
                ):
                    success += 1
                if str(candidate.get("status") or "").strip().lower() in {
                    "px_challenge",
                    "page_challenge",
                    "captcha_solve_failed",
                }:
                    challenge_like += 1
                if str(candidate.get("source_kind") or "").strip().lower() in {
                    "browser_fallback",
                    "cdp_reuse",
                }:
                    browser_like += 1
    source_kind = str(row.get("source_kind") or "").strip().lower()
    if total == 0:
        if source_kind in {"browser_fallback", "cdp_reuse"}:
            return "需浏览器兜底"
        return "历史样本少"
    success_rate = success / total
    if challenge_like >= max(2, total // 2):
        return "常触发验证"
    if browser_like >= max(2, total // 2) or source_kind in {"browser_fallback", "cdp_reuse"}:
        return "偏依赖浏览器"
    if success_rate >= 0.8:
        return "稳定可下单"
    if success_rate >= 0.5:
        return "成功率一般"
    return "成功率偏低"


def _enrich_decision_rows(
    rows: list[CombinedQuoteRow],
    history_records: list[Any],
) -> list[CombinedQuoteRow]:
    enriched: list[CombinedQuoteRow] = []
    history_summary = summarize_query_history(history_records) if history_records else None
    recommended_signature = _row_signature(min(rows, key=_decision_price_key)) if rows else None
    for row in rows:
        next_row = dict(row)
        stability_label, trend_direction = _compute_stability_label(next_row, history_records)
        next_row["stability_label"] = stability_label
        next_row["history_trend_direction"] = trend_direction
        next_row["market_reliability_label"] = _compute_market_reliability_label(
            next_row, history_records
        )
        next_row["history_low_price"] = (
            history_summary.history_low_price if history_summary is not None else None
        )
        next_row["is_recommended"] = _row_signature(next_row) == recommended_signature
        enriched.append(next_row)
    return enriched


def _build_top_recommendations(
    rows: list[CombinedQuoteRow],
    *,
    mode: str = "cheapest",
    limit: int = _TOP_RECOMMENDATION_LIMIT,
) -> list[CombinedQuoteRow]:
    candidates = [
        row
        for row in rows
        if isinstance(row.get("cheapest_cny_price"), (int, float))
        or isinstance(row.get("best_cny_price"), (int, float))
    ]
    return sorted(candidates, key=lambda row: _decision_price_key(row, mode))[:limit]


def _build_recommendation_payload(rows: list[CombinedQuoteRow]) -> dict[str, str | None]:
    recommendations = _build_top_recommendations(rows, mode="cheapest", limit=2)
    if not recommendations:
        return {
            "headline": "等待推荐方案",
            "price": "暂无可比较价格",
            "supporting": "完成扫描后生成推荐下单方案",
            "meta": "",
            "insight": "推荐方案会结合价格、历史稳定性和来源质量。",
            "link": None,
            "button_text": "等待结果",
        }

    winner = recommendations[0]
    winner_price = winner.get("cheapest_cny_price")
    if not isinstance(winner_price, (int, float)):
        return {
            "headline": "等待推荐方案",
            "price": "暂无可比较价格",
            "supporting": "完成扫描后生成推荐下单方案",
            "meta": "",
            "insight": "推荐方案会结合价格、历史稳定性和来源质量。",
            "link": None,
            "button_text": "等待结果",
        }
    runner_up = recommendations[1] if len(recommendations) > 1 else None
    spread_text = "当前没有第二候选。"
    if runner_up is not None and isinstance(runner_up.get("cheapest_cny_price"), (int, float)):
        spread = float(runner_up["cheapest_cny_price"]) - float(winner_price)
        spread_text = (
            f"比次优方案低 ¥{spread:,.2f}。"
            if spread >= 0.01
            else "与次优方案几乎持平。"
        )
    source_text = str(winner.get("source_label") or source_kind_label(winner.get("source_kind")))
    reliability = str(winner.get("market_reliability_label") or "-")
    stability = str(winner.get("stability_label") or "-")
    return {
        "headline": f"推荐优先打开 {winner.get('region_name') or '-'}",
        "price": f"¥{float(winner_price):,.2f}",
        "supporting": f"{winner.get('date') or '-'} · {winner.get('route') or '-'}",
        "meta": f"{source_text} · {stability} · {reliability}",
        "insight": spread_text,
        "link": str(winner.get("link") or ""),
        "button_text": "打开推荐方案",
    }


def _build_calendar_summary(
    rows: list[CombinedQuoteRow],
) -> dict[str, dict[str, CombinedQuoteRow]]:
    grouped: dict[str, dict[str, CombinedQuoteRow]] = {}
    for row in rows:
        trip_label = str(row.get("date") or "").strip()
        if not trip_label:
            continue
        departure_date, return_date = _split_trip_label(trip_label)
        bucket_key = return_date or "__oneway__"
        departure_bucket = grouped.setdefault(departure_date, {})
        current = departure_bucket.get(bucket_key)
        if current is None or _decision_price_key(row) < _decision_price_key(current):
            departure_bucket[bucket_key] = row
    return grouped


def _build_compare_rows(
    current_rows: list[CombinedQuoteRow],
    previous_rows: list[dict[str, Any]],
) -> list[dict[str, str]]:
    previous_index = {
        (
            str(row.get("date") or ""),
            str(row.get("route") or ""),
            str(row.get("region_name") or row.get("region_code") or ""),
        ): row
        for row in previous_rows
    }
    compare_rows: list[dict[str, str]] = []
    for row in current_rows:
        key = (
            str(row.get("date") or ""),
            str(row.get("route") or ""),
            str(row.get("region_name") or row.get("region_code") or ""),
        )
        previous = previous_index.get(key)
        current_price = row.get("cheapest_cny_price")
        previous_price = previous.get("cheapest_cny_price") if previous else None
        if isinstance(current_price, (int, float)) and isinstance(previous_price, (int, float)):
            if abs(float(current_price) - float(previous_price)) < 0.01:
                change = "持平"
            elif float(current_price) < float(previous_price):
                change = f"新低价 (-¥{float(previous_price) - float(current_price):,.2f})"
            else:
                change = f"变贵 (+¥{float(current_price) - float(previous_price):,.2f})"
        elif isinstance(current_price, (int, float)):
            change = "由失败变成功"
        elif isinstance(previous_price, (int, float)):
            change = "由成功变失败"
        else:
            change = "仍无有效价格"
        compare_rows.append(
            {
                "date": key[0] or "-",
                "route": key[1] or "-",
                "region": key[2] or "-",
                "current": f"¥{float(current_price):,.2f}" if isinstance(current_price, (int, float)) else "-",
                "previous": (
                    f"¥{float(previous_price):,.2f}" if isinstance(previous_price, (int, float)) else "-"
                ),
                "change": change,
            }
        )
    compare_rows.sort(key=lambda item: (item["change"], item["date"], item["region"]))
    return compare_rows


def _build_trend_sparkline(prices: list[float]) -> str:
    if not prices:
        return "暂无趋势数据"
    blocks = "▁▂▃▄▅▆▇█"
    if len(prices) == 1:
        return blocks[4]
    low = min(prices)
    high = max(prices)
    if high - low < 0.01:
        return blocks[4] * len(prices)
    chars: list[str] = []
    for price in prices:
        ratio = (price - low) / (high - low)
        index = min(len(blocks) - 1, max(0, int(round(ratio * (len(blocks) - 1)))))
        chars.append(blocks[index])
    return "".join(chars)


def _default_query_state(
    *, default_departure: str, default_return: str
) -> dict[str, Any]:
    return {
        "origin": "北京",
        "destination": "阿拉木图",
        "trip_type": _TRIP_TYPE_ONE_WAY,
        "date": default_departure,
        "return_date": default_return,
        "regions": "",
        "wait": "10",
        "date_window": "3",
        "exact_airport": False,
        "origin_country": False,
        "destination_country": False,
        "combined_summary": True,
    }


def _normalize_query_state(
    payload: Any, *, default_departure: str, default_return: str
) -> dict[str, Any]:
    normalized = _default_query_state(
        default_departure=default_departure,
        default_return=default_return,
    )
    if not isinstance(payload, dict):
        return normalized

    def assign_text(key: str) -> None:
        value = payload.get(key)
        if value is None:
            return
        if isinstance(value, str):
            normalized[key] = value.strip()
        elif isinstance(value, (int, float)):
            normalized[key] = str(value)

    for key in ("origin", "destination", "regions", "wait", "date_window"):
        assign_text(key)

    for key in ("date", "return_date"):
        value = payload.get(key)
        if not isinstance(value, str):
            continue
        try:
            datetime.strptime(value.strip(), "%Y-%m-%d")
        except ValueError:
            continue
        normalized[key] = value.strip()

    trip_type = payload.get("trip_type")
    if trip_type in {_TRIP_TYPE_ONE_WAY, _TRIP_TYPE_ROUND_TRIP}:
        normalized["trip_type"] = trip_type

    for key in (
        "exact_airport",
        "origin_country",
        "destination_country",
        "combined_summary",
    ):
        value = payload.get(key)
        if isinstance(value, bool):
            normalized[key] = value

    return normalized


def _load_query_state(
    state_path: Path, *, default_departure: str, default_return: str
) -> dict[str, Any]:
    try:
        payload = json.loads(state_path.read_text(encoding="utf-8"))
    except (FileNotFoundError, OSError, json.JSONDecodeError):
        payload = None
    return _normalize_query_state(
        payload,
        default_departure=default_departure,
        default_return=default_return,
    )


def _write_query_state(state_path: Path, payload: dict[str, Any]) -> None:
    state_path.parent.mkdir(parents=True, exist_ok=True)
    state_path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True),
        encoding="utf-8",
    )


def _build_cheapest_conclusion(rows: list[CombinedQuoteRow]) -> dict[str, str | None]:
    cheapest_candidates = [
        row
        for row in rows
        if isinstance(row.get("cheapest_cny_price"), (int, float))
    ]
    if cheapest_candidates:
        sorted_rows = sorted(
            cheapest_candidates,
            key=lambda row: (
                float(row.get("cheapest_cny_price")),  # type: ignore[arg-type]
                str(row.get("date") or ""),
                str(row.get("region_name") or ""),
            ),
        )
        winner = sorted_rows[0]
        runner_up = sorted_rows[1] if len(sorted_rows) > 1 else None
        winner_price = float(winner["cheapest_cny_price"])  # type: ignore[index]
        delta_text = "当前只有 1 条可比较的最低价结果。"
        history_delta = str(winner.get("delta_label") or "").strip()
        if runner_up is not None:
            runner_up_price = float(runner_up["cheapest_cny_price"])  # type: ignore[index]
            delta = runner_up_price - winner_price
            if delta >= 0.01:
                delta_text = f"比下一低价再省 ¥{delta:,.2f}。"
            else:
                delta_text = "与下一低价几乎持平。"
        if history_delta and history_delta not in {"-", "持平"}:
            delta_text = f"{history_delta}；{delta_text}"
        return {
            "headline": f"当前最低价来自 {winner.get('region_name') or '-'}",
            "price": f"¥{winner_price:,.2f}",
            "supporting": str(winner.get("cheapest_display_price") or "-"),
            "meta": (
                f"{winner.get('date') or '-'} · {winner.get('route') or '-'} · "
                f"{winner.get('status') or '-'}"
            ),
            "insight": delta_text,
            "link": str(winner.get("link") or ""),
            "button_text": "打开最低价结果页",
        }

    native_only_candidates = [
        row for row in rows if isinstance(row.get("cheapest_display_price"), str)
        and row.get("cheapest_display_price") not in {"", "-"}
    ]
    if native_only_candidates:
        return {
            "headline": "已抓到原币报价",
            "price": "等待人民币换算",
            "supporting": f"共 {len(native_only_candidates)} 条最低价原币结果",
            "meta": "当前无法跨币种直接比较最低价。",
            "insight": "请检查汇率服务，或稍后重试以生成统一结论。",
            "link": None,
            "button_text": "等待换算完成",
        }

    if rows:
        return {
            "headline": "暂无最低价结论",
            "price": "未识别到可比较价格",
            "supporting": f"本次共返回 {len(rows)} 条结果",
            "meta": "这些市场暂未产出可用的最低价金额。",
            "insight": "可结合下方状态列排查 challenge / loading / parse failed。",
            "link": None,
            "button_text": "暂无可打开页面",
        }

    return {
        "headline": "等待比价开始",
        "price": "这里会出现最低价结论",
        "supporting": "完成扫描后自动更新",
        "meta": "",
        "insight": "最低价市场、日期、航段和价差会在这里集中展示。",
        "link": None,
        "button_text": "等待结果",
    }


def _row_signature(row: CombinedQuoteRow) -> tuple[str, str, str, str, str]:
    return (
        str(row.get("date") or ""),
        str(row.get("route") or ""),
        str(row.get("region_name") or ""),
        str(row.get("link") or ""),
        str(row.get("status") or ""),
    )


def _find_cheapest_highlight_signatures(
    rows: list[CombinedQuoteRow],
) -> set[tuple[str, str, str, str, str]]:
    cheapest_candidates = [
        row
        for row in rows
        if isinstance(row.get("cheapest_cny_price"), (int, float))
    ]
    if not cheapest_candidates:
        return set()
    minimum_price = min(float(row["cheapest_cny_price"]) for row in cheapest_candidates)  # type: ignore[index]
    return {
        _row_signature(row)
        for row in cheapest_candidates
        if abs(float(row["cheapest_cny_price"]) - minimum_price) < 0.0001  # type: ignore[index]
    }


def _row_has_price(row: CombinedQuoteRow) -> bool:
    return any(
        isinstance(row.get(key), (int, float))
        for key in ("best_cny_price", "cheapest_cny_price")
    )


def _sort_combined_rows(rows: list[CombinedQuoteRow]) -> list[CombinedQuoteRow]:
    return sorted(
        rows,
        key=lambda row: (
            row.get("cheapest_cny_price") is None,
            float(row.get("cheapest_cny_price"))
            if isinstance(row.get("cheapest_cny_price"), (int, float))
            else float("inf"),
            row.get("best_cny_price") is None,
            float(row.get("best_cny_price"))
            if isinstance(row.get("best_cny_price"), (int, float))
            else float("inf"),
            str(row.get("updated_at") or ""),
            str(row.get("region_name") or ""),
        ),
    )


def _format_history_record(record: Any) -> str:
    title = str(getattr(record, "title", "") or "未命名查询")
    created_at = str(getattr(record, "created_at", "") or "-")
    prefix = "★ " if bool(getattr(record, "is_favorite", False)) else ""
    return f"{prefix}{title} [{created_at}]"


def _upsert_rows_by_date(
    rows_by_date: list[tuple[str, list[dict[str, str | float | None]]]],
    trip_label: str,
    rows: list[dict[str, str | float | None]],
) -> list[tuple[str, list[dict[str, str | float | None]]]]:
    updated: list[tuple[str, list[dict[str, str | float | None]]]] = []
    replaced = False
    for current_trip_label, current_rows in rows_by_date:
        if current_trip_label == trip_label:
            updated.append((trip_label, rows))
            replaced = True
        else:
            updated.append((current_trip_label, current_rows))
    if not replaced:
        updated.append((trip_label, rows))
    return updated


def _upsert_quotes_by_date(
    quotes_by_date: list[tuple[str, list[dict[str, Any]]]],
    trip_label: str,
    quotes: list[dict[str, Any]],
) -> list[tuple[str, list[dict[str, Any]]]]:
    updated: list[tuple[str, list[dict[str, Any]]]] = []
    replaced = False
    for current_trip_label, current_quotes in quotes_by_date:
        if current_trip_label == trip_label:
            updated.append((trip_label, quotes))
            replaced = True
        else:
            updated.append((current_trip_label, current_quotes))
    if not replaced:
        updated.append((trip_label, quotes))
    return updated


def _order_grouped_by_trip_labels(
    trip_labels: list[str],
    grouped_items: list[tuple[str, Any]],
) -> list[tuple[str, Any]]:
    order = {label: index for index, label in enumerate(trip_labels)}
    return sorted(
        grouped_items,
        key=lambda item: order.get(item[0], len(order)),
    )


def _find_missing_apify_data_files() -> list[str]:
    try:
        import apify_fingerprint_datapoints
    except ImportError:
        return _REQUIRED_APIFY_DATA_FILES[:]

    package_dir = Path(inspect.getfile(apify_fingerprint_datapoints)).resolve().parent
    data_dir = package_dir / "data"
    return [name for name in _REQUIRED_APIFY_DATA_FILES if not (data_dir / name).exists()]


def _collect_startup_issues() -> list[str]:
    issues: list[str] = []

    if importlib.util.find_spec("scrapling") is None:
        issues.append("缺少 Scrapling 主抓取依赖，请重新安装项目依赖。")

    if not AIRPORT_DATASET_PATH.exists():
        issues.append(f"缺少机场数据文件：{AIRPORT_DATASET_PATH}")

    if not LOCATION_MAPPINGS_PATH.exists():
        issues.append(f"缺少地点映射文件：{LOCATION_MAPPINGS_PATH}")

    missing_apify_files = _find_missing_apify_data_files()
    if missing_apify_files:
        missing_text = "、".join(missing_apify_files)
        issues.append(
            "缺少 Scrapling 指纹数据资源："
            f"{missing_text}。请使用最新桌面包重新解压后再试。"
        )

    return issues


def _show_startup_issues_and_exit(issues: list[str]) -> None:
    root = tk.Tk()
    root.withdraw()
    detail = "\n".join(f"{idx}. {issue}" for idx, issue in enumerate(issues, start=1))
    messagebox.showerror(
        "启动前自检失败",
        "应用缺少必要运行条件，已停止启动。\n\n"
        f"{detail}\n\n"
        "建议：请重新下载并解压最新桌面包；如果仍失败，再联系我排查。",
        parent=root,
    )
    root.destroy()


def _apply_classic_mac_theme(root: tk.Tk) -> None:
    """Apply a warm retro-desktop appearance."""
    root.configure(bg=_PAPER_GRAIN)
    root.option_add("*Background", _PAPER_GRAIN)
    root.option_add("*Foreground", _INK)
    root.option_add("*selectBackground", _HIGHLIGHT)
    root.option_add("*selectForeground", "#FFFFFF")

    style = ttk.Style()
    style.theme_use("clam")

    style.configure(
        ".", background=_PAPER_GRAIN, foreground=_INK,
        font=_FONT_BODY, borderwidth=1,
    )
    style.configure("TFrame", background=_PANEL_BG)
    style.configure("TLabel", background=_PANEL_BG, font=_FONT_BODY, foreground=_INK)
    style.configure(
        "TLabelframe", background=_PANEL_BG,
        relief="groove", borderwidth=2, bordercolor=_PANEL_EDGE,
    )
    style.configure(
        "TLabelframe.Label", background=_PANEL_BG,
        font=_FONT_HEADING, foreground=_MUTED_INK,
    )
    style.configure(
        "Panel.TLabelframe", background=_PANEL_BG,
        relief="groove", borderwidth=2, bordercolor=_PANEL_EDGE,
    )
    style.configure(
        "Panel.TLabelframe.Label", background=_PANEL_BG,
        font=_FONT_HEADING, foreground=_MUTED_INK,
    )
    style.configure(
        "TEntry", fieldbackground=_PLATINUM_LIGHT, foreground=_INK, font=_FONT_BODY,
        borderwidth=2, relief="sunken", insertcolor=_INK,
    )
    style.configure(
        "TButton", background=_BUTTON_FACE, foreground=_INK, font=_FONT_BUTTON,
        borderwidth=2, relief="raised", padding=(10, 6), bordercolor=_PANEL_EDGE,
    )
    style.map(
        "TButton",
        background=[("active", _PLATINUM_LIGHT), ("pressed", _PLATINUM_DARK)],
        relief=[("pressed", "sunken")],
    )
    style.configure(
        "Primary.TButton",
        background=_PRIMARY_BUTTON,
        foreground="#FFFFFF",
        bordercolor="#425972",
        padding=(14, 7),
    )
    style.map(
        "Primary.TButton",
        background=[
            ("active", _PRIMARY_BUTTON_ACTIVE),
            ("pressed", _PRIMARY_BUTTON_PRESSED),
        ],
        foreground=[("disabled", "#E4EAF0")],
        relief=[("pressed", "sunken")],
    )
    style.configure(
        "Secondary.TButton",
        background=_PLATINUM_LIGHT,
        foreground=_INK,
        bordercolor=_PANEL_EDGE,
        padding=(12, 6),
    )
    style.map(
        "Secondary.TButton",
        background=[("active", "#FBF7EF"), ("pressed", _PLATINUM)],
        relief=[("pressed", "sunken")],
    )
    style.configure(
        "Quiet.TLabel",
        background=_PANEL_BG,
        foreground=_MUTED_INK,
        font=_FONT_BODY,
    )
    style.configure("TCheckbutton", background=_PANEL_BG, foreground=_INK, font=_FONT_BODY)
    style.configure("TRadiobutton", background=_PANEL_BG, foreground=_INK, font=_FONT_BODY)
    style.configure(
        "Treeview", background=_PLATINUM_LIGHT, fieldbackground=_PLATINUM_LIGHT,
        foreground=_INK, font=_FONT_BODY, rowheight=26, borderwidth=1,
        bordercolor=_RULE,
    )
    style.configure(
        "Treeview.Heading", font=_FONT_HEADING,
        background=_BUTTON_FACE, foreground=_INK, relief="raised", borderwidth=1,
        bordercolor=_PANEL_EDGE, padding=(6, 6),
    )
    style.map("Treeview.Heading", background=[("active", _PLATINUM_LIGHT)])
    style.configure(
        "Horizontal.TProgressbar",
        background="#6C9C85", troughcolor="#DFD4C4", borderwidth=1,
        bordercolor=_PANEL_EDGE,
    )


def _draw_pinstripes(canvas: tk.Canvas, _event: tk.Event | None = None) -> None:
    """Draw subtle parchment pinstripes on a canvas."""
    canvas.delete("stripe")
    w = canvas.winfo_width()
    h = canvas.winfo_height()
    for y in range(0, h, 2):
        fill = _RULE if y % 4 == 0 else _PLATINUM_LIGHT
        canvas.create_line(0, y, w, y, fill=fill, tags="stripe")


class DatePickerDialog(tk.Toplevel):
    def __init__(
        self,
        parent: tk.Misc,
        *,
        initial_date: str,
        min_date: str | None = None,
    ) -> None:
        super().__init__(parent)
        self.title("选择日期")
        self.resizable(False, False)
        self.transient(parent)

        self.result: str | None = None
        self._calendar = calendar.Calendar(firstweekday=0)
        self._min_date = (
            datetime.strptime(min_date, "%Y-%m-%d").date() if min_date else None
        )
        self._selected_date = datetime.strptime(initial_date, "%Y-%m-%d").date()
        if self._min_date and self._selected_date < self._min_date:
            self._selected_date = self._min_date
        self._display_year = self._selected_date.year
        self._display_month = self._selected_date.month
        self._month_label_var = tk.StringVar()

        self.configure(bg=_PAPER_GRAIN)
        container = ttk.Frame(self, padding=12)
        container.pack(fill=tk.BOTH, expand=True)

        header = ttk.Frame(container)
        header.pack(fill=tk.X)
        ttk.Button(header, text="‹ 上月", command=self._show_previous_month, style="Secondary.TButton").pack(
            side=tk.LEFT
        )
        ttk.Label(
            header,
            textvariable=self._month_label_var,
            font=_FONT_HEADING,
        ).pack(side=tk.LEFT, expand=True)
        ttk.Button(header, text="下月 ›", command=self._show_next_month, style="Secondary.TButton").pack(
            side=tk.RIGHT
        )

        weekday_row = ttk.Frame(container)
        weekday_row.pack(fill=tk.X, pady=(10, 4))
        for column, label in enumerate(("一", "二", "三", "四", "五", "六", "日")):
            ttk.Label(weekday_row, text=label, anchor="center", width=4).grid(
                row=0, column=column, padx=1
            )

        self._days_frame = ttk.Frame(container)
        self._days_frame.pack()

        footer = ttk.Frame(container)
        footer.pack(fill=tk.X, pady=(10, 0))
        ttk.Button(footer, text="今天", command=self._choose_today, style="Primary.TButton").pack(side=tk.LEFT)
        ttk.Button(footer, text="取消", command=self.destroy, style="Secondary.TButton").pack(side=tk.RIGHT)

        self.protocol("WM_DELETE_WINDOW", self.destroy)
        self.bind("<Escape>", lambda _event: self.destroy())

        self._render_calendar()
        self.update_idletasks()
        self._center_over_parent(parent)
        self.grab_set()
        self.focus_set()

    def _center_over_parent(self, parent: tk.Misc) -> None:
        parent.update_idletasks()
        width = self.winfo_reqwidth()
        height = self.winfo_reqheight()
        x = parent.winfo_rootx() + max((parent.winfo_width() - width) // 2, 0)
        y = parent.winfo_rooty() + max((parent.winfo_height() - height) // 2, 0)
        self.geometry(f"{width}x{height}+{x}+{y}")

    def _choose_today(self) -> None:
        today = date.today()
        if self._min_date and today < self._min_date:
            today = self._min_date
        self._select_date(today)

    def _show_previous_month(self) -> None:
        year = self._display_year
        month = self._display_month - 1
        if month == 0:
            month = 12
            year -= 1
        if not self._month_has_selectable_day(year, month):
            return
        self._display_year = year
        self._display_month = month
        self._render_calendar()

    def _show_next_month(self) -> None:
        year = self._display_year
        month = self._display_month + 1
        if month == 13:
            month = 1
            year += 1
        self._display_year = year
        self._display_month = month
        self._render_calendar()

    def _month_has_selectable_day(self, year: int, month: int) -> bool:
        days = [
            day_value
            for week in self._calendar.monthdatescalendar(year, month)
            for day_value in week
            if day_value.month == month
        ]
        if not days:
            return False
        if self._min_date is None:
            return True
        return max(days) >= self._min_date

    def _render_calendar(self) -> None:
        for child in self._days_frame.winfo_children():
            child.destroy()
        self._month_label_var.set(f"{self._display_year} 年 {self._display_month:02d} 月")

        for row_index, week in enumerate(
            self._calendar.monthdatescalendar(self._display_year, self._display_month)
        ):
            for column_index, day_value in enumerate(week):
                is_current_month = day_value.month == self._display_month
                is_selectable = is_current_month and (
                    self._min_date is None or day_value >= self._min_date
                )
                button = tk.Button(
                    self._days_frame,
                    text=str(day_value.day),
                    width=4,
                    font=_FONT_BODY,
                    bg=_BUTTON_FACE if is_selectable else _PLATINUM_LIGHT,
                    fg=_INK if is_current_month else _MUTED_INK,
                    relief="raised",
                    borderwidth=2,
                    disabledforeground="#999999",
                    command=lambda current=day_value: self._select_date(current),
                )
                if day_value == self._selected_date and is_selectable:
                    button.configure(bg=_PRIMARY_BUTTON, fg="#FFFFFF", relief="sunken")
                if not is_selectable:
                    button.configure(state=tk.DISABLED)
                button.grid(row=row_index, column=column_index, padx=1, pady=1)

    def _select_date(self, value: date) -> None:
        self.result = value.strftime("%Y-%m-%d")
        self.destroy()


class App:
    def __init__(self, root: tk.Tk) -> None:
        self.root = root
        self.root.title("Skyscanner 多市场比价")
        self.root.geometry("1120x860")
        self.root.minsize(980, 720)

        self.cli = SimpleCLI()
        self.history_store = ScanHistoryStore()
        self.queue: queue.Queue[tuple[str, Any]] = queue.Queue()
        self._cancel_event = threading.Event()
        self.current_output: Path | None = None
        self._sort_state: dict[str, bool] = {}
        self._state_save_job: str | None = None
        self._cheapest_link: str | None = None
        self._recommendation_link: str | None = None
        self._state_path = get_gui_state_file()
        self._current_query_payload: dict[str, Any] | None = None
        self._display_rows: list[CombinedQuoteRow] = []
        self._rows_by_date: list[tuple[str, list[dict[str, str | float | None]]]] = []
        self._failure_row_by_item: dict[str, CombinedQuoteRow] = {}
        self._recommendation_row_by_item: dict[str, CombinedQuoteRow] = {}
        self._compare_row_by_item: dict[str, dict[str, str]] = {}
        self._recent_records: list[Any] = []
        self._favorite_records: list[Any] = []
        self._pending_retry_targets: dict[tuple[str, str, str], CombinedQuoteRow] = {}
        self._history_records_for_current_query: list[Any] = []
        self._previous_scan_record: Any | None = None
        self._selected_calendar_trip_label: str | None = None
        self._quick_filter_mode = "all"
        self._history_collapsed = True
        self._logs_collapsed = True

        default_departure = (datetime.now() + timedelta(days=30)).date()
        default_return = default_departure + timedelta(days=7)
        saved_state = _load_query_state(
            self._state_path,
            default_departure=default_departure.strftime("%Y-%m-%d"),
            default_return=default_return.strftime("%Y-%m-%d"),
        )

        self.origin_var = tk.StringVar(value=str(saved_state["origin"]))
        self.destination_var = tk.StringVar(value=str(saved_state["destination"]))
        self.trip_type_var = tk.StringVar(value=str(saved_state["trip_type"]))
        self.date_var = tk.StringVar(value=str(saved_state["date"]))
        self.return_date_var = tk.StringVar(value=str(saved_state["return_date"]))
        self.regions_var = tk.StringVar(value=str(saved_state["regions"]))
        self.wait_var = tk.StringVar(value=str(saved_state["wait"]))
        self.date_window_var = tk.StringVar(value=str(saved_state["date_window"]))
        self.exact_airport_var = tk.BooleanVar(value=bool(saved_state["exact_airport"]))
        self.origin_country_var = tk.BooleanVar(value=bool(saved_state["origin_country"]))
        self.destination_country_var = tk.BooleanVar(
            value=bool(saved_state["destination_country"])
        )
        self.combined_summary_var = tk.BooleanVar(
            value=bool(saved_state["combined_summary"])
        )
        self.status_var = tk.StringVar(value="就绪")
        self.origin_hint_var = tk.StringVar(value="")
        self.destination_hint_var = tk.StringVar(value="")
        self.regions_hint_var = tk.StringVar(value="")
        self.history_summary_var = tk.StringVar(value="已折叠。双击列表或展开后可查看最近查询与收藏路线。")
        self.logs_summary_var = tk.StringVar(value="日志已折叠。展开后可查看运行细节。")
        self.cheapest_card_headline_var = tk.StringVar(value="")
        self.cheapest_card_price_var = tk.StringVar(value="")
        self.cheapest_card_supporting_var = tk.StringVar(value="")
        self.cheapest_card_meta_var = tk.StringVar(value="")
        self.cheapest_card_insight_var = tk.StringVar(value="")
        self.recommendation_card_headline_var = tk.StringVar(value="")
        self.recommendation_card_price_var = tk.StringVar(value="")
        self.recommendation_card_supporting_var = tk.StringVar(value="")
        self.recommendation_card_meta_var = tk.StringVar(value="")
        self.recommendation_card_insight_var = tk.StringVar(value="")
        self.view_mode_var = tk.StringVar(value="calendar")
        self.price_mode_var = tk.StringVar(value="cheapest")
        self.source_mode_var = tk.StringVar(value="all")
        self.stability_mode_var = tk.StringVar(value="all")
        self.history_detail_var = tk.StringVar(value="等待扫描后生成路线复盘。")
        self.calendar_summary_var = tk.StringVar(value="等待扫描后生成价格日历。")
        self.filter_success_var = tk.BooleanVar(value=True)
        self.filter_failure_var = tk.BooleanVar(value=True)
        self.filter_changed_var = tk.BooleanVar(value=False)
        self.filter_lowest_var = tk.BooleanVar(value=False)
        self.location_entries: dict[str, ttk.Entry] = {}
        self.location_listboxes: dict[str, tk.Listbox] = {}
        self.location_hint_labels: dict[str, ttk.Label] = {}
        self.location_suggestion_values: dict[str, list[LocationRecord]] = {
            "origin": [],
            "destination": [],
        }
        self.return_date_cell: ttk.Frame | None = None

        self._build_ui()
        self.root.protocol("WM_DELETE_WINDOW", self._handle_close)
        self.origin_var.trace_add("write", self._refresh_location_hints)
        self.destination_var.trace_add("write", self._refresh_location_hints)
        self.origin_var.trace_add("write", self._refresh_origin_suggestions)
        self.destination_var.trace_add("write", self._refresh_destination_suggestions)
        self.regions_var.trace_add("write", self._refresh_location_hints)
        self.exact_airport_var.trace_add("write", self._refresh_location_hints)
        self.exact_airport_var.trace_add("write", self._refresh_origin_suggestions)
        self.origin_country_var.trace_add("write", self._refresh_location_hints)
        self.origin_country_var.trace_add("write", self._refresh_origin_suggestions)
        self.origin_country_var.trace_add("write", self._refresh_route_mode)
        self.destination_country_var.trace_add("write", self._refresh_location_hints)
        self.destination_country_var.trace_add("write", self._refresh_destination_suggestions)
        self.destination_country_var.trace_add("write", self._refresh_route_mode)
        self.trip_type_var.trace_add("write", self._refresh_trip_mode)
        self.date_var.trace_add("write", self._sync_return_date_minimum)
        for variable in (
            self.origin_var,
            self.destination_var,
            self.trip_type_var,
            self.date_var,
            self.return_date_var,
            self.regions_var,
            self.wait_var,
            self.date_window_var,
            self.exact_airport_var,
            self.origin_country_var,
            self.destination_country_var,
            self.combined_summary_var,
        ):
            variable.trace_add("write", self._schedule_query_state_save)
        self._refresh_location_hints()
        self._refresh_origin_suggestions()
        self._refresh_destination_suggestions()
        self._refresh_trip_mode()
        self._refresh_route_mode()
        self._apply_cheapest_conclusion(_build_cheapest_conclusion([]))
        self._apply_recommendation_conclusion(_build_recommendation_payload([]))
        self._refresh_history_lists()
        self._set_view_mode(self.view_mode_var.get())
        if self._state_path.exists():
            self.log("已恢复上次查询条件。")
        self._poll_queue()

    def _build_ui(self) -> None:
        outer = ttk.Frame(self.root, padding=14)
        outer.pack(fill=tk.BOTH, expand=True)

        hero = tk.Frame(
            outer,
            bg=_PANEL_BG,
            relief="groove",
            borderwidth=2,
            highlightthickness=1,
            highlightbackground=_PANEL_EDGE,
            padx=16,
            pady=12,
        )
        hero.pack(fill=tk.X, pady=(0, 8))

        stripe_bar = tk.Canvas(hero, height=6, bg=_PANEL_BG, highlightthickness=0)
        stripe_bar.pack(fill=tk.X, pady=(0, 8))
        stripe_bar.bind("<Configure>", lambda e: _draw_pinstripes(stripe_bar, e))

        title = tk.Label(
            hero, text="\u2318  Skyscanner 多市场比价", font=_FONT_TITLE, bg=_PANEL_BG, fg=_INK
        )
        title.pack(anchor="w")
        subtitle = tk.Label(
            hero,
            text="预览缓存优先，实时结果分批刷新；失败市场尽量复用已打开页面，少打扰浏览器。",
            bg=_PANEL_BG,
            fg=_MUTED_INK,
            font=_FONT_BODY,
        )
        subtitle.pack(anchor="w", pady=(3, 3))

        stripe_bar2 = tk.Canvas(hero, height=3, bg=_PANEL_BG, highlightthickness=0)
        stripe_bar2.pack(fill=tk.X, pady=(3, 0))
        stripe_bar2.bind("<Configure>", lambda e: _draw_pinstripes(stripe_bar2, e))

        form = ttk.LabelFrame(outer, text="查询参数", padding=12, style="Panel.TLabelframe")
        form.pack(fill=tk.X)

        self._add_labeled_entry(
            form,
            "出发地",
            self.origin_var,
            0,
            0,
            hint_var=self.origin_hint_var,
            location_field="origin",
        )
        self._add_labeled_entry(
            form,
            "目的地",
            self.destination_var,
            0,
            1,
            hint_var=self.destination_hint_var,
            location_field="destination",
        )
        self._add_trip_type_selector(form, 0, 2)
        self._add_date_selector(form, "出发日期", self.date_var, 1, 0)
        self.return_date_cell = self._add_date_selector(
            form,
            "返程日期",
            self.return_date_var,
            1,
            1,
            min_date_var=self.date_var,
        )
        self._add_labeled_entry(
            form,
            "额外地区代码",
            self.regions_var,
            2,
            0,
            colspan=2,
            hint_var=self.regions_hint_var,
        )
        self._add_labeled_entry(form, "等待秒数", self.wait_var, 1, 2)
        self._add_labeled_entry(form, "±天数", self.date_window_var, 2, 2)

        ttk.Checkbutton(
            form,
            text="保存多日期汇总",
            variable=self.combined_summary_var,
        ).grid(row=3, column=0, sticky="w", pady=(8, 0))

        ttk.Checkbutton(
            form,
            text="严格机场代码（例如北京不自动转成 BJSA）",
            variable=self.exact_airport_var,
        ).grid(row=3, column=1, columnspan=2, sticky="w", pady=(8, 0))

        ttk.Checkbutton(
            form,
            text="出发地按国家",
            variable=self.origin_country_var,
        ).grid(row=4, column=0, sticky="w", pady=(8, 0))

        ttk.Checkbutton(
            form,
            text="目的地按国家",
            variable=self.destination_country_var,
        ).grid(row=4, column=1, sticky="w", pady=(8, 0))

        button_row = ttk.Frame(form)
        button_row.grid(row=4, column=2, sticky="e")
        self.doctor_button = ttk.Button(
            button_row, text="检查环境", command=self.check_environment, style="Secondary.TButton"
        )
        self.doctor_button.pack(side=tk.LEFT, padx=(0, 8))
        self.run_button = ttk.Button(
            button_row, text="开始比价", command=self.start_scan, style="Primary.TButton"
        )
        self.run_button.pack(side=tk.LEFT)
        self.favorite_button = ttk.Button(
            button_row, text="收藏当前查询", command=self._toggle_current_favorite, style="Secondary.TButton"
        )
        self.favorite_button.pack(side=tk.LEFT, padx=(8, 0))

        history = ttk.LabelFrame(outer, text="最近查询与收藏路线", padding=12, style="Panel.TLabelframe")
        history.pack(fill=tk.X, pady=(8, 0))
        history_top = ttk.Frame(history)
        history_top.pack(fill=tk.X)
        ttk.Label(history_top, textvariable=self.history_summary_var, style="Quiet.TLabel").pack(
            side=tk.LEFT, anchor="w"
        )
        self.history_toggle_button = ttk.Button(
            history_top,
            text="展开",
            command=self._toggle_history_panel,
            style="Secondary.TButton",
        )
        self.history_toggle_button.pack(side=tk.RIGHT)

        self.history_content = ttk.Frame(history)
        history_columns = ttk.Frame(self.history_content)
        history_columns.pack(fill=tk.X)

        favorites_frame = ttk.Frame(history_columns)
        favorites_frame.pack(side=tk.LEFT, fill=tk.BOTH, expand=True, padx=(0, 8))
        ttk.Label(
            favorites_frame,
            text="收藏路线（置顶 10 条）",
            style="Quiet.TLabel",
        ).pack(anchor="w")
        self.favorites_listbox = tk.Listbox(
            favorites_frame,
            height=3,
            activestyle="none",
            exportselection=False,
            font=_FONT_BODY,
            bg=_PLATINUM_LIGHT,
            fg=_INK,
            relief="sunken",
            borderwidth=2,
            selectbackground=_HIGHLIGHT,
            selectforeground="#FFFFFF",
        )
        self.favorites_listbox.pack(fill=tk.X, expand=True, pady=(4, 0))
        self.favorites_listbox.bind(
            "<Double-Button-1>",
            lambda _event: self._rerun_selected_history_query("favorites"),
        )

        recent_frame = ttk.Frame(history_columns)
        recent_frame.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        ttk.Label(
            recent_frame,
            text="最近查询（最近 20 条）",
            style="Quiet.TLabel",
        ).pack(anchor="w")
        self.recent_listbox = tk.Listbox(
            recent_frame,
            height=3,
            activestyle="none",
            exportselection=False,
            font=_FONT_BODY,
            bg=_PLATINUM_LIGHT,
            fg=_INK,
            relief="sunken",
            borderwidth=2,
            selectbackground=_HIGHLIGHT,
            selectforeground="#FFFFFF",
        )
        self.recent_listbox.pack(fill=tk.X, expand=True, pady=(4, 0))
        self.recent_listbox.bind(
            "<Double-Button-1>",
            lambda _event: self._rerun_selected_history_query("recent"),
        )

        history_actions = ttk.Frame(self.history_content)
        history_actions.pack(fill=tk.X, pady=(8, 0))
        ttk.Button(
            history_actions,
            text="应用所选查询",
            command=self._apply_selected_history_query,
            style="Secondary.TButton",
        ).pack(side=tk.LEFT)
        ttk.Button(
            history_actions,
            text="一键重跑",
            command=lambda: self._rerun_selected_history_query(None),
            style="Primary.TButton",
        ).pack(side=tk.LEFT, padx=(8, 0))
        ttk.Button(
            history_actions,
            text="只重跑失败市场",
            command=self._rerun_failed_from_current_query,
            style="Secondary.TButton",
        ).pack(side=tk.LEFT, padx=(8, 0))
        ttk.Button(
            history_actions,
            text="查看路线详情",
            command=self._show_selected_history_detail,
            style="Secondary.TButton",
        ).pack(side=tk.LEFT, padx=(8, 0))
        ttk.Button(
            history_actions,
            text="刷新历史",
            command=self._refresh_history_lists,
            style="Secondary.TButton",
        ).pack(side=tk.RIGHT)

        status = ttk.LabelFrame(outer, text="状态", padding=12, style="Panel.TLabelframe")
        status.pack(fill=tk.X, pady=(8, 0))
        status_top = ttk.Frame(status)
        status_top.pack(fill=tk.X)
        ttk.Label(status_top, textvariable=self.status_var).pack(anchor="w", side=tk.LEFT)
        self.cancel_button = ttk.Button(
            status_top, text="取消", command=self._cancel_scan, style="Secondary.TButton"
        )
        self.cancel_button.pack(side=tk.RIGHT)
        self.cancel_button.pack_forget()
        self.progress_bar = ttk.Progressbar(status, mode="determinate", length=400)
        self.progress_bar.pack(fill=tk.X, pady=(6, 0))
        self.progress_bar.pack_forget()

        results = ttk.LabelFrame(outer, text="结果", padding=12, style="Panel.TLabelframe")
        results.pack(fill=tk.BOTH, expand=True, pady=(8, 0))

        cards_row = ttk.Frame(results)
        cards_row.pack(fill=tk.X, pady=(0, 10))

        conclusion = tk.Frame(
            cards_row,
            bg=_CARD_BG,
            relief="groove",
            borderwidth=2,
            highlightthickness=1,
            highlightbackground=_CARD_BORDER,
            padx=14,
            pady=12,
        )
        conclusion.pack(side=tk.LEFT, fill=tk.BOTH, expand=True, padx=(0, 8))
        conclusion_left = tk.Frame(conclusion, bg=_CARD_BG)
        conclusion_left.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        tk.Label(
            conclusion_left,
            text="最低价结论",
            bg=_CARD_BG,
            fg="#6E5620",
            font=_FONT_HEADING,
        ).pack(anchor="w")
        tk.Label(
            conclusion_left,
            textvariable=self.cheapest_card_headline_var,
            bg=_CARD_BG,
            fg=_INK,
            font=_FONT_CARD_HEADLINE,
        ).pack(anchor="w", pady=(4, 0))
        tk.Label(
            conclusion_left,
            textvariable=self.cheapest_card_price_var,
            bg=_CARD_BG,
            fg=_CARD_PRICE,
            font=_FONT_CARD_PRICE,
        ).pack(anchor="w", pady=(4, 0))
        tk.Label(
            conclusion_left,
            textvariable=self.cheapest_card_supporting_var,
            bg=_CARD_BG,
            fg=_INK,
            font=_FONT_HEADING,
        ).pack(anchor="w", pady=(2, 0))
        tk.Label(
            conclusion_left,
            textvariable=self.cheapest_card_meta_var,
            bg=_CARD_BG,
            fg=_MUTED_INK,
            font=_FONT_BODY,
        ).pack(anchor="w", pady=(6, 0))
        tk.Label(
            conclusion_left,
            textvariable=self.cheapest_card_insight_var,
            bg=_CARD_BG,
            fg=_MUTED_INK,
            font=_FONT_BODY,
            justify="left",
            wraplength=620,
        ).pack(anchor="w", pady=(4, 0))

        conclusion_actions = tk.Frame(conclusion, bg=_CARD_BG)
        conclusion_actions.pack(side=tk.RIGHT, anchor="n", padx=(12, 0))
        self.cheapest_card_button = ttk.Button(
            conclusion_actions,
            text="等待结果",
            command=self._open_cheapest_link,
            state=tk.DISABLED,
            style="Primary.TButton",
        )
        self.cheapest_card_button.pack(anchor="e")

        recommendation = tk.Frame(
            cards_row,
            bg=_PLATINUM_LIGHT,
            relief="groove",
            borderwidth=2,
            highlightthickness=1,
            highlightbackground=_PANEL_EDGE,
            padx=14,
            pady=12,
        )
        recommendation.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        recommendation_left = tk.Frame(recommendation, bg=_PLATINUM_LIGHT)
        recommendation_left.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        tk.Label(
            recommendation_left,
            text="推荐下单方案",
            bg=_PLATINUM_LIGHT,
            fg=_MUTED_INK,
            font=_FONT_HEADING,
        ).pack(anchor="w")
        tk.Label(
            recommendation_left,
            textvariable=self.recommendation_card_headline_var,
            bg=_PLATINUM_LIGHT,
            fg=_INK,
            font=_FONT_CARD_HEADLINE,
        ).pack(anchor="w", pady=(4, 0))
        tk.Label(
            recommendation_left,
            textvariable=self.recommendation_card_price_var,
            bg=_PLATINUM_LIGHT,
            fg=_PRIMARY_BUTTON_PRESSED,
            font=_FONT_CARD_PRICE,
        ).pack(anchor="w", pady=(4, 0))
        tk.Label(
            recommendation_left,
            textvariable=self.recommendation_card_supporting_var,
            bg=_PLATINUM_LIGHT,
            fg=_INK,
            font=_FONT_HEADING,
        ).pack(anchor="w", pady=(2, 0))
        tk.Label(
            recommendation_left,
            textvariable=self.recommendation_card_meta_var,
            bg=_PLATINUM_LIGHT,
            fg=_MUTED_INK,
            font=_FONT_BODY,
        ).pack(anchor="w", pady=(6, 0))
        tk.Label(
            recommendation_left,
            textvariable=self.recommendation_card_insight_var,
            bg=_PLATINUM_LIGHT,
            fg=_MUTED_INK,
            font=_FONT_BODY,
            justify="left",
            wraplength=420,
        ).pack(anchor="w", pady=(4, 0))

        recommendation_actions = tk.Frame(recommendation, bg=_PLATINUM_LIGHT)
        recommendation_actions.pack(side=tk.RIGHT, anchor="n", padx=(12, 0))
        self.recommendation_card_button = ttk.Button(
            recommendation_actions,
            text="等待结果",
            command=self._open_recommendation_link,
            state=tk.DISABLED,
            style="Primary.TButton",
        )
        self.recommendation_card_button.pack(anchor="e")

        ttk.Label(
            results,
            text="默认先看结论卡片和成功结果；失败市场会单独放在下方，便于快速补救。",
            style="Quiet.TLabel",
        ).pack(anchor="w", pady=(0, 6))

        filter_row = ttk.Frame(results)
        filter_row.pack(fill=tk.X, pady=(0, 8))
        ttk.Label(filter_row, text="快捷筛选", style="Quiet.TLabel").pack(side=tk.LEFT)
        ttk.Button(
            filter_row,
            text="最低价优先",
            command=lambda: self._apply_quick_filter("lowest_priority"),
            style="Secondary.TButton",
        ).pack(side=tk.LEFT, padx=(8, 0))
        ttk.Button(
            filter_row,
            text="最佳优先",
            command=lambda: self._apply_quick_filter("best_priority"),
            style="Secondary.TButton",
        ).pack(side=tk.LEFT, padx=(8, 0))
        ttk.Button(
            filter_row,
            text="仅实时结果",
            command=lambda: self._apply_quick_filter("live_only"),
            style="Secondary.TButton",
        ).pack(side=tk.LEFT, padx=(8, 0))
        ttk.Button(
            filter_row,
            text="仅今日最低",
            command=lambda: self._apply_quick_filter("trip_lowest_only"),
            style="Secondary.TButton",
        ).pack(side=tk.LEFT, padx=(8, 0))
        ttk.Button(
            filter_row,
            text="仅可下单成功市场",
            command=lambda: self._apply_quick_filter("bookable_only"),
            style="Secondary.TButton",
        ).pack(side=tk.LEFT, padx=(8, 0))
        ttk.Button(
            filter_row,
            text="重置筛选",
            command=lambda: self._apply_quick_filter("all"),
            style="Secondary.TButton",
        ).pack(side=tk.LEFT, padx=(8, 0))

        detail_row = ttk.Frame(results)
        detail_row.pack(fill=tk.BOTH, pady=(0, 8))

        top_panel = ttk.LabelFrame(detail_row, text="Top 方案", padding=10, style="Panel.TLabelframe")
        top_panel.pack(side=tk.LEFT, fill=tk.BOTH, expand=False, padx=(0, 8))
        self.top_tree = ttk.Treeview(
            top_panel,
            columns=("rank", "date", "region", "price", "stability", "source"),
            show="headings",
            height=6,
        )
        for column, label, width in (
            ("rank", "#", 40),
            ("date", "日期", 120),
            ("region", "地区", 90),
            ("price", "最低价", 100),
            ("stability", "稳定性", 120),
            ("source", "来源", 100),
        ):
            self.top_tree.heading(column, text=label)
            self.top_tree.column(column, width=width, anchor="w")
        self.top_tree.pack(fill=tk.BOTH, expand=True)
        self.top_tree.bind("<Double-1>", self._open_selected_recommendation)
        ttk.Button(
            top_panel,
            text="打开所选方案",
            command=self._open_selected_recommendation,
            style="Primary.TButton",
        ).pack(anchor="e", pady=(8, 0))

        insight_panel = ttk.LabelFrame(detail_row, text="决策视图", padding=10, style="Panel.TLabelframe")
        insight_panel.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        view_buttons = ttk.Frame(insight_panel)
        view_buttons.pack(fill=tk.X, pady=(0, 8))
        for value, label in (
            ("calendar", "价格日历"),
            ("compare", "历史对比"),
            ("history_detail", "路线复盘"),
            ("table", "表格聚焦"),
        ):
            ttk.Button(
                view_buttons,
                text=label,
                command=lambda target=value: self._set_view_mode(target),
                style="Secondary.TButton",
            ).pack(side=tk.LEFT, padx=(0, 8))

        self.insight_stack = ttk.Frame(insight_panel)
        self.insight_stack.pack(fill=tk.BOTH, expand=True)

        self.calendar_frame = ttk.Frame(self.insight_stack)
        self.calendar_summary_label = ttk.Label(
            self.calendar_frame,
            textvariable=self.calendar_summary_var,
            style="Quiet.TLabel",
        )
        self.calendar_summary_label.pack(anchor="w", pady=(0, 6))
        self.calendar_grid = ttk.Frame(self.calendar_frame)
        self.calendar_grid.pack(fill=tk.BOTH, expand=True)

        self.compare_frame = ttk.Frame(self.insight_stack)
        self.compare_tree = ttk.Treeview(
            self.compare_frame,
            columns=("date", "route", "region", "current", "previous", "change"),
            show="headings",
            height=6,
        )
        for column, label, width in (
            ("date", "日期", 120),
            ("route", "航段", 120),
            ("region", "地区", 90),
            ("current", "当前", 90),
            ("previous", "上次", 90),
            ("change", "变化", 140),
        ):
            self.compare_tree.heading(column, text=label)
            self.compare_tree.column(column, width=width, anchor="w")
        self.compare_tree.pack(fill=tk.BOTH, expand=True)

        self.history_detail_frame = ttk.Frame(self.insight_stack)
        ttk.Label(
            self.history_detail_frame,
            textvariable=self.history_detail_var,
            justify="left",
            style="Quiet.TLabel",
        ).pack(anchor="w")
        ttk.Checkbutton(
            filter_row,
            text="只看成功",
            variable=self.filter_success_var,
            command=self._refresh_result_views,
        ).pack(side=tk.LEFT)
        ttk.Checkbutton(
            filter_row,
            text="只看失败",
            variable=self.filter_failure_var,
            command=self._refresh_result_views,
        ).pack(side=tk.LEFT, padx=(8, 0))
        ttk.Checkbutton(
            filter_row,
            text="只看价格变化的市场",
            variable=self.filter_changed_var,
            command=self._refresh_result_views,
        ).pack(side=tk.LEFT, padx=(8, 0))
        ttk.Checkbutton(
            filter_row,
            text="只看最低价候选",
            variable=self.filter_lowest_var,
            command=self._refresh_result_views,
        ).pack(side=tk.LEFT, padx=(8, 0))

        ttk.Label(results, text="成功结果", font=_FONT_HEADING).pack(anchor="w")
        columns = (
            "date",
            "route",
            "region",
            "source",
            "best_native",
            "best_cny",
            "cheapest_native",
            "cheapest_cny",
            "delta",
            "updated_at",
            "link",
        )
        self.tree = ttk.Treeview(results, columns=columns, show="headings", height=8)
        for col, label in _COLUMN_LABELS.items():
            self.tree.heading(
                col, text=label, command=lambda c=col: self._sort_column(c)
            )
        self.tree.column("date", width=180, anchor="w")
        self.tree.column("route", width=120, anchor="w")
        self.tree.column("region", width=110, anchor="w")
        self.tree.column("source", width=110, anchor="w")
        self.tree.column("best_native", width=120, anchor="e")
        self.tree.column("best_cny", width=120, anchor="e")
        self.tree.column("cheapest_native", width=120, anchor="e")
        self.tree.column("cheapest_cny", width=120, anchor="e")
        self.tree.column("delta", width=120, anchor="w")
        self.tree.column("updated_at", width=150, anchor="w")
        self.tree.column("link", width=240, anchor="w")
        self.tree.tag_configure("cheapest_highlight", background=_CHEAPEST_TINT)
        self.tree.tag_configure("changed_highlight", background=_SUCCESS_TINT)
        self.tree.pack(fill=tk.BOTH, expand=True)
        self.tree.bind("<Double-1>", self._on_tree_double_click)

        ttk.Label(results, text="失败市场", font=_FONT_HEADING).pack(anchor="w", pady=(10, 0))
        failure_columns = tuple(_FAILURE_COLUMN_LABELS.keys())
        self.failure_tree = ttk.Treeview(
            results,
            columns=failure_columns,
            show="headings",
            height=4,
        )
        for col, label in _FAILURE_COLUMN_LABELS.items():
            self.failure_tree.heading(col, text=label)
        self.failure_tree.column("date", width=180, anchor="w")
        self.failure_tree.column("route", width=120, anchor="w")
        self.failure_tree.column("region", width=110, anchor="w")
        self.failure_tree.column("source", width=110, anchor="w")
        self.failure_tree.column("category", width=150, anchor="w")
        self.failure_tree.column("action", width=200, anchor="w")
        self.failure_tree.column("reuse", width=100, anchor="center")
        self.failure_tree.column("status", width=120, anchor="w")
        self.failure_tree.column("error", width=220, anchor="w")
        self.failure_tree.column("link", width=240, anchor="w")
        self.failure_tree.tag_configure("reuse_ready", background="#EEF3EA")
        self.failure_tree.tag_configure("failure_default", background=_FAILURE_TINT)
        self.failure_tree.pack(fill=tk.BOTH, expand=False, pady=(4, 0))
        self.failure_tree.bind("<Double-1>", self._on_failure_tree_double_click)

        failure_actions = ttk.Frame(results)
        failure_actions.pack(fill=tk.X, pady=(8, 0))
        ttk.Button(
            failure_actions,
            text="仅重试此市场",
            command=self._retry_selected_failure_market,
            style="Primary.TButton",
        ).pack(side=tk.LEFT)
        ttk.Button(
            failure_actions,
            text="打开该市场结果页",
            command=self._open_selected_failure_link,
            style="Secondary.TButton",
        ).pack(side=tk.LEFT, padx=(8, 0))
        ttk.Button(
            failure_actions,
            text="加入补扫队列",
            command=self._queue_selected_failure_market,
            style="Secondary.TButton",
        ).pack(side=tk.LEFT, padx=(8, 0))
        ttk.Button(
            failure_actions,
            text="运行补扫队列",
            command=self._run_retry_queue,
            style="Secondary.TButton",
        ).pack(side=tk.LEFT, padx=(8, 0))

        actions = ttk.Frame(results)
        actions.pack(fill=tk.X, pady=(8, 0))
        ttk.Button(actions, text="打开结果文件夹", command=self.open_outputs, style="Secondary.TButton").pack(
            side=tk.LEFT
        )
        ttk.Button(
            actions,
            text="导出决策摘要",
            command=self._export_decision_summary,
            style="Secondary.TButton",
        ).pack(side=tk.LEFT, padx=(8, 0))

        logs = ttk.LabelFrame(outer, text="运行日志", padding=12, style="Panel.TLabelframe")
        logs.pack(fill=tk.X, expand=False, pady=(8, 0))
        logs_top = ttk.Frame(logs)
        logs_top.pack(fill=tk.X)
        ttk.Label(logs_top, textvariable=self.logs_summary_var, style="Quiet.TLabel").pack(
            side=tk.LEFT, anchor="w"
        )
        self.logs_toggle_button = ttk.Button(
            logs_top,
            text="展开",
            command=self._toggle_logs_panel,
            style="Secondary.TButton",
        )
        self.logs_toggle_button.pack(side=tk.RIGHT)
        self.logs_content = ttk.Frame(logs)
        self.log_text = tk.Text(
            self.logs_content, height=6, wrap="word", font=_FONT_MONO,
            bg=_PLATINUM_LIGHT, fg=_INK, insertbackground=_INK,
            relief="sunken", borderwidth=2,
        )
        self.log_text.pack(fill=tk.X, expand=False)
        self.log("界面已启动。可先点“检查环境”确认主抓取与回退环境，再开始比价。")
        self._apply_history_panel_state()
        self._apply_logs_panel_state()

    def _refresh_history_lists(self) -> None:
        self._favorite_records = self.history_store.get_favorites()
        self._recent_records = self.history_store.get_recent_queries()
        if hasattr(self, "favorites_listbox"):
            self.favorites_listbox.delete(0, tk.END)
            for record in self._favorite_records:
                self.favorites_listbox.insert(tk.END, _format_history_record(record))
        if hasattr(self, "recent_listbox"):
            self.recent_listbox.delete(0, tk.END)
            for record in self._recent_records:
                self.recent_listbox.insert(tk.END, _format_history_record(record))

    def _selected_history_record(self, preferred: str | None = None) -> Any | None:
        if preferred in {None, "favorites"} and hasattr(self, "favorites_listbox"):
            selection = self.favorites_listbox.curselection()
            if selection:
                return self._favorite_records[selection[0]]
        if preferred in {None, "recent"} and hasattr(self, "recent_listbox"):
            selection = self.recent_listbox.curselection()
            if selection:
                return self._recent_records[selection[0]]
        return None

    def _apply_history_record_to_form(self, record: Any) -> None:
        payload = getattr(record, "query_payload", {}) or {}
        identity = payload.get("identity") or {}
        manual_regions = identity.get("manual_regions") or []
        self.origin_var.set(str(identity.get("origin_input") or identity.get("origin_label") or ""))
        self.destination_var.set(
            str(identity.get("destination_input") or identity.get("destination_label") or "")
        )
        self.date_var.set(str(identity.get("date") or self.date_var.get()))
        self.return_date_var.set(str(identity.get("return_date") or ""))
        self.trip_type_var.set(
            _TRIP_TYPE_ROUND_TRIP if identity.get("return_date") else _TRIP_TYPE_ONE_WAY
        )
        self.date_window_var.set(str(identity.get("date_window_days") or "0"))
        self.regions_var.set(",".join(str(code) for code in manual_regions if code))
        self.exact_airport_var.set(bool(identity.get("exact_airport")))
        self.origin_country_var.set(bool(identity.get("origin_is_country")))
        self.destination_country_var.set(bool(identity.get("destination_is_country")))
        self._current_query_payload = payload if isinstance(payload, dict) else None
        self._persist_query_state()

    def _apply_selected_history_query(self) -> None:
        record = self._selected_history_record()
        if record is None:
            messagebox.showinfo("未选择查询", "先在“收藏路线”或“最近查询”里选中一条记录。")
            return
        self._apply_history_record_to_form(record)
        self.status_var.set("已应用历史查询")
        self.log(f"已载入历史查询: {getattr(record, 'title', '未命名查询')}")

    def _rerun_selected_history_query(self, preferred: str | None = None) -> None:
        record = self._selected_history_record(preferred)
        if record is None:
            messagebox.showinfo("未选择查询", "先在“收藏路线”或“最近查询”里选中一条记录。")
            return
        self._apply_history_record_to_form(record)
        self.start_scan()

    def _build_fallback_current_query_payload(self) -> dict[str, Any]:
        manual_regions = [
            code.strip().upper() for code in self.regions_var.get().split(",") if code.strip()
        ]
        if self.origin_country_var.get() or self.destination_country_var.get():
            return self.cli.build_expanded_query_payload(
                origin_value=self.origin_var.get().strip(),
                destination_value=self.destination_var.get().strip(),
                origin_label=self.origin_var.get().strip() or "出发地",
                destination_label=self.destination_var.get().strip() or "目的地",
                origin_file_token=self.origin_var.get().strip() or "-",
                destination_file_token=self.destination_var.get().strip() or "-",
                date=self.date_var.get().strip(),
                return_date=self.return_date_var.get().strip() or None,
                date_window_days=int(self.date_window_var.get() or "0"),
                manual_regions=manual_regions,
                effective_regions=manual_regions or list(DEFAULT_REGIONS),
                exact_airport=bool(self.exact_airport_var.get()),
                origin_is_country=bool(self.origin_country_var.get()),
                destination_is_country=bool(self.destination_country_var.get()),
                airport_limit=COUNTRY_ROUTE_DEFAULT_AIRPORT_LIMIT,
            )
        return self.cli.build_point_query_payload(
            origin_input=self.origin_var.get().strip(),
            destination_input=self.destination_var.get().strip(),
            origin_label=self.origin_var.get().strip() or "出发地",
            destination_label=self.destination_var.get().strip() or "目的地",
            origin_code=self.origin_var.get().strip() or "-",
            destination_code=self.destination_var.get().strip() or "-",
            date=self.date_var.get().strip(),
            return_date=self.return_date_var.get().strip() or None,
            date_window_days=int(self.date_window_var.get() or "0"),
            manual_regions=manual_regions,
            effective_regions=manual_regions or list(DEFAULT_REGIONS),
            exact_airport=bool(self.exact_airport_var.get()),
        )

    def _toggle_current_favorite(self) -> None:
        query_payload = self._current_query_payload or self._build_fallback_current_query_payload()
        is_favorite = self.history_store.toggle_favorite(query_payload)
        self._refresh_history_lists()
        self.log("已收藏当前查询。" if is_favorite else "已取消收藏当前查询。")

    def _rerun_failed_from_current_query(self) -> None:
        self.start_scan(rerun_scope_override="failed_only")

    def _add_labeled_entry(
        self,
        parent: ttk.Widget,
        label: str,
        var: tk.StringVar,
        row: int,
        column: int,
        colspan: int = 1,
        hint_var: tk.StringVar | None = None,
        location_field: str | None = None,
    ) -> None:
        cell = ttk.Frame(parent)
        cell.grid(
            row=row,
            column=column,
            columnspan=colspan,
            sticky="ew",
            padx=(0, 12),
            pady=(0, 8),
        )
        ttk.Label(cell, text=label).pack(anchor="w")
        entry = ttk.Entry(cell, textvariable=var)
        entry.pack(fill=tk.X, expand=True)
        if location_field is not None:
            self.location_entries[location_field] = entry
            listbox = tk.Listbox(
                cell, height=0, activestyle="none", exportselection=False,
                font=_FONT_BODY, bg=_PLATINUM_LIGHT, fg=_INK,
                relief="sunken", borderwidth=2,
                selectbackground=_HIGHLIGHT, selectforeground="#FFFFFF",
            )
            listbox.pack(fill=tk.X, expand=True, pady=(4, 0))
            listbox.pack_forget()
            listbox.bind(
                "<ButtonRelease-1>",
                lambda event, field=location_field: self._select_location_suggestion(
                    field
                ),
            )
            listbox.bind(
                "<Return>",
                lambda event, field=location_field: self._select_location_suggestion(
                    field
                )
                or "break",
            )
            listbox.bind(
                "<Double-Button-1>",
                lambda event, field=location_field: self._select_location_suggestion(
                    field
                ),
            )
            self.location_listboxes[location_field] = listbox
            entry.bind(
                "<Down>",
                lambda event, field=location_field: self._focus_location_suggestions(
                    field
                ),
            )
            entry.bind(
                "<Return>",
                lambda event,
                field=location_field: self._accept_first_location_suggestion(field),
            )
            entry.bind(
                "<FocusOut>",
                lambda event, field=location_field: self.root.after(
                    150, lambda: self._hide_location_suggestions(field)
                ),
            )
            listbox.bind(
                "<Up>",
                lambda event, field=location_field: self._move_location_selection(
                    field, -1
                ),
            )
            listbox.bind(
                "<Down>",
                lambda event, field=location_field: self._move_location_selection(
                    field, 1
                ),
            )
            listbox.bind(
                "<Escape>",
                lambda event, field=location_field: self._close_location_suggestions(
                    field
                ),
            )
        if hint_var is not None:
            hint_label = ttk.Label(cell, textvariable=hint_var, style="Quiet.TLabel")
            hint_label.pack(anchor="w", pady=(4, 0))
            if location_field is not None:
                self.location_hint_labels[location_field] = hint_label
        parent.columnconfigure(column, weight=1)

    def _add_trip_type_selector(
        self, parent: ttk.Widget, row: int, column: int
    ) -> ttk.Frame:
        cell = ttk.Frame(parent)
        cell.grid(row=row, column=column, sticky="ew", padx=(0, 12), pady=(0, 8))
        ttk.Label(cell, text="行程类型").pack(anchor="w")
        radios = ttk.Frame(cell)
        radios.pack(fill=tk.X, expand=True)
        ttk.Radiobutton(
            radios,
            text="单程",
            value=_TRIP_TYPE_ONE_WAY,
            variable=self.trip_type_var,
        ).pack(side=tk.LEFT)
        ttk.Radiobutton(
            radios,
            text="往返",
            value=_TRIP_TYPE_ROUND_TRIP,
            variable=self.trip_type_var,
        ).pack(side=tk.LEFT, padx=(8, 0))
        parent.columnconfigure(column, weight=1)
        return cell

    def _add_date_selector(
        self,
        parent: ttk.Widget,
        label: str,
        var: tk.StringVar,
        row: int,
        column: int,
        *,
        min_date_var: tk.StringVar | None = None,
    ) -> ttk.Frame:
        cell = ttk.Frame(parent)
        cell.grid(row=row, column=column, sticky="ew", padx=(0, 12), pady=(0, 8))
        ttk.Label(cell, text=label).pack(anchor="w")

        picker_row = ttk.Frame(cell)
        picker_row.pack(fill=tk.X, expand=True)
        entry = ttk.Entry(picker_row, textvariable=var, state="readonly")
        entry.pack(side=tk.LEFT, fill=tk.X, expand=True)
        entry.bind(
            "<Button-1>",
            lambda _event, target=var, minimum=min_date_var: self._open_date_picker(
                target, min_date_var=minimum
            ),
        )
        entry.bind("<Key>", lambda _event: "break")
        ttk.Button(
            picker_row,
            text="选择...",
            command=lambda target=var, minimum=min_date_var: self._open_date_picker(
                target, min_date_var=minimum
            ),
            style="Secondary.TButton",
        ).pack(side=tk.LEFT, padx=(6, 0))
        parent.columnconfigure(column, weight=1)
        return cell

    def _open_date_picker(
        self,
        target_var: tk.StringVar,
        *,
        min_date_var: tk.StringVar | None = None,
    ) -> None:
        min_date = min_date_var.get().strip() if min_date_var is not None else None
        dialog = DatePickerDialog(
            self.root,
            initial_date=target_var.get().strip(),
            min_date=min_date or None,
        )
        self.root.wait_window(dialog)
        if dialog.result:
            target_var.set(dialog.result)

    def _sync_return_date_minimum(self, *_args: object) -> None:
        if self.trip_type_var.get() != _TRIP_TYPE_ROUND_TRIP:
            return
        departure_date = self.date_var.get().strip()
        return_date = self.return_date_var.get().strip()
        if return_date and return_date < departure_date:
            self.return_date_var.set(departure_date)

    def _refresh_trip_mode(self, *_args: object) -> None:
        if self.return_date_cell is None:
            return
        if self.trip_type_var.get() == _TRIP_TYPE_ROUND_TRIP:
            self.return_date_cell.grid()
            self._sync_return_date_minimum()
        else:
            self.return_date_cell.grid_remove()

    def _refresh_route_mode(self, *_args: object) -> None:
        if self.origin_country_var.get():
            self.exact_airport_var.set(False)

    def _field_uses_country_mode(self, field: str) -> bool:
        return self.origin_country_var.get() if field == "origin" else self.destination_country_var.get()

    def _format_location_suggestion(self, item: LocationRecord) -> str:
        if item.kind == "country":
            return f"{item.name} ({item.code}, 国家)"
        if item.kind == "metro":
            return f"{item.name} ({item.code}, 城市)"
        details = [part for part in [item.municipality, item.country] if part]
        suffix = f" - {' / '.join(details)}" if details else ""
        return f"{item.name} ({item.code}){suffix}"

    def _get_location_suggestions(
        self, field: str, value: str, *, prefer_metro: bool
    ) -> list[LocationRecord]:
        if self._field_uses_country_mode(field):
            return [
                LocationRecord(name=item.name, code=item.code, kind="country")
                for item in self.cli.location_resolver.search_countries(
                    value,
                    limit=MAX_LOCATION_SUGGESTIONS,
                )
            ]
        return self.cli.location_resolver.search_locations(
            value,
            prefer_metro=prefer_metro,
            limit=MAX_LOCATION_SUGGESTIONS,
        )

    def _set_location_suggestions(
        self, field: str, suggestions: list[LocationRecord]
    ) -> None:
        listbox = self.location_listboxes[field]
        self.location_suggestion_values[field] = suggestions
        listbox.delete(0, tk.END)
        if not suggestions:
            self._hide_location_suggestions(field)
            return

        for item in suggestions:
            listbox.insert(tk.END, self._format_location_suggestion(item))
        listbox.config(height=min(len(suggestions), MAX_LOCATION_SUGGESTIONS))
        listbox.selection_clear(0, tk.END)
        listbox.selection_set(0)
        listbox.activate(0)
        if not listbox.winfo_ismapped():
            pack_kwargs: dict[str, Any] = {
                "fill": tk.X,
                "expand": True,
                "pady": (4, 0),
            }
            hint_label = self.location_hint_labels.get(field)
            if hint_label is not None:
                pack_kwargs["before"] = hint_label
            listbox.pack(**pack_kwargs)

    def _hide_location_suggestions(self, field: str) -> None:
        listbox = self.location_listboxes[field]
        self.location_suggestion_values[field] = []
        listbox.delete(0, tk.END)
        if listbox.winfo_ismapped():
            listbox.pack_forget()

    def _refresh_origin_suggestions(self, *args: object) -> None:
        suggestions = self._get_location_suggestions(
            "origin",
            self.origin_var.get(),
            prefer_metro=not self.exact_airport_var.get(),
        )
        self._set_location_suggestions("origin", suggestions)

    def _refresh_destination_suggestions(self, *args: object) -> None:
        suggestions = self._get_location_suggestions(
            "destination",
            self.destination_var.get(),
            prefer_metro=False,
        )
        self._set_location_suggestions("destination", suggestions)

    def _focus_location_suggestions(self, field: str) -> str:
        values = self.location_suggestion_values[field]
        if not values:
            return "break"
        listbox = self.location_listboxes[field]
        listbox.focus_set()
        if not listbox.curselection():
            listbox.selection_set(0)
            listbox.activate(0)
        return "break"

    def _accept_first_location_suggestion(self, field: str) -> str:
        values = self.location_suggestion_values[field]
        if not values:
            return ""
        listbox = self.location_listboxes[field]
        selection = listbox.curselection()
        index = selection[0] if selection else 0
        self._apply_location_suggestion(field, index)
        return "break"

    def _move_location_selection(self, field: str, step: int) -> str:
        values = self.location_suggestion_values[field]
        if not values:
            return "break"
        listbox = self.location_listboxes[field]
        current = listbox.curselection()
        index = current[0] if current else 0
        next_index = max(0, min(len(values) - 1, index + step))
        listbox.selection_clear(0, tk.END)
        listbox.selection_set(next_index)
        listbox.activate(next_index)
        listbox.see(next_index)
        return "break"

    def _close_location_suggestions(self, field: str) -> str:
        self._hide_location_suggestions(field)
        self.location_entries[field].focus_set()
        return "break"

    def _select_location_suggestion(self, field: str) -> None:
        listbox = self.location_listboxes[field]
        selection = listbox.curselection()
        if not selection:
            return
        self._apply_location_suggestion(field, selection[0])

    def _apply_location_suggestion(self, field: str, index: int) -> None:
        values = self.location_suggestion_values[field]
        if index < 0 or index >= len(values):
            return
        value = values[index].name
        if field == "origin":
            self.origin_var.set(value)
        else:
            self.destination_var.set(value)
        self._hide_location_suggestions(field)
        self.location_entries[field].focus_set()
        self.location_entries[field].icursor(tk.END)

    def _set_location_hint(
        self,
        field: str,
        hint_var: tk.StringVar,
        label: str,
        value: str,
        prefer_metro: bool,
    ) -> None:
        raw = value.strip()
        if not raw:
            hint_var.set("")
            return
        if self._field_uses_country_mode(field):
            try:
                country = self.cli.resolve_country(raw)
                _resolved, airports = self.cli.location_resolver.get_country_route_airports(
                    raw,
                    limit=COUNTRY_ROUTE_DEFAULT_AIRPORT_LIMIT,
                )
                hint_var.set(
                    f"{label}将使用国家代码: {country.code}；候选机场: "
                    + ", ".join(airport.code for airport in airports)
                )
            except ValueError as exc:
                hint_var.set(str(exc))
            return
        try:
            code = self.cli.normalize_location(raw, prefer_metro=prefer_metro)
            kind = self.cli.location_resolver.describe_code_kind(code)
            hint_var.set(f"{label}将使用 {kind}: {code}")
        except ValueError as exc:
            hint_var.set(str(exc))

    def _compute_effective_regions(self) -> list[str]:
        manual_regions = [
            code.strip().upper()
            for code in self.regions_var.get().split(",")
            if code.strip()
        ]
        try:
            if self.origin_country_var.get():
                origin_country = self.cli.resolve_country(self.origin_var.get()).code
            else:
                origin_country = self.cli.resolve_location(
                    self.origin_var.get(), prefer_metro=not self.exact_airport_var.get()
                ).country
            if self.destination_country_var.get():
                destination_country = self.cli.resolve_country(self.destination_var.get()).code
            else:
                destination_country = self.cli.resolve_location(
                    self.destination_var.get(), prefer_metro=False
                ).country
        except ValueError:
            return build_effective_region_codes(manual_region_codes=manual_regions)
        return build_effective_region_codes(
            origin_country=origin_country,
            destination_country=destination_country,
            manual_region_codes=manual_regions,
        )

    def _refresh_location_hints(self, *args: object) -> None:
        self._set_location_hint(
            "origin",
            self.origin_hint_var,
            "出发地",
            self.origin_var.get(),
            prefer_metro=not self.exact_airport_var.get(),
        )
        self._set_location_hint(
            "destination",
            self.destination_hint_var,
            "目的地",
            self.destination_var.get(),
            prefer_metro=False,
        )
        effective_regions = self._compute_effective_regions()
        self.regions_hint_var.set(
            f"默认包含 {','.join(DEFAULT_REGIONS)}；本次实际地区: {', '.join(effective_regions)}"
        )

    def _current_query_state(self) -> dict[str, Any]:
        return {
            "origin": self.origin_var.get().strip(),
            "destination": self.destination_var.get().strip(),
            "trip_type": self.trip_type_var.get().strip() or _TRIP_TYPE_ONE_WAY,
            "date": self.date_var.get().strip(),
            "return_date": self.return_date_var.get().strip(),
            "regions": self.regions_var.get().strip(),
            "wait": self.wait_var.get().strip(),
            "date_window": self.date_window_var.get().strip(),
            "exact_airport": bool(self.exact_airport_var.get()),
            "origin_country": bool(self.origin_country_var.get()),
            "destination_country": bool(self.destination_country_var.get()),
            "combined_summary": bool(self.combined_summary_var.get()),
        }

    def _schedule_query_state_save(self, *_args: object) -> None:
        if self._state_save_job is not None:
            self.root.after_cancel(self._state_save_job)
        self._state_save_job = self.root.after(250, self._persist_query_state)

    def _persist_query_state(self) -> None:
        self._state_save_job = None
        try:
            _write_query_state(self._state_path, self._current_query_state())
        except OSError as exc:
            self.log(f"保存上次查询条件失败: {exc}")

    def _handle_close(self) -> None:
        if self._state_save_job is not None:
            self.root.after_cancel(self._state_save_job)
            self._state_save_job = None
        self._persist_query_state()
        self.root.destroy()

    def _apply_cheapest_conclusion(
        self, payload: dict[str, str | None]
    ) -> None:
        self.cheapest_card_headline_var.set(str(payload.get("headline") or ""))
        self.cheapest_card_price_var.set(str(payload.get("price") or ""))
        self.cheapest_card_supporting_var.set(str(payload.get("supporting") or ""))
        self.cheapest_card_meta_var.set(str(payload.get("meta") or ""))
        self.cheapest_card_insight_var.set(str(payload.get("insight") or ""))
        self._cheapest_link = payload.get("link") or None
        button_text = str(payload.get("button_text") or "打开最低价结果页")
        self.cheapest_card_button.config(text=button_text)
        self.cheapest_card_button.config(
            state=tk.NORMAL if self._cheapest_link else tk.DISABLED
        )

    def _apply_recommendation_conclusion(
        self, payload: dict[str, str | None]
    ) -> None:
        self.recommendation_card_headline_var.set(str(payload.get("headline") or ""))
        self.recommendation_card_price_var.set(str(payload.get("price") or ""))
        self.recommendation_card_supporting_var.set(str(payload.get("supporting") or ""))
        self.recommendation_card_meta_var.set(str(payload.get("meta") or ""))
        self.recommendation_card_insight_var.set(str(payload.get("insight") or ""))
        self._recommendation_link = payload.get("link") or None
        button_text = str(payload.get("button_text") or "打开推荐方案")
        self.recommendation_card_button.config(text=button_text)
        self.recommendation_card_button.config(
            state=tk.NORMAL if self._recommendation_link else tk.DISABLED
        )

    def _open_cheapest_link(self) -> None:
        if self._cheapest_link and self._cheapest_link.startswith("http"):
            webbrowser.open(self._cheapest_link)

    def _open_recommendation_link(self) -> None:
        if self._recommendation_link and self._recommendation_link.startswith("http"):
            webbrowser.open(self._recommendation_link)

    def _set_view_mode(self, mode: str) -> None:
        self.view_mode_var.set(mode)
        for frame in (
            self.calendar_frame,
            self.compare_frame,
            self.history_detail_frame,
        ):
            if frame.winfo_ismapped():
                frame.pack_forget()
        if mode == "calendar":
            self.calendar_frame.pack(fill=tk.BOTH, expand=True)
        elif mode == "compare":
            self.compare_frame.pack(fill=tk.BOTH, expand=True)
        elif mode == "history_detail":
            self.history_detail_frame.pack(fill=tk.BOTH, expand=True)
        else:
            self.calendar_summary_var.set("当前聚焦下方结果表；可切回价格日历查看日期矩阵。")
            self.calendar_frame.pack(fill=tk.BOTH, expand=True)

    def _apply_quick_filter(self, mode: str) -> None:
        self._quick_filter_mode = mode
        if mode == "lowest_priority":
            self.price_mode_var.set("cheapest")
            self.source_mode_var.set("all")
            self.filter_success_var.set(True)
            self.filter_failure_var.set(False)
        elif mode == "best_priority":
            self.price_mode_var.set("best")
            self.source_mode_var.set("all")
            self.filter_success_var.set(True)
            self.filter_failure_var.set(False)
        elif mode == "live_only":
            self.source_mode_var.set("live_only")
            self.filter_success_var.set(True)
            self.filter_failure_var.set(False)
        elif mode == "trip_lowest_only":
            self.filter_success_var.set(True)
            self.filter_failure_var.set(False)
        elif mode == "bookable_only":
            self.source_mode_var.set("bookable_only")
            self.filter_success_var.set(True)
            self.filter_failure_var.set(False)
        else:
            self.price_mode_var.set("cheapest")
            self.source_mode_var.set("all")
            self.filter_success_var.set(True)
            self.filter_failure_var.set(True)
            self.filter_changed_var.set(False)
            self.filter_lowest_var.set(False)
            self._selected_calendar_trip_label = None
        self._refresh_result_views()

    def _open_selected_recommendation(self, _event: tk.Event | None = None) -> None:
        selection = self.top_tree.selection()
        if not selection:
            return
        row = self._recommendation_row_by_item.get(selection[0])
        if row is None:
            return
        link = str(row.get("link") or "")
        if link.startswith("http"):
            webbrowser.open(link)

    def _show_selected_history_detail(self) -> None:
        record = self._selected_history_record()
        if record is None:
            messagebox.showinfo("未选择查询", "先在“收藏路线”或“最近查询”里选中一条记录。")
            return
        history_records = self.history_store.get_query_history(
            getattr(record, "query_payload", {}) or {},
            limit=10,
        )
        self._history_records_for_current_query = history_records
        self.history_detail_var.set(self._format_history_detail(history_records))
        self._set_view_mode("history_detail")

    def log(self, message: str) -> None:
        timestamp = datetime.now().strftime("%H:%M:%S")
        self.log_text.insert(tk.END, f"[{timestamp}] {message}\n")
        self.log_text.see(tk.END)
        self.logs_summary_var.set(f"最新日志: [{timestamp}] {message}")

    def _apply_history_panel_state(self) -> None:
        if self._history_collapsed:
            if self.history_content.winfo_ismapped():
                self.history_content.pack_forget()
            self.history_toggle_button.configure(text="展开")
        else:
            if not self.history_content.winfo_ismapped():
                self.history_content.pack(fill=tk.X, pady=(8, 0))
            self.history_toggle_button.configure(text="折叠")

    def _toggle_history_panel(self) -> None:
        self._history_collapsed = not self._history_collapsed
        self._apply_history_panel_state()

    def _apply_logs_panel_state(self) -> None:
        if self._logs_collapsed:
            if self.logs_content.winfo_ismapped():
                self.logs_content.pack_forget()
            self.logs_toggle_button.configure(text="展开")
        else:
            if not self.logs_content.winfo_ismapped():
                self.logs_content.pack(fill=tk.X, pady=(8, 0))
            self.logs_toggle_button.configure(text="折叠")

    def _toggle_logs_panel(self) -> None:
        self._logs_collapsed = not self._logs_collapsed
        self._apply_logs_panel_state()

    def _prioritize_results_view(self) -> None:
        self._history_collapsed = True
        self._logs_collapsed = True
        self._apply_history_panel_state()
        self._apply_logs_panel_state()
        self.root.update_idletasks()
        if hasattr(self, "tree"):
            self.tree.focus_set()

    def _format_history_detail(self, history_records: list[Any]) -> str:
        if not history_records:
            return "暂无路线历史。完成至少一次扫描后，这里会展示历史最低价、成功市场和价格趋势。"
        summary = summarize_query_history(history_records)
        lines = [
            f"近 {summary.scan_count} 次扫描，最近一次: {summary.latest_scan_at or '-'}",
            (
                f"历史最低价: ¥{summary.history_low_price:,.2f} "
                f"({summary.history_low_trip_label or '-'} · {summary.history_low_region or '-'})"
                if isinstance(summary.history_low_price, (int, float))
                else "历史最低价: 暂无可比较价格"
            ),
        ]
        if summary.market_win_counts:
            best_market, best_count = max(
                summary.market_win_counts.items(),
                key=lambda item: (item[1], item[0]),
            )
            lines.append(f"最常胜出市场: {best_market}（{best_count} 次最低价）")
        if summary.market_success_counts:
            stable_market, stable_count = max(
                summary.market_success_counts.items(),
                key=lambda item: (
                    item[1] / max(summary.market_total_counts.get(item[0], 1), 1),
                    item[1],
                    item[0],
                ),
            )
            total_count = summary.market_total_counts.get(stable_market, stable_count)
            lines.append(
                f"最常成功市场: {stable_market}（成功 {stable_count}/{total_count}）"
            )
        if summary.recent_prices:
            lines.append(
                f"最近价格走势: {_build_trend_sparkline(summary.recent_prices)} "
                f"({', '.join(f'¥{price:,.0f}' for price in summary.recent_prices)})"
            )
        return "\n".join(lines)

    def _render_top_recommendations(self, rows: list[CombinedQuoteRow]) -> None:
        for item in self.top_tree.get_children():
            self.top_tree.delete(item)
        self._recommendation_row_by_item.clear()
        for index, row in enumerate(
            _build_top_recommendations(rows, mode=self.price_mode_var.get()),
            start=1,
        ):
            price_value = (
                row.get("best_cny_price")
                if self.price_mode_var.get() == "best"
                else row.get("cheapest_cny_price")
            )
            item_id = self.top_tree.insert(
                "",
                tk.END,
                values=(
                    index,
                    row.get("date") or "-",
                    row.get("region_name") or "-",
                    f"¥{float(price_value):,.2f}" if isinstance(price_value, (int, float)) else "-",
                    row.get("stability_label") or "-",
                    row.get("source_label") or source_kind_label(row.get("source_kind")),
                ),
            )
            self._recommendation_row_by_item[item_id] = row

    def _render_calendar_view(self, rows: list[CombinedQuoteRow]) -> None:
        for child in self.calendar_grid.winfo_children():
            child.destroy()
        if not rows:
            self.calendar_summary_var.set("等待扫描后生成价格日历。")
            return
        summary = _build_calendar_summary(rows)
        departures = sorted(summary.keys())
        return_dates = sorted(
            {
                return_key
                for departure in departures
                for return_key in summary[departure].keys()
                if return_key != "__oneway__"
            }
        )
        one_way = not return_dates
        if one_way:
            self.calendar_summary_var.set("点击日期卡片可只看该日结果。")
            max_columns = 4
            for index, departure in enumerate(departures):
                row_index = index // max_columns
                column_index = index % max_columns
                winner = summary[departure]["__oneway__"]
                button = ttk.Button(
                    self.calendar_grid,
                    text=(
                        f"{departure}\n"
                        f"¥{float(winner.get('cheapest_cny_price')):,.0f}\n"
                        f"{winner.get('region_name') or '-'}"
                    )
                    if isinstance(winner.get("cheapest_cny_price"), (int, float))
                    else f"{departure}\n无价格",
                    command=lambda label=departure: self._select_trip_label(label),
                    style="Secondary.TButton",
                )
                button.grid(row=row_index, column=column_index, sticky="nsew", padx=4, pady=4)
                self.calendar_grid.columnconfigure(column_index, weight=1)
            return

        self.calendar_summary_var.set("往返矩阵：点击已填价格单元格可只看对应去返程组合。")
        ttk.Label(self.calendar_grid, text="出发\\返程", style="Quiet.TLabel").grid(
            row=0, column=0, sticky="w", padx=4, pady=4
        )
        for column_index, return_date in enumerate(return_dates, start=1):
            ttk.Label(self.calendar_grid, text=return_date, style="Quiet.TLabel").grid(
                row=0, column=column_index, sticky="w", padx=4, pady=4
            )
            self.calendar_grid.columnconfigure(column_index, weight=1)
        for row_index, departure in enumerate(departures, start=1):
            ttk.Label(self.calendar_grid, text=departure, style="Quiet.TLabel").grid(
                row=row_index, column=0, sticky="w", padx=4, pady=4
            )
            for column_index, return_date in enumerate(return_dates, start=1):
                winner = summary.get(departure, {}).get(return_date)
                if winner is None:
                    ttk.Label(self.calendar_grid, text="—", style="Quiet.TLabel").grid(
                        row=row_index, column=column_index, sticky="nsew", padx=4, pady=4
                    )
                    continue
                trip_label = f"{departure} -> {return_date}"
                text = (
                    f"¥{float(winner.get('cheapest_cny_price')):,.0f}\n{winner.get('region_name') or '-'}"
                    if isinstance(winner.get("cheapest_cny_price"), (int, float))
                    else f"无价格\n{winner.get('region_name') or '-'}"
                )
                ttk.Button(
                    self.calendar_grid,
                    text=text,
                    command=lambda label=trip_label: self._select_trip_label(label),
                    style="Secondary.TButton",
                ).grid(row=row_index, column=column_index, sticky="nsew", padx=4, pady=4)

    def _render_compare_view(self, rows: list[CombinedQuoteRow]) -> None:
        for item in self.compare_tree.get_children():
            self.compare_tree.delete(item)
        self._compare_row_by_item.clear()
        previous_rows = []
        if self._previous_scan_record is not None:
            for trip_label, prior_rows in getattr(self._previous_scan_record, "rows_by_date", []) or []:
                for row in prior_rows:
                    previous_rows.append({"date": trip_label, **row})
        for item in _build_compare_rows(rows, previous_rows):
            item_id = self.compare_tree.insert(
                "",
                tk.END,
                values=(
                    item["date"],
                    item["route"],
                    item["region"],
                    item["current"],
                    item["previous"],
                    item["change"],
                ),
            )
            self._compare_row_by_item[item_id] = item

    def _select_trip_label(self, trip_label: str) -> None:
        self._selected_calendar_trip_label = trip_label
        self._quick_filter_mode = "calendar_trip"
        self._refresh_result_views()

    def _update_decision_views(self) -> None:
        self._apply_recommendation_conclusion(_build_recommendation_payload(self._display_rows))
        self._render_top_recommendations(self._display_rows)
        self._render_calendar_view(self._display_rows)
        self._render_compare_view(self._display_rows)
        self.history_detail_var.set(
            self._format_history_detail(self._history_records_for_current_query)
        )

    def clear_results(self) -> None:
        for item in self.tree.get_children():
            self.tree.delete(item)
        if hasattr(self, "failure_tree"):
            for item in self.failure_tree.get_children():
                self.failure_tree.delete(item)
        self._failure_row_by_item.clear()
        self._display_rows = []
        self._rows_by_date = []
        self._sort_state.clear()
        for col, label in _COLUMN_LABELS.items():
            self.tree.heading(col, text=label)
        self._apply_cheapest_conclusion(_build_cheapest_conclusion([]))
        self._apply_recommendation_conclusion(_build_recommendation_payload([]))
        if hasattr(self, "top_tree"):
            for item in self.top_tree.get_children():
                self.top_tree.delete(item)
        if hasattr(self, "compare_tree"):
            for item in self.compare_tree.get_children():
                self.compare_tree.delete(item)
        if hasattr(self, "calendar_grid"):
            for child in self.calendar_grid.winfo_children():
                child.destroy()
        self.calendar_summary_var.set("等待扫描后生成价格日历。")
        self.history_detail_var.set("等待扫描后生成路线复盘。")

    def _refresh_result_views(self) -> None:
        success_rows = [row for row in self._display_rows if _row_has_price(row)]
        failure_rows = [row for row in self._display_rows if not _row_has_price(row)]
        if self.price_mode_var.get() == "best":
            success_rows = sorted(success_rows, key=lambda row: _decision_price_key(row, "best"))
        else:
            success_rows = _sort_combined_rows(success_rows)
        cheapest_highlight_signatures = _find_cheapest_highlight_signatures(success_rows)

        if self.source_mode_var.get() == "live_only":
            success_rows = [row for row in success_rows if _is_live_source_kind(row.get("source_kind"))]
            failure_rows = [row for row in failure_rows if _is_live_source_kind(row.get("source_kind"))]
        elif self.source_mode_var.get() == "bookable_only":
            success_rows = [
                row
                for row in success_rows
                if str(row.get("link") or "").startswith("http")
                and str(row.get("status") or "").strip().lower() not in {
                    "page_loading",
                    "px_challenge",
                    "page_challenge",
                    "captcha_solve_failed",
                }
            ]
            failure_rows = []

        if self.stability_mode_var.get() == "stable_only":
            success_rows = [
                row
                for row in success_rows
                if str(row.get("stability_label") or "") in {"近期稳定", "连续 2 次低位", "连续 3 次低位", "连续 4 次低位"}
            ]

        if self._quick_filter_mode == "trip_lowest_only" and success_rows:
            winner = min(success_rows, key=_decision_price_key)
            target_trip_label = str(winner.get("date") or "")
            success_rows = [row for row in success_rows if str(row.get("date") or "") == target_trip_label]
            failure_rows = [row for row in failure_rows if str(row.get("date") or "") == target_trip_label]

        if self._selected_calendar_trip_label:
            success_rows = [
                row for row in success_rows if str(row.get("date") or "") == self._selected_calendar_trip_label
            ]
            failure_rows = [
                row for row in failure_rows if str(row.get("date") or "") == self._selected_calendar_trip_label
            ]

        if self.filter_changed_var.get():
            def changed_only(row: CombinedQuoteRow) -> bool:
                return str(row.get("delta_label") or "-") not in {"-", "持平", ""}

            success_rows = [row for row in success_rows if changed_only(row)]
            failure_rows = [row for row in failure_rows if changed_only(row)]

        if self.filter_lowest_var.get():
            success_rows = [
                row
                for row in success_rows
                if _row_signature(row) in cheapest_highlight_signatures
            ]
            failure_rows = []

        if self.filter_success_var.get() and not self.filter_failure_var.get():
            failure_rows = []
        elif self.filter_failure_var.get() and not self.filter_success_var.get():
            success_rows = []

        for item in self.tree.get_children():
            self.tree.delete(item)
        for item in self.failure_tree.get_children():
            self.failure_tree.delete(item)
        self._failure_row_by_item.clear()

        for row in success_rows:
            row_signature = _row_signature(row)
            row_tags: tuple[str, ...] = ()
            if row_signature in cheapest_highlight_signatures:
                row_tags = ("cheapest_highlight",)
            elif str(row.get("delta_label") or "-") not in {"-", "持平", ""}:
                row_tags = ("changed_highlight",)
            best_cny_text = (
                f"¥{row['best_cny_price']:,.2f}"
                if isinstance(row.get("best_cny_price"), (int, float))
                else "-"
            )
            cheapest_cny_text = (
                f"¥{row['cheapest_cny_price']:,.2f}"
                if isinstance(row.get("cheapest_cny_price"), (int, float))
                else "-"
            )
            self.tree.insert(
                "",
                tk.END,
                values=(
                    row.get("date") or "-",
                    row.get("route") or "-",
                    row.get("region_name") or "-",
                    row.get("source_label") or source_kind_label(row.get("source_kind")),
                    row.get("best_display_price") or "-",
                    best_cny_text,
                    row.get("cheapest_display_price") or "-",
                    cheapest_cny_text,
                    row.get("delta_label") or "-",
                    row.get("updated_at") or "-",
                    row.get("link") or "-",
                ),
                tags=row_tags,
            )

        for row in failure_rows:
            failure_tags = ("reuse_ready",) if row.get("can_reuse_page") else ("failure_default",)
            item_id = self.failure_tree.insert(
                "",
                tk.END,
                values=(
                    row.get("date") or "-",
                    row.get("route") or "-",
                    row.get("region_name") or "-",
                    row.get("source_label") or source_kind_label(row.get("source_kind")),
                    row.get("failure_category") or "-",
                    row.get("failure_action") or "-",
                    "是" if row.get("can_reuse_page") else "-",
                    row.get("status") or "-",
                    row.get("error") or "-",
                    row.get("link") or "-",
                ),
                tags=failure_tags,
            )
            self._failure_row_by_item[item_id] = row

    def _set_display_rows_from_grouped(
        self,
        rows_by_date: list[tuple[str, list[dict[str, str | float | None]]]],
    ) -> None:
        combined_rows: list[CombinedQuoteRow] = []
        self._rows_by_date = rows_by_date
        for row_date, rows in rows_by_date:
            for row in rows:
                combined_rows.append({"date": row_date, **row})
        self._display_rows = _enrich_decision_rows(
            combined_rows,
            self._history_records_for_current_query,
        )
        self._refresh_result_views()
        self._apply_cheapest_conclusion(_build_cheapest_conclusion(self._display_rows))
        self._update_decision_views()

    def _sort_column(self, col: str) -> None:
        """Sort treeview rows by *col*, toggling asc/desc on repeated clicks."""
        reverse = self._sort_state.get(col, False)
        items = [(self.tree.set(iid, col), iid) for iid in self.tree.get_children()]

        if col in _PRICE_COLUMNS:
            items.sort(key=lambda p: self._parse_price(p[0]), reverse=reverse)
        else:
            items.sort(key=lambda p: p[0], reverse=reverse)

        for index, (_, iid) in enumerate(items):
            self.tree.move(iid, "", index)

        self._sort_state[col] = not reverse
        arrow = " ↑" if not reverse else " ↓"
        for c, label in _COLUMN_LABELS.items():
            self.tree.heading(c, text=label + (arrow if c == col else ""))

    @staticmethod
    def _parse_price(text: str) -> float:
        """Extract a numeric value from a display price string for sorting."""
        if not text or text == "-":
            return float("inf")
        cleaned = text.replace(",", "")
        nums = re.findall(r"[\d.]+", cleaned)
        if nums:
            try:
                return float(nums[0])
            except ValueError:
                pass
        return float("inf")

    def set_busy(self, busy: bool) -> None:
        state = tk.DISABLED if busy else tk.NORMAL
        self.run_button.config(state=state)
        self.doctor_button.config(state=state)
        if hasattr(self, "favorite_button"):
            self.favorite_button.config(state=state)
        if busy:
            self.cancel_button.pack(side=tk.RIGHT)
            self.progress_bar.pack(fill=tk.X, pady=(6, 0))
        else:
            self.cancel_button.pack_forget()
            self.progress_bar.pack_forget()
            self.progress_bar["value"] = 0

    def _cancel_scan(self) -> None:
        self._cancel_event.set()
        self.status_var.set("正在取消...")
        self.cancel_button.config(state=tk.DISABLED)

    def _on_tree_double_click(self, event: tk.Event) -> None:
        col_id = self.tree.identify_column(event.x)
        item = self.tree.identify_row(event.y)
        if not item:
            return
        col_index = int(col_id.replace("#", "")) - 1
        columns = (
            "date",
            "route",
            "region",
            "source",
            "best_native",
            "best_cny",
            "cheapest_native",
            "cheapest_cny",
            "delta",
            "updated_at",
            "link",
        )
        if col_index < 0 or col_index >= len(columns):
            return
        if columns[col_index] == "link":
            url = self.tree.set(item, "link")
            if url and url.startswith("http"):
                webbrowser.open(url)

    def _on_failure_tree_double_click(self, event: tk.Event) -> None:
        col_id = self.failure_tree.identify_column(event.x)
        item = self.failure_tree.identify_row(event.y)
        if not item:
            return
        col_index = int(col_id.replace("#", "")) - 1
        columns = tuple(_FAILURE_COLUMN_LABELS.keys())
        if col_index < 0 or col_index >= len(columns):
            return
        if columns[col_index] == "link":
            self._open_selected_failure_link()

    def _selected_failure_row(self) -> CombinedQuoteRow | None:
        selection = self.failure_tree.selection()
        if not selection:
            return None
        return self._failure_row_by_item.get(selection[0])

    def _open_selected_failure_link(self) -> None:
        row = self._selected_failure_row()
        if row is None:
            messagebox.showinfo("未选择失败市场", "先在失败市场列表里选中一条记录。")
            return
        link = str(row.get("link") or "")
        if link.startswith("http"):
            webbrowser.open(link)

    def _queue_selected_failure_market(self) -> None:
        row = self._selected_failure_row()
        if row is None:
            messagebox.showinfo("未选择失败市场", "先在失败市场列表里选中一条记录。")
            return
        region_code = str(row.get("region_code") or "")
        retry_key = (
            str(row.get("date") or ""),
            str(row.get("route") or ""),
            region_code,
        )
        if region_code:
            self._pending_retry_targets[retry_key] = row
        self.log(
            f"已加入补扫队列: {row.get('region_name') or region_code or '未知市场'} "
            f"(当前待补扫 {len(self._pending_retry_targets)} 个)"
        )

    def _run_retry_queue(self) -> None:
        queued_regions = sorted(
            {
                str(row.get("region_code") or "").strip().upper()
                for row in self._pending_retry_targets.values()
                if str(row.get("region_code") or "").strip()
            }
        )
        if not queued_regions:
            messagebox.showinfo("补扫队列为空", "先把失败市场加入补扫队列，再执行补扫。")
            return
        self.log(f"开始执行补扫队列: {', '.join(queued_regions)}")
        self._pending_retry_targets = {}
        self.start_scan(
            rerun_scope_override="selected_regions",
            selected_region_codes=queued_regions,
            allow_browser_fallback=False,
        )

    def _retry_selected_failure_market(self) -> None:
        row = self._selected_failure_row()
        if row is None:
            messagebox.showinfo("未选择失败市场", "先在失败市场列表里选中一条记录。")
            return
        region_code = str(row.get("region_code") or "")
        if not region_code:
            messagebox.showwarning("缺少地区代码", "当前失败记录缺少可重试的地区代码。")
            return
        self.start_scan(
            rerun_scope_override="selected_regions",
            selected_region_codes=[region_code],
            allow_browser_fallback=False,
        )

    def check_environment(self) -> None:
        neo = NeoCli(self.cli.project_root)
        scrapling_ready = importlib.util.find_spec("scrapling") is not None
        cdp = detect_cdp_version()
        if cdp:
            cdp_line = f"浏览器/CDP 回退: {cdp.get('Browser', '已连接')}"
        else:
            cdp_line = "浏览器/CDP 回退: 未连接（优先复用 Comet，其次 Edge；仅影响已打开浏览器复用与失败市场自动兜底）"
        lines = [
            f"Scrapling 主抓取: {'已安装' if scrapling_ready else '未安装'}",
            f"Neo CLI: {'已找到' if neo.available else '未找到'}",
            cdp_line,
            f"项目目录: {self.cli.project_root}",
        ]
        self.status_var.set(lines[0] if scrapling_ready else "主抓取环境未就绪")
        for line in lines:
            self.log(line)
        if not scrapling_ready:
            messagebox.showwarning(
                "环境未就绪",
                '未检测到 Scrapling。请先安装依赖，例如执行: pip install -r requirements.txt',
            )
        elif not cdp:
            messagebox.showinfo(
                "主抓取已就绪",
                "Scrapling 主抓取可用，但未检测到浏览器/CDP。大多数扫描仍可运行，只是失败市场无法自动回退。",
            )
        else:
            messagebox.showinfo(
                "环境已就绪", "主抓取与浏览器/CDP 回退均可用，可以开始比价。"
            )

    def start_scan(
        self,
        *,
        rerun_scope_override: str = "all",
        selected_region_codes: list[str] | None = None,
        allow_browser_fallback: bool = True,
    ) -> None:
        origin = self.origin_var.get().strip()
        destination = self.destination_var.get().strip()
        date = self.date_var.get().strip()
        trip_type = self.trip_type_var.get()
        return_date = (
            self.return_date_var.get().strip()
            if trip_type == _TRIP_TYPE_ROUND_TRIP
            else None
        )
        manual_regions = [
            code.strip().upper()
            for code in self.regions_var.get().split(",")
            if code.strip()
        ]

        if not origin or not destination or not date:
            messagebox.showerror("参数不完整", "请填写出发地、目的地和出发日期。")
            return

        try:
            departure_value = datetime.strptime(date, "%Y-%m-%d")
        except ValueError:
            messagebox.showerror("日期格式错误", "出发日期必须是 YYYY-MM-DD。")
            return
        if return_date:
            try:
                return_value = datetime.strptime(return_date, "%Y-%m-%d")
            except ValueError:
                messagebox.showerror("日期格式错误", "返程日期必须是 YYYY-MM-DD。")
                return
            if return_value < departure_value:
                messagebox.showerror("日期错误", "返程日期不能早于出发日期。")
                return

        try:
            wait_seconds = int(self.wait_var.get() or "10")
        except ValueError:
            messagebox.showerror("等待秒数错误", "等待秒数必须是整数。")
            return

        try:
            date_window_days = int(self.date_window_var.get() or "0")
        except ValueError:
            messagebox.showerror("±天数错误", "±天数必须是非负整数。")
            return
        if date_window_days < 0:
            messagebox.showerror("±天数错误", "±天数必须是非负整数。")
            return

        if self.origin_country_var.get() or self.destination_country_var.get():
            self._start_expanded_scan(
                origin=origin,
                destination=destination,
                date=date,
                return_date=return_date,
                manual_regions=manual_regions,
                wait_seconds=wait_seconds,
                date_window_days=date_window_days,
                rerun_scope_override=rerun_scope_override,
                selected_region_codes=selected_region_codes,
                allow_browser_fallback=allow_browser_fallback,
            )
            return

        try:
            origin_resolved = self.cli.resolve_location(
                origin, prefer_metro=not self.exact_airport_var.get()
            )
            destination_resolved = self.cli.resolve_location(
                destination, prefer_metro=False
            )
        except ValueError as exc:
            messagebox.showerror("地点无法识别", str(exc))
            return

        regions = build_effective_region_codes(
            origin_country=origin_resolved.country,
            destination_country=destination_resolved.country,
            manual_region_codes=manual_regions,
        )
        if not regions:
            messagebox.showerror("地区为空", "无法生成可用地区代码。")
            return

        query_payload = self.cli.build_point_query_payload(
            origin_input=origin,
            destination_input=destination,
            origin_label=origin_resolved.query or origin_resolved.name or origin_resolved.code,
            destination_label=destination_resolved.query
            or destination_resolved.name
            or destination_resolved.code,
            origin_code=origin_resolved.code,
            destination_code=destination_resolved.code,
            date=date,
            return_date=return_date,
            date_window_days=date_window_days,
            manual_regions=manual_regions,
            effective_regions=regions,
            exact_airport=bool(self.exact_airport_var.get()),
        )
        self._current_query_payload = query_payload
        self._history_records_for_current_query = self.history_store.get_query_history(
            query_payload,
            limit=10,
        )
        previous_record = self.history_store.get_latest_scan(query_payload)
        self._previous_scan_record = previous_record
        if rerun_scope_override == "failed_only" and previous_record is None:
            self.log("未找到历史记录，当前改为全量扫描。")
            rerun_scope_override = "all"
        if previous_record is not None:
            regions = prioritize_region_codes(regions, previous_record.rows_by_date)

        self.clear_results()
        self._cancel_event.clear()
        self.set_busy(True)
        preview_record = self.history_store.get_cached_preview(query_payload)
        if preview_record is not None:
            self._set_display_rows_from_grouped(preview_record.rows_by_date)
            self.status_var.set("已显示预览缓存，正在刷新实时结果...")
            self.log("已先展示最近 6 小时内的预览缓存，后台继续刷新实时结果。")
        else:
            self.status_var.set("正在运行...")
        trip_label = format_trip_date_label(date, return_date)
        trip_mode_label = "往返" if return_date else "单程"
        self.log(
            f"开始比价: {origin} -> {destination}, {trip_mode_label} {trip_label} "
            f"(±{date_window_days} 天), "
            f"地区: {', '.join(regions)} "
            f"(实际代码 {origin_resolved.code} -> {destination_resolved.code})"
        )
        self._apply_cheapest_conclusion(
            {
                "headline": "正在寻找最低价…",
                "price": "扫描进行中",
                "supporting": f"{origin} -> {destination} · {trip_label}",
                "meta": f"正在比较 {len(regions)} 个市场。",
                "insight": "扫描完成后，这里会汇总最便宜的市场、日期、航段和价差。",
                "link": None,
                "button_text": "等待结果",
            }
        )
        self._persist_query_state()

        thread = threading.Thread(
            target=self._run_scan_worker,
            args=(
                origin_resolved.code,
                destination_resolved.code,
                date,
                return_date,
                regions,
                wait_seconds,
                date_window_days,
                self.combined_summary_var.get(),
                query_payload,
                rerun_scope_override,
                selected_region_codes or [],
                allow_browser_fallback,
            ),
            daemon=True,
        )
        thread.start()

    def _start_expanded_scan(
        self,
        *,
        origin: str,
        destination: str,
        date: str,
        return_date: str | None,
        manual_regions: list[str],
        wait_seconds: int,
        date_window_days: int,
        rerun_scope_override: str = "all",
        selected_region_codes: list[str] | None = None,
        allow_browser_fallback: bool = True,
    ) -> None:
        try:
            (
                origin_label,
                destination_label,
                origin_file_token,
                destination_file_token,
                origin_points,
                destination_points,
                regions,
            ) = self.cli.build_expanded_route_plan(
                origin_value=origin,
                destination_value=destination,
                origin_is_country=self.origin_country_var.get(),
                destination_is_country=self.destination_country_var.get(),
                prefer_origin_metro=not self.exact_airport_var.get(),
                manual_region_codes=manual_regions,
                airport_limit=COUNTRY_ROUTE_DEFAULT_AIRPORT_LIMIT,
            )
        except ValueError as exc:
            messagebox.showerror("地点无法识别", str(exc))
            return

        if not regions:
            messagebox.showerror("地区为空", "无法生成可用地区代码。")
            return

        query_payload = self.cli.build_expanded_query_payload(
            origin_value=origin,
            destination_value=destination,
            origin_label=origin_label,
            destination_label=destination_label,
            origin_file_token=origin_file_token,
            destination_file_token=destination_file_token,
            date=date,
            return_date=return_date,
            date_window_days=date_window_days,
            manual_regions=manual_regions,
            effective_regions=regions,
            exact_airport=bool(self.exact_airport_var.get()),
            origin_is_country=bool(self.origin_country_var.get()),
            destination_is_country=bool(self.destination_country_var.get()),
            airport_limit=COUNTRY_ROUTE_DEFAULT_AIRPORT_LIMIT,
        )
        self._current_query_payload = query_payload
        self._history_records_for_current_query = self.history_store.get_query_history(
            query_payload,
            limit=10,
        )
        previous_record = self.history_store.get_latest_scan(query_payload)
        self._previous_scan_record = previous_record
        if rerun_scope_override == "failed_only" and previous_record is None:
            self.log("未找到历史记录，当前改为全量扫描。")
            rerun_scope_override = "all"
        if previous_record is not None:
            regions = prioritize_region_codes(regions, previous_record.rows_by_date)

        self.clear_results()
        self._cancel_event.clear()
        self.set_busy(True)
        preview_record = self.history_store.get_cached_preview(query_payload)
        if preview_record is not None:
            self._set_display_rows_from_grouped(preview_record.rows_by_date)
            self.status_var.set("已显示预览缓存，正在刷新实时结果...")
            self.log("已先展示最近 6 小时内的预览缓存，后台继续刷新实时结果。")
        else:
            self.status_var.set("正在运行...")
        trip_label = format_trip_date_label(date, return_date)
        trip_mode_label = "往返" if return_date else "单程"
        mode_label = (
            f"{'国家' if self.origin_country_var.get() else '地点'}"
            f"-{'国家' if self.destination_country_var.get() else '地点'}"
        )
        self.log(
            f"开始扩展比价[{mode_label}]: {origin_label} -> {destination_label}, "
            f"{trip_mode_label} {trip_label} (±{date_window_days} 天), "
            f"地区: {', '.join(regions)}"
        )
        self._apply_cheapest_conclusion(
            {
                "headline": "正在寻找最低价…",
                "price": "扫描进行中",
                "supporting": f"{origin_label} -> {destination_label} · {trip_label}",
                "meta": f"正在比较 {len(regions)} 个市场与候选机场组合。",
                "insight": "扫描完成后，这里会给出当前最值得优先打开的最低价结果页。",
                "link": None,
                "button_text": "等待结果",
            }
        )
        self._persist_query_state()
        self.log(
            "出发候选机场: "
            + ", ".join(
                f"{airport.code}({airport.municipality or airport.name})"
                for airport in origin_points
            )
        )
        self.log(
            "目的候选机场: "
            + ", ".join(
                f"{airport.code}({airport.municipality or airport.name})"
                for airport in destination_points
            )
        )

        thread = threading.Thread(
            target=self._run_expanded_scan_worker,
            args=(
                origin_label,
                destination_label,
                origin_file_token,
                destination_file_token,
                origin_points,
                destination_points,
                date,
                return_date,
                regions,
                wait_seconds,
                date_window_days,
                self.combined_summary_var.get(),
                query_payload,
                rerun_scope_override,
                selected_region_codes or [],
                allow_browser_fallback,
            ),
            daemon=True,
        )
        thread.start()

    def _run_scan_worker(
        self,
        origin_code: str,
        destination_code: str,
        date: str,
        return_date: str | None,
        regions: list[str],
        wait_seconds: int,
        date_window_days: int,
        save_combined: bool,
        query_payload: dict[str, Any],
        rerun_scope: str,
        selected_region_codes: list[str],
        allow_browser_fallback: bool,
    ) -> None:
        try:
            if return_date:
                trip_dates = build_round_trip_date_window(
                    date, return_date, date_window_days
                )
            else:
                trip_dates = [
                    (current_date, None)
                    for current_date in build_date_window(date, date_window_days)
                ]
            latest_record = self.history_store.get_latest_scan(query_payload)
            normalized_selected_codes = {
                code.strip().upper() for code in selected_region_codes if code.strip()
            }
            trip_labels = [
                format_trip_date_label(current_date, current_return_date)
                for current_date, current_return_date in trip_dates
            ]

            def _region_count_for_trip(trip_label: str) -> int:
                if rerun_scope == "failed_only":
                    return len(
                        get_failed_region_codes(
                            latest_record.quotes_by_date if latest_record else None,
                            trip_label=trip_label,
                        )
                    )
                if rerun_scope == "selected_regions":
                    return len(normalized_selected_codes)
                return len(regions)

            total_steps = sum(_region_count_for_trip(trip_label) for trip_label in trip_labels)
            total_steps = max(total_steps, 1)
            step = 0
            rows_progress: list[tuple[str, list[dict[str, str | float | None]]]] = []
            quote_progress: list[tuple[str, list[dict[str, Any]]]] = []

            self.queue.put(("progress_init", total_steps))
            async def orchestrate() -> tuple[
                list[tuple[str, list[dict[str, str | float | None]]]],
                list[tuple[str, list[dict[str, Any]]]],
                list[Path],
            ]:
                nonlocal step, rows_progress, quote_progress
                progress_lock = asyncio.Lock()
                date_semaphore = asyncio.Semaphore(_GUI_DATE_WINDOW_CONCURRENCY)

                async def update_partial_view(
                    trip_label: str,
                    rows: list[dict[str, str | float | None]],
                    quotes: list[dict[str, Any]],
                    *,
                    status: str,
                    log_message: str,
                ) -> None:
                    nonlocal rows_progress, quote_progress
                    async with progress_lock:
                        rows_progress = _order_grouped_by_trip_labels(
                            trip_labels,
                            _upsert_rows_by_date(rows_progress, trip_label, rows),
                        )
                        quote_progress = _order_grouped_by_trip_labels(
                            trip_labels,
                            _upsert_quotes_by_date(quote_progress, trip_label, quotes),
                        )
                        self.queue.put(
                            (
                                "scan_partial",
                                {
                                    "rows_by_date": list(rows_progress),
                                    "status": status,
                                    "log": log_message,
                                },
                            )
                        )

                async def scan_trip(
                    date_idx: int,
                    current_date: str,
                    current_return_date: str | None,
                ) -> tuple[int, str, list[dict[str, str | float | None]], list[dict[str, Any]], Path | None]:
                    async with date_semaphore:
                        if self._cancel_event.is_set():
                            raise asyncio.CancelledError
                        trip_label = format_trip_date_label(current_date, current_return_date)

                        def on_region_start(
                            region: Any,
                            _trip_label: str = trip_label,
                        ) -> None:
                            nonlocal step
                            step += 1
                            self.queue.put(
                                (
                                    "progress",
                                    {
                                        "step": step,
                                        "total": total_steps,
                                        "date": _trip_label,
                                        "region_name": region.name,
                                    },
                                )
                            )

                        self.queue.put(("log", f"开始扫描行程 {trip_label}。"))
                        current_scope = rerun_scope
                        current_selected_codes = set(normalized_selected_codes)
                        if rerun_scope == "failed_only":
                            current_selected_codes = {
                                code.upper()
                                for code in get_failed_region_codes(
                                    latest_record.quotes_by_date if latest_record else None,
                                    trip_label=trip_label,
                                )
                            }
                            current_scope = "selected_regions"

                        cached_rows_by_date = override_rows_source_kind(
                            [(trip_label, get_rows_for_trip_label(latest_record.rows_by_date, trip_label))]
                            if latest_record is not None
                            else [],
                            "cached",
                            updated_at=getattr(latest_record, "created_at", None),
                        )
                        cached_quotes_by_date = override_quotes_source_kind(
                            [(trip_label, get_quotes_for_trip_label(latest_record.quotes_by_date, trip_label))]
                            if latest_record is not None
                            else [],
                            "cached",
                        )

                        if current_scope == "selected_regions" and not current_selected_codes:
                            cached_rows = get_rows_for_trip_label(cached_rows_by_date, trip_label)
                            cached_quotes = get_quotes_for_trip_label(cached_quotes_by_date, trip_label)
                            await update_partial_view(
                                trip_label,
                                cached_rows,
                                cached_quotes,
                                status=f"{trip_label} 无需重扫，已复用历史结果。",
                                log_message=f"{trip_label} 没有失败市场，直接复用缓存结果。",
                            )
                            output = None
                            if cached_rows:
                                output = self.cli.save_simplified_results(
                                    cached_rows,
                                    origin_code,
                                    destination_code,
                                    current_date,
                                    return_date=current_return_date,
                                )
                            return (date_idx, trip_label, cached_rows, cached_quotes, output)

                        merged_rows_by_date = cached_rows_by_date
                        merged_quotes_by_date = cached_quotes_by_date

                        async def on_progress(progress_payload: dict[str, Any]) -> None:
                            nonlocal merged_rows_by_date, merged_quotes_by_date
                            if self._cancel_event.is_set():
                                raise asyncio.CancelledError
                            stage = str(progress_payload.get("stage") or "").strip().lower()
                            quote_dicts = [
                                quote
                                for quote in (progress_payload.get("quotes") or [])
                                if isinstance(quote, dict)
                            ]
                            if not quote_dicts:
                                return
                            live_rows_by_date = annotate_rows_with_history(
                                [
                                    (
                                        trip_label,
                                        self.cli.simplify_quotes(
                                            quote_dicts,
                                            route_label=f"{origin_code} -> {destination_code}",
                                        ),
                                    )
                                ],
                                latest_record.rows_by_date if latest_record else None,
                            )
                            merged_rows_by_date = merge_rows_by_date(
                                cached_rows_by_date,
                                live_rows_by_date,
                            )
                            merged_quotes_by_date = merge_quotes_by_date(
                                cached_quotes_by_date,
                                [(trip_label, quote_dicts)],
                            )
                            partial_rows = get_rows_for_trip_label(merged_rows_by_date, trip_label)
                            partial_quotes = get_quotes_for_trip_label(merged_quotes_by_date, trip_label)
                            status_map = {
                                "preview_cache": f"{trip_label} 预览缓存已展示。",
                                "quick_live": f"{trip_label} 已返回高优先级市场结果，正在补全其余市场...",
                                "background_live": f"{trip_label} 正在后台补全其余市场...",
                                "final": f"{trip_label} 已完成，继续处理其余日期...",
                            }
                            log_map = {
                                "preview_cache": f"{trip_label} 已展示预览缓存。",
                                "quick_live": f"{trip_label} 已先刷新高优先级市场的实时结果。",
                                "background_live": f"{trip_label} 已补充更多市场的实时结果。",
                                "final": f"{trip_label} 已完成实时刷新。",
                            }
                            await update_partial_view(
                                trip_label,
                                partial_rows,
                                partial_quotes,
                                status=status_map.get(stage, f"{trip_label} 正在刷新结果..."),
                                log_message=log_map.get(stage, f"{trip_label} 正在刷新结果。"),
                            )

                        await run_page_scan(
                            origin=origin_code,
                            destination=destination_code,
                            date=current_date,
                            region_codes=regions,
                            return_date=current_return_date,
                            page_wait=wait_seconds,
                            timeout=30,
                            transport="scrapling",
                            on_region_start=on_region_start,
                            scan_mode="preview_first",
                            rerun_scope=current_scope,
                            selected_region_codes=sorted(current_selected_codes),
                            region_concurrency=_GUI_REGION_CONCURRENCY,
                            query_payload=query_payload,
                            on_progress=on_progress,
                            allow_browser_fallback=allow_browser_fallback,
                        )

                        rows = get_rows_for_trip_label(merged_rows_by_date, trip_label)
                        quote_snapshots = get_quotes_for_trip_label(merged_quotes_by_date, trip_label)
                        if not rows:
                            self.queue.put(
                                ("log", f"行程 {trip_label} 未返回结果，请检查地区或环境。")
                            )
                            await update_partial_view(
                                trip_label,
                                [],
                                [],
                                status=f"{trip_label} 未返回结果。",
                                log_message=f"{trip_label} 未拿到可展示结果。",
                            )
                            return (date_idx, trip_label, [], [], None)

                        output = self.cli.save_simplified_results(
                            rows,
                            origin_code,
                            destination_code,
                            current_date,
                            return_date=current_return_date,
                        )
                        await update_partial_view(
                            trip_label,
                            rows,
                            quote_snapshots,
                            status=f"{trip_label} 已完成，继续处理其余日期...",
                            log_message=f"{trip_label} 已完成实时刷新。",
                        )
                        return (date_idx, trip_label, rows, quote_snapshots, output)

                tasks = [
                    asyncio.create_task(scan_trip(index, current_date, current_return_date))
                    for index, (current_date, current_return_date) in enumerate(trip_dates)
                ]
                collected: list[
                    tuple[int, str, list[dict[str, str | float | None]], list[dict[str, Any]], Path | None]
                ] = []
                try:
                    for task in asyncio.as_completed(tasks):
                        if self._cancel_event.is_set():
                            raise asyncio.CancelledError
                        collected.append(await task)
                except asyncio.CancelledError:
                    for task in tasks:
                        task.cancel()
                    await asyncio.gather(*tasks, return_exceptions=True)
                    raise

                collected.sort(key=lambda item: item[0])
                final_rows = [(trip_label, rows) for _, trip_label, rows, _, _ in collected]
                final_quotes = [(trip_label, quotes) for _, trip_label, _, quotes, _ in collected]
                final_outputs = [output for _, _, rows, _, output in collected if output is not None and rows]
                return (
                    _order_grouped_by_trip_labels(trip_labels, final_rows),
                    _order_grouped_by_trip_labels(trip_labels, final_quotes),
                    final_outputs,
                )

            rows_by_date, quote_snapshots_by_date, outputs = asyncio.run(orchestrate())

            combined_output = None
            if rows_by_date:
                self.history_store.record_scan(
                    query_payload,
                    rows_by_date,
                    quote_snapshots_by_date,
                    scan_mode=(
                        "failed_only"
                        if rerun_scope == "failed_only"
                        else ("selected_regions" if rerun_scope == "selected_regions" else "preview_first")
                    ),
                )
            if save_combined and rows_by_date:
                start_date, start_return_date = trip_dates[0]
                end_date, end_return_date = trip_dates[-1]
                combined_output = self.cli.save_window_results(
                    rows_by_date,
                    origin_code,
                    destination_code,
                    start_date,
                    end_date,
                    start_return_date=start_return_date,
                    end_return_date=end_return_date,
                )
            self.queue.put(
                (
                    "scan_done",
                    {
                        "rows_by_date": rows_by_date,
                        "outputs": outputs,
                        "combined_output": combined_output,
                        "origin_code": origin_code,
                        "date_window_days": date_window_days,
                    },
                )
            )
            self.queue.put(("refresh_history", None))
        except asyncio.CancelledError:
            self.queue.put(("cancelled", None))
        except Exception as exc:
            self.queue.put(("error", str(exc)))

    def _run_expanded_scan_worker(
        self,
        origin_label: str,
        destination_label: str,
        origin_file_token: str,
        destination_file_token: str,
        origin_points: list[LocationRecord],
        destination_points: list[LocationRecord],
        date: str,
        return_date: str | None,
        regions: list[str],
        wait_seconds: int,
        date_window_days: int,
        save_combined: bool,
        query_payload: dict[str, Any],
        rerun_scope: str,
        selected_region_codes: list[str],
        allow_browser_fallback: bool,
    ) -> None:
        try:
            if return_date:
                trip_dates = build_round_trip_date_window(
                    date, return_date, date_window_days
                )
            else:
                trip_dates = [
                    (current_date, None)
                    for current_date in build_date_window(date, date_window_days)
                ]
            latest_record = self.history_store.get_latest_scan(query_payload)
            normalized_selected_codes = {
                code.strip().upper() for code in selected_region_codes if code.strip()
            }
            trip_labels = [
                format_trip_date_label(current_date, current_return_date)
                for current_date, current_return_date in trip_dates
            ]
            pair_specs = [
                (origin_airport, destination_airport)
                for origin_airport in origin_points
                for destination_airport in destination_points
            ]
            pair_count = len(pair_specs)

            def _region_count_for_trip(trip_label: str) -> int:
                if rerun_scope == "failed_only":
                    return len(
                        get_failed_region_codes(
                            latest_record.quotes_by_date if latest_record else None,
                            trip_label=trip_label,
                        )
                    )
                if rerun_scope == "selected_regions":
                    return len(normalized_selected_codes)
                return len(regions)

            total_steps = sum(_region_count_for_trip(trip_label) * pair_count for trip_label in trip_labels)
            total_steps = max(total_steps, 1)
            step = 0
            rows_progress: list[tuple[str, list[dict[str, str | float | None]]]] = []
            quote_progress: list[tuple[str, list[dict[str, Any]]]] = []

            self.queue.put(("progress_init", total_steps))
            async def orchestrate() -> tuple[
                list[tuple[str, list[dict[str, str | float | None]]]],
                list[tuple[str, list[dict[str, Any]]]],
                list[Path],
            ]:
                nonlocal step, rows_progress, quote_progress
                progress_lock = asyncio.Lock()
                date_semaphore = asyncio.Semaphore(_GUI_DATE_WINDOW_CONCURRENCY)

                async def update_partial_view(
                    trip_label: str,
                    rows: list[dict[str, str | float | None]],
                    quotes: list[dict[str, Any]],
                    *,
                    status: str,
                    log_message: str,
                ) -> None:
                    nonlocal rows_progress, quote_progress
                    async with progress_lock:
                        rows_progress = _order_grouped_by_trip_labels(
                            trip_labels,
                            _upsert_rows_by_date(rows_progress, trip_label, rows),
                        )
                        quote_progress = _order_grouped_by_trip_labels(
                            trip_labels,
                            _upsert_quotes_by_date(quote_progress, trip_label, quotes),
                        )
                        self.queue.put(
                            (
                                "scan_partial",
                                {
                                    "rows_by_date": list(rows_progress),
                                    "status": status,
                                    "log": log_message,
                                },
                            )
                        )

                async def scan_trip(
                    date_idx: int,
                    current_date: str,
                    current_return_date: str | None,
                ) -> tuple[int, str, list[dict[str, str | float | None]], list[dict[str, Any]], Path | None]:
                    async with date_semaphore:
                        if self._cancel_event.is_set():
                            raise asyncio.CancelledError
                        trip_label = format_trip_date_label(current_date, current_return_date)
                        self.queue.put(("log", f"开始扫描行程 {trip_label}。"))
                        best_rows_by_region: dict[str, CombinedQuoteRow] = {}
                        current_scope = rerun_scope
                        current_selected_codes = set(normalized_selected_codes)

                        if rerun_scope == "failed_only":
                            current_selected_codes = {
                                code.upper()
                                for code in get_failed_region_codes(
                                    latest_record.quotes_by_date if latest_record else None,
                                    trip_label=trip_label,
                                )
                            }
                            current_scope = "selected_regions"

                        cached_rows_by_date = override_rows_source_kind(
                            [(trip_label, get_rows_for_trip_label(latest_record.rows_by_date, trip_label))]
                            if latest_record is not None
                            else [],
                            "cached",
                            updated_at=getattr(latest_record, "created_at", None),
                        )
                        cached_quotes_by_date = override_quotes_source_kind(
                            [(trip_label, get_quotes_for_trip_label(latest_record.quotes_by_date, trip_label))]
                            if latest_record is not None
                            else [],
                            "cached",
                        )

                        if current_scope == "selected_regions" and not current_selected_codes:
                            cached_rows = get_rows_for_trip_label(cached_rows_by_date, trip_label)
                            cached_quotes = get_quotes_for_trip_label(cached_quotes_by_date, trip_label)
                            await update_partial_view(
                                trip_label,
                                cached_rows,
                                cached_quotes,
                                status=f"{trip_label} 无需重扫，已复用历史结果。",
                                log_message=f"{trip_label} 没有失败市场，直接复用缓存结果。",
                            )
                            output = self.cli.save_simplified_results(
                                cached_rows,
                                origin_label,
                                destination_label,
                                current_date,
                                return_date=current_return_date,
                                file_origin_token=origin_file_token,
                                file_destination_token=destination_file_token,
                            )
                            return (date_idx, trip_label, cached_rows, cached_quotes, output)

                        def build_rows_snapshot() -> tuple[
                            list[dict[str, str | float | None]],
                            list[dict[str, Any]],
                        ]:
                            live_rows_by_date = annotate_rows_with_history(
                                [
                                    (
                                        trip_label,
                                        self.cli._sort_simplified_rows(
                                            list(best_rows_by_region.values())
                                        ),
                                    )
                                ],
                                latest_record.rows_by_date if latest_record else None,
                            )
                            merged_rows_by_date = merge_rows_by_date(
                                cached_rows_by_date,
                                live_rows_by_date,
                            )
                            rows = get_rows_for_trip_label(merged_rows_by_date, trip_label)
                            return (rows, self.cli.rows_to_quote_snapshots(rows))

                        pair_semaphore = asyncio.Semaphore(_GUI_AIRPORT_PAIR_CONCURRENCY)

                        async def scan_pair(
                            origin_airport: LocationRecord,
                            destination_airport: LocationRecord,
                        ) -> tuple[str, list[SimplifiedQuoteRow]]:
                            async with pair_semaphore:
                                if self._cancel_event.is_set():
                                    raise asyncio.CancelledError
                                route_label = f"{origin_airport.code} -> {destination_airport.code}"

                                def on_region_start(
                                    region: Any,
                                    _trip_label: str = trip_label,
                                    _route_label: str = route_label,
                                ) -> None:
                                    nonlocal step
                                    step += 1
                                    self.queue.put(
                                        (
                                            "progress",
                                            {
                                                "step": step,
                                                "total": total_steps,
                                                "date": _trip_label,
                                                "region_name": f"{region.name} / {_route_label}",
                                            },
                                        )
                                    )

                                quotes = await run_page_scan(
                                    origin=origin_airport.code,
                                    destination=destination_airport.code,
                                    date=current_date,
                                    region_codes=regions,
                                    return_date=current_return_date,
                                    page_wait=wait_seconds,
                                    timeout=30,
                                    transport="scrapling",
                                    on_region_start=on_region_start,
                                    scan_mode="preview_first",
                                    rerun_scope=current_scope,
                                    selected_region_codes=sorted(current_selected_codes),
                                    region_concurrency=_GUI_REGION_CONCURRENCY,
                                    query_payload=query_payload,
                                    allow_browser_fallback=allow_browser_fallback,
                                )
                                if not quotes:
                                    return (route_label, [])
                                return (
                                    route_label,
                                    self.cli.simplify_quotes(
                                        quotes_to_dicts(quotes),
                                        route_label=route_label,
                                    ),
                                )

                        pair_tasks = [
                            asyncio.create_task(scan_pair(origin_airport, destination_airport))
                            for origin_airport, destination_airport in pair_specs
                        ]
                        completed_pairs = 0
                        try:
                            for pair_task in asyncio.as_completed(pair_tasks):
                                if self._cancel_event.is_set():
                                    raise asyncio.CancelledError
                                _route_label, rows = await pair_task
                                completed_pairs += 1
                                for row in rows:
                                    region_name = str(row.get("region_name") or "-")
                                    best_rows_by_region[region_name] = self.cli._pick_better_row(
                                        best_rows_by_region.get(region_name),
                                        row,
                                    )
                                should_emit_partial = (
                                    completed_pairs == 1
                                    or completed_pairs == pair_count
                                    or completed_pairs % _GUI_AIRPORT_PAIR_CONCURRENCY == 0
                                )
                                if should_emit_partial:
                                    partial_rows, partial_quotes = build_rows_snapshot()
                                    await update_partial_view(
                                        trip_label,
                                        partial_rows,
                                        partial_quotes,
                                        status=(
                                            f"{trip_label} 已完成 {completed_pairs}/{pair_count} 个候选航段，"
                                            "正在更新当前最优结果..."
                                        ),
                                        log_message=(
                                            f"{trip_label} 已刷新 {completed_pairs}/{pair_count} 个候选航段。"
                                        ),
                                    )
                        except asyncio.CancelledError:
                            for pair_task in pair_tasks:
                                pair_task.cancel()
                            await asyncio.gather(*pair_tasks, return_exceptions=True)
                            raise

                        rows, quote_snapshots = build_rows_snapshot()
                        if not rows:
                            self.queue.put(
                                ("log", f"行程 {trip_label} 未拿到可展示的扩展路线结果。")
                            )
                            return (date_idx, trip_label, [], [], None)

                        output = self.cli.save_simplified_results(
                            rows,
                            origin_label,
                            destination_label,
                            current_date,
                            return_date=current_return_date,
                            file_origin_token=origin_file_token,
                            file_destination_token=destination_file_token,
                        )
                        await update_partial_view(
                            trip_label,
                            rows,
                            quote_snapshots,
                            status=f"{trip_label} 已完成，继续处理其余日期...",
                            log_message=f"{trip_label} 已完成扩展路线实时刷新。",
                        )
                        return (date_idx, trip_label, rows, quote_snapshots, output)

                tasks = [
                    asyncio.create_task(scan_trip(index, current_date, current_return_date))
                    for index, (current_date, current_return_date) in enumerate(trip_dates)
                ]
                collected: list[
                    tuple[int, str, list[dict[str, str | float | None]], list[dict[str, Any]], Path | None]
                ] = []
                try:
                    for task in asyncio.as_completed(tasks):
                        if self._cancel_event.is_set():
                            raise asyncio.CancelledError
                        collected.append(await task)
                except asyncio.CancelledError:
                    for task in tasks:
                        task.cancel()
                    await asyncio.gather(*tasks, return_exceptions=True)
                    raise

                collected.sort(key=lambda item: item[0])
                final_rows = [(trip_label, rows) for _, trip_label, rows, _, _ in collected]
                final_quotes = [(trip_label, quotes) for _, trip_label, _, quotes, _ in collected]
                final_outputs = [output for _, _, rows, _, output in collected if output is not None and rows]
                return (
                    _order_grouped_by_trip_labels(trip_labels, final_rows),
                    _order_grouped_by_trip_labels(trip_labels, final_quotes),
                    final_outputs,
                )

            rows_by_date, quote_snapshots_by_date, outputs = asyncio.run(orchestrate())

            combined_output = None
            if rows_by_date:
                self.history_store.record_scan(
                    query_payload,
                    rows_by_date,
                    quote_snapshots_by_date,
                    scan_mode=(
                        "failed_only"
                        if rerun_scope == "failed_only"
                        else ("selected_regions" if rerun_scope == "selected_regions" else "preview_first")
                    ),
                )
            if save_combined and rows_by_date:
                start_date, start_return_date = trip_dates[0]
                end_date, end_return_date = trip_dates[-1]
                combined_output = self.cli.save_window_results(
                    rows_by_date,
                    origin_label,
                    destination_label,
                    start_date,
                    end_date,
                    start_return_date=start_return_date,
                    end_return_date=end_return_date,
                    file_origin_token=origin_file_token,
                    file_destination_token=destination_file_token,
                )
            self.queue.put(
                (
                    "scan_done",
                    {
                        "rows_by_date": rows_by_date,
                        "outputs": outputs,
                        "combined_output": combined_output,
                        "origin_code": origin_file_token,
                        "date_window_days": date_window_days,
                    },
                )
            )
            self.queue.put(("refresh_history", None))
        except asyncio.CancelledError:
            self.queue.put(("cancelled", None))
        except Exception as exc:
            self.queue.put(("error", str(exc)))

    def _poll_queue(self) -> None:
        try:
            while True:
                kind, payload = self.queue.get_nowait()
                if kind == "scan_done":
                    self._handle_scan_done(payload)
                elif kind == "scan_partial":
                    self._handle_scan_partial(payload)
                elif kind == "error":
                    self._handle_error(str(payload))
                elif kind == "log":
                    self.log(str(payload))
                elif kind == "status":
                    self.status_var.set(str(payload))
                elif kind == "progress_init":
                    self.progress_bar["maximum"] = int(payload)
                    self.progress_bar["value"] = 0
                elif kind == "progress":
                    p = payload
                    self.progress_bar["value"] = p["step"]
                    self.status_var.set(
                        f"正在扫描 {p['date']} [{p['region_name']}] ({p['step']}/{p['total']})"
                    )
                elif kind == "refresh_history":
                    self._refresh_history_lists()
                elif kind == "cancelled":
                    self.set_busy(False)
                    self.status_var.set("已取消")
                    self._apply_cheapest_conclusion(
                        {
                            "headline": "扫描已取消",
                            "price": "未生成新的最低价结论",
                            "supporting": "你可以调整条件后重新开始。",
                            "meta": "",
                            "insight": "下次启动时会自动保留这次填写的查询条件。",
                            "link": None,
                            "button_text": "等待结果",
                        }
                    )
                    self.log("扫描已被用户取消。")
        except queue.Empty:
            pass
        self.root.after(200, self._poll_queue)

    def _handle_scan_done(self, payload: dict[str, Any]) -> None:
        self.set_busy(False)
        outputs: list[Path] = payload.get("outputs") or []
        self.current_output = payload.get("combined_output") or (
            outputs[-1] if outputs else None
        )
        self.status_var.set("完成")
        if self._current_query_payload is not None:
            self._history_records_for_current_query = self.history_store.get_query_history(
                self._current_query_payload,
                limit=10,
            )
        rows_by_date: list[tuple[str, list[dict[str, str | float | None]]]] = payload[
            "rows_by_date"
        ]
        self._set_display_rows_from_grouped(rows_by_date)
        combined_rows = list(self._display_rows)

        best_candidates = [
            row
            for row in combined_rows
            if isinstance(row.get("best_cny_price"), (int, float))
        ]
        cheapest_candidates = [
            row
            for row in combined_rows
            if isinstance(row.get("cheapest_cny_price"), (int, float))
        ]

        def _price_value(row: CombinedQuoteRow, key: str) -> float:
            value = row.get(key)
            return float(value) if isinstance(value, (int, float)) else float("inf")

        best_winner = (
            min(best_candidates, key=lambda row: _price_value(row, "best_cny_price"))
            if best_candidates
            else None
        )
        cheapest_winner = (
            min(
                cheapest_candidates,
                key=lambda row: _price_value(row, "cheapest_cny_price"),
            )
            if cheapest_candidates
            else None
        )
        if best_winner:
            best_price = best_winner.get("best_cny_price")
            if isinstance(best_price, (int, float)):
                self.log(
                    "最佳: ¥{price:,.2f} 来自 {region} ({date}, {route})".format(
                        price=float(best_price),
                        region=best_winner["region_name"],
                        date=best_winner.get("date") or "-",
                        route=best_winner.get("route") or "-",
                    )
                )
        if cheapest_winner:
            cheapest_price = cheapest_winner.get("cheapest_cny_price")
            if isinstance(cheapest_price, (int, float)):
                self.log(
                    "最低价: ¥{price:,.2f} 来自 {region} ({date}, {route})".format(
                        price=float(cheapest_price),
                        region=cheapest_winner["region_name"],
                        date=cheapest_winner.get("date") or "-",
                        route=cheapest_winner.get("route") or "-",
                    )
                )
        elif combined_rows:
            self.log("已提取市场价格，但人民币换算暂不可用。")
        else:
            self.log("没有可展示的市场结果。")
        if outputs:
            self.log(f"单日结果已保存: {len(outputs)} 份。")
        combined_output = payload.get("combined_output")
        if combined_output:
            self.log(f"汇总结果已保存: {combined_output}")
        if self.current_output:
            self.log(f"最新结果文件: {self.current_output}")

    def _handle_scan_partial(self, payload: dict[str, Any]) -> None:
        rows_by_date: list[tuple[str, list[dict[str, str | float | None]]]] = (
            payload.get("rows_by_date") or []
        )
        if rows_by_date:
            self._set_display_rows_from_grouped(rows_by_date)
        status_text = str(payload.get("status") or "").strip()
        if status_text:
            self.status_var.set(status_text)
        log_text = str(payload.get("log") or "").strip()
        if log_text:
            self.log(log_text)

    def _handle_error(self, message: str) -> None:
        self.set_busy(False)
        self.status_var.set("失败")
        self._apply_cheapest_conclusion(
            {
                "headline": "本次比价失败",
                "price": "未生成最低价结论",
                "supporting": "请调整条件或检查抓取环境后重试。",
                "meta": "",
                "insight": message,
                "link": None,
                "button_text": "暂无可打开页面",
            }
        )
        self.log(f"失败: {message}")
        messagebox.showerror("运行失败", message)

    def _export_decision_summary(self) -> None:
        recommendations = _build_top_recommendations(
            self._display_rows,
            mode=self.price_mode_var.get(),
            limit=3,
        )
        if not recommendations:
            messagebox.showinfo("暂无结果", "当前没有可导出的决策摘要。")
            return
        export_dir = get_reports_dir()
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        markdown_path = export_dir / f"decision_summary_{stamp}.md"
        csv_path = export_dir / f"decision_summary_{stamp}.csv"
        lines = [
            "# 决策摘要",
            "",
            f"- 生成时间: `{datetime.now().isoformat(timespec='seconds')}`",
            "",
            "| 排名 | 日期 | 航段 | 地区 | 最低价 | 稳定性 | 可信度 | 来源 | 链接 |",
            "| --- | --- | --- | --- | ---: | --- | --- | --- | --- |",
        ]
        with csv_path.open("w", encoding="utf-8", newline="") as fh:
            writer = csv.writer(fh)
            writer.writerow(["rank", "date", "route", "region", "cheapest_cny", "stability", "reliability", "source", "link"])
            for index, row in enumerate(recommendations, start=1):
                price = row.get("cheapest_cny_price")
                price_text = f"¥{float(price):,.2f}" if isinstance(price, (int, float)) else "-"
                lines.append(
                    "| "
                    + " | ".join(
                        [
                            str(index),
                            str(row.get("date") or "-"),
                            str(row.get("route") or "-"),
                            str(row.get("region_name") or "-"),
                            price_text,
                            str(row.get("stability_label") or "-"),
                            str(row.get("market_reliability_label") or "-"),
                            str(row.get("source_label") or source_kind_label(row.get("source_kind"))),
                            f"[打开结果页]({row.get('link') or '-'})",
                        ]
                    )
                    + " |"
                )
                writer.writerow(
                    [
                        index,
                        row.get("date") or "-",
                        row.get("route") or "-",
                        row.get("region_name") or "-",
                        float(price) if isinstance(price, (int, float)) else "",
                        row.get("stability_label") or "-",
                        row.get("market_reliability_label") or "-",
                        row.get("source_label") or source_kind_label(row.get("source_kind")),
                        row.get("link") or "-",
                    ]
                )
        markdown_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
        self.log(f"已导出决策摘要: {markdown_path.name} / {csv_path.name}")

    def open_outputs(self) -> None:
        output_dir = get_reports_dir()
        try:
            import subprocess

            subprocess.run(["open", str(output_dir)], check=False)
        except Exception as exc:
            messagebox.showerror("打开失败", str(exc))


def main() -> None:
    if os.environ.get("SKYSCANNER_GUI_SMOKE_TEST") == "1":
        root = tk.Tk()
        _apply_classic_mac_theme(root)
        root.update_idletasks()
        root.destroy()
        print("smoke-ok")
        return

    startup_issues = _collect_startup_issues()
    if startup_issues:
        _show_startup_issues_and_exit(startup_issues)
        return

    root = tk.Tk()
    _apply_classic_mac_theme(root)
    App(root)
    root.mainloop()


if __name__ == "__main__":
    main()
