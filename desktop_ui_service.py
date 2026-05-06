from __future__ import annotations

import asyncio
import importlib.util
import re
import subprocess
import threading
import time
import webbrowser
from copy import deepcopy
from dataclasses import asdict
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from app_paths import get_gui_state_file, get_reports_dir
from cli import CombinedQuoteRow, SimpleCLI
from date_window import format_trip_date_label
from desktop_logic import (
    _AUTO_REFRESH_DISABLED,
    _GUI_AIRPORT_PAIR_CONCURRENCY,
    _GUI_DATE_WINDOW_CONCURRENCY,
    _GUI_REGION_CONCURRENCY,
    _TOP_RECOMMENDATION_LIMIT,
    _TRIP_TYPE_ONE_WAY,
    _TRIP_TYPE_ROUND_TRIP,
    MAX_LOCATION_SUGGESTIONS,
    _build_calendar_summary,
    _build_cheapest_conclusion,
    _build_compare_rows,
    _build_recommendation_payload,
    _build_top_recommendations,
    _build_trend_sparkline,
    _build_window_summary_text,
    _collect_startup_issues,
    _compute_market_reliability_label,
    _compute_stability_label,
    _decision_price_key,
    _enrich_decision_rows,
    _find_cheapest_highlight_signatures,
    _format_history_record,
    _is_live_source_kind,
    _load_query_state,
    _normalize_query_state,
    _order_grouped_by_trip_labels,
    _row_has_price,
    _row_signature,
    _send_desktop_notification,
    _sort_combined_rows,
    _split_trip_label,
    _upsert_quotes_by_date,
    _upsert_rows_by_date,
    _write_query_state,
)
from location_resolver import COUNTRY_ROUTE_DEFAULT_AIRPORT_LIMIT, LocationRecord
from scan_history import (
    AlertConfig,
    annotate_rows_with_history,
    build_query_key,
    build_query_title,
    get_failed_region_codes,
    get_quotes_for_trip_label,
    get_rows_for_trip_label,
    merge_quotes_by_date,
    merge_rows_by_date,
    override_quotes_source_kind,
    override_rows_source_kind,
    prioritize_region_codes,
    source_kind_label,
    summarize_query_history,
    ScanHistoryStore,
)
from search_plan import build_ordered_trip_dates, rank_route_pairs
from skyscanner_neo import (
    DEFAULT_REGIONS,
    NeoCli,
    build_effective_region_codes,
    detect_cdp_version,
    quotes_to_dicts,
    run_page_scan,
)


_POLL_INTERVAL_SECONDS = 0.2


def _serialize_path(path: Path | None) -> str | None:
    return str(path) if isinstance(path, Path) else None


def _serialize_alert_config(config: AlertConfig | None) -> dict[str, Any] | None:
    return asdict(config) if config is not None else None


def _serialize_history_record(record: Any) -> dict[str, Any]:
    return {
        "id": getattr(record, "id", None),
        "queryKey": getattr(record, "query_key", ""),
        "title": getattr(record, "title", ""),
        "createdAt": getattr(record, "created_at", ""),
        "isFavorite": bool(getattr(record, "is_favorite", False)),
        "label": _format_history_record(record),
        "queryPayload": deepcopy(getattr(record, "query_payload", {}) or {}),
    }


class DesktopUIService:
    def __init__(self) -> None:
        self._lock = threading.RLock()
        self.cli = SimpleCLI()
        self.history_store = ScanHistoryStore()
        self._cancel_event = threading.Event()
        self._state_path = get_gui_state_file()
        self._pending_retry_targets: dict[tuple[str, str, str], CombinedQuoteRow] = {}
        self._history_records_for_current_query: list[Any] = []
        self._previous_scan_record: Any | None = None
        self._current_alert_config: AlertConfig | None = None
        self._current_query_payload: dict[str, Any] | None = None
        self._display_rows: list[CombinedQuoteRow] = []
        self._rows_by_date: list[tuple[str, list[dict[str, Any]]]] = []
        self._quote_snapshots_by_date: list[tuple[str, list[dict[str, Any]]]] = []
        self._favorite_records: list[Any] = []
        self._recent_records: list[Any] = []
        self._current_output: Path | None = None
        self._environment_lines: list[str] = []
        self._logs: list[dict[str, str]] = []
        self._status_message = "就绪"
        self._busy = False
        self._last_error: str | None = None
        self._progress = {"step": 0, "total": 0, "date": "", "regionName": ""}
        self._last_auto_refresh_check_at = 0.0

        default_departure = (datetime.now() + timedelta(days=30)).date()
        default_return = default_departure + timedelta(days=7)
        self._form_state = _load_query_state(
            self._state_path,
            default_departure=default_departure.strftime("%Y-%m-%d"),
            default_return=default_return.strftime("%Y-%m-%d"),
        )
        self._refresh_history_lists()
        self._reset_derived_state()
        self._reload_current_alert_config()
        self._log("界面服务已启动。")

    def get_initial_state(self) -> dict[str, Any]:
        return self.get_ui_state()

    def get_ui_state(self) -> dict[str, Any]:
        self._maybe_run_auto_refresh()
        with self._lock:
            return self._snapshot_state_locked()

    def update_query_state(self, payload: dict[str, Any] | None = None) -> dict[str, Any]:
        with self._lock:
            self._apply_form_state_locked(payload or {})
            self._persist_query_state_locked()
            return self._snapshot_state_locked()

    def list_history(self) -> dict[str, Any]:
        with self._lock:
            self._refresh_history_lists()
            return {
                "favorites": [_serialize_history_record(record) for record in self._favorite_records],
                "recent": [_serialize_history_record(record) for record in self._recent_records],
            }

    def get_location_suggestions(
        self,
        field: str,
        query: str,
        options: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        field_name = "origin" if field != "destination" else "destination"
        current_form = deepcopy(self._form_state)
        if options:
            current_form.update(
                {
                    "exact_airport": bool(options.get("exactAirport", current_form["exact_airport"])),
                    "origin_country": bool(options.get("originCountry", current_form["origin_country"])),
                    "destination_country": bool(
                        options.get("destinationCountry", current_form["destination_country"])
                    ),
                }
            )
        use_country = current_form["origin_country"] if field_name == "origin" else current_form["destination_country"]
        prefer_metro = bool(options.get("preferMetro", not current_form["exact_airport"])) if options else not current_form["exact_airport"]
        suggestions = self._resolve_location_suggestions(
            field=field_name,
            value=query,
            use_country_mode=use_country,
            prefer_metro=prefer_metro,
        )
        return {
            "field": field_name,
            "items": [
                {
                    "name": item.name,
                    "code": item.code,
                    "kind": item.kind,
                    "label": self._format_location_suggestion(item),
                }
                for item in suggestions
            ],
        }

    def check_environment(self) -> dict[str, Any]:
        neo = NeoCli(self.cli.project_root)
        scrapling_ready = importlib.util.find_spec("scrapling") is not None
        cdp = detect_cdp_version()
        if cdp:
            cdp_line = f"浏览器/CDP 回退: {cdp.get('Browser', '已连接')}"
        else:
            cdp_line = "浏览器/CDP 回退: 未连接（仅影响已打开浏览器复用与失败市场自动兜底）"
        lines = [
            f"Scrapling 主抓取: {'已安装' if scrapling_ready else '未安装'}",
            f"Neo CLI: {'已找到' if neo.available else '未找到'}",
            cdp_line,
            f"项目目录: {self.cli.project_root}",
        ]
        issues = _collect_startup_issues()
        with self._lock:
            self._environment_lines = lines
            self._status_message = lines[0] if scrapling_ready else "主抓取环境未就绪"
            for line in lines:
                self._log_locked(line)
            return {
                "ok": scrapling_ready,
                "lines": list(lines),
                "issues": issues,
            }

    def open_link(self, url: str) -> bool:
        if not str(url).startswith("http"):
            return False
        webbrowser.open(str(url))
        return True

    def open_outputs(self) -> bool:
        output_dir = get_reports_dir()
        subprocess.run(["open", str(output_dir)], check=False)
        return True

    def export_decision_summary(self) -> dict[str, Any]:
        with self._lock:
            recommendations = _build_top_recommendations(
                self._display_rows,
                mode="cheapest",
                limit=3,
            )
            if not recommendations:
                raise ValueError("当前没有可导出的决策摘要。")
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
            csv_rows = [
                "rank,date,route,region,cheapest_cny,stability,reliability,source,link"
            ]
            for index, row in enumerate(recommendations, start=1):
                price = row.get("cheapest_cny_price")
                price_text = f"¥{float(price):,.2f}" if isinstance(price, (int, float)) else "-"
                link = str(row.get("link") or "-")
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
                            f"[打开结果页]({link})",
                        ]
                    )
                    + " |"
                )
                csv_rows.append(
                    ",".join(
                        [
                            str(index),
                            str(row.get("date") or "-"),
                            str(row.get("route") or "-"),
                            str(row.get("region_name") or "-"),
                            str(float(price)) if isinstance(price, (int, float)) else "",
                            str(row.get("stability_label") or "-"),
                            str(row.get("market_reliability_label") or "-"),
                            str(row.get("source_label") or source_kind_label(row.get("source_kind"))),
                            link,
                        ]
                    )
                )
            markdown_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
            csv_path.write_text("\n".join(csv_rows) + "\n", encoding="utf-8")
            self._log_locked(f"已导出决策摘要: {markdown_path.name} / {csv_path.name}")
            return {
                "markdownPath": str(markdown_path),
                "csvPath": str(csv_path),
            }

    def apply_history_record(self, record_id: int | str) -> dict[str, Any]:
        record = self._find_history_record(record_id)
        if record is None:
            raise ValueError("未找到对应的历史查询。")
        with self._lock:
            self._apply_history_record_locked(record)
            self._status_message = "已应用历史查询"
            self._log_locked(f"已载入历史查询: {getattr(record, 'title', '未命名查询')}")
            return self._snapshot_state_locked()

    def toggle_favorite_current_query(self, payload: dict[str, Any] | None = None) -> dict[str, Any]:
        with self._lock:
            if payload and isinstance(payload.get("form"), dict):
                self._apply_form_state_locked(payload["form"])
            query_payload = self._current_query_payload or self._build_fallback_current_query_payload_locked()
            is_favorite = self.history_store.toggle_favorite(query_payload)
            self._refresh_history_lists()
            if not is_favorite:
                self._apply_alert_config_locked(None)
            self._log_locked("已收藏当前查询。" if is_favorite else "已取消收藏当前查询。")
            return {
                "isFavorite": is_favorite,
                "history": self.list_history(),
            }

    def save_alert_config(self, payload: dict[str, Any]) -> dict[str, Any]:
        with self._lock:
            if isinstance(payload.get("form"), dict):
                self._apply_form_state_locked(payload["form"])
            query_payload = self._current_query_payload or self._build_fallback_current_query_payload_locked()
            target_price = self._parse_optional_positive_float(str(payload.get("targetPrice") or ""), "目标价")
            drop_amount = self._parse_optional_positive_float(str(payload.get("dropAmount") or ""), "再降提醒")
            auto_refresh_minutes = self._parse_optional_positive_int(
                str(payload.get("autoRefreshMinutes") or ""),
                "自动复扫",
            )
            if target_price is None and drop_amount is None and auto_refresh_minutes is None:
                raise ValueError("至少填写一个提醒条件或自动复扫间隔。")
            if not self.history_store.is_favorite_query_key(build_query_key(query_payload)):
                self.history_store.toggle_favorite(query_payload)
                self._refresh_history_lists()
            config = self.history_store.save_alert_config(
                query_payload,
                notifications_enabled=bool(payload.get("notificationsEnabled", True)),
                target_price=target_price,
                drop_amount=drop_amount,
                auto_refresh_minutes=auto_refresh_minutes,
                notify_on_recovery=bool(payload.get("notifyOnRecovery", True)),
                notify_on_new_low=bool(payload.get("notifyOnNewLow", True)),
            )
            self._apply_alert_config_locked(config)
            self._log_locked("已保存当前路线的提醒与自动复扫设置。")
            return {
                "config": _serialize_alert_config(config),
                "summary": self._format_alert_summary(config),
            }

    def clear_alert_config(self, payload: dict[str, Any] | None = None) -> dict[str, Any]:
        with self._lock:
            if payload and isinstance(payload.get("form"), dict):
                self._apply_form_state_locked(payload["form"])
            query_payload = self._current_query_payload
            if query_payload is not None:
                self.history_store.delete_alert_config(query_payload)
            self._apply_alert_config_locked(None)
            self._log_locked("已清除当前路线的提醒设置。")
            return {"ok": True}

    def queue_failure_region(self, payload: dict[str, Any]) -> dict[str, Any]:
        region_code = str(payload.get("regionCode") or "").strip().upper()
        if not region_code:
            raise ValueError("缺少可加入补扫队列的地区代码。")
        retry_key = (
            str(payload.get("date") or ""),
            str(payload.get("route") or ""),
            region_code,
        )
        row = {
            "date": payload.get("date") or "",
            "route": payload.get("route") or "",
            "region_code": region_code,
            "region_name": payload.get("regionName") or region_code,
        }
        with self._lock:
            self._pending_retry_targets[retry_key] = row
            self._log_locked(
                f"已加入补扫队列: {row.get('region_name') or region_code} "
                f"(当前待补扫 {len(self._pending_retry_targets)} 个)"
            )
            return {"queuedRegions": self._queued_retry_regions_locked()}

    def run_retry_queue(self) -> dict[str, Any]:
        with self._lock:
            queued_regions = self._queued_retry_regions_locked()
        if not queued_regions:
            raise ValueError("补扫队列为空。")
        self.start_scan(
            {
                "rerunScopeOverride": "selected_regions",
                "selectedRegionCodes": queued_regions,
                "allowBrowserFallback": False,
            }
        )
        with self._lock:
            self._pending_retry_targets = {}
        return {"queuedRegions": queued_regions}

    def cancel_scan(self) -> dict[str, Any]:
        with self._lock:
            self._cancel_event.set()
            self._status_message = "正在取消..."
            self._log_locked("正在取消当前扫描。")
            return {"ok": True}

    def start_scan(self, payload: dict[str, Any] | None = None) -> dict[str, Any]:
        payload = payload or {}
        with self._lock:
            if self._busy:
                raise RuntimeError("当前已有扫描任务正在运行。")
            if isinstance(payload.get("form"), dict):
                self._apply_form_state_locked(payload["form"])
            rerun_scope_override = str(payload.get("rerunScopeOverride") or "all")
            selected_region_codes = [
                str(code).strip().upper()
                for code in (payload.get("selectedRegionCodes") or [])
                if str(code).strip()
            ]
            allow_browser_fallback = bool(payload.get("allowBrowserFallback", True))
            origin = self._form_state["origin"].strip()
            destination = self._form_state["destination"].strip()
            date = self._form_state["date"].strip()
            trip_type = self._form_state["trip_type"]
            return_date = self._form_state["return_date"].strip() if trip_type == _TRIP_TYPE_ROUND_TRIP else None
            manual_regions = [
                code.strip().upper()
                for code in self._form_state["regions"].split(",")
                if code.strip()
            ]
            if not origin or not destination or not date:
                raise ValueError("请填写出发地、目的地和出发日期。")
            try:
                departure_value = datetime.strptime(date, "%Y-%m-%d")
            except ValueError as exc:
                raise ValueError("出发日期必须是 YYYY-MM-DD。") from exc
            if return_date:
                try:
                    return_value = datetime.strptime(return_date, "%Y-%m-%d")
                except ValueError as exc:
                    raise ValueError("返程日期必须是 YYYY-MM-DD。") from exc
                if return_value < departure_value:
                    raise ValueError("返程日期不能早于出发日期。")
            try:
                wait_seconds = int(self._form_state["wait"] or "10")
            except ValueError as exc:
                raise ValueError("等待秒数必须是整数。") from exc
            try:
                date_window_days = int(self._form_state["date_window"] or "0")
            except ValueError as exc:
                raise ValueError("±天数必须是非负整数。") from exc
            if date_window_days < 0:
                raise ValueError("±天数必须是非负整数。")

            if self._form_state["origin_country"] or self._form_state["destination_country"]:
                thread = self._prepare_expanded_scan_locked(
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
            else:
                origin_resolved = self.cli.resolve_location(
                    origin,
                    prefer_metro=not self._form_state["exact_airport"],
                )
                destination_resolved = self.cli.resolve_location(destination, prefer_metro=False)
                regions = build_effective_region_codes(
                    origin_country=origin_resolved.country,
                    destination_country=destination_resolved.country,
                    manual_region_codes=manual_regions,
                )
                if not regions:
                    raise ValueError("无法生成可用地区代码。")
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
                    exact_airport=bool(self._form_state["exact_airport"]),
                )
                self._prepare_common_scan_state_locked(
                    query_payload=query_payload,
                    rerun_scope_override=rerun_scope_override,
                    origin_label=origin,
                    destination_label=destination,
                )
                trip_label = format_trip_date_label(date, return_date)
                trip_mode_label = "往返" if return_date else "单程"
                self._log_locked(
                    f"开始比价: {origin} -> {destination}, {trip_mode_label} {trip_label} "
                    f"(±{date_window_days} 天), 地区: {', '.join(regions)} "
                    f"(实际代码 {origin_resolved.code} -> {destination_resolved.code})"
                )
                self._set_busy_locked(True)
                self._persist_query_state_locked()
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
                        bool(self._form_state["combined_summary"]),
                        query_payload,
                        rerun_scope_override,
                        selected_region_codes,
                        allow_browser_fallback,
                    ),
                    daemon=True,
                )

        thread.start()
        return {"ok": True}

    def _prepare_expanded_scan_locked(
        self,
        *,
        origin: str,
        destination: str,
        date: str,
        return_date: str | None,
        manual_regions: list[str],
        wait_seconds: int,
        date_window_days: int,
        rerun_scope_override: str,
        selected_region_codes: list[str],
        allow_browser_fallback: bool,
    ) -> threading.Thread:
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
            origin_is_country=self._form_state["origin_country"],
            destination_is_country=self._form_state["destination_country"],
            prefer_origin_metro=not self._form_state["exact_airport"],
            manual_region_codes=manual_regions,
            airport_limit=COUNTRY_ROUTE_DEFAULT_AIRPORT_LIMIT,
        )
        if not regions:
            raise ValueError("无法生成可用地区代码。")
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
            exact_airport=bool(self._form_state["exact_airport"]),
            origin_is_country=bool(self._form_state["origin_country"]),
            destination_is_country=bool(self._form_state["destination_country"]),
            airport_limit=COUNTRY_ROUTE_DEFAULT_AIRPORT_LIMIT,
        )
        self._prepare_common_scan_state_locked(
            query_payload=query_payload,
            rerun_scope_override=rerun_scope_override,
            origin_label=origin_label,
            destination_label=destination_label,
        )
        trip_label = format_trip_date_label(date, return_date)
        trip_mode_label = "往返" if return_date else "单程"
        mode_label = (
            f"{'国家' if self._form_state['origin_country'] else '地点'}"
            f"-{'国家' if self._form_state['destination_country'] else '地点'}"
        )
        self._log_locked(
            f"开始扩展比价[{mode_label}]: {origin_label} -> {destination_label}, "
            f"{trip_mode_label} {trip_label} (±{date_window_days} 天), 地区: {', '.join(regions)}"
        )
        self._log_locked(
            "出发候选机场: "
            + ", ".join(f"{airport.code}({airport.municipality or airport.name})" for airport in origin_points)
        )
        self._log_locked(
            "目的候选机场: "
            + ", ".join(
                f"{airport.code}({airport.municipality or airport.name})" for airport in destination_points
            )
        )
        self._set_busy_locked(True)
        self._persist_query_state_locked()
        return threading.Thread(
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
                bool(self._form_state["combined_summary"]),
                query_payload,
                rerun_scope_override,
                selected_region_codes,
                allow_browser_fallback,
            ),
            daemon=True,
        )

    def _prepare_common_scan_state_locked(
        self,
        *,
        query_payload: dict[str, Any],
        rerun_scope_override: str,
        origin_label: str,
        destination_label: str,
    ) -> None:
        self._current_query_payload = query_payload
        self._reload_current_alert_config()
        self._history_records_for_current_query = self.history_store.get_query_history(
            query_payload,
            limit=10,
        )
        self._previous_scan_record = self.history_store.get_latest_scan(query_payload)
        if rerun_scope_override == "failed_only" and self._previous_scan_record is None:
            rerun_scope_override = "all"
            self._log_locked("未找到历史记录，当前改为全量扫描。")
        self._clear_results_locked()
        self._cancel_event.clear()
        preview_record = self.history_store.get_cached_preview(query_payload)
        if preview_record is not None:
            self._set_display_rows_from_grouped_locked(preview_record.rows_by_date)
            self._status_message = "已显示预览缓存，正在刷新实时结果..."
            self._log_locked("已先展示最近 6 小时内的预览缓存，后台继续刷新实时结果。")
        else:
            self._status_message = "正在运行..."
        self._apply_cheapest_conclusion_locked(
            {
                "headline": "正在寻找最低价…",
                "price": "扫描进行中",
                "supporting": f"{origin_label} -> {destination_label}",
                "meta": "正在比较多个市场。",
                "insight": "扫描完成后，这里会汇总最低价、推荐方案和价格变化。",
                "link": None,
                "button_text": "等待结果",
            }
        )

    def _set_busy_locked(self, busy: bool) -> None:
        self._busy = busy
        if not busy:
            self._progress = {"step": 0, "total": 0, "date": "", "regionName": ""}

    def _persist_query_state_locked(self) -> None:
        _write_query_state(self._state_path, self._form_state)

    def _refresh_history_lists(self) -> None:
        self._favorite_records = self.history_store.get_favorites()
        self._recent_records = self.history_store.get_recent_queries()

    def _reload_current_alert_config(self) -> None:
        self._apply_alert_config_locked(
            self.history_store.get_alert_config(self._current_query_payload)
            if self._current_query_payload is not None
            else None
        )

    def _apply_alert_config_locked(self, config: AlertConfig | None) -> None:
        self._current_alert_config = config

    def _format_alert_summary(self, config: AlertConfig | None) -> str:
        if config is None:
            return "未设置提醒。可为当前路线设置目标价、降价阈值和自动复扫。"
        parts: list[str] = []
        if config.target_price is not None:
            parts.append(f"目标价 ≤ ¥{config.target_price:,.0f}")
        if config.drop_amount is not None:
            parts.append(f"再降 ≥ ¥{config.drop_amount:,.0f}")
        if config.auto_refresh_minutes is not None and config.auto_refresh_minutes > 0:
            parts.append(f"自动复扫 {config.auto_refresh_minutes} 分钟")
        if config.notifications_enabled:
            parts.append("桌面通知开启")
        return "；".join(parts) if parts else "当前路线已保存提醒，但未启用具体条件。"

    def _parse_optional_positive_float(self, raw: str, field_label: str) -> float | None:
        value = raw.strip()
        if not value:
            return None
        try:
            parsed = float(value)
        except ValueError as exc:
            raise ValueError(f"{field_label}必须是数字。") from exc
        if parsed <= 0:
            raise ValueError(f"{field_label}必须大于 0。")
        return parsed

    def _parse_optional_positive_int(self, raw: str, field_label: str) -> int | None:
        value = raw.strip()
        if not value or value == _AUTO_REFRESH_DISABLED:
            return None
        try:
            parsed = int(value)
        except ValueError as exc:
            raise ValueError(f"{field_label}必须是整数分钟。") from exc
        if parsed <= 0:
            raise ValueError(f"{field_label}必须大于 0。")
        return parsed

    def _apply_form_state_locked(self, payload: dict[str, Any]) -> None:
        normalized = _normalize_query_state(
            payload,
            default_departure=self._form_state["date"],
            default_return=self._form_state["return_date"],
        )
        self._form_state.update(normalized)

    def _find_history_record(self, record_id: int | str) -> Any | None:
        for record in [*self._favorite_records, *self._recent_records]:
            if str(getattr(record, "id", "")) == str(record_id):
                return record
            if str(getattr(record, "query_key", "")) == str(record_id):
                return record
        return None

    def _apply_history_record_locked(self, record: Any) -> None:
        payload = getattr(record, "query_payload", {}) or {}
        identity = payload.get("identity") or {}
        manual_regions = identity.get("manual_regions") or []
        self._form_state.update(
            {
                "origin": str(identity.get("origin_input") or identity.get("origin_label") or ""),
                "destination": str(identity.get("destination_input") or identity.get("destination_label") or ""),
                "date": str(identity.get("date") or self._form_state["date"]),
                "return_date": str(identity.get("return_date") or ""),
                "trip_type": _TRIP_TYPE_ROUND_TRIP if identity.get("return_date") else _TRIP_TYPE_ONE_WAY,
                "date_window": str(identity.get("date_window_days") or "0"),
                "regions": ",".join(str(code) for code in manual_regions if code),
                "exact_airport": bool(identity.get("exact_airport")),
                "origin_country": bool(identity.get("origin_is_country")),
                "destination_country": bool(identity.get("destination_is_country")),
            }
        )
        self._current_query_payload = payload if isinstance(payload, dict) else None
        self._persist_query_state_locked()
        self._reload_current_alert_config()

    def _field_uses_country_mode(self, field: str) -> bool:
        return self._form_state["origin_country"] if field == "origin" else self._form_state["destination_country"]

    def _format_location_suggestion(self, item: LocationRecord) -> str:
        if item.kind == "country":
            return f"{item.name} ({item.code}, 国家)"
        if item.kind == "metro":
            return f"{item.name} ({item.code}, 城市)"
        details = [part for part in [item.municipality, item.country] if part]
        suffix = f" - {' / '.join(details)}" if details else ""
        return f"{item.name} ({item.code}){suffix}"

    def _resolve_location_suggestions(
        self,
        *,
        field: str,
        value: str,
        use_country_mode: bool,
        prefer_metro: bool,
    ) -> list[LocationRecord]:
        if use_country_mode:
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

    def _set_location_hint_locked(
        self,
        field: str,
        label: str,
        value: str,
        prefer_metro: bool,
    ) -> str:
        raw = value.strip()
        if not raw:
            return ""
        if self._field_uses_country_mode(field):
            try:
                country = self.cli.resolve_country(raw)
                _resolved, airports = self.cli.location_resolver.get_country_route_airports(
                    raw,
                    limit=COUNTRY_ROUTE_DEFAULT_AIRPORT_LIMIT,
                )
                return (
                    f"{label}将使用国家代码: {country.code}；候选机场: "
                    + ", ".join(airport.code for airport in airports)
                )
            except ValueError as exc:
                return str(exc)
        try:
            code = self.cli.normalize_location(raw, prefer_metro=prefer_metro)
            kind = self.cli.location_resolver.describe_code_kind(code)
            return f"{label}将使用 {kind}: {code}"
        except ValueError as exc:
            return str(exc)

    def _compute_effective_regions_locked(self) -> list[str]:
        manual_regions = [
            code.strip().upper()
            for code in self._form_state["regions"].split(",")
            if code.strip()
        ]
        try:
            if self._form_state["origin_country"]:
                origin_country = self.cli.resolve_country(self._form_state["origin"]).code
            else:
                origin_country = self.cli.resolve_location(
                    self._form_state["origin"],
                    prefer_metro=not self._form_state["exact_airport"],
                ).country
            if self._form_state["destination_country"]:
                destination_country = self.cli.resolve_country(self._form_state["destination"]).code
            else:
                destination_country = self.cli.resolve_location(
                    self._form_state["destination"],
                    prefer_metro=False,
                ).country
        except ValueError:
            return build_effective_region_codes(manual_region_codes=manual_regions)
        return build_effective_region_codes(
            origin_country=origin_country,
            destination_country=destination_country,
            manual_region_codes=manual_regions,
        )

    def _build_fallback_current_query_payload_locked(self) -> dict[str, Any]:
        manual_regions = [
            code.strip().upper()
            for code in self._form_state["regions"].split(",")
            if code.strip()
        ]
        if self._form_state["origin_country"] or self._form_state["destination_country"]:
            return self.cli.build_expanded_query_payload(
                origin_value=self._form_state["origin"],
                destination_value=self._form_state["destination"],
                origin_label=self._form_state["origin"] or "出发地",
                destination_label=self._form_state["destination"] or "目的地",
                origin_file_token=self._form_state["origin"] or "-",
                destination_file_token=self._form_state["destination"] or "-",
                date=self._form_state["date"],
                return_date=self._form_state["return_date"] or None,
                date_window_days=int(self._form_state["date_window"] or "0"),
                manual_regions=manual_regions,
                effective_regions=manual_regions or list(DEFAULT_REGIONS),
                exact_airport=bool(self._form_state["exact_airport"]),
                origin_is_country=bool(self._form_state["origin_country"]),
                destination_is_country=bool(self._form_state["destination_country"]),
                airport_limit=COUNTRY_ROUTE_DEFAULT_AIRPORT_LIMIT,
            )
        return self.cli.build_point_query_payload(
            origin_input=self._form_state["origin"],
            destination_input=self._form_state["destination"],
            origin_label=self._form_state["origin"] or "出发地",
            destination_label=self._form_state["destination"] or "目的地",
            origin_code=self._form_state["origin"] or "-",
            destination_code=self._form_state["destination"] or "-",
            date=self._form_state["date"],
            return_date=self._form_state["return_date"] or None,
            date_window_days=int(self._form_state["date_window"] or "0"),
            manual_regions=manual_regions,
            effective_regions=manual_regions or list(DEFAULT_REGIONS),
            exact_airport=bool(self._form_state["exact_airport"]),
        )

    def _log(self, message: str) -> None:
        with self._lock:
            self._log_locked(message)

    def _log_locked(self, message: str) -> None:
        timestamp = datetime.now().strftime("%H:%M:%S")
        self._logs.append({"timestamp": timestamp, "message": message})
        self._logs = self._logs[-400:]

    def _reset_derived_state(self) -> None:
        self._cheapest_conclusion = _build_cheapest_conclusion([])
        self._recommendation_conclusion = _build_recommendation_payload([])
        self._top_recommendations: list[dict[str, Any]] = []
        self._calendar_payload: dict[str, Any] = {"kind": "empty", "cells": []}
        self._compare_rows: list[dict[str, str]] = []
        self._history_detail = "等待扫描后生成路线复盘。"
        self._success_rows: list[dict[str, Any]] = []
        self._failure_rows: list[dict[str, Any]] = []

    def _clear_results_locked(self) -> None:
        self._display_rows = []
        self._rows_by_date = []
        self._quote_snapshots_by_date = []
        self._last_error = None
        self._reset_derived_state()

    def _apply_cheapest_conclusion_locked(self, payload: dict[str, Any]) -> None:
        self._cheapest_conclusion = payload

    def _apply_recommendation_conclusion_locked(self, payload: dict[str, Any]) -> None:
        self._recommendation_conclusion = payload

    def _build_calendar_payload_locked(self, rows: list[CombinedQuoteRow]) -> dict[str, Any]:
        if not rows:
            return {"kind": "empty", "cells": []}
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
        if not return_dates:
            cells = []
            for departure in departures:
                winner = summary[departure]["__oneway__"]
                cells.append(
                    {
                        "tripLabel": departure,
                        "departure": departure,
                        "price": winner.get("cheapest_cny_price"),
                        "regionName": winner.get("region_name") or "-",
                    }
                )
            return {
                "kind": "one_way",
                "summaryText": _build_window_summary_text(rows, self._history_records_for_current_query),
                "cells": cells,
            }

        cells = []
        for departure in departures:
            for return_date in return_dates:
                winner = summary.get(departure, {}).get(return_date)
                cells.append(
                    {
                        "tripLabel": f"{departure} -> {return_date}",
                        "departure": departure,
                        "returnDate": return_date,
                        "price": winner.get("cheapest_cny_price") if winner else None,
                        "regionName": winner.get("region_name") if winner else None,
                    }
                )
        return {
            "kind": "round_trip",
            "summaryText": _build_window_summary_text(rows, self._history_records_for_current_query),
            "departures": departures,
            "returnDates": return_dates,
            "cells": cells,
        }

    def _format_history_detail_locked(self, history_records: list[Any]) -> str:
        if not history_records:
            return "暂无路线历史。完成至少一次扫描后，这里会展示历史最低价、成功市场和价格趋势。"
        summary = summarize_query_history(history_records)
        config = self.history_store.get_alert_config(history_records[0].query_payload)
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
            lines.append(f"最常成功市场: {stable_market}（成功 {stable_count}/{total_count}）")
        if summary.recent_prices:
            lines.append(
                f"最近价格走势: {_build_trend_sparkline(summary.recent_prices)} "
                f"({', '.join(f'¥{price:,.0f}' for price in summary.recent_prices)})"
            )
        if config is not None:
            lines.append(f"提醒设置: {self._format_alert_summary(config)}")
        return "\n".join(lines)

    def _refresh_result_views_locked(self) -> None:
        success_rows = [deepcopy(row) for row in self._display_rows if _row_has_price(row)]
        failure_rows = [deepcopy(row) for row in self._display_rows if not _row_has_price(row)]
        success_rows = _sort_combined_rows(success_rows)
        cheapest_highlight_signatures = _find_cheapest_highlight_signatures(success_rows)

        self._success_rows = []
        self._failure_rows = []
        for row in success_rows:
            row["isCheapestHighlight"] = _row_signature(row) in cheapest_highlight_signatures
            row["isChangedHighlight"] = str(row.get("delta_label") or "-") not in {"-", "持平", ""}
            self._success_rows.append(row)
        for row in failure_rows:
            row["isReuseReady"] = bool(row.get("can_reuse_page"))
            self._failure_rows.append(row)

    def _update_decision_views_locked(self) -> None:
        self._apply_recommendation_conclusion_locked(
            _build_recommendation_payload(
                self._display_rows,
                self._history_records_for_current_query,
            )
        )
        self._top_recommendations = [
            deepcopy(row)
            for row in _build_top_recommendations(
                self._display_rows,
                mode="cheapest",
                limit=_TOP_RECOMMENDATION_LIMIT,
            )
        ]
        self._calendar_payload = self._build_calendar_payload_locked(self._display_rows)
        previous_rows = []
        if self._previous_scan_record is not None:
            for trip_label, prior_rows in getattr(self._previous_scan_record, "rows_by_date", []) or []:
                for row in prior_rows:
                    previous_rows.append({"date": trip_label, **row})
        self._compare_rows = _build_compare_rows(self._display_rows, previous_rows)
        self._history_detail = self._format_history_detail_locked(self._history_records_for_current_query)

    def _set_display_rows_from_grouped_locked(
        self,
        rows_by_date: list[tuple[str, list[dict[str, Any]]]],
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
        self._refresh_result_views_locked()
        self._apply_cheapest_conclusion_locked(_build_cheapest_conclusion(self._display_rows))
        self._update_decision_views_locked()

    def _update_progress(self, *, step: int, total: int, date: str, region_name: str) -> None:
        with self._lock:
            self._progress = {
                "step": step,
                "total": total,
                "date": date,
                "regionName": region_name,
            }
            self._status_message = (
                f"正在扫描 {date} [{region_name}] "
                f"(attempts/expected: {step}/{total})"
            )

    def _update_partial_scan(
        self,
        *,
        rows_by_date: list[tuple[str, list[dict[str, Any]]]],
        status: str,
        log_message: str,
    ) -> None:
        with self._lock:
            self._set_display_rows_from_grouped_locked(rows_by_date)
            self._status_message = status
            if log_message:
                self._log_locked(log_message)

    def _handle_scan_done(self, payload: dict[str, Any]) -> None:
        with self._lock:
            self._set_busy_locked(False)
            outputs: list[Path] = payload.get("outputs") or []
            self._current_output = payload.get("combined_output") or (outputs[-1] if outputs else None)
            self._status_message = "完成"
            if self._current_query_payload is not None:
                self._history_records_for_current_query = self.history_store.get_query_history(
                    self._current_query_payload,
                    limit=10,
                )
            rows_by_date = payload["rows_by_date"]
            self._quote_snapshots_by_date = payload.get("quote_snapshots_by_date") or []
            self._set_display_rows_from_grouped_locked(rows_by_date)
            self._trigger_alert_notifications_locked(rows_by_date)
            self._reload_current_alert_config()
            combined_rows = list(self._display_rows)
            best_candidates = [
                row for row in combined_rows if isinstance(row.get("best_cny_price"), (int, float))
            ]
            cheapest_candidates = [
                row for row in combined_rows if isinstance(row.get("cheapest_cny_price"), (int, float))
            ]

            def _price_value(row: CombinedQuoteRow, key: str) -> float:
                value = row.get(key)
                return float(value) if isinstance(value, (int, float)) else float("inf")

            best_winner = min(best_candidates, key=lambda row: _price_value(row, "best_cny_price")) if best_candidates else None
            cheapest_winner = min(cheapest_candidates, key=lambda row: _price_value(row, "cheapest_cny_price")) if cheapest_candidates else None
            if best_winner and isinstance(best_winner.get("best_cny_price"), (int, float)):
                self._log_locked(
                    "最佳: ¥{price:,.2f} 来自 {region} ({date}, {route})".format(
                        price=float(best_winner["best_cny_price"]),
                        region=best_winner["region_name"],
                        date=best_winner.get("date") or "-",
                        route=best_winner.get("route") or "-",
                    )
                )
            if cheapest_winner and isinstance(cheapest_winner.get("cheapest_cny_price"), (int, float)):
                self._log_locked(
                    "最低价: ¥{price:,.2f} 来自 {region} ({date}, {route})".format(
                        price=float(cheapest_winner["cheapest_cny_price"]),
                        region=cheapest_winner["region_name"],
                        date=cheapest_winner.get("date") or "-",
                        route=cheapest_winner.get("route") or "-",
                    )
                )
            elif combined_rows:
                self._log_locked("已提取市场价格，但人民币换算暂不可用。")
            else:
                self._log_locked("没有可展示的市场结果。")
            if outputs:
                self._log_locked(f"单日结果已保存: {len(outputs)} 份。")
            combined_output = payload.get("combined_output")
            if combined_output:
                self._log_locked(f"汇总结果已保存: {combined_output}")
            if self._current_output:
                self._log_locked(f"最新结果文件: {self._current_output}")
            self._refresh_history_lists()

    def _handle_scan_error(self, message: str) -> None:
        with self._lock:
            self._set_busy_locked(False)
            self._status_message = "失败"
            self._last_error = message
            self._apply_cheapest_conclusion_locked(
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
            self._log_locked(f"失败: {message}")

    def _handle_cancelled(self) -> None:
        with self._lock:
            self._set_busy_locked(False)
            self._status_message = "已取消"
            self._apply_cheapest_conclusion_locked(
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
            self._log_locked("扫描已被用户取消。")

    def _trigger_alert_notifications_locked(
        self,
        rows_by_date: list[tuple[str, list[dict[str, Any]]]],
    ) -> None:
        query_payload = self._current_query_payload
        if query_payload is None:
            return
        config = self.history_store.get_alert_config(query_payload)
        if config is None or not config.notifications_enabled:
            self._apply_alert_config_locked(config)
            return
        current_rows = [{"date": trip_label, **row} for trip_label, rows in rows_by_date for row in rows]
        priced_rows = [row for row in current_rows if isinstance(row.get("cheapest_cny_price"), (int, float))]
        previous_rows = []
        if self._previous_scan_record is not None:
            previous_rows = [
                {"date": trip_label, **row}
                for trip_label, rows in getattr(self._previous_scan_record, "rows_by_date", []) or []
                for row in rows
            ]
        notification_lines: list[str] = []
        notify_price: float | None = None
        if priced_rows:
            winner = min(priced_rows, key=_decision_price_key)
            winner_price = float(winner["cheapest_cny_price"])
            notify_price = winner_price
            if config.target_price is not None and winner_price <= config.target_price:
                should_notify = (
                    config.last_notified_price is None
                    or winner_price < config.last_notified_price - 0.01
                )
                if should_notify:
                    notification_lines.append(
                        f"达到目标价：{winner.get('region_name') or '-'} ¥{winner_price:,.2f}"
                    )
            previous_priced = [
                row for row in previous_rows if isinstance(row.get("cheapest_cny_price"), (int, float))
            ]
            if config.drop_amount is not None and previous_priced:
                previous_winner = min(previous_priced, key=_decision_price_key)
                previous_price = float(previous_winner["cheapest_cny_price"])
                if previous_price - winner_price >= config.drop_amount:
                    notification_lines.append(f"较上次再降 ¥{previous_price - winner_price:,.2f}")
            if config.notify_on_new_low and self._history_records_for_current_query:
                previous_history = self._history_records_for_current_query[1:]
                previous_summary = summarize_query_history(previous_history) if previous_history else None
                previous_low = previous_summary.history_low_price if previous_summary is not None else None
                if previous_low is not None and winner_price < previous_low - 0.01:
                    notification_lines.append(f"刷新历史新低：¥{winner_price:,.2f}")

        if config.notify_on_recovery:
            previous_success = any(
                isinstance(row.get("cheapest_cny_price"), (int, float))
                for row in previous_rows
            )
            current_success = bool(priced_rows)
            if current_success and not previous_success and previous_rows:
                notification_lines.append("失败市场已恢复为可用价格结果")

        if notification_lines:
            title = f"机票提醒：{build_query_title(query_payload)}"
            message = "；".join(notification_lines[:3])
            delivered = _send_desktop_notification(title, message)
            self._log_locked(f"已触发提醒: {message}")
            if not delivered:
                self._log_locked("桌面通知发送失败，已仅记录在日志。")
            self.history_store.mark_alert_notified(
                query_payload,
                last_notified_price=notify_price,
            )
            self._apply_alert_config_locked(self.history_store.get_alert_config(query_payload))

    def _queued_retry_regions_locked(self) -> list[str]:
        return sorted(
            {
                str(row.get("region_code") or "").strip().upper()
                for row in self._pending_retry_targets.values()
                if str(row.get("region_code") or "").strip()
            }
        )

    def _snapshot_state_locked(self) -> dict[str, Any]:
        origin_hint = self._set_location_hint_locked(
            "origin",
            "出发地",
            self._form_state["origin"],
            prefer_metro=not self._form_state["exact_airport"],
        )
        destination_hint = self._set_location_hint_locked(
            "destination",
            "目的地",
            self._form_state["destination"],
            prefer_metro=False,
        )
        effective_regions = self._compute_effective_regions_locked()
        regions_hint = f"默认包含 {','.join(DEFAULT_REGIONS)}；本次实际地区: {', '.join(effective_regions)}"
        return {
            "form": deepcopy(self._form_state),
            "hints": {
                "origin": origin_hint,
                "destination": destination_hint,
                "regions": regions_hint,
                "effectiveRegions": effective_regions,
            },
            "status": {
                "message": self._status_message,
                "busy": self._busy,
                "error": self._last_error,
                "progress": deepcopy(self._progress),
            },
            "environment": {
                "lines": list(self._environment_lines),
            },
            "logs": list(self._logs),
            "history": {
                "favorites": [_serialize_history_record(record) for record in self._favorite_records],
                "recent": [_serialize_history_record(record) for record in self._recent_records],
                "historyDetail": self._history_detail,
            },
            "alerts": {
                "config": _serialize_alert_config(self._current_alert_config),
                "summary": self._format_alert_summary(self._current_alert_config),
                "pendingRetryRegions": self._queued_retry_regions_locked(),
            },
            "results": {
                "cheapestConclusion": deepcopy(self._cheapest_conclusion),
                "recommendationConclusion": deepcopy(self._recommendation_conclusion),
                "topRecommendations": deepcopy(self._top_recommendations),
                "calendar": deepcopy(self._calendar_payload),
                "compareRows": deepcopy(self._compare_rows),
                "successRows": deepcopy(self._success_rows),
                "failureRows": deepcopy(self._failure_rows),
                "displayRows": deepcopy(self._display_rows),
                "rowsByDate": deepcopy(self._rows_by_date),
                "quoteSnapshotsByDate": deepcopy(self._quote_snapshots_by_date),
            },
            "outputs": {
                "currentOutput": _serialize_path(self._current_output),
                "reportsDir": str(get_reports_dir()),
            },
        }

    def _maybe_run_auto_refresh(self) -> None:
        now = time.monotonic()
        with self._lock:
            if self._busy or now - self._last_auto_refresh_check_at < 30:
                return
            self._last_auto_refresh_check_at = now
        due_configs = self.history_store.get_due_auto_refresh_configs(limit=1)
        if not due_configs:
            return
        config = due_configs[0]
        self.history_store.mark_alert_auto_refreshed(config.query_payload)
        self._log(f"自动复扫触发: {config.title}")
        self._run_saved_query_scan(config.query_payload, reason="自动复扫")

    def _run_saved_query_scan(self, query_payload: dict[str, Any], *, reason: str) -> None:
        record = self.history_store.get_latest_scan(query_payload)
        if record is None:
            record = type(
                "_PseudoRecord",
                (),
                {"query_payload": query_payload, "title": build_query_title(query_payload)},
            )()
        with self._lock:
            self._apply_history_record_locked(record)
            self._log_locked(f"{reason}: {build_query_title(query_payload)}")
        self.start_scan()

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
            trip_dates = build_ordered_trip_dates(date, return_date, date_window_days)
            latest_record = self.history_store.get_latest_scan(query_payload)
            normalized_selected_codes = {code.strip().upper() for code in selected_region_codes if code.strip()}
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

            total_steps = max(sum(_region_count_for_trip(trip_label) for trip_label in trip_labels), 1)
            step = 0
            rows_progress: list[tuple[str, list[dict[str, Any]]]] = []
            quote_progress: list[tuple[str, list[dict[str, Any]]]] = []
            self._update_progress(step=0, total=total_steps, date="", region_name="")

            async def orchestrate() -> tuple[
                list[tuple[str, list[dict[str, Any]]]],
                list[tuple[str, list[dict[str, Any]]]],
                list[Path],
            ]:
                nonlocal step, rows_progress, quote_progress
                progress_lock = asyncio.Lock()
                date_semaphore = asyncio.Semaphore(_GUI_DATE_WINDOW_CONCURRENCY)

                async def update_partial_view(
                    trip_label: str,
                    rows: list[dict[str, Any]],
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
                        self._update_partial_scan(
                            rows_by_date=list(rows_progress),
                            status=status,
                            log_message=log_message,
                        )

                async def scan_trip(
                    date_idx: int,
                    current_date: str,
                    current_return_date: str | None,
                ) -> tuple[int, str, list[dict[str, Any]], list[dict[str, Any]], Path | None]:
                    async with date_semaphore:
                        if self._cancel_event.is_set():
                            raise asyncio.CancelledError
                        trip_label = format_trip_date_label(current_date, current_return_date)

                        def on_region_start(region: Any, _trip_label: str = trip_label) -> None:
                            nonlocal step
                            step += 1
                            self._update_progress(
                                step=step,
                                total=total_steps,
                                date=_trip_label,
                                region_name=region.name,
                            )

                        self._log(f"开始扫描行程 {trip_label}。")
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
                            quote_dicts = [
                                quote for quote in (progress_payload.get("quotes") or []) if isinstance(quote, dict)
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
                            merged_rows_by_date = merge_rows_by_date(cached_rows_by_date, live_rows_by_date)
                            merged_quotes_by_date = merge_quotes_by_date(
                                cached_quotes_by_date,
                                [(trip_label, quote_dicts)],
                            )
                            partial_rows = get_rows_for_trip_label(merged_rows_by_date, trip_label)
                            partial_quotes = get_quotes_for_trip_label(merged_quotes_by_date, trip_label)
                            stage = str(progress_payload.get("stage") or "").strip().lower()
                            completed_regions = progress_payload.get("completed_regions") or []
                            status_map = {
                                "preview_cache": f"{trip_label} 预览缓存已展示。",
                                "quick_live": f"{trip_label} 已返回高优先级市场结果，正在补全其余市场...",
                                "background_live": f"{trip_label} 正在后台补全其余市场...",
                                "region_update": f"{trip_label} 正在扫描 ({len(completed_regions)}/{total_steps})...",
                                "final": f"{trip_label} 已完成，继续处理其余日期...",
                            }
                            log_map = {
                                "preview_cache": f"{trip_label} 已展示预览缓存。",
                                "quick_live": f"{trip_label} 已先刷新高优先级市场的实时结果。",
                                "background_live": f"{trip_label} 已补充更多市场的实时结果。",
                                "region_update": f"{trip_label} 正在扫描...",
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
                            fetch_pipeline="balanced",
                        )
                        rows = get_rows_for_trip_label(merged_rows_by_date, trip_label)
                        quote_snapshots = get_quotes_for_trip_label(merged_quotes_by_date, trip_label)
                        if not rows:
                            self._log(f"行程 {trip_label} 未返回结果，请检查地区或环境。")
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
                collected = []
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
            self._handle_scan_done(
                {
                    "rows_by_date": rows_by_date,
                    "quote_snapshots_by_date": quote_snapshots_by_date,
                    "outputs": outputs,
                    "combined_output": combined_output,
                }
            )
        except asyncio.CancelledError:
            self._handle_cancelled()
        except Exception as exc:
            if self._cancel_event.is_set():
                self._handle_cancelled()
            else:
                self._handle_scan_error(str(exc))

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
            trip_dates = build_ordered_trip_dates(date, return_date, date_window_days)
            latest_record = self.history_store.get_latest_scan(query_payload)
            normalized_selected_codes = {code.strip().upper() for code in selected_region_codes if code.strip()}
            trip_labels = [
                format_trip_date_label(current_date, current_return_date)
                for current_date, current_return_date in trip_dates
            ]
            pair_specs = rank_route_pairs(
                origin_points,
                destination_points,
                latest_record.rows_by_date if latest_record is not None else None,
            )
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

            total_steps = max(sum(_region_count_for_trip(trip_label) * pair_count for trip_label in trip_labels), 1)
            step = 0
            rows_progress: list[tuple[str, list[dict[str, Any]]]] = []
            quote_progress: list[tuple[str, list[dict[str, Any]]]] = []
            self._update_progress(step=0, total=total_steps, date="", region_name="")

            async def orchestrate() -> tuple[
                list[tuple[str, list[dict[str, Any]]]],
                list[tuple[str, list[dict[str, Any]]]],
                list[Path],
            ]:
                nonlocal step, rows_progress, quote_progress
                progress_lock = asyncio.Lock()
                date_semaphore = asyncio.Semaphore(_GUI_DATE_WINDOW_CONCURRENCY)

                async def update_partial_view(
                    trip_label: str,
                    rows: list[dict[str, Any]],
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
                        self._update_partial_scan(
                            rows_by_date=list(rows_progress),
                            status=status,
                            log_message=log_message,
                        )

                async def scan_trip(
                    date_idx: int,
                    current_date: str,
                    current_return_date: str | None,
                ) -> tuple[int, str, list[dict[str, Any]], list[dict[str, Any]], Path | None]:
                    async with date_semaphore:
                        if self._cancel_event.is_set():
                            raise asyncio.CancelledError
                        trip_label = format_trip_date_label(current_date, current_return_date)
                        self._log(f"开始扫描行程 {trip_label}。")
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

                        def build_rows_snapshot() -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
                            live_rows_by_date = annotate_rows_with_history(
                                [
                                    (
                                        trip_label,
                                        self.cli._sort_simplified_rows(list(best_rows_by_region.values())),
                                    )
                                ],
                                latest_record.rows_by_date if latest_record else None,
                            )
                            merged_rows_by_date = merge_rows_by_date(cached_rows_by_date, live_rows_by_date)
                            rows = get_rows_for_trip_label(merged_rows_by_date, trip_label)
                            return rows, self.cli.rows_to_quote_snapshots(rows)

                        pair_semaphore = asyncio.Semaphore(_GUI_AIRPORT_PAIR_CONCURRENCY)

                        async def scan_pair(
                            origin_airport: LocationRecord,
                            destination_airport: LocationRecord,
                        ) -> tuple[str, list[dict[str, Any]]]:
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
                                    self._update_progress(
                                        step=step,
                                        total=total_steps,
                                        date=_trip_label,
                                        region_name=f"{region.name} / {_route_label}",
                                    )

                                async def on_pair_progress(progress_payload: dict[str, Any]) -> None:
                                    if self._cancel_event.is_set():
                                        raise asyncio.CancelledError
                                    quote_dicts = [
                                        quote
                                        for quote in (progress_payload.get("quotes") or [])
                                        if isinstance(quote, dict)
                                    ]
                                    if not quote_dicts:
                                        return
                                    rows = self.cli.simplify_quotes(
                                        quote_dicts,
                                        route_label=route_label,
                                    )
                                    for row in rows:
                                        region_name = str(row.get("region_name") or "-")
                                        best_rows_by_region[region_name] = self.cli._pick_better_row(
                                            best_rows_by_region.get(region_name),
                                            row,
                                        )
                                    partial_rows, partial_quotes = build_rows_snapshot()
                                    stage = str(progress_payload.get("stage") or "").strip().lower()
                                    completed_regions = progress_payload.get("completed_regions") or []
                                    status_map = {
                                        "preview_cache": f"{trip_label} / {route_label} 预览缓存已展示。",
                                        "quick_live": f"{trip_label} / {route_label} 已返回高优先级市场结果，正在补全其余市场...",
                                        "background_live": f"{trip_label} / {route_label} 正在后台补全其余市场...",
                                        "region_update": (
                                            f"{trip_label} / {route_label} 已完成 {len(completed_regions)} "
                                            "个市场，正在更新当前最优结果..."
                                        ),
                                        "final": f"{trip_label} / {route_label} 已完成，正在比较候选航段...",
                                    }
                                    log_map = {
                                        "preview_cache": f"{trip_label} / {route_label} 已展示预览缓存。",
                                        "quick_live": f"{trip_label} / {route_label} 已先刷新高优先级市场。",
                                        "background_live": f"{trip_label} / {route_label} 已补充更多市场。",
                                        "region_update": f"{trip_label} / {route_label} 已刷新一个市场结果。",
                                        "final": f"{trip_label} / {route_label} 已完成实时刷新。",
                                    }
                                    await update_partial_view(
                                        trip_label,
                                        partial_rows,
                                        partial_quotes,
                                        status=status_map.get(stage, f"{trip_label} / {route_label} 正在刷新结果..."),
                                        log_message=log_map.get(stage, f"{trip_label} / {route_label} 正在刷新结果。"),
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
                                    on_progress=on_pair_progress,
                                    allow_browser_fallback=allow_browser_fallback,
                                    fetch_pipeline="balanced",
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
                                        log_message=f"{trip_label} 已刷新 {completed_pairs}/{pair_count} 个候选航段。",
                                    )
                        except asyncio.CancelledError:
                            for pair_task in pair_tasks:
                                pair_task.cancel()
                            await asyncio.gather(*pair_tasks, return_exceptions=True)
                            raise

                        rows, quote_snapshots = build_rows_snapshot()
                        if not rows:
                            self._log(f"行程 {trip_label} 未拿到可展示的扩展路线结果。")
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
                collected = []
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
            self._handle_scan_done(
                {
                    "rows_by_date": rows_by_date,
                    "quote_snapshots_by_date": quote_snapshots_by_date,
                    "outputs": outputs,
                    "combined_output": combined_output,
                }
            )
        except asyncio.CancelledError:
            self._handle_cancelled()
        except Exception as exc:
            if self._cancel_event.is_set():
                self._handle_cancelled()
            else:
                self._handle_scan_error(str(exc))
