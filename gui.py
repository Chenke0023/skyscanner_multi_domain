"""
Tkinter GUI for non-technical users.

Launch:
  python gui.py
"""

from __future__ import annotations

import asyncio
import queue
import threading
from datetime import datetime, timedelta
from pathlib import Path
import tkinter as tk
from tkinter import messagebox, ttk
from typing import Any

from app_paths import get_reports_dir
from cli import SimpleCLI
from skyscanner_neo import (
    DEFAULT_REGIONS,
    NeoCli,
    ensure_cdp_ready,
    quotes_to_dicts,
    run_page_scan,
)


class App:
    def __init__(self, root: tk.Tk) -> None:
        self.root = root
        self.root.title("Skyscanner 多市场比价")
        self.root.geometry("980x720")
        self.root.minsize(920, 640)

        self.cli = SimpleCLI()
        self.queue: queue.Queue[tuple[str, Any]] = queue.Queue()
        self.current_output: Path | None = None

        default_date = (datetime.now() + timedelta(days=30)).strftime("%Y-%m-%d")

        self.origin_var = tk.StringVar(value="北京")
        self.destination_var = tk.StringVar(value="阿拉木图")
        self.date_var = tk.StringVar(value=default_date)
        self.regions_var = tk.StringVar(value=",".join(DEFAULT_REGIONS))
        self.wait_var = tk.StringVar(value="10")
        self.exact_airport_var = tk.BooleanVar(value=False)
        self.status_var = tk.StringVar(value="就绪")
        self.origin_hint_var = tk.StringVar(value="")
        self.destination_hint_var = tk.StringVar(value="")

        self._build_ui()
        self.origin_var.trace_add("write", self._refresh_location_hints)
        self.destination_var.trace_add("write", self._refresh_location_hints)
        self.exact_airport_var.trace_add("write", self._refresh_location_hints)
        self._refresh_location_hints()
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
            form, "出发地", self.origin_var, 0, 0, hint_var=self.origin_hint_var
        )
        self._add_labeled_entry(
            form,
            "目的地",
            self.destination_var,
            0,
            1,
            hint_var=self.destination_hint_var,
        )
        self._add_labeled_entry(form, "日期", self.date_var, 0, 2)
        self._add_labeled_entry(form, "地区代码", self.regions_var, 1, 0, colspan=2)
        self._add_labeled_entry(form, "等待秒数", self.wait_var, 1, 2)

        ttk.Checkbutton(
            form,
            text="严格机场代码（例如北京不自动转成 BJSA）",
            variable=self.exact_airport_var,
        ).grid(row=2, column=0, columnspan=2, sticky="w", pady=(8, 0))

        button_row = ttk.Frame(form)
        button_row.grid(row=2, column=2, sticky="e")
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

        columns = ("region", "price_cny", "link")
        self.tree = ttk.Treeview(results, columns=columns, show="headings", height=10)
        self.tree.heading("region", text="地区")
        self.tree.heading("price_cny", text="价格（人民币）")
        self.tree.heading("link", text="链接")
        self.tree.column("region", width=120, anchor="w")
        self.tree.column("price_cny", width=140, anchor="e")
        self.tree.column("link", width=520, anchor="w")
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
        ttk.Entry(cell, textvariable=var).pack(fill=tk.X, expand=True)
        if hint_var is not None:
            ttk.Label(cell, textvariable=hint_var, foreground="#666666").pack(
                anchor="w", pady=(4, 0)
            )
        parent.columnconfigure(column, weight=1)

    def _set_location_hint(
        self, hint_var: tk.StringVar, label: str, value: str, prefer_metro: bool
    ) -> None:
        raw = value.strip()
        if not raw:
            hint_var.set("")
            return
        try:
            code = self.cli.normalize_location(raw, prefer_metro=prefer_metro)
            kind = "城市代码" if len(code) == 4 else "机场代码"
            hint_var.set(f"{label}将使用 {kind}: {code}")
        except ValueError as exc:
            hint_var.set(str(exc))

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

    def log(self, message: str) -> None:
        timestamp = datetime.now().strftime("%H:%M:%S")
        self.log_text.insert(tk.END, f"[{timestamp}] {message}\n")
        self.log_text.see(tk.END)

    def clear_results(self) -> None:
        for item in self.tree.get_children():
            self.tree.delete(item)

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
        regions = [
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

        if not regions:
            messagebox.showerror("地区为空", "请至少填写一个地区代码。")
            return

        try:
            wait_seconds = int(self.wait_var.get() or "10")
        except ValueError:
            messagebox.showerror("等待秒数错误", "等待秒数必须是整数。")
            return

        try:
            origin_code = self.cli.normalize_location(
                origin, prefer_metro=not self.exact_airport_var.get()
            )
            destination_code = self.cli.normalize_location(
                destination, prefer_metro=False
            )
        except ValueError as exc:
            messagebox.showerror("地点无法识别", str(exc))
            return

        self.clear_results()
        self.set_busy(True)
        self.status_var.set("正在运行...")
        self.log(
            f"开始比价: {origin} -> {destination}, {date}, 地区: {', '.join(regions)} "
            f"(实际代码 {origin_code} -> {destination_code})"
        )

        thread = threading.Thread(
            target=self._run_scan_worker,
            args=(origin_code, destination_code, date, regions, wait_seconds),
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
    ) -> None:
        try:
            quotes = asyncio.run(
                run_page_scan(
                    origin=origin_code,
                    destination=destination_code,
                    date=date,
                    region_codes=regions,
                    page_wait=wait_seconds,
                    timeout=30,
                )
            )
            quote_dicts = quotes_to_dicts(quotes)
            output = self.cli.save_results(
                quote_dicts, origin_code, destination_code, date
            )
            self.queue.put(
                (
                    "scan_done",
                    {
                        "quotes": quote_dicts,
                        "output": output,
                        "origin_code": origin_code,
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
        except queue.Empty:
            pass
        self.root.after(200, self._poll_queue)

    def _handle_scan_done(self, payload: dict[str, Any]) -> None:
        self.set_busy(False)
        self.current_output = payload["output"]
        self.status_var.set("完成")
        quotes = payload["quotes"]
        rows = self.cli.simplify_quotes(quotes)
        self.clear_results()
        for row in rows:
            self.tree.insert(
                "",
                tk.END,
                values=(
                    row["region_name"],
                    f"¥{row['cny_price']:,.2f}",
                    row["link"],
                ),
            )

        winner = rows[0] if rows else None
        if winner:
            self.log(
                f"最低价: ¥{winner['cny_price']:,.2f} 来自 {winner['region_name']}"
            )
        else:
            self.log("没有可展示的人民币价格结果。")
        self.log(f"结果已保存: {self.current_output}")

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
