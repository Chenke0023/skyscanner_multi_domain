from __future__ import annotations

import importlib.util
import inspect
import json
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

from cli import CombinedQuoteRow
from location_resolver import (
    AIRPORT_DATASET_PATH,
    LOCATION_MAPPINGS_PATH,
)
from scan_history import source_kind_label, summarize_query_history


MAX_LOCATION_SUGGESTIONS = 8

_TRIP_TYPE_ONE_WAY = "one_way"
_TRIP_TYPE_ROUND_TRIP = "round_trip"
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
_AUTO_REFRESH_DISABLED = "关闭"


def _escape_applescript_text(value: str) -> str:
    return value.replace("\\", "\\\\").replace('"', '\\"')


def _send_desktop_notification(title: str, message: str) -> bool:
    if sys.platform != "darwin":
        return False
    script = (
        'display notification "{message}" with title "{title}"'
    ).format(
        message=_escape_applescript_text(message),
        title=_escape_applescript_text(title),
    )
    try:
        subprocess.run(
            ["osascript", "-e", script],
            check=False,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    except OSError:
        return False
    return True


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


def _build_market_delta_explanation(
    rows: list[CombinedQuoteRow],
    history_records: list[Any],
) -> str:
    priced_rows = [
        row
        for row in rows
        if isinstance(row.get("cheapest_cny_price"), (int, float))
    ]
    if len(priced_rows) < 2:
        return "当前只有 1 个可比较市场。"
    ranked = sorted(priced_rows, key=_decision_price_key)
    winner = ranked[0]
    runner_up = ranked[1]
    spread = float(runner_up["cheapest_cny_price"]) - float(winner["cheapest_cny_price"])
    explanation = (
        f"{winner.get('region_name') or '-'} 比 {runner_up.get('region_name') or '-'} 低 ¥{spread:,.2f}"
        if spread >= 0.01
        else f"{winner.get('region_name') or '-'} 与 {runner_up.get('region_name') or '-'} 基本持平"
    )
    if history_records:
        history_summary = summarize_query_history(history_records)
        winner_market = str(winner.get("region_name") or winner.get("region_code") or "-")
        win_count = history_summary.market_win_counts.get(winner_market, 0)
        if win_count > 0:
            explanation += f"，近 {history_summary.scan_count} 次里该市场赢过 {win_count} 次"
    return explanation + "。"


def _build_recommendation_payload(
    rows: list[CombinedQuoteRow],
    history_records: list[Any] | None = None,
) -> dict[str, str | None]:
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
        "insight": (
            f"{spread_text} {_build_market_delta_explanation(rows, history_records or [])}".strip()
        ),
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


def _build_window_summary_text(
    rows: list[CombinedQuoteRow],
    history_records: list[Any],
) -> str:
    if not rows:
        return "等待扫描后生成价格日历。"
    priced_rows = [
        row
        for row in rows
        if isinstance(row.get("cheapest_cny_price"), (int, float))
    ]
    if not priced_rows:
        return "当前窗口暂无可比较价格，可结合失败列表补扫。"
    ranked = sorted(priced_rows, key=_decision_price_key)
    winner = ranked[0]
    runner_up = ranked[1] if len(ranked) > 1 else None
    spread_text = ""
    if runner_up is not None and isinstance(runner_up.get("cheapest_cny_price"), (int, float)):
        spread = float(runner_up["cheapest_cny_price"]) - float(winner["cheapest_cny_price"])
        spread_text = f"；比次优组合低 ¥{spread:,.2f}" if spread >= 0.01 else "；与次优组合几乎持平"
    history_summary = summarize_query_history(history_records) if history_records else None
    trend_text = ""
    if history_summary is not None and history_summary.recent_prices:
        trend_text = (
            f"；最近走势 {_build_trend_sparkline(history_summary.recent_prices)}"
        )
    return (
        f"窗口最低价: {winner.get('date') or '-'} · {winner.get('region_name') or '-'} · "
        f"¥{float(winner['cheapest_cny_price']):,.2f}{spread_text}{trend_text}"
    )


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
                float(row.get("cheapest_cny_price")),
                str(row.get("date") or ""),
                str(row.get("region_name") or ""),
            ),
        )
        winner = sorted_rows[0]
        runner_up = sorted_rows[1] if len(sorted_rows) > 1 else None
        winner_price = float(winner["cheapest_cny_price"])
        delta_text = "当前只有 1 条可比较的最低价结果。"
        history_delta = str(winner.get("delta_label") or "").strip()
        if runner_up is not None:
            runner_up_price = float(runner_up["cheapest_cny_price"])
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
    minimum_price = min(float(row["cheapest_cny_price"]) for row in cheapest_candidates)
    return {
        _row_signature(row)
        for row in cheapest_candidates
        if abs(float(row["cheapest_cny_price"]) - minimum_price) < 0.0001
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
        return list(_REQUIRED_APIFY_DATA_FILES)

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
