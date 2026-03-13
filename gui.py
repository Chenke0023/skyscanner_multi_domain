"""
Tkinter GUI for non-technical users.

Launch:
  python gui.py
"""

from __future__ import annotations

import asyncio
import queue
import re
import threading
from datetime import datetime, timedelta
from pathlib import Path
import tkinter as tk
from tkinter import messagebox, ttk
from typing import Any

from app_paths import get_reports_dir
from cli import CombinedQuoteRow, SimpleCLI
from date_window import build_date_window
from location_resolver import LocationRecord
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


class App:
    def __init__(self, root: tk.Tk) -> None:
        self.root = root
        self.root.title("Skyscanner 多市场比价")
        self.root.geometry("980x720")
        self.root.minsize(920, 640)

        self.cli = SimpleCLI()
        self.queue: queue.Queue[tuple[str, Any]] = queue.Queue()
        self.current_output: Path | None = None
        self._sort_state: dict[str, bool] = {}

        default_date = (datetime.now() + timedelta(days=30)).strftime("%Y-%m-%d")

        self.origin_var = tk.StringVar(value="北京")
        self.destination_var = tk.StringVar(value="阿拉木图")
        self.date_var = tk.StringVar(value=default_date)
        self.regions_var = tk.StringVar(value="")
        self.wait_var = tk.StringVar(value="10")
        self.date_window_var = tk.StringVar(value="3")
        self.exact_airport_var = tk.BooleanVar(value=False)
        self.combined_summary_var = tk.BooleanVar(value=True)
        self.status_var = tk.StringVar(value="就绪")
        self.origin_hint_var = tk.StringVar(value="")
        self.destination_hint_var = tk.StringVar(value="")
        self.regions_hint_var = tk.StringVar(value="")
        self.location_entries: dict[str, ttk.Entry] = {}
        self.location_listboxes: dict[str, tk.Listbox] = {}
        self.location_hint_labels: dict[str, ttk.Label] = {}
        self.location_suggestion_values: dict[str, list[LocationRecord]] = {
            "origin": [],
            "destination": [],
        }

        self._build_ui()
        self.origin_var.trace_add("write", self._refresh_location_hints)
        self.destination_var.trace_add("write", self._refresh_location_hints)
        self.origin_var.trace_add("write", self._refresh_origin_suggestions)
        self.destination_var.trace_add("write", self._refresh_destination_suggestions)
        self.regions_var.trace_add("write", self._refresh_location_hints)
        self.exact_airport_var.trace_add("write", self._refresh_location_hints)
        self.exact_airport_var.trace_add("write", self._refresh_origin_suggestions)
        self._refresh_location_hints()
        self._refresh_origin_suggestions()
        self._refresh_destination_suggestions()
        self._poll_queue()

    def _build_ui(self) -> None:
        outer = ttk.Frame(self.root, padding=16)
        outer.pack(fill=tk.BOTH, expand=True)

        title = ttk.Label(
            outer, text="Skyscanner 多市场比价", font=("SF Pro Display", 20, "bold")
        )
        title.pack(anchor="w")
        subtitle = ttk.Label(
            outer,
            text="基于本机 Edge 结果页直接读取价格，适合不懂命令行的同事直接使用。",
        )
        subtitle.pack(anchor="w", pady=(4, 14))

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
        self._add_labeled_entry(form, "日期", self.date_var, 0, 2)
        self._add_labeled_entry(
            form,
            "额外地区代码",
            self.regions_var,
            1,
            0,
            colspan=2,
            hint_var=self.regions_hint_var,
        )
        self._add_labeled_entry(form, "等待秒数", self.wait_var, 1, 2)
        self._add_labeled_entry(form, "±天数", self.date_window_var, 2, 0)

        ttk.Checkbutton(
            form,
            text="保存多日期汇总",
            variable=self.combined_summary_var,
        ).grid(row=2, column=1, sticky="w", pady=(8, 0))

        ttk.Checkbutton(
            form,
            text="严格机场代码（例如北京不自动转成 BJSA）",
            variable=self.exact_airport_var,
        ).grid(row=3, column=0, columnspan=2, sticky="w", pady=(8, 0))

        button_row = ttk.Frame(form)
        button_row.grid(row=3, column=2, sticky="e")
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
        ttk.Label(status, textvariable=self.status_var).pack(anchor="w")

        results = ttk.LabelFrame(outer, text="结果", padding=12)
        results.pack(fill=tk.BOTH, expand=True, pady=(12, 0))

        columns = (
            "date",
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
        self.tree.column("date", width=100, anchor="w")
        self.tree.column("region", width=110, anchor="w")
        self.tree.column("best_native", width=120, anchor="e")
        self.tree.column("best_cny", width=120, anchor="e")
        self.tree.column("cheapest_native", width=120, anchor="e")
        self.tree.column("cheapest_cny", width=120, anchor="e")
        self.tree.column("status", width=100, anchor="w")
        self.tree.column("error", width=220, anchor="w")
        self.tree.column("link", width=240, anchor="w")
        self.tree.pack(fill=tk.BOTH, expand=True)

        actions = ttk.Frame(results)
        actions.pack(fill=tk.X, pady=(8, 0))
        ttk.Button(actions, text="打开结果文件夹", command=self.open_outputs).pack(
            side=tk.LEFT
        )

        logs = ttk.LabelFrame(outer, text="运行日志", padding=12)
        logs.pack(fill=tk.BOTH, expand=True, pady=(12, 0))
        self.log_text = tk.Text(logs, height=10, wrap="word")
        self.log_text.pack(fill=tk.BOTH, expand=True)
        self.log("界面已启动。先点“检查环境”，确认 Edge/CDP 正常。")

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
                cell, height=0, activestyle="none", exportselection=False
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
            hint_label = ttk.Label(cell, textvariable=hint_var, foreground="#666666")
            hint_label.pack(anchor="w", pady=(4, 0))
            if location_field is not None:
                self.location_hint_labels[location_field] = hint_label
        parent.columnconfigure(column, weight=1)

    def _format_location_suggestion(self, item: LocationRecord) -> str:
        if item.kind == "metro":
            return f"{item.name} ({item.code}, 城市)"
        details = [part for part in [item.municipality, item.country] if part]
        suffix = f" - {' / '.join(details)}" if details else ""
        return f"{item.name} ({item.code}){suffix}"

    def _get_location_suggestions(
        self, value: str, *, prefer_metro: bool
    ) -> list[LocationRecord]:
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
            self.origin_var.get(), prefer_metro=not self.exact_airport_var.get()
        )
        self._set_location_suggestions("origin", suggestions)

    def _refresh_destination_suggestions(self, *args: object) -> None:
        suggestions = self._get_location_suggestions(
            self.destination_var.get(), prefer_metro=False
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
        self, hint_var: tk.StringVar, label: str, value: str, prefer_metro: bool
    ) -> None:
        raw = value.strip()
        if not raw:
            hint_var.set("")
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
            origin = self.cli.resolve_location(
                self.origin_var.get(), prefer_metro=not self.exact_airport_var.get()
            )
            destination = self.cli.resolve_location(
                self.destination_var.get(), prefer_metro=False
            )
        except ValueError:
            return build_effective_region_codes(manual_region_codes=manual_regions)
        return build_effective_region_codes(
            origin_country=origin.country,
            destination_country=destination.country,
            manual_region_codes=manual_regions,
        )

    def _refresh_location_hints(self, *args: object) -> None:
        self._set_location_hint(
            self.origin_hint_var,
            "出发地",
            self.origin_var.get(),
            prefer_metro=not self.exact_airport_var.get(),
        )
        self._set_location_hint(
            self.destination_hint_var,
            "目的地",
            self.destination_var.get(),
            prefer_metro=False,
        )
        effective_regions = self._compute_effective_regions()
        self.regions_hint_var.set(
            f"默认包含 {','.join(DEFAULT_REGIONS)}；本次实际地区: {', '.join(effective_regions)}"
        )

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

    def check_environment(self) -> None:
        neo = NeoCli(self.cli.project_root)
        try:
            cdp = ensure_cdp_ready(wait_timeout=8)
            cdp_line = f"Edge/CDP 9222: {cdp.get('Browser', '已连接')}"
        except RuntimeError as exc:
            cdp = None
            cdp_line = f"Edge/CDP 9222: 未连接 ({exc})"
        lines = [
            f"Neo CLI: {'已找到' if neo.available else '未找到'}",
            cdp_line,
            f"项目目录: {self.cli.project_root}",
        ]
        self.status_var.set(lines[1])
        for line in lines:
            self.log(line)
        if not cdp:
            messagebox.showwarning("环境未就绪", cdp_line)
        else:
            messagebox.showinfo(
                "环境已就绪", "已检测到可用的 Edge/CDP 9222，可以开始比价。"
            )

    def start_scan(self) -> None:
        origin = self.origin_var.get().strip()
        destination = self.destination_var.get().strip()
        date = self.date_var.get().strip()
        manual_regions = [
            code.strip().upper()
            for code in self.regions_var.get().split(",")
            if code.strip()
        ]

        if not origin or not destination or not date:
            messagebox.showerror("参数不完整", "请填写出发地、目的地和日期。")
            return

        try:
            datetime.strptime(date, "%Y-%m-%d")
        except ValueError:
            messagebox.showerror("日期格式错误", "日期必须是 YYYY-MM-DD。")
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
        self.set_busy(True)
        self.status_var.set("正在运行...")
        self.log(
            f"开始比价: {origin} -> {destination}, {date} (±{date_window_days} 天), "
            f"地区: {', '.join(regions)} "
            f"(实际代码 {origin_resolved.code} -> {destination_resolved.code})"
        )

        thread = threading.Thread(
            target=self._run_scan_worker,
            args=(
                origin_resolved.code,
                destination_resolved.code,
                date,
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
        regions: list[str],
        wait_seconds: int,
        date_window_days: int,
        save_combined: bool,
    ) -> None:
        try:
            date_list = build_date_window(date, date_window_days)
            rows_by_date: list[tuple[str, list[dict[str, str | float | None]]]] = []
            outputs: list[Path] = []

            for current_date in date_list:
                self.queue.put(("status", f"正在扫描 {current_date}..."))
                self.queue.put(("log", f"开始扫描日期 {current_date}。"))
                quotes = asyncio.run(
                    run_page_scan(
                        origin=origin_code,
                        destination=destination_code,
                        date=current_date,
                        region_codes=regions,
                        page_wait=wait_seconds,
                        timeout=30,
                        transport="scrapling",
                    )
                )
                if not quotes:
                    rows_by_date.append((current_date, []))
                    self.queue.put(
                        ("log", f"日期 {current_date} 未返回结果，请检查地区或环境。")
                    )
                    continue

                quote_dicts = quotes_to_dicts(quotes)
                output = self.cli.save_results(
                    quote_dicts, origin_code, destination_code, current_date
                )
                outputs.append(output)
                rows = self.cli.simplify_quotes(quote_dicts)
                rows_by_date.append((current_date, rows))

            combined_output = None
            if save_combined and rows_by_date:
                start_date = date_list[0]
                end_date = date_list[-1]
                combined_output = self.cli.save_window_results(
                    rows_by_date, origin_code, destination_code, start_date, end_date
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
                        row["region_name"],
                        row.get("best_display_price") or "-",
                        best_cny_text,
                        row.get("cheapest_display_price") or "-",
                        cheapest_cny_text,
                        row.get("status") or "-",
                        row.get("error") or "-",
                        row["link"],
                    ),
                )

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
                    "最佳: ¥{price:,.2f} 来自 {region} ({date})".format(
                        price=float(best_price),
                        region=best_winner["region_name"],
                        date=best_winner.get("date") or "-",
                    )
                )
        if cheapest_winner:
            cheapest_price = cheapest_winner.get("cheapest_cny_price")
            if isinstance(cheapest_price, (int, float)):
                self.log(
                    "最低价: ¥{price:,.2f} 来自 {region} ({date})".format(
                        price=float(cheapest_price),
                        region=cheapest_winner["region_name"],
                        date=cheapest_winner.get("date") or "-",
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
    root = tk.Tk()
    style = ttk.Style()
    try:
        style.theme_use("aqua")
    except tk.TclError:
        pass
    App(root)
    root.mainloop()


if __name__ == "__main__":
    main()
