from __future__ import annotations

import os
from pathlib import Path
from typing import Any
from urllib.parse import quote

from app_paths import PROJECT_ROOT
from desktop_ui_service import DesktopUIService


def _frontend_index_path() -> Path:
    return PROJECT_ROOT / "webui" / "dist" / "index.html"


class DesktopBridge:
    def __init__(self) -> None:
        self.service = DesktopUIService()

    def get_initial_state(self) -> dict[str, Any]:
        return self.service.get_initial_state()

    def get_ui_state(self) -> dict[str, Any]:
        return self.service.get_ui_state()

    def update_query_state(self, payload: dict[str, Any] | None = None) -> dict[str, Any]:
        return self.service.update_query_state(payload)

    def get_location_suggestions(
        self,
        field: str,
        query: str,
        options: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        return self.service.get_location_suggestions(field, query, options)

    def start_scan(self, payload: dict[str, Any] | None = None) -> dict[str, Any]:
        return self.service.start_scan(payload)

    def cancel_scan(self) -> dict[str, Any]:
        return self.service.cancel_scan()

    def check_environment(self) -> dict[str, Any]:
        return self.service.check_environment()

    def open_link(self, url: str) -> bool:
        return self.service.open_link(url)

    def open_outputs(self) -> bool:
        return self.service.open_outputs()

    def export_decision_summary(self) -> dict[str, Any]:
        return self.service.export_decision_summary()

    def list_history(self) -> dict[str, Any]:
        return self.service.list_history()

    def apply_history_record(self, record_id: int | str) -> dict[str, Any]:
        return self.service.apply_history_record(record_id)

    def toggle_favorite_current_query(self, payload: dict[str, Any] | None = None) -> dict[str, Any]:
        return self.service.toggle_favorite_current_query(payload)

    def save_alert_config(self, payload: dict[str, Any]) -> dict[str, Any]:
        return self.service.save_alert_config(payload)

    def clear_alert_config(self, payload: dict[str, Any] | None = None) -> dict[str, Any]:
        return self.service.clear_alert_config(payload)

    def queue_failure_region(self, payload: dict[str, Any]) -> dict[str, Any]:
        return self.service.queue_failure_region(payload)

    def run_retry_queue(self) -> dict[str, Any]:
        return self.service.run_retry_queue()


def _error_page_uri(title: str, body: str, detail: str = "") -> str:
    html = f"""<!doctype html>
<html lang="zh-CN">
  <head>
    <meta charset="utf-8" />
    <meta name="viewport" content="width=device-width, initial-scale=1" />
    <title>{title}</title>
    <style>
      :root {{
        color-scheme: light;
        --bg: #efe6d6;
        --paper: rgba(255, 252, 247, 0.88);
        --ink: #2c251e;
        --muted: #6d6257;
        --accent: #8c6a30;
        --line: rgba(101, 82, 58, 0.16);
      }}
      * {{ box-sizing: border-box; }}
      body {{
        margin: 0;
        min-height: 100vh;
        display: grid;
        place-items: center;
        font-family: "Avenir Next", "PingFang SC", sans-serif;
        color: var(--ink);
        background:
          radial-gradient(circle at top left, rgba(255, 248, 232, 0.92), transparent 28%),
          linear-gradient(180deg, #f4ecdd 0%, var(--bg) 38%, #e3d7c3 100%);
      }}
      main {{
        width: min(720px, calc(100vw - 48px));
        padding: 32px;
        border-radius: 28px;
        border: 1px solid var(--line);
        background: var(--paper);
        box-shadow: 0 30px 90px rgba(63, 44, 18, 0.1);
      }}
      .eyebrow {{
        margin: 0 0 12px;
        color: var(--accent);
        letter-spacing: 0.18em;
        text-transform: uppercase;
        font-size: 12px;
      }}
      h1 {{ margin: 0; font-size: 34px; line-height: 1.02; }}
      p {{ margin: 14px 0 0; color: var(--muted); line-height: 1.65; }}
      pre {{
        margin: 18px 0 0;
        padding: 16px 18px;
        border-radius: 18px;
        border: 1px solid var(--line);
        background: rgba(242, 235, 224, 0.8);
        white-space: pre-wrap;
        word-break: break-word;
        color: var(--ink);
      }}
    </style>
  </head>
  <body>
    <main>
      <p class="eyebrow">Desktop Startup</p>
      <h1>{title}</h1>
      <p>{body}</p>
      {f"<pre>{detail}</pre>" if detail else ""}
    </main>
  </body>
</html>"""
    return f"data:text/html;charset=utf-8,{quote(html)}"


def _run_legacy_gui_if_explicitly_enabled() -> bool:
    if os.environ.get("SKYSCANNER_ALLOW_LEGACY_GUI") != "1":
        return False
    from legacy import gui as legacy_gui

    legacy_gui.main()
    return True


def main() -> None:
    if os.environ.get("SKYSCANNER_GUI_SMOKE_TEST") == "1":
        DesktopUIService()
        print("smoke-ok")
        return

    frontend_index = _frontend_index_path()
    try:
        import webview
    except ImportError:
        if _run_legacy_gui_if_explicitly_enabled():
            return
        raise SystemExit(
            "缺少 pywebview，无法启动桌面 Web UI。请安装依赖后重试；如需临时打开旧 Tk 界面，可设置 SKYSCANNER_ALLOW_LEGACY_GUI=1。"
        )

    if frontend_index.exists():
        window_url = frontend_index.as_uri()
        bridge: DesktopBridge | None = DesktopBridge()
    else:
        window_url = _error_page_uri(
            "未找到前端静态资源",
            "桌面入口已停止静默回退到旧 Tk 界面。请先构建 webui/dist 后再启动。",
            detail="缺失文件: webui/dist/index.html\n可选临时方案: 设置 SKYSCANNER_ALLOW_LEGACY_GUI=1 再启动旧界面。",
        )
        bridge = None

    window = webview.create_window(
        "Skyscanner 多市场比价",
        url=window_url,
        js_api=bridge,
        width=1440,
        height=920,
        min_size=(1200, 760),
        text_select=True,
        background_color="#efe6d6",
    )
    webview.start(
        debug=bool(os.environ.get("SKYSCANNER_WEBVIEW_DEBUG")),
        private_mode=False,
        gui="cocoa",
    )


if __name__ == "__main__":
    main()
