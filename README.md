# Skyscanner 多市场比价

这是一个以 `opencli` 浏览器自动化为主方案、以 Edge + CDP `page` 和 Scrapling legacy 方案为备用兜底的 Skyscanner 多市场比价工具。

当前只维护一条面向终端用户的产品路径：桌面 WebView。CLI 保留为开发、调试、自动化和导出入口；旧 Tk GUI 与根目录兼容 shim 只做 legacy 兼容。

## Active / Legacy Map

### Primary product

- `desktop_webview.py` — 桌面 WebView app 壳
- `desktop_ui_service.py` — UI 与扫描引擎之间的 bridge
- `webui/` — 打包进桌面 app 的 React UI

### Developer entry

- `cli.py` — headless runner，用于 smoke test、bug 复现、脚本化扫描、SearchPlan 验证和 Markdown/CSV/JSON 导出

### Core modules

- `skyscanner_multi_domain/planning/search_plan.py` — 候选排序、扫描计划解释和批次计划
- `skyscanner_multi_domain/scan/orchestrator.py` — 扫描编排
- `skyscanner_multi_domain/scan/history.py` — 历史记录、预览缓存、plan telemetry
- `skyscanner_multi_domain/transports/opencli.py` — opencli 传输
- `skyscanner_multi_domain/transports/cdp.py` — CDP 传输
- `skyscanner_multi_domain/transports/scrapling.py` — Scrapling legacy 传输
- `skyscanner_multi_domain/parsing/page_parser.py` — 页面解析
- `skyscanner_multi_domain/geo/location_resolver.py` — 地点/国家/机场解析
- `skyscanner_multi_domain/geo/regions.py` — 地区配置
- `skyscanner_multi_domain/models.py` — 数据模型
- `skyscanner_multi_domain/planning/date_window.py` — 日期窗口与往返日期标签
- `skyscanner_multi_domain/runtime/paths.py` — 运行时路径
- `skyscanner_multi_domain/diagnostics/attempt_trace.py` — attempt trace 日志
- `skyscanner_multi_domain/pricing/fx_rates.py` — 汇率换算

### Legacy

- `skyscanner_neo.py` — compatibility / legacy Neo entry。保留现有 Neo CLI、replay、URL mutation 兼容能力；新逻辑不要继续写入这里，中期再拆到 package。
- `legacy/gui.py` / `gui.py` — deprecated Tk interface。只修启动级别问题，不再增加 SearchPlan UI、历史抽屉、失败修复 UI 或视觉优化。
- 根目录 `app_paths.py`、`attempt_trace.py`、`date_window.py`、`fx_rates.py`、`skyscanner_models.py`、`scan_orchestrator.py`、`scan_history.py`、`search_plan.py`、`transport_*.py`、`skyscanner_page_parser.py`、`location_resolver.py`、`skyscanner_regions.py` — compatibility shims。旧测试和 mock target 仍会访问这些路径，新逻辑不得写入这里。

### Compatibility shim policy

- 保留 root-level shim 至少 2 个小版本，或直到所有 tests / mock targets 迁移完成。
- 新代码不得 import root-level shim；新测试应优先 import package path。
- 旧测试可以继续覆盖 root-level shim，以验证兼容路径。
- 删除 shim 前必须跑 full pytest、CLI smoke 和 desktop import smoke。

## Engineering Rules

新增功能先按边界归类：

1. 扫描核心能力：放在 `skyscanner_multi_domain/scan/`、`skyscanner_multi_domain/planning/`、`skyscanner_multi_domain/parsing/`、`skyscanner_multi_domain/geo/` 或 `skyscanner_multi_domain/transports/`。
2. 终端用户体验：只做 `webui/` + `desktop_webview.py` + `desktop_ui_service.py`。
3. 调试、自动化、导出：CLI 可以暴露。
4. 旧 GUI 兼容：默认不做新功能，只修启动级别问题。

## 当前主方案

- 默认抓取方案：`opencli`
- 备用抓取方案：`page`（通过本机 Edge + CDP 读取结果页）
- Legacy 兜底方案：`scrapling`

当前推荐优先使用 opencli 驱动浏览器打开结果页、抽取正文并解析 Best / Cheapest 价格；opencli 未取到价格时会先 fallback 到 `page`，仍失败时再尝试 Scrapling legacy。

历史 refactor、旧分支状态和迁移细节记录在 `docs/history/2026-04-refactor-notes.md`。当前开发以本文件的 active / legacy 边界为准。

当前任务优先级和验收标准记录在 `docs/todo.md`。下一阶段重点是 SearchPlan batch progress、桌面 WebView 阶段展示和结果可信度，不做动态剪枝。

## 启动方式

### macOS App（推荐）

构建独立桌面版：

```bash
pip install pyinstaller
./scripts/build_macos_standalone_app.sh
```

产物位于 `dist/Skyscanner 多市场比价.app`，双击即可运行。

App 自包含 Python 运行时与所有依赖，可独立分发，无需安装 Python 或依赖包。

### 源码运行（开发调试）

GUI：

```bash
python3 desktop_webview.py
```

如果本机尚未安装 `pywebview`，桌面入口会直接给出明确错误并退出。

如果前端静态资源 `webui/dist/index.html` 缺失，桌面窗口会显示错误页，不再静默回退到 Tk。

如需临时打开旧 Tk 版本，请显式执行：

```bash
SKYSCANNER_ALLOW_LEGACY_GUI=1 python3 desktop_webview.py
```

CLI：

```bash
# 基础扫描
python3 cli.py page -o 北京 -d 阿拉木图 -t 2026-04-29
python3 cli.py page -o 北京 -d 阿拉木图 -t 2026-04-29 --date-window 0
python3 cli.py page -o 北京 -d 阿拉木图 -t 2026-04-29 --show-plan

# 指定传输方案
python3 cli.py page -o 北京 -d 阿拉木图 -t 2026-04-29 --transport opencli
python3 cli.py page -o 北京 -d 阿拉木图 -t 2026-04-29 --transport page
python3 cli.py page -o 北京 -d 阿拉木图 -t 2026-04-29 --transport scrapling

# 往返与多机场
python3 cli.py page -o 北京 -d 香港 -t 2026-05-20 --return-date 2026-05-25
python3 cli.py page -o 北京 --destination-country 乌兹别克斯坦 -t 2026-05-20
python3 cli.py page --origin-country 中国 --destination-country 乌兹别克斯坦 -t 2026-05-20 --country-airport-limit 8

# 输出格式
python3 cli.py page -o 北京 -d 阿拉木图 -t 2026-04-29 --output json
python3 cli.py page -o 北京 -d 阿拉木图 -t 2026-04-29 --output jsonl --output-file results.jsonl

# 调试与 trace
python3 cli.py page -o 北京 -d 阿拉木图 -t 2026-04-29 --show-attempts
python3 cli.py page -o 北京 -d 阿拉木图 -t 2026-04-29 --show-low-confidence

# CDP 模式
python3 cli.py page -o 北京 -d 阿拉木图 -t 2026-04-29 --cdp-mode managed
python3 cli.py page -o 北京 -d 阿拉木图 -t 2026-04-29 --cdp-mode manual --manual-tabs-json tabs.json

# 置信度与验证码策略
python3 cli.py page -o 北京 -d 阿拉木图 -t 2026-04-29 --rankable-confidence 0.90
python3 cli.py page -o 北京 -d 阿拉木图 -t 2026-04-29 --low-confidence-policy accept-review
python3 cli.py page -o 北京 -d 阿拉木图 -t 2026-04-29 --challenge-policy manual
```

## 当前功能

- 默认通过 opencli 浏览器自动化抓取结果页可见正文
- SearchPlan 会对路线、日期和市场候选进行可解释排序；`--show-plan` 可打印扫描计划并退出，不发起实时扫描
- 当单个市场在 opencli 下仍失败时，自动回退到 `page` 方案，再尝试 Scrapling legacy 兜底
- 在需要时可显式切换到 `page` 方案，自动连接或拉起带 `9222` 调试端口的 Edge
- 在需要对比 legacy 行为时可显式切换到 `scrapling` 方案
- 按路线智能拼出实际比较地区（基线地区 + 出发/目的地所属市场 + 手动追加地区）
- 支持单程与往返
- 支持日期窗口扫描（默认 `±3` 天，`--date-window 0` 表示只扫单日；往返时保持停留天数）
- 支持地点解析：
  - 地点 -> 地点
  - 地点 -> 国家
  - 国家 -> 地点
  - 国家 -> 国家
- 国家端点会自动展开为候选主机场集合，逐航段扫描后按市场聚合最优结果
- 解析真实结果页中的 Best / Cheapest 排序区块
- 同时提取 Best / Cheapest
- 按汇率统一换算为人民币
- 保存 Markdown 报告，便于直接对比
- 结果表 / Markdown 报告包含“航段”列，能看到实际命中的机场组合
- GUI 结果表格支持点击列头排序（价格列按数值排序，支持升序/降序切换）
- GUI 扫描进度条：逐市场实时更新状态（如 `正在扫描 2026-04-29 [中国] (attempts/expected: 3/49)`），附 `ttk.Progressbar`
- GUI 取消按钮：扫描期间可随时中断，worker 线程在日期/地区间隙安全退出
- GUI 链接可点击：双击结果表"链接"列可在默认浏览器中打开对应 Skyscanner 结果页
- GUI 支持独立勾选“出发地按国家”与“目的地按国家”
- GUI 内置出发 / 返程日期选择器

## 输出与运行时路径

项目内输出：

- 报告：`outputs/reports/`
- 日志：`logs/`
- 失败样本：`logs/failures/`

当以源码方式运行时，上述路径都在项目目录内。

当以独立桌面版 `.app` 运行时，运行时文件会落到：

- `~/Library/Application Support/skyscanner_multi_domain/outputs/reports/`
- `~/Library/Application Support/skyscanner_multi_domain/logs/`
- `~/Library/Application Support/skyscanner_multi_domain/runtime/`

运行时状态目录：

- 浏览器 profile（`page` 方案使用）：`$XDG_STATE_HOME/skyscanner_multi_domain/browser-profiles/`
- 汇率缓存：`$XDG_STATE_HOME/skyscanner_multi_domain/fx_rates_cache.json`

当某个市场抓取失败时，程序会把失败摘要和页面正文摘录写入 `logs/failures/`，便于排查 loading / challenge / parse failed 等问题。

如果没有设置 `XDG_STATE_HOME`，默认会落到：

- `~/.local/state/skyscanner_multi_domain/`

首次运行时，如果旧 profile 仍在 `outputs/*-cdp-profile/`，程序会尝试迁移到状态目录。

## 报告格式

当前 Markdown 报告会先给出 `扫描结论`，包括建议优先验证的最低价、备选结果、价差、可信度、价格来源和风险提示。随后是 `价格明细` 表格。

当前 Markdown 明细表格列为：

- 航段
- 地区
- 来源
- 计划
- 最佳（原币）
- 最佳（人民币）
- 最低价（原币）
- 最低价（人民币）
- 可信度
- 价格来源
- 警告
- 较上次变化
- 状态
- 错误
- 链接

如果解析器产生 warning，报告末尾还会生成 `解析警告与证据`，列出对应市场、航段、可信度、价格来源和页面证据片段。

示例文件：

- `outputs/reports/edge_page_BJSA_ALA_20260429.md`
- `outputs/reports/edge_page_BJSA_UZ_ANY_20260520.md`

如果启用日期窗口，还会额外生成一个窗口汇总文件，例如：

- `outputs/reports/edge_page_BJSA_ALA_20260426_20260502_summary.md`

## 输出格式

CLI 支持三种输出格式，通过 `--output` 选择：

| 格式 | 说明 | 适用场景 |
|------|------|----------|
| `table` (默认) | 终端表格，含 Region/Price/Conf/Rankable/Transport/Attempts/Status 列 | 人工查看 |
| `json` | 结构化 JSON，含 scan_id、best_rankable、quotes 列表 | 程序消费、脚本处理 |
| `jsonl` | 每行一个 JSON 对象，每行对应一个 quote | grep/jq 逐行分析、追加写入 |

```bash
# 终端表格（默认）
python3 cli.py page -o 北京 -d 阿拉木图 -t 2026-04-29

# 结构化 JSON
python3 cli.py page -o 北京 -d 阿拉木图 -t 2026-04-29 --output json

# JSONL 写入文件
python3 cli.py page -o 北京 -d 阿拉木图 -t 2026-04-29 --output jsonl --output-file scan.jsonl
```

`--show-attempts` 在表格模式下展示每个 region 的完整 fallback attempt 链路。
`--show-low-confidence` 解除对 `debug_only` 结果的隐藏。

## Trace 文件

每次扫描可选生成一个 JSONL trace 文件，记录每个传输尝试的结构化事件。
Trace 文件位于 `traces/` 目录（可通过 `--trace-dir` 自定义），文件名为 `<scan_id>.jsonl`。

每条 trace 事件包含：

| 字段 | 说明 |
|------|------|
| `scan_id` | 扫描批次 ID |
| `route_id` | 路线标识 |
| `origin` / `destination` / `depart_date` | 行程信息 |
| `region` / `domain` | 市场与域名 |
| `attempt_index` | 该 region 内的尝试序号（从 1 开始） |
| `transport` | 传输方案：opencli / cdp / scrapling / google_jump |
| `status` | 本次尝试的状态码 |
| `action` | 决策动作：accept / fallback_cdp / fallback_scrapling / terminal / manual_review |
| `failure_class` | 失败分类：success / semantic_mismatch / no_flights / challenge / network / low_confidence ... |
| `reason` | 人类可读的决策原因 |
| `price` / `currency` / `confidence` | 解析出的价格与置信度 |
| `rankable` | 是否满足可排序条件 |
| `elapsed_ms` | 耗时（毫秒） |
| `url` / `phase` | 请求 URL 与执行阶段 |
| `metadata` | 传输层元数据（exit_code、stderr_tail 等） |

**jq 分析示例：**

```bash
# 查看所有 fallback 事件
jq 'select(.action | startswith("fallback"))' traces/*.jsonl

# 按 region 统计尝试次数
jq -r '[.region, .attempt_index] | @tsv' traces/*.jsonl | sort | uniq -c

# 统计各 failure_class 出现频率
jq -r '.failure_class' traces/*.jsonl | sort | uniq -c | sort -rn

# 查看低置信度事件
jq 'select(.failure_class == "low_confidence")' traces/*.jsonl

# 提取所有需要人工复核的事件
jq 'select(.requires_manual_review == true)' traces/*.jsonl
```

禁用 trace：`--no-trace`。

## 置信度与排序

解析器为每个价格输出一个置信度分数（0.0-1.0），反映 parser 对提取结果的确定性。

**置信度阈值：**

| 参数 | 默认值 | 说明 |
|------|--------|------|
| `--rankable-confidence` | 0.80 | 可参与排序的最低置信度。低于此阈值的价格不会出现在 Best Rankable 推荐中 |
| `--review-confidence` | 0.50 | 触发人工复核标记的阈值 |

**低置信度处理策略 (`--low-confidence-policy`)：**

| 策略 | 行为 |
|------|------|
| `fallback` (默认) | 置信度不足时触发备选传输方案（CDP → Scrapling），用其他来源交叉验证 |
| `show` | 展示低置信度价格但不参与排序 |
| `hide` | 低置信度价格仅写入 trace，不在输出中展示 |
| `accept-review` | 接受低置信度价格但标记为需要人工复核 |

```bash
# 严格要求高置信度
python3 cli.py page -o 北京 -d 阿拉木图 -t 2026-04-29 --rankable-confidence 0.90

# 低置信度接受但标记复核
python3 cli.py page -o 北京 -d 阿拉木图 -t 2026-04-29 --low-confidence-policy accept-review
```

## 验证码处理

遇到 captcha / challenge 页面时的策略 (`--challenge-policy`)：

| 策略 | 行为 |
|------|------|
| `stop` (默认) | 终止该 market，不尝试绕过。challenge 页面记录在 trace 中 |
| `manual` | 输出需要用户处理的 URL/Tab 信息，标记为人工复核。不自动绕过 |

```bash
# 遇到验证码时输出手动处理信息
python3 cli.py page -o 北京 -d 阿拉木图 -t 2026-04-29 --challenge-policy manual
```

## CDP 模式

`--cdp-mode` 控制如何连接浏览器：

| 模式 | 说明 |
|------|------|
| `attach` (默认) | 连接已有浏览器的调试端口（默认 `localhost:9222`）。需要浏览器已开启远程调试 |
| `managed` | 自动启动独立浏览器实例，扫描结束后关闭 |
| `manual` | 手动指定 Tab 映射。需通过 `--manual-tabs-json` 提供 JSON 文件，格式为 `{"region_code": "tab_id"}` |

```bash
# 自动管理浏览器生命周期
python3 cli.py page -o 北京 -d 阿拉木图 -t 2026-04-29 --cdp-mode managed

# 自定义调试端口
python3 cli.py page -o 北京 -d 阿拉木图 -t 2026-04-29 --cdp-host http://192.168.1.100:9222

# 手动 Tab 映射
python3 cli.py page -o 北京 -d 阿拉木图 -t 2026-04-29 --cdp-mode manual --manual-tabs-json tabs.json
```

`--keep-tabs` 调试用，阻止 managed 模式下自动关闭标签页。

## 调试失败的扫描

Trace 文件是排查扫描问题的主要入口：

```bash
# 找到所有失败事件（非 accept 动作）
jq 'select(.action != "accept")' traces/<scan_id>.jsonl

# 按 region 查看完整 fallback 链
jq -r '[.region, .attempt_index, .transport, .action, .failure_class, .reason] | @tsv' traces/<scan_id>.jsonl | sort -k1,1 -k2,2n

# 检查特定 region 的每次尝试详情
jq 'select(.region == "CN")' traces/<scan_id>.jsonl
```

失败页面正文摘录写入 `failures/` 目录（可通过 `--failure-log-dir` 自定义）。
`--debug-page-text` 在 failure log 中保留完整页面文本（默认截断）。

## 默认地区逻辑

当前基线默认地区为：

- `CN`
- `HK`
- `SG`
- `UK`

`JP` / `KR` 不在默认基线里，但仍可手动追加。

## 开发与验证

安装依赖：

```bash
pip install -r requirements.txt
```

构建前端桌面界面：

```bash
cd webui
npm install
npm run build
```

运行测试：

```bash
python3 -m pytest -q test_location_resolver.py test_cli.py
python3 -m pytest -q test_skyscanner_neo.py
python3 -m pytest -q test_date_window.py
```

OpenCLI fetch reliability diagnostics:

```bash
python3 tools/replay_parser_snapshots.py logs/snapshots/opencli
python3 tools/replay_parser_snapshots.py logs/snapshots/opencli --json
```

Current OpenCLI behavior:

- OpenCLI is the default browser automation fetch path.
- The OpenCLI tab pool is serial and bounded: `region_concurrency` controls retained tab lanes, not parallel market execution.
- The scanner does not prune markets, early stop, or skip tasks.
- Exact Mode is the default execution policy and scans the full planned market set.
- Fast Mode is policy scaffold only; pruning, early stop, and task skipping are not enabled by default.
- Repair Mode builds targeted failed-market repair plans without rescanning successful markets.
- Challenge/captcha pages are identified and recorded; they are not bypassed.
- Fetch history stores `fetch_quality_telemetry`, `parser_recovery_telemetry`, and `snapshot_summary`.
- Failed or low-confidence OpenCLI parses can write bounded JSON snapshots under `logs/snapshots/opencli/` for offline parser replay.

后台自动复扫：

```bash
# 只检查到期的后台复扫配置，不执行扫描
python3 cli.py auto-refresh-once --dry-run

# 执行一次到期的后台复扫配置
python3 cli.py auto-refresh-once --limit 1

# 仅在接入电源时执行后台复扫
python3 cli.py auto-refresh-once --limit 1 --only-on-ac-power

# 安装 macOS launchd 调度器，默认每 600 分钟检查一次
python3 cli.py install-auto-refresh --limit 1 --only-on-ac-power

# 卸载 macOS launchd 调度器
python3 cli.py uninstall-auto-refresh
```

后台任务只处理 UI 中选择为“后台”的自动复扫配置；“应用内”配置仍由桌面 App 打开时的轮询触发。后台命令使用 `runtime/background_auto_refresh.lock` 做进程锁，避免多次调度重叠执行。

Legacy Tk status:

- `legacy/gui.py` and root-level `gui.py` are frozen compatibility entry points.
- New end-user UX must be implemented in `desktop_webview.py`, `desktop_ui_service.py`, and `webui/`.
- See `docs/legacy_tk_policy.md`.

验证浏览器会话持久化：

```bash
# 验证 Comet 浏览器会话持久化（推荐生产浏览器）
python3 cli.py doctor --verify-session-persistence --persistence-browser comet

# 验证 Edge 浏览器会话持久化
python3 cli.py doctor --verify-session-persistence --persistence-browser edge

# 验证 Chrome 浏览器会话持久化
python3 cli.py doctor --verify-session-persistence --persistence-browser chrome
```

会话持久化验证流程：

1. 启动本地 HTTP 探测服务器，设置一个唯一 cookie
2. 启动指定浏览器，访问探测服务器设置 cookie
3. 通过 CDP 读取并验证 cookie
4. 关闭浏览器，等待 CDP 端口完全释放
5. 使用相同 profile 重启浏览器，访问探测服务器
6. 验证之前的 cookie 是否仍然存在

验证结果（截至 2026-04-28）：

- **Comet**: ✅ 通过 — 会话在重启后保持
- **Edge**: ❌ 失败 — 会话在重启后丢失（可能需要额外的启动参数或 profile 配置）

重新构建 macOS App：

```bash
# 生成应用图标（首次构建前执行一次）
python3 scripts/generate_icon.py

# 构建独立桌面版
python3 -m pip install pyinstaller
./scripts/build_macos_standalone_app.sh
```
