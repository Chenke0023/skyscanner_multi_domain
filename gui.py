"""
Tkinter GUI for non-technical users.

Launch:
  python gui.py
"""

from __future__ import annotations

import asyncio
import calendar
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
from skyscanner_neo import (
    DEFAULT_REGIONS,
    NeoCli,
    build_effective_region_codes,
    ensure_cdp_ready,
    quotes_to_dicts,
    run_page_scan,
)


MAX_LOCATION_SUGGESTIONS = 8

_COLUMN_LABELS = {
    "date": "日期",
    "route": "航段",
    "region": "地区",
    "best_native": "最佳（原币）",
    "best_cny": "最佳（人民币）",
    "cheapest_native": "最低价（原币）",
    "cheapest_cny": "最低价（人民币）",
    "status": "状态",
    "error": "错误",
    "link": "链接",
}
_PRICE_COLUMNS = {"best_native", "best_cny", "cheapest_native", "cheapest_cny"}

# ── Classic Mac OS 8/9 Platinum Theme ──────────────────────────────────
_PLATINUM = "#DDDDDD"
_PLATINUM_DARK = "#BBBBBB"
_PLATINUM_LIGHT = "#EEEEEE"
_BUTTON_FACE = "#CCCCCC"
_HIGHLIGHT = "#336699"
_FONT_BODY = ("Geneva", 11)
_FONT_TITLE = ("Geneva", 20, "bold")
_FONT_BUTTON = ("Geneva", 12, "bold")
_FONT_HEADING = ("Geneva", 11, "bold")
_FONT_MONO = ("Monaco", 10)
_FONT_CARD_PRICE = ("Geneva", 18, "bold")
_FONT_CARD_HEADLINE = ("Geneva", 12, "bold")
_TRIP_TYPE_ONE_WAY = "one_way"
_TRIP_TYPE_ROUND_TRIP = "round_trip"
_CARD_BG = "#F6E7B1"
_CARD_BORDER = "#8A7331"
_CARD_PRICE = "#234423"
_REQUIRED_APIFY_DATA_FILES = (
    "browser-helper-file.json",
    "fingerprint-network-definition.zip",
    "header-network-definition.zip",
    "headers-order.json",
    "input-network-definition.zip",
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
                float(row.get("cheapest_cny_price")),  # type: ignore[arg-type]
                str(row.get("date") or ""),
                str(row.get("region_name") or ""),
            ),
        )
        winner = sorted_rows[0]
        runner_up = sorted_rows[1] if len(sorted_rows) > 1 else None
        winner_price = float(winner["cheapest_cny_price"])  # type: ignore[index]
        delta_text = "当前只有 1 条可比较的最低价结果。"
        if runner_up is not None:
            runner_up_price = float(runner_up["cheapest_cny_price"])  # type: ignore[index]
            delta = runner_up_price - winner_price
            if delta >= 0.01:
                delta_text = f"比下一低价再省 ¥{delta:,.2f}。"
            else:
                delta_text = "与下一低价几乎持平。"
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
    """Apply a Classic Mac OS 8/9 Platinum appearance."""
    root.configure(bg=_PLATINUM)
    root.option_add("*Background", _PLATINUM)
    root.option_add("*Foreground", "#000000")
    root.option_add("*selectBackground", _HIGHLIGHT)
    root.option_add("*selectForeground", "#FFFFFF")

    style = ttk.Style()
    style.theme_use("clam")

    style.configure(
        ".", background=_PLATINUM, foreground="#000000",
        font=_FONT_BODY, borderwidth=1,
    )
    style.configure("TFrame", background=_PLATINUM)
    style.configure("TLabel", background=_PLATINUM, font=_FONT_BODY)
    style.configure(
        "TLabelframe", background=_PLATINUM,
        relief="groove", borderwidth=2,
    )
    style.configure(
        "TLabelframe.Label", background=_PLATINUM,
        font=_FONT_HEADING, foreground="#000000",
    )
    style.configure(
        "TEntry", fieldbackground="#FFFFFF", font=_FONT_BODY,
        borderwidth=2, relief="sunken",
    )
    style.configure(
        "TButton", background=_BUTTON_FACE, font=_FONT_BUTTON,
        borderwidth=2, relief="raised", padding=(10, 4),
    )
    style.map(
        "TButton",
        background=[("active", _PLATINUM_LIGHT), ("pressed", _PLATINUM_DARK)],
        relief=[("pressed", "sunken")],
    )
    style.configure("TCheckbutton", background=_PLATINUM, font=_FONT_BODY)
    style.configure("TRadiobutton", background=_PLATINUM, font=_FONT_BODY)
    style.configure(
        "Treeview", background="#FFFFFF", fieldbackground="#FFFFFF",
        font=_FONT_BODY, rowheight=22, borderwidth=1,
    )
    style.configure(
        "Treeview.Heading", font=_FONT_HEADING,
        background=_BUTTON_FACE, relief="raised", borderwidth=1,
    )
    style.map("Treeview.Heading", background=[("active", _PLATINUM_LIGHT)])
    style.configure(
        "Horizontal.TProgressbar",
        background=_HIGHLIGHT, troughcolor=_PLATINUM_DARK, borderwidth=1,
    )


def _draw_pinstripes(canvas: tk.Canvas, _event: tk.Event | None = None) -> None:
    """Draw classic Mac OS horizontal pinstripes on a canvas."""
    canvas.delete("stripe")
    w = canvas.winfo_width()
    h = canvas.winfo_height()
    for y in range(0, h, 2):
        fill = _PLATINUM_DARK if y % 4 == 0 else _PLATINUM_LIGHT
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

        container = ttk.Frame(self, padding=12)
        container.pack(fill=tk.BOTH, expand=True)

        header = ttk.Frame(container)
        header.pack(fill=tk.X)
        ttk.Button(header, text="‹ 上月", command=self._show_previous_month).pack(
            side=tk.LEFT
        )
        ttk.Label(
            header,
            textvariable=self._month_label_var,
            font=_FONT_HEADING,
        ).pack(side=tk.LEFT, expand=True)
        ttk.Button(header, text="下月 ›", command=self._show_next_month).pack(
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
        ttk.Button(footer, text="今天", command=self._choose_today).pack(side=tk.LEFT)
        ttk.Button(footer, text="取消", command=self.destroy).pack(side=tk.RIGHT)

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
                    fg="#000000" if is_current_month else "#777777",
                    relief="raised",
                    borderwidth=2,
                    disabledforeground="#999999",
                    command=lambda current=day_value: self._select_date(current),
                )
                if day_value == self._selected_date and is_selectable:
                    button.configure(bg=_HIGHLIGHT, fg="#FFFFFF", relief="sunken")
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
        self.root.geometry("980x720")
        self.root.minsize(920, 640)

        self.cli = SimpleCLI()
        self.queue: queue.Queue[tuple[str, Any]] = queue.Queue()
        self._cancel_event = threading.Event()
        self.current_output: Path | None = None
        self._sort_state: dict[str, bool] = {}
        self._state_save_job: str | None = None
        self._cheapest_link: str | None = None
        self._state_path = get_gui_state_file()

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
        self.cheapest_card_headline_var = tk.StringVar(value="")
        self.cheapest_card_price_var = tk.StringVar(value="")
        self.cheapest_card_supporting_var = tk.StringVar(value="")
        self.cheapest_card_meta_var = tk.StringVar(value="")
        self.cheapest_card_insight_var = tk.StringVar(value="")
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
        if self._state_path.exists():
            self.log("已恢复上次查询条件。")
        self._poll_queue()

    def _build_ui(self) -> None:
        outer = ttk.Frame(self.root, padding=16)
        outer.pack(fill=tk.BOTH, expand=True)

        # ── Classic Mac pinstripe banner ──
        stripe_bar = tk.Canvas(outer, height=8, bg=_PLATINUM, highlightthickness=0)
        stripe_bar.pack(fill=tk.X, pady=(0, 6))
        stripe_bar.bind("<Configure>", lambda e: _draw_pinstripes(stripe_bar, e))

        title = ttk.Label(
            outer, text="\u2318  Skyscanner 多市场比价", font=_FONT_TITLE,
        )
        title.pack(anchor="w")
        subtitle = ttk.Label(
            outer,
            text="优先直连抓取结果页；失败市场会自动回退到本机 Edge 读取。",
            font=_FONT_BODY,
        )
        subtitle.pack(anchor="w", pady=(4, 2))

        stripe_bar2 = tk.Canvas(outer, height=4, bg=_PLATINUM, highlightthickness=0)
        stripe_bar2.pack(fill=tk.X, pady=(2, 10))
        stripe_bar2.bind("<Configure>", lambda e: _draw_pinstripes(stripe_bar2, e))

        form = ttk.LabelFrame(outer, text="查询参数", padding=12)
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
            button_row, text="检查环境", command=self.check_environment
        )
        self.doctor_button.pack(side=tk.LEFT, padx=(0, 8))
        self.run_button = ttk.Button(
            button_row, text="开始比价", command=self.start_scan
        )
        self.run_button.pack(side=tk.LEFT)

        status = ttk.LabelFrame(outer, text="状态", padding=12)
        status.pack(fill=tk.X, pady=(12, 0))
        status_top = ttk.Frame(status)
        status_top.pack(fill=tk.X)
        ttk.Label(status_top, textvariable=self.status_var).pack(anchor="w", side=tk.LEFT)
        self.cancel_button = ttk.Button(
            status_top, text="取消", command=self._cancel_scan
        )
        self.cancel_button.pack(side=tk.RIGHT)
        self.cancel_button.pack_forget()
        self.progress_bar = ttk.Progressbar(status, mode="determinate", length=400)
        self.progress_bar.pack(fill=tk.X, pady=(6, 0))
        self.progress_bar.pack_forget()

        results = ttk.LabelFrame(outer, text="结果", padding=12)
        results.pack(fill=tk.BOTH, expand=True, pady=(12, 0))

        conclusion = tk.Frame(
            results,
            bg=_CARD_BG,
            relief="groove",
            borderwidth=2,
            highlightthickness=1,
            highlightbackground=_CARD_BORDER,
            padx=14,
            pady=12,
        )
        conclusion.pack(fill=tk.X, pady=(0, 10))
        conclusion_left = tk.Frame(conclusion, bg=_CARD_BG)
        conclusion_left.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        tk.Label(
            conclusion_left,
            text="最低价结论",
            bg=_CARD_BG,
            fg="#5B4A12",
            font=_FONT_HEADING,
        ).pack(anchor="w")
        tk.Label(
            conclusion_left,
            textvariable=self.cheapest_card_headline_var,
            bg=_CARD_BG,
            fg="#000000",
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
            fg="#2F2F2F",
            font=_FONT_HEADING,
        ).pack(anchor="w", pady=(2, 0))
        tk.Label(
            conclusion_left,
            textvariable=self.cheapest_card_meta_var,
            bg=_CARD_BG,
            fg="#2F2F2F",
            font=_FONT_BODY,
        ).pack(anchor="w", pady=(6, 0))
        tk.Label(
            conclusion_left,
            textvariable=self.cheapest_card_insight_var,
            bg=_CARD_BG,
            fg="#4F4F4F",
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
        )
        self.cheapest_card_button.pack(anchor="e")

        ttk.Label(
            results,
            text="完整价格列表会保留在下方，最低价行会高亮显示。",
            foreground="#555555",
        ).pack(anchor="w", pady=(0, 6))

        columns = (
            "date",
            "route",
            "region",
            "best_native",
            "best_cny",
            "cheapest_native",
            "cheapest_cny",
            "status",
            "error",
            "link",
        )
        self.tree = ttk.Treeview(results, columns=columns, show="headings", height=10)
        for col, label in _COLUMN_LABELS.items():
            self.tree.heading(
                col, text=label, command=lambda c=col: self._sort_column(c)
            )
        self.tree.column("date", width=180, anchor="w")
        self.tree.column("route", width=120, anchor="w")
        self.tree.column("region", width=110, anchor="w")
        self.tree.column("best_native", width=120, anchor="e")
        self.tree.column("best_cny", width=120, anchor="e")
        self.tree.column("cheapest_native", width=120, anchor="e")
        self.tree.column("cheapest_cny", width=120, anchor="e")
        self.tree.column("status", width=100, anchor="w")
        self.tree.column("error", width=220, anchor="w")
        self.tree.column("link", width=240, anchor="w")
        self.tree.tag_configure("cheapest_highlight", background="#FFF2B6")
        self.tree.pack(fill=tk.BOTH, expand=True)
        self.tree.bind("<Double-1>", self._on_tree_double_click)

        actions = ttk.Frame(results)
        actions.pack(fill=tk.X, pady=(8, 0))
        ttk.Button(actions, text="打开结果文件夹", command=self.open_outputs).pack(
            side=tk.LEFT
        )

        logs = ttk.LabelFrame(outer, text="运行日志", padding=12)
        logs.pack(fill=tk.BOTH, expand=True, pady=(12, 0))
        self.log_text = tk.Text(
            logs, height=10, wrap="word", font=_FONT_MONO,
            bg="#FFFFFF", fg="#000000", insertbackground="#000000",
            relief="sunken", borderwidth=2,
        )
        self.log_text.pack(fill=tk.BOTH, expand=True)
        self.log("界面已启动。可先点“检查环境”确认主抓取与回退环境，再开始比价。")

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
                font=_FONT_BODY, bg="#FFFFFF", fg="#000000",
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
            hint_label = ttk.Label(cell, textvariable=hint_var, foreground="#555555")
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

    def _open_cheapest_link(self) -> None:
        if self._cheapest_link and self._cheapest_link.startswith("http"):
            webbrowser.open(self._cheapest_link)

    def log(self, message: str) -> None:
        timestamp = datetime.now().strftime("%H:%M:%S")
        self.log_text.insert(tk.END, f"[{timestamp}] {message}\n")
        self.log_text.see(tk.END)

    def clear_results(self) -> None:
        for item in self.tree.get_children():
            self.tree.delete(item)
        self._sort_state.clear()
        for col, label in _COLUMN_LABELS.items():
            self.tree.heading(col, text=label)
        self._apply_cheapest_conclusion(_build_cheapest_conclusion([]))

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
        columns = ("date", "route", "region", "best_native", "best_cny",
                    "cheapest_native", "cheapest_cny", "status", "error", "link")
        if col_index < 0 or col_index >= len(columns):
            return
        if columns[col_index] == "link":
            url = self.tree.set(item, "link")
            if url and url.startswith("http"):
                webbrowser.open(url)

    def check_environment(self) -> None:
        neo = NeoCli(self.cli.project_root)
        scrapling_ready = importlib.util.find_spec("scrapling") is not None
        try:
            cdp = ensure_cdp_ready(wait_timeout=8)
            cdp_line = f"Edge/CDP 回退: {cdp.get('Browser', '已连接')}"
        except RuntimeError as exc:
            cdp = None
            cdp_line = f"Edge/CDP 回退: 未连接（仅影响失败市场自动兜底）({exc})"
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
                "Scrapling 主抓取可用，但未检测到 Edge/CDP。大多数扫描仍可运行，只是失败市场无法自动回退。",
            )
        else:
            messagebox.showinfo(
                "环境已就绪", "主抓取与 Edge/CDP 回退均可用，可以开始比价。"
            )

    def start_scan(self) -> None:
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

        self.clear_results()
        self._cancel_event.clear()
        self.set_busy(True)
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

        self.clear_results()
        self._cancel_event.clear()
        self.set_busy(True)
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
            total_steps = len(trip_dates) * len(regions)
            step = 0
            rows_by_date: list[tuple[str, list[dict[str, str | float | None]]]] = []
            outputs: list[Path] = []

            self.queue.put(("progress_init", total_steps))

            for date_idx, (current_date, current_return_date) in enumerate(trip_dates):
                if self._cancel_event.is_set():
                    self.queue.put(("cancelled", None))
                    return
                trip_label = format_trip_date_label(current_date, current_return_date)

                def on_region_start(
                    region: Any,
                    _trip_label: str = trip_label,
                    _di: int = date_idx,
                ) -> None:
                    nonlocal step
                    step += 1
                    self.queue.put((
                        "progress",
                        {
                            "step": step,
                            "total": total_steps,
                            "date": _trip_label,
                            "region_name": region.name,
                        },
                    ))

                self.queue.put(("log", f"开始扫描行程 {trip_label}。"))
                quotes = asyncio.run(
                    run_page_scan(
                        origin=origin_code,
                        destination=destination_code,
                        date=current_date,
                        region_codes=regions,
                        return_date=current_return_date,
                        page_wait=wait_seconds,
                        timeout=30,
                        transport="scrapling",
                        on_region_start=on_region_start,
                    )
                )

                if self._cancel_event.is_set():
                    self.queue.put(("cancelled", None))
                    return

                if not quotes:
                    rows_by_date.append((trip_label, []))
                    self.queue.put(
                        ("log", f"行程 {trip_label} 未返回结果，请检查地区或环境。")
                    )
                    continue

                quote_dicts = quotes_to_dicts(quotes)
                output = self.cli.save_results(
                    quote_dicts,
                    origin_code,
                    destination_code,
                    current_date,
                    return_date=current_return_date,
                    route_label=f"{origin_code} -> {destination_code}",
                )
                outputs.append(output)
                rows = self.cli.simplify_quotes(
                    quote_dicts,
                    route_label=f"{origin_code} -> {destination_code}",
                )
                rows_by_date.append((trip_label, rows))

            combined_output = None
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
            pair_count = len(origin_points) * len(destination_points)
            total_steps = len(trip_dates) * len(regions) * pair_count
            step = 0
            rows_by_date: list[tuple[str, list[dict[str, str | float | None]]]] = []
            outputs: list[Path] = []

            self.queue.put(("progress_init", total_steps))

            for current_date, current_return_date in trip_dates:
                if self._cancel_event.is_set():
                    self.queue.put(("cancelled", None))
                    return
                trip_label = format_trip_date_label(current_date, current_return_date)
                self.queue.put(("log", f"开始扫描行程 {trip_label}。"))
                best_rows_by_region: dict[str, CombinedQuoteRow] = {}

                for origin_airport in origin_points:
                    for destination_airport in destination_points:
                        if self._cancel_event.is_set():
                            self.queue.put(("cancelled", None))
                            return

                        route_label = f"{origin_airport.code} -> {destination_airport.code}"

                        def on_region_start(
                            region: Any,
                            _trip_label: str = trip_label,
                            _route_label: str = route_label,
                        ) -> None:
                            nonlocal step
                            step += 1
                            self.queue.put((
                                "progress",
                                {
                                    "step": step,
                                    "total": total_steps,
                                    "date": _trip_label,
                                    "region_name": f"{region.name} / {_route_label}",
                                },
                            ))

                        quotes = asyncio.run(
                            run_page_scan(
                                origin=origin_airport.code,
                                destination=destination_airport.code,
                                date=current_date,
                                region_codes=regions,
                                return_date=current_return_date,
                                page_wait=wait_seconds,
                                timeout=30,
                                transport="scrapling",
                                on_region_start=on_region_start,
                            )
                        )
                        if not quotes:
                            continue

                        rows = self.cli.simplify_quotes(
                            quotes_to_dicts(quotes),
                            route_label=route_label,
                        )
                        for row in rows:
                            region_name = str(row.get("region_name") or "-")
                            best_rows_by_region[region_name] = self.cli._pick_better_row(
                                best_rows_by_region.get(region_name),
                                row,
                            )

                rows = self.cli._sort_simplified_rows(list(best_rows_by_region.values()))
                rows_by_date.append((trip_label, rows))
                output = self.cli.save_simplified_results(
                    rows,
                    origin_label,
                    destination_label,
                    current_date,
                    return_date=current_return_date,
                    file_origin_token=origin_file_token,
                    file_destination_token=destination_file_token,
                )
                outputs.append(output)

            combined_output = None
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
        except Exception as exc:
            self.queue.put(("error", str(exc)))

    def _poll_queue(self) -> None:
        try:
            while True:
                kind, payload = self.queue.get_nowait()
                if kind == "scan_done":
                    self._handle_scan_done(payload)
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
        self.clear_results()
        rows_by_date: list[tuple[str, list[dict[str, str | float | None]]]] = payload[
            "rows_by_date"
        ]
        combined_rows: list[CombinedQuoteRow] = []
        for row_date, rows in rows_by_date:
            for row in rows:
                combined_rows.append({"date": row_date, **row})

        cheapest_highlight_signatures = _find_cheapest_highlight_signatures(combined_rows)

        for row in combined_rows:
            row_date = str(row.get("date") or "-")
            row_signature = _row_signature(row)
            row_tags = ("cheapest_highlight",) if row_signature in cheapest_highlight_signatures else ()
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
                    row_date,
                    row.get("route") or "-",
                    row["region_name"],
                    row.get("best_display_price") or "-",
                    best_cny_text,
                    row.get("cheapest_display_price") or "-",
                    cheapest_cny_text,
                    row.get("status") or "-",
                    row.get("error") or "-",
                    row["link"],
                ),
                tags=row_tags,
            )

        self._apply_cheapest_conclusion(_build_cheapest_conclusion(combined_rows))

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
