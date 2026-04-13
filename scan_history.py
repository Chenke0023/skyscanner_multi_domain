from __future__ import annotations

import hashlib
import json
import sqlite3
from copy import deepcopy
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Iterable

from app_paths import get_scan_history_file


PREVIEW_MAX_AGE_HOURS = 6
DEFAULT_RECENT_QUERY_LIMIT = 20
DEFAULT_FAVORITES_LIMIT = 10


RowsByDate = list[tuple[str, list[dict[str, Any]]]]
QuotesByDate = list[tuple[str, list[dict[str, Any]]]]


def _serialize_grouped_rows(rows_by_date: RowsByDate) -> str:
    payload = [{"date": trip_label, "rows": rows} for trip_label, rows in rows_by_date]
    return json.dumps(payload, ensure_ascii=False, sort_keys=True)


def _deserialize_grouped_rows(payload: str) -> RowsByDate:
    try:
        data = json.loads(payload)
    except json.JSONDecodeError:
        return []
    if not isinstance(data, list):
        return []
    grouped: RowsByDate = []
    for item in data:
        if not isinstance(item, dict):
            continue
        trip_label = str(item.get("date") or "")
        rows = item.get("rows")
        if not isinstance(rows, list):
            continue
        grouped.append((trip_label, [row for row in rows if isinstance(row, dict)]))
    return grouped


def build_query_key(query_payload: dict[str, Any]) -> str:
    identity = query_payload.get("identity") or {}
    identity_json = json.dumps(identity, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha1(identity_json.encode("utf-8")).hexdigest()


def build_query_title(query_payload: dict[str, Any]) -> str:
    display = query_payload.get("display") or {}
    title = str(display.get("title") or "").strip()
    if title:
        return title
    identity = query_payload.get("identity") or {}
    origin = str(identity.get("origin_label") or identity.get("origin_code") or "-")
    destination = str(
        identity.get("destination_label") or identity.get("destination_code") or "-"
    )
    travel_date = str(identity.get("date") or "-")
    return_date = str(identity.get("return_date") or "").strip()
    if return_date:
        return f"{origin} -> {destination} ({travel_date} / {return_date})"
    return f"{origin} -> {destination} ({travel_date})"


def source_kind_label(source_kind: str | None) -> str:
    return {
        "cached": "预览缓存",
        "live": "实时直连",
        "cdp_reuse": "复用已打开页面",
        "browser_fallback": "浏览器兜底",
    }.get(str(source_kind or "").strip().lower(), "-")


def can_reuse_page_for_row(row: dict[str, Any]) -> bool:
    return str(row.get("source_kind") or "").strip().lower() == "cdp_reuse"


def classify_failure(status: str | None, error: str | None) -> tuple[str, str]:
    normalized = str(status or "").strip().lower()
    normalized_error = str(error or "").strip()
    if normalized in {"px_challenge", "page_challenge", "captcha_solve_failed"}:
        return ("需要浏览器验证", "打开该市场结果页并完成验证后重试")
    if normalized == "page_loading":
        return ("页面仍在加载", "稍后重试，或复用已打开的页面")
    if normalized in {"page_parse_failed", "scrapling_parse_failed"}:
        return ("页面结构可见但未识别价格", "打开结果页确认是否已完整加载")
    if normalized:
        return ("网络/抓取异常", normalized_error or "重试该市场")
    return ("网络/抓取异常", normalized_error or "重试该市场")


def flatten_rows_by_date(rows_by_date: RowsByDate) -> list[dict[str, Any]]:
    flattened: list[dict[str, Any]] = []
    for trip_label, rows in rows_by_date:
        for row in rows:
            flattened.append({"date": trip_label, **deepcopy(row)})
    return flattened


def _row_key(row_date: str, row: dict[str, Any]) -> tuple[str, str, str]:
    return (
        row_date,
        str(row.get("route") or "-"),
        str(row.get("region_code") or row.get("region_name") or "-"),
    )


def prioritize_region_codes(region_codes: Iterable[str], previous_rows_by_date: RowsByDate | None) -> list[str]:
    ordered = [code.strip().upper() for code in region_codes if str(code).strip()]
    if not previous_rows_by_date:
        return ordered
    ranked_rows = [
        row
        for row in flatten_rows_by_date(previous_rows_by_date)
        if isinstance(row.get("cheapest_cny_price"), (int, float))
        or isinstance(row.get("best_cny_price"), (int, float))
    ]
    ranked_rows.sort(
        key=lambda row: (
            row.get("cheapest_cny_price") is None,
            float(row.get("cheapest_cny_price"))
            if isinstance(row.get("cheapest_cny_price"), (int, float))
            else float("inf"),
            row.get("best_cny_price") is None,
            float(row.get("best_cny_price"))
            if isinstance(row.get("best_cny_price"), (int, float))
            else float("inf"),
        )
    )
    winners: list[str] = []
    for row in ranked_rows:
        region_code = str(row.get("region_code") or "").strip().upper()
        if region_code and region_code in ordered and region_code not in winners:
            winners.append(region_code)
    for code in ordered:
        if code not in winners:
            winners.append(code)
    return winners


def get_failed_region_codes(
    quotes_by_date: QuotesByDate | None,
    *,
    trip_label: str | None = None,
) -> list[str]:
    if not quotes_by_date:
        return []
    failed: list[str] = []
    for current_trip_label, quotes in quotes_by_date:
        if trip_label is not None and current_trip_label != trip_label:
            continue
        for quote in quotes:
            region_code = str(quote.get("region") or "").strip().upper()
            if not region_code:
                continue
            price = quote.get("price")
            best_price = quote.get("best_price")
            cheapest_price = quote.get("cheapest_price")
            if any(isinstance(value, (int, float)) for value in (price, best_price, cheapest_price)):
                continue
            if region_code not in failed:
                failed.append(region_code)
    return failed


def _build_delta_label(
    current_row: dict[str, Any],
    previous_row: dict[str, Any] | None,
) -> tuple[float | None, str]:
    current_price = current_row.get("cheapest_cny_price")
    previous_price = previous_row.get("cheapest_cny_price") if previous_row else None

    if isinstance(current_price, (int, float)) and isinstance(previous_price, (int, float)):
        delta = float(current_price) - float(previous_price)
        if abs(delta) < 0.01:
            return (0.0, "持平")
        if delta < 0:
            return (delta, f"降 ¥{abs(delta):,.2f}")
        return (delta, f"涨 ¥{abs(delta):,.2f}")

    current_success = isinstance(current_price, (int, float))
    previous_success = isinstance(previous_price, (int, float))
    if current_success and not previous_success:
        return (None, "由失败变成功")
    if previous_success and not current_success:
        return (None, "由成功变失败")
    if previous_row is None:
        return (None, "新结果")
    return (None, "-")


def annotate_rows_with_history(
    rows_by_date: RowsByDate,
    previous_rows_by_date: RowsByDate | None = None,
    *,
    source_kind_override: str | None = None,
    updated_at: str | None = None,
) -> RowsByDate:
    previous_index: dict[tuple[str, str, str], dict[str, Any]] = {}
    for trip_label, rows in previous_rows_by_date or []:
        for row in rows:
            previous_index[_row_key(trip_label, row)] = row

    stamped_at = updated_at or datetime.now().isoformat(timespec="seconds")
    annotated: RowsByDate = []
    for trip_label, rows in rows_by_date:
        next_rows: list[dict[str, Any]] = []
        for row in rows:
            next_row = deepcopy(row)
            source_kind = source_kind_override or str(next_row.get("source_kind") or "")
            next_row["source_kind"] = source_kind or next_row.get("source_kind") or None
            next_row["source_label"] = source_kind_label(next_row.get("source_kind"))
            next_row["updated_at"] = stamped_at
            next_row["can_reuse_page"] = can_reuse_page_for_row(next_row)
            previous_row = previous_index.get(_row_key(trip_label, next_row))
            delta_value, delta_label = _build_delta_label(next_row, previous_row)
            next_row["delta_vs_last_scan"] = delta_value
            next_row["delta_label"] = delta_label
            has_price = any(
                isinstance(next_row.get(key), (int, float))
                for key in ("best_cny_price", "cheapest_cny_price")
            )
            if not has_price:
                failure_category, failure_action = classify_failure(
                    str(next_row.get("status") or ""),
                    str(next_row.get("error") or ""),
                )
                if next_row.get("can_reuse_page"):
                    failure_action = "复用已打开的页面后重试"
                next_row["failure_category"] = failure_category
                next_row["failure_action"] = failure_action
            else:
                next_row["failure_category"] = None
                next_row["failure_action"] = None
            next_rows.append(next_row)
        annotated.append((trip_label, next_rows))
    return annotated


def override_rows_source_kind(
    rows_by_date: RowsByDate,
    source_kind: str,
    *,
    updated_at: str | None = None,
) -> RowsByDate:
    stamped_at = updated_at or datetime.now().isoformat(timespec="seconds")
    overridden: RowsByDate = []
    for trip_label, rows in rows_by_date:
        next_rows: list[dict[str, Any]] = []
        for row in rows:
            next_row = deepcopy(row)
            next_row["source_kind"] = source_kind
            next_row["source_label"] = source_kind_label(source_kind)
            next_row["updated_at"] = next_row.get("updated_at") or stamped_at
            next_row["can_reuse_page"] = can_reuse_page_for_row(next_row)
            has_price = any(
                isinstance(next_row.get(key), (int, float))
                for key in ("best_cny_price", "cheapest_cny_price")
            )
            if not has_price:
                failure_category, failure_action = classify_failure(
                    str(next_row.get("status") or ""),
                    str(next_row.get("error") or ""),
                )
                if next_row.get("can_reuse_page"):
                    failure_action = "复用已打开的页面后重试"
                next_row["failure_category"] = next_row.get("failure_category") or failure_category
                next_row["failure_action"] = next_row.get("failure_action") or failure_action
            next_rows.append(next_row)
        overridden.append((trip_label, next_rows))
    return overridden


def override_quotes_source_kind(quotes_by_date: QuotesByDate, source_kind: str) -> QuotesByDate:
    overridden: QuotesByDate = []
    for trip_label, quotes in quotes_by_date:
        next_quotes: list[dict[str, Any]] = []
        for quote in quotes:
            next_quote = deepcopy(quote)
            next_quote["source_kind"] = source_kind
            next_quotes.append(next_quote)
        overridden.append((trip_label, next_quotes))
    return overridden


def merge_quotes_by_date(base_quotes_by_date: QuotesByDate, updates_by_date: QuotesByDate) -> QuotesByDate:
    merged: dict[str, dict[tuple[str, str], dict[str, Any]]] = {}
    order: list[str] = []

    def ingest(items: QuotesByDate) -> None:
        for trip_label, quotes in items:
            if trip_label not in merged:
                merged[trip_label] = {}
                order.append(trip_label)
            bucket = merged[trip_label]
            for quote in quotes:
                key = (
                    str(quote.get("route") or "-"),
                    str(quote.get("region") or "-"),
                )
                bucket[key] = deepcopy(quote)

    ingest(base_quotes_by_date)
    ingest(updates_by_date)

    return [(trip_label, list(merged[trip_label].values())) for trip_label in order]


def merge_rows_by_date(base_rows_by_date: RowsByDate, updates_by_date: RowsByDate) -> RowsByDate:
    merged: dict[str, dict[tuple[str, str], dict[str, Any]]] = {}
    order: list[str] = []

    def ingest(items: RowsByDate) -> None:
        for trip_label, rows in items:
            if trip_label not in merged:
                merged[trip_label] = {}
                order.append(trip_label)
            bucket = merged[trip_label]
            for row in rows:
                key = (
                    str(row.get("route") or "-"),
                    str(row.get("region_code") or row.get("region_name") or "-"),
                )
                bucket[key] = deepcopy(row)

    ingest(base_rows_by_date)
    ingest(updates_by_date)

    return [(trip_label, list(merged[trip_label].values())) for trip_label in order]


def get_rows_for_trip_label(rows_by_date: RowsByDate | None, trip_label: str) -> list[dict[str, Any]]:
    for current_trip_label, rows in rows_by_date or []:
        if current_trip_label == trip_label:
            return [deepcopy(row) for row in rows]
    return []


def get_quotes_for_trip_label(
    quotes_by_date: QuotesByDate | None,
    trip_label: str,
) -> list[dict[str, Any]]:
    for current_trip_label, quotes in quotes_by_date or []:
        if current_trip_label == trip_label:
            return [deepcopy(quote) for quote in quotes]
    return []


def select_preview_region_batches(
    region_codes: Iterable[str],
    previous_rows_by_date: RowsByDate | None,
    *,
    first_batch_size: int = 3,
) -> tuple[list[str], list[str]]:
    ordered = prioritize_region_codes(region_codes, previous_rows_by_date)
    batch_size = max(int(first_batch_size), 1)
    return ordered[:batch_size], ordered[batch_size:]


def build_delta_summary_lines(rows_by_date: RowsByDate) -> list[str]:
    lines: list[str] = []
    for trip_label, rows in rows_by_date:
        changed_rows = [
            row
            for row in rows
            if str(row.get("delta_label") or "-") not in {"-", "持平", ""}
        ]
        changed_rows.sort(
            key=lambda row: (
                row.get("cheapest_cny_price") is None,
                float(row.get("cheapest_cny_price"))
                if isinstance(row.get("cheapest_cny_price"), (int, float))
                else float("inf"),
                str(row.get("region_name") or ""),
            )
        )
        for row in changed_rows:
            lines.append(
                "{trip} | {route} | {region} | {delta}".format(
                    trip=trip_label,
                    route=row.get("route") or "-",
                    region=row.get("region_name") or row.get("region_code") or "-",
                    delta=row.get("delta_label") or "-",
                )
            )
    return lines


def build_history_series(
    records: list["ScanRecord"],
    *,
    trip_label: str | None = None,
) -> list[HistorySeriesPoint]:
    series: list[HistorySeriesPoint] = []
    for record in sorted(records, key=lambda item: (item.created_at, item.id)):
        for current_trip_label, rows in record.rows_by_date:
            if trip_label is not None and current_trip_label != trip_label:
                continue
            priced_rows = [
                row
                for row in rows
                if isinstance(row.get("cheapest_cny_price"), (int, float))
            ]
            priced_rows.sort(
                key=lambda row: (
                    float(row.get("cheapest_cny_price"))
                    if isinstance(row.get("cheapest_cny_price"), (int, float))
                    else float("inf"),
                    str(row.get("region_name") or ""),
                )
            )
            winner = priced_rows[0] if priced_rows else None
            series.append(
                HistorySeriesPoint(
                    created_at=record.created_at,
                    trip_label=current_trip_label,
                    cheapest_cny_price=(
                        float(winner["cheapest_cny_price"])
                        if winner and isinstance(winner.get("cheapest_cny_price"), (int, float))
                        else None
                    ),
                    best_cny_price=(
                        float(winner["best_cny_price"])
                        if winner and isinstance(winner.get("best_cny_price"), (int, float))
                        else None
                    ),
                    region_name=str(winner.get("region_name") or "") or None if winner else None,
                    route=str(winner.get("route") or "") or None if winner else None,
                    source_kind=str(winner.get("source_kind") or "") or None if winner else None,
                )
            )
    return series


def summarize_query_history(records: list["ScanRecord"]) -> QueryHistorySummary:
    market_win_counts: dict[str, int] = {}
    market_success_counts: dict[str, int] = {}
    market_total_counts: dict[str, int] = {}
    history_low_price: float | None = None
    history_low_trip_label: str | None = None
    history_low_region: str | None = None
    latest_scan_at = records[0].created_at if records else None

    for record in records:
        for trip_label, rows in record.rows_by_date:
            priced_rows = [
                row
                for row in rows
                if isinstance(row.get("cheapest_cny_price"), (int, float))
            ]
            for row in rows:
                region_name = str(row.get("region_name") or row.get("region_code") or "-")
                market_total_counts[region_name] = market_total_counts.get(region_name, 0) + 1
                if any(
                    isinstance(row.get(key), (int, float))
                    for key in ("best_cny_price", "cheapest_cny_price")
                ):
                    market_success_counts[region_name] = (
                        market_success_counts.get(region_name, 0) + 1
                    )
            if not priced_rows:
                continue
            priced_rows.sort(
                key=lambda row: (
                    float(row.get("cheapest_cny_price"))
                    if isinstance(row.get("cheapest_cny_price"), (int, float))
                    else float("inf"),
                    str(row.get("region_name") or ""),
                )
            )
            winner = priced_rows[0]
            region_name = str(winner.get("region_name") or winner.get("region_code") or "-")
            market_win_counts[region_name] = market_win_counts.get(region_name, 0) + 1
            price = winner.get("cheapest_cny_price")
            if isinstance(price, (int, float)) and (
                history_low_price is None or float(price) < history_low_price
            ):
                history_low_price = float(price)
                history_low_trip_label = trip_label
                history_low_region = region_name

    series = build_history_series(records)
    recent_prices = [
        float(point.cheapest_cny_price)
        for point in series[-7:]
        if isinstance(point.cheapest_cny_price, (int, float))
    ]
    return QueryHistorySummary(
        scan_count=len(records),
        latest_scan_at=latest_scan_at,
        history_low_price=history_low_price,
        history_low_trip_label=history_low_trip_label,
        history_low_region=history_low_region,
        recent_prices=recent_prices,
        market_win_counts=market_win_counts,
        market_success_counts=market_success_counts,
        market_total_counts=market_total_counts,
    )


@dataclass
class ScanRecord:
    id: int
    query_key: str
    title: str
    created_at: str
    scan_mode: str
    query_payload: dict[str, Any]
    rows_by_date: RowsByDate
    quotes_by_date: QuotesByDate
    is_favorite: bool = False


@dataclass(frozen=True)
class HistorySeriesPoint:
    created_at: str
    trip_label: str
    cheapest_cny_price: float | None
    best_cny_price: float | None
    region_name: str | None
    route: str | None
    source_kind: str | None


@dataclass(frozen=True)
class QueryHistorySummary:
    scan_count: int
    latest_scan_at: str | None
    history_low_price: float | None
    history_low_trip_label: str | None
    history_low_region: str | None
    recent_prices: list[float]
    market_win_counts: dict[str, int]
    market_success_counts: dict[str, int]
    market_total_counts: dict[str, int]


@dataclass(frozen=True)
class AlertConfig:
    query_key: str
    title: str
    query_payload: dict[str, Any]
    notifications_enabled: bool
    target_price: float | None
    drop_amount: float | None
    auto_refresh_minutes: int | None
    notify_on_recovery: bool
    notify_on_new_low: bool
    last_notified_price: float | None
    last_notified_at: str | None
    last_auto_refresh_at: str | None


class ScanHistoryStore:
    def __init__(self, db_path: Path | None = None) -> None:
        self.db_path = db_path or get_scan_history_file()
        self._ensure_schema()

    def _connect(self) -> sqlite3.Connection:
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(self.db_path)
        connection.row_factory = sqlite3.Row
        return connection

    def _ensure_schema(self) -> None:
        with self._connect() as connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS scans (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    query_key TEXT NOT NULL,
                    title TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    scan_mode TEXT NOT NULL,
                    query_payload_json TEXT NOT NULL,
                    rows_by_date_json TEXT NOT NULL,
                    quotes_by_date_json TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_scans_query_key_created_at
                    ON scans(query_key, created_at DESC);
                CREATE TABLE IF NOT EXISTS favorites (
                    query_key TEXT PRIMARY KEY,
                    title TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    query_payload_json TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS alert_configs (
                    query_key TEXT PRIMARY KEY,
                    title TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    query_payload_json TEXT NOT NULL,
                    notifications_enabled INTEGER NOT NULL DEFAULT 1,
                    target_price REAL,
                    drop_amount REAL,
                    auto_refresh_minutes INTEGER,
                    notify_on_recovery INTEGER NOT NULL DEFAULT 1,
                    notify_on_new_low INTEGER NOT NULL DEFAULT 1,
                    last_notified_price REAL,
                    last_notified_at TEXT,
                    last_auto_refresh_at TEXT
                );
                """
            )

    def _row_to_record(self, row: sqlite3.Row) -> ScanRecord:
        query_payload = json.loads(str(row["query_payload_json"]))
        query_key = str(row["query_key"])
        is_favorite = self.is_favorite_query_key(query_key)
        return ScanRecord(
            id=int(row["id"]),
            query_key=query_key,
            title=str(row["title"]),
            created_at=str(row["created_at"]),
            scan_mode=str(row["scan_mode"]),
            query_payload=query_payload,
            rows_by_date=_deserialize_grouped_rows(str(row["rows_by_date_json"])),
            quotes_by_date=_deserialize_grouped_rows(str(row["quotes_by_date_json"])),
            is_favorite=is_favorite,
        )

    def _row_to_alert_config(self, row: sqlite3.Row) -> AlertConfig:
        return AlertConfig(
            query_key=str(row["query_key"]),
            title=str(row["title"]),
            query_payload=json.loads(str(row["query_payload_json"])),
            notifications_enabled=bool(int(row["notifications_enabled"])),
            target_price=float(row["target_price"]) if row["target_price"] is not None else None,
            drop_amount=float(row["drop_amount"]) if row["drop_amount"] is not None else None,
            auto_refresh_minutes=(
                int(row["auto_refresh_minutes"]) if row["auto_refresh_minutes"] is not None else None
            ),
            notify_on_recovery=bool(int(row["notify_on_recovery"])),
            notify_on_new_low=bool(int(row["notify_on_new_low"])),
            last_notified_price=(
                float(row["last_notified_price"]) if row["last_notified_price"] is not None else None
            ),
            last_notified_at=str(row["last_notified_at"]) if row["last_notified_at"] is not None else None,
            last_auto_refresh_at=(
                str(row["last_auto_refresh_at"]) if row["last_auto_refresh_at"] is not None else None
            ),
        )

    def record_scan(
        self,
        query_payload: dict[str, Any],
        rows_by_date: RowsByDate,
        quotes_by_date: QuotesByDate,
        *,
        scan_mode: str,
    ) -> int:
        query_key = build_query_key(query_payload)
        title = build_query_title(query_payload)
        created_at = datetime.now().isoformat(timespec="seconds")
        with self._connect() as connection:
            cursor = connection.execute(
                """
                INSERT INTO scans (
                    query_key,
                    title,
                    created_at,
                    scan_mode,
                    query_payload_json,
                    rows_by_date_json,
                    quotes_by_date_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    query_key,
                    title,
                    created_at,
                    scan_mode,
                    json.dumps(query_payload, ensure_ascii=False, sort_keys=True),
                    _serialize_grouped_rows(rows_by_date),
                    _serialize_grouped_rows(quotes_by_date),
                ),
            )
            return int(cursor.lastrowid)

    def is_favorite_query_key(self, query_key: str) -> bool:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT 1 FROM favorites WHERE query_key = ?",
                (query_key,),
            ).fetchone()
        return row is not None

    def toggle_favorite(self, query_payload: dict[str, Any]) -> bool:
        query_key = build_query_key(query_payload)
        title = build_query_title(query_payload)
        with self._connect() as connection:
            existing = connection.execute(
                "SELECT 1 FROM favorites WHERE query_key = ?",
                (query_key,),
            ).fetchone()
            if existing:
                connection.execute(
                    "DELETE FROM favorites WHERE query_key = ?",
                    (query_key,),
                )
                connection.execute(
                    "DELETE FROM alert_configs WHERE query_key = ?",
                    (query_key,),
                )
                return False
            connection.execute(
                """
                INSERT OR REPLACE INTO favorites (
                    query_key,
                    title,
                    updated_at,
                    query_payload_json
                ) VALUES (?, ?, ?, ?)
                """,
                (
                    query_key,
                    title,
                    datetime.now().isoformat(timespec="seconds"),
                    json.dumps(query_payload, ensure_ascii=False, sort_keys=True),
                ),
            )
            return True

    def get_alert_config(self, query_payload: dict[str, Any]) -> AlertConfig | None:
        query_key = build_query_key(query_payload)
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT *
                FROM alert_configs
                WHERE query_key = ?
                LIMIT 1
                """,
                (query_key,),
            ).fetchone()
        if row is None:
            return None
        return self._row_to_alert_config(row)

    def save_alert_config(
        self,
        query_payload: dict[str, Any],
        *,
        notifications_enabled: bool,
        target_price: float | None,
        drop_amount: float | None,
        auto_refresh_minutes: int | None,
        notify_on_recovery: bool = True,
        notify_on_new_low: bool = True,
    ) -> AlertConfig:
        query_key = build_query_key(query_payload)
        title = build_query_title(query_payload)
        with self._connect() as connection:
            connection.execute(
                """
                INSERT OR REPLACE INTO alert_configs (
                    query_key,
                    title,
                    updated_at,
                    query_payload_json,
                    notifications_enabled,
                    target_price,
                    drop_amount,
                    auto_refresh_minutes,
                    notify_on_recovery,
                    notify_on_new_low,
                    last_notified_price,
                    last_notified_at,
                    last_auto_refresh_at
                ) VALUES (
                    ?,
                    ?,
                    ?,
                    ?,
                    ?,
                    ?,
                    ?,
                    ?,
                    ?,
                    ?,
                    COALESCE((SELECT last_notified_price FROM alert_configs WHERE query_key = ?), NULL),
                    COALESCE((SELECT last_notified_at FROM alert_configs WHERE query_key = ?), NULL),
                    COALESCE((SELECT last_auto_refresh_at FROM alert_configs WHERE query_key = ?), NULL)
                )
                """,
                (
                    query_key,
                    title,
                    datetime.now().isoformat(timespec="seconds"),
                    json.dumps(query_payload, ensure_ascii=False, sort_keys=True),
                    1 if notifications_enabled else 0,
                    target_price,
                    drop_amount,
                    auto_refresh_minutes,
                    1 if notify_on_recovery else 0,
                    1 if notify_on_new_low else 0,
                    query_key,
                    query_key,
                    query_key,
                ),
            )
        config = self.get_alert_config(query_payload)
        assert config is not None
        return config

    def delete_alert_config(self, query_payload: dict[str, Any]) -> None:
        query_key = build_query_key(query_payload)
        with self._connect() as connection:
            connection.execute(
                "DELETE FROM alert_configs WHERE query_key = ?",
                (query_key,),
            )

    def list_alert_configs(self, *, notifications_only: bool = False) -> list[AlertConfig]:
        query = "SELECT * FROM alert_configs"
        params: tuple[Any, ...] = ()
        if notifications_only:
            query += " WHERE notifications_enabled = 1"
        query += " ORDER BY updated_at DESC"
        with self._connect() as connection:
            rows = connection.execute(query, params).fetchall()
        return [self._row_to_alert_config(row) for row in rows]

    def mark_alert_notified(
        self,
        query_payload: dict[str, Any],
        *,
        last_notified_price: float | None,
    ) -> None:
        query_key = build_query_key(query_payload)
        with self._connect() as connection:
            connection.execute(
                """
                UPDATE alert_configs
                SET last_notified_price = ?, last_notified_at = ?
                WHERE query_key = ?
                """,
                (
                    last_notified_price,
                    datetime.now().isoformat(timespec="seconds"),
                    query_key,
                ),
            )

    def mark_alert_auto_refreshed(self, query_payload: dict[str, Any]) -> None:
        query_key = build_query_key(query_payload)
        with self._connect() as connection:
            connection.execute(
                """
                UPDATE alert_configs
                SET last_auto_refresh_at = ?
                WHERE query_key = ?
                """,
                (
                    datetime.now().isoformat(timespec="seconds"),
                    query_key,
                ),
            )

    def get_due_auto_refresh_configs(self, *, limit: int = 1) -> list[AlertConfig]:
        now = datetime.now()
        due_configs: list[AlertConfig] = []
        for config in self.list_alert_configs():
            if config.auto_refresh_minutes is None or config.auto_refresh_minutes <= 0:
                continue
            if not self.is_favorite_query_key(config.query_key):
                continue
            if not config.last_auto_refresh_at:
                due_configs.append(config)
                continue
            try:
                last_run = datetime.fromisoformat(config.last_auto_refresh_at)
            except ValueError:
                due_configs.append(config)
                continue
            if now - last_run >= timedelta(minutes=config.auto_refresh_minutes):
                due_configs.append(config)
            if len(due_configs) >= limit:
                break
        return due_configs

    def get_latest_scan(self, query_payload: dict[str, Any]) -> ScanRecord | None:
        query_key = build_query_key(query_payload)
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT *
                FROM scans
                WHERE query_key = ?
                ORDER BY created_at DESC, id DESC
                LIMIT 1
                """,
                (query_key,),
            ).fetchone()
        if row is None:
            return None
        return self._row_to_record(row)

    def get_cached_preview(
        self,
        query_payload: dict[str, Any],
        *,
        max_age_hours: int = PREVIEW_MAX_AGE_HOURS,
    ) -> ScanRecord | None:
        record = self.get_latest_scan(query_payload)
        if record is None:
            return None
        if not flatten_rows_by_date(record.rows_by_date):
            return None
        age_limit = datetime.now() - timedelta(hours=max_age_hours)
        try:
            record_time = datetime.fromisoformat(record.created_at)
        except ValueError:
            return None
        if record_time < age_limit:
            return None
        return record

    def _load_latest_unique_scans(self, limit: int) -> list[ScanRecord]:
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT *
                FROM scans
                ORDER BY created_at DESC, id DESC
                LIMIT ?
                """,
                (limit * 8,),
            ).fetchall()

        records: list[ScanRecord] = []
        seen: set[str] = set()
        for row in rows:
            record = self._row_to_record(row)
            if record.query_key in seen:
                continue
            seen.add(record.query_key)
            records.append(record)
            if len(records) >= limit:
                break
        return records

    def get_recent_queries(self, limit: int = DEFAULT_RECENT_QUERY_LIMIT) -> list[ScanRecord]:
        return self._load_latest_unique_scans(limit)

    def get_favorites(self, limit: int = DEFAULT_FAVORITES_LIMIT) -> list[ScanRecord]:
        with self._connect() as connection:
            favorite_rows = connection.execute(
                """
                SELECT query_key, query_payload_json
                FROM favorites
                ORDER BY updated_at DESC
                LIMIT ?
                """,
                (limit,),
            ).fetchall()
        records: list[ScanRecord] = []
        for favorite_row in favorite_rows:
            query_payload = json.loads(str(favorite_row["query_payload_json"]))
            record = self.get_latest_scan(query_payload)
            if record is None:
                record = ScanRecord(
                    id=0,
                    query_key=str(favorite_row["query_key"]),
                    title=build_query_title(query_payload),
                    created_at="",
                    scan_mode="favorite_only",
                    query_payload=query_payload,
                    rows_by_date=[],
                    quotes_by_date=[],
                    is_favorite=True,
                )
            else:
                record.is_favorite = True
            records.append(record)
        return records

    def get_query_history(
        self,
        query_payload: dict[str, Any],
        *,
        limit: int = 10,
    ) -> list[ScanRecord]:
        query_key = build_query_key(query_payload)
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT *
                FROM scans
                WHERE query_key = ?
                ORDER BY created_at DESC, id DESC
                LIMIT ?
                """,
                (query_key, limit),
            ).fetchall()
        return [self._row_to_record(row) for row in rows]

    def get_query_history_summary(
        self,
        query_payload: dict[str, Any],
        *,
        limit: int = 10,
    ) -> QueryHistorySummary:
        return summarize_query_history(self.get_query_history(query_payload, limit=limit))
