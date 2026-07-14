# Skyscanner 多市场比价

一个本地运行的 Skyscanner 多市场机票价格比较工具。

输入出发地、目的地和日期后，程序会通过本机 Chrome、Edge 或 Comet 浏览器依次打开不同地区的 Skyscanner 页面，提取页面显示的价格，并在桌面界面中汇总比较。

> 本项目用于辅助搜索和记录公开展示的价格，不提供订票服务，也不会自动绕过 CAPTCHA 或其他人机验证。最终票价、税费、库存及销售条件请以跳转后的页面为准。

[![CI](https://github.com/Chenke0023/skyscanner_multi_domain/actions/workflows/ci.yml/badge.svg?branch=main)](https://github.com/Chenke0023/skyscanner_multi_domain/actions/workflows/ci.yml)
[![Release](https://img.shields.io/github/v/release/Chenke0023/skyscanner_multi_domain)](https://github.com/Chenke0023/skyscanner_multi_domain/releases/latest)
[![Python](https://img.shields.io/badge/Python-3.12-3776AB)](https://www.python.org/)

![多市场价格比较界面](docs/assets/product-results.png)

<details>
<summary><strong>查看操作演示</strong></summary>

![创建查询、查看结果并展开解析信息](docs/assets/demo-60s.gif)

</details>

## 主要功能

- 同时比较中国、香港、新加坡、英国、韩国、日本、哈萨克斯坦等 Skyscanner 市场。
- 支持单程、往返、日期窗口和国家级机场组合搜索。
- 提取“最佳”和“最便宜”价格，并记录币种、来源页面和解析置信度。
- 保存搜索历史，支持失败市场重试、价格提醒和结果复核。
- 导出 Markdown、CSV 和 JSON 报告。
- 检测到机器人或 CAPTCHA 检查时发送桌面提醒；未完成验证的浏览器标签页不会在等待超时后自动关闭。
- 提供桌面 WebView 和 CLI 两种入口。

## 工作方式

```mermaid
flowchart LR
    UI["桌面界面 / CLI"] --> Scan["扫描调度器"]
    Scan --> CDP["Chrome DevTools Protocol"]
    CDP --> Browser["本机 Chrome / Edge / Comet"]
    Browser --> Markets["不同地区的 Skyscanner 页面"]
    Markets --> Parser["页面价格解析"]
    Parser --> Results["比较结果、历史和导出文件"]
```

程序只使用本机浏览器的 Chrome DevTools Protocol（CDP），不会内嵌或下载额外浏览器。默认的稳定扫描方式是 `page`；`cdp_structured` 是需要显式启用的实验性解析模式。

## 使用限制

- Skyscanner 页面结构、地区限制或登录状态变化可能导致解析失败。
- 出现 CAPTCHA、人机验证或安全检查时，需要用户在保留的浏览器标签页中手动处理。
- 不同市场展示的币种、税费、行李规则和销售条件可能不同，不能只根据数字直接判断最终可购买价格。
- 本项目不保证持续可用性，也不代替 Skyscanner、航司或出票平台的最终报价。

## 快速安装 / 运行

### macOS Apple Silicon

从 [Latest Release](https://github.com/Chenke0023/skyscanner_multi_domain/releases/latest) 下载 `skyscanner-multi-domain-v1.3.0-macos-arm64.zip`。

应用采用 ad-hoc 签名，尚未经过 Apple notarization；首次启动可能需要在 Finder 中右键选择“打开”。

### 从源码运行

要求：

- Python 3.12
- Node.js 20 或更高版本（构建桌面前端时使用）
- 已安装 Chrome、Edge、Comet，或已有可连接的 CDP endpoint

```bash
python3 -m pip install -r requirements.txt
cd webui
npm install
npm test -- --run
npm run build
cd ..
python3 desktop_webview.py
```

无法连接或启动系统浏览器时，程序会返回 `browser-unavailable` 以及对应的安装或启动提示。

### CLI 示例

```bash
python3 cli.py doctor
python3 cli.py page -o PEK -d ALA -t 2026-08-20 --date-window 0
python3 cli.py page -o 北京 -d 阿拉木图 -t 2026-08-20 --output json --output-file result.json
python3 cli.py page -o PEK -d ALA -t 2026-08-20 --transport cdp_structured
```

完整参数见：

```bash
python3 cli.py page --help
```

## 主要目录

- `desktop_webview.py`：桌面应用入口
- `desktop_ui_service.py`：桌面界面与扫描服务之间的桥接层
- `webui/`：React 桌面界面
- `cli.py`：命令行入口
- `skyscanner_multi_domain/transports/cdp.py`：浏览器连接和页面扫描
- `skyscanner_multi_domain/transports/cdp_structured.py`：实验性结构化解析
- `skyscanner_multi_domain/scan/`：扫描调度、历史、报告和导出
- `skyscanner_multi_domain/geo/`：地点、机场和国家/地区数据

## 开发与验证

```bash
python3 -m ruff check .
python3 -m mypy
python3 -m pytest -q
cd webui
npm test -- --run
npm run build
cd ..
python3 scripts/release_smoke.py
```

构建 macOS 应用：

```bash
python3 scripts/generate_icon.py
python3 -m pip install pyinstaller
scripts/build_macos_standalone_app.sh
```

版本由 `data/version.txt` 管理；尚未发布的改动记录在 `CHANGELOG.md` 的 `Unreleased` 部分。
