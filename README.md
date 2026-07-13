# Skyscanner 多市场比价

使用本机浏览器 CDP 扫描 Skyscanner 多个市场，并保留地点搜索、历史记录、报告及 Markdown/CSV/JSON 导出。桌面 WebView 是主要入口，CLI 用于调试、自动化与导出。

## 运行环境

- Python 3.11+
- 已安装 Chrome、Edge 或 Comet，或已有可连接的 CDP endpoint
- 运行依赖仅为 `aiohttp`、`requests`、`pywebview`

项目不会内嵌或下载浏览器。无法连接或启动系统浏览器时，会返回 `browser-unavailable` 以及安装/启动提示。

## 扫描模式

- `page`：默认且稳定，直接通过 CDP 读取页面并解析。
- `cdp_structured`：显式启用的实验性结构化解析；使用同一 CDP 运行时，不是备用浏览器栈。

扫描只在 CDP 内部执行必要的页面重试和超时处理，不跨 transport 回退。

## 快速安装 / 运行

```bash
python3 -m pip install -r requirements.txt
cd webui && npm install && npm test -- --run && npm run build && cd ..
python3 desktop_webview.py
```

CLI 示例：

```bash
python3 cli.py doctor
python3 cli.py page -o 北京 -d 阿拉木图 -t 2026-04-29
python3 cli.py page -o PEK -d ALA -t 2026-04-29 --transport cdp_structured
python3 cli.py page -o 北京 -d 阿拉木图 -t 2026-04-29 --output json --output-file result.json
```

`page` 支持日期窗口、往返、国家/机场地点解析、历史预览、失败重跑、报告和导出；完整参数见 `python3 cli.py page --help`。

## 主要模块

- `desktop_webview.py`：桌面 WebView 入口
- `desktop_ui_service.py`：桌面 UI bridge
- `webui/`：React 桌面界面
- `cli.py`：CLI 入口
- `skyscanner_multi_domain/transports/cdp.py`：浏览器检测、启动和 CDP 页面扫描
- `skyscanner_multi_domain/transports/cdp_structured.py`：实验性结构化 CDP 解析
- `skyscanner_multi_domain/scan/`：扫描、历史、报告和导出
- `skyscanner_multi_domain/geo/`：地点、机场和静态国家元数据

## 测试

```bash
python3 -m pytest -q
cd webui && npm test -- --run && npm run build
cd .. && python3 scripts/release_smoke.py
```

## macOS 构建

```bash
python3 scripts/generate_icon.py
python3 -m pip install pyinstaller
scripts/build_macos_standalone_app.sh
```

构建脚本会拒绝已退役或开发专用模块进入应用包，并强制：

- `.app` 不超过 150MB
- 发布 ZIP 不超过 60MB

产物位于 `dist/`。版本由 `data/version.txt` 管理，未发布变更记录在 `CHANGELOG.md` 的 `Unreleased`。
