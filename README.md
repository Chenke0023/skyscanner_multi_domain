# Skyscanner 多市场比价

这是一个以 Scrapling 为主方案、以 Edge + CDP `page` 方案为备用兜底的 Skyscanner 多市场比价工具。

当前维护的是一条明确的运行路径：

- GUI 入口：`gui.py`
- CLI 入口：`cli.py`
- 扫描编排：`scan_orchestrator.py`
- Scrapling 传输：`transport_scrapling.py`
- CDP 传输：`transport_cdp.py`
- Neo 兼容层：`skyscanner_neo.py`
- 地区配置：`skyscanner_regions.py`
- 页面解析：`skyscanner_page_parser.py`
- 数据模型：`skyscanner_models.py`
- App 构建脚本：`scripts/build_macos_app.sh`
- 独立桌面版构建脚本：`scripts/build_macos_standalone_app.sh`
- Neo 依赖：`vendor/neo`
- 交接文档：`AI_AGENT_HANDOFF.md`

## 当前主方案

- 默认抓取方案：`scrapling`
- 备用抓取方案：`page`（通过本机 Edge + CDP 读取结果页）

当前推荐优先使用 Scrapling 获取页面正文并解析 Best / Cheapest 价格；仅在需要排查兼容性问题时再切换到 `page`。

当前项目进展：

- Scrapling 主方案已合入 `main`
- `skyscanner_neo.py` 拆分成果已合入 `main`，当前保留四模块结构：
  - `scan_orchestrator.py`
  - `transport_scrapling.py`
  - `transport_cdp.py`
  - `skyscanner_neo.py`（兼容层 + re-export）
- CLI 默认路径已完成真实取价验证
- GUI 当前也默认走 Scrapling
- Scrapling 失败市场会自动 fallback 到 `page`

## 仓库整理状态（2026-04-09）

当前仓库以 `main` 为唯一活跃主线：

- 历史功能分支已合并并清理
- 本地分支当前只保留 `main`
- 远端分支当前只保留 `origin/main`

当前建议工作方式：

- 新功能使用短生命周期分支开发
- 合并进 `main` 后立即删除本地 / 远端功能分支
- 不再长期保留已合并的历史分支

## 启动方式

GUI：

```bash
python3 gui.py
```

CLI：

```bash
python3 cli.py page -o 北京 -d 阿拉木图 -t 2026-04-29
python3 cli.py page -o 北京 -d 阿拉木图 -t 2026-04-29 --date-window 0
python3 cli.py page -o 北京 -d 阿拉木图 -t 2026-04-29 --transport page
python3 cli.py page -o 北京 -d 香港 -t 2026-05-20 --return-date 2026-05-25
python3 cli.py page -o 北京 --destination-country 乌兹别克斯坦 -t 2026-05-20
python3 cli.py page --origin-country 中国 --destination-country 乌兹别克斯坦 -t 2026-05-20 --country-airport-limit 8
```

如果已经构建过 macOS App，也可以双击打开 `Skyscanner 多市场比价.app`。

当前有两种 macOS 打包方式：

- 轻量启动器：`./scripts/build_macos_app.sh`
  - 只生成一个 `.app` 外壳
  - 仍依赖当前仓库目录和本机 `python3`
  - 适合开发机本地自用
- 独立桌面版：`./scripts/build_macos_standalone_app.sh`
  - 通过 PyInstaller 打出可单独分发的 `.app`
  - 内含 Python 解释器与项目代码，不再依赖仓库路径
  - 首次构建前需要：`python3 -m pip install pyinstaller`

独立桌面版产物位于：

- `dist/Skyscanner 多市场比价.app`

## 当前功能

- 默认通过 Scrapling 抓取结果页可见正文
- 当单个市场在 Scrapling 下仍失败时，自动回退到 `page` 方案重试该市场
- 在需要时可切换到 `page` 方案，自动连接或拉起带 `9222` 调试端口的 Edge
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
- GUI 扫描进度条：逐市场实时更新状态（如 `正在扫描 2026-04-29 [中国] (3/49)`），附 `ttk.Progressbar`
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

当前 Markdown 表格列为：

- 航段
- 地区
- 最佳（原币）
- 最佳（人民币）
- 最低价（原币）
- 最低价（人民币）
- 状态
- 错误
- 链接

示例文件：

- `outputs/reports/edge_page_BJSA_ALA_20260429.md`
- `outputs/reports/edge_page_BJSA_UZ_ANY_20260520.md`

如果启用日期窗口，还会额外生成一个窗口汇总文件，例如：

- `outputs/reports/edge_page_BJSA_ALA_20260426_20260502_summary.md`

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

运行测试：

```bash
python3 -m pytest -q test_location_resolver.py test_cli.py
python3 -m pytest -q test_skyscanner_neo.py
python3 -m pytest -q test_date_window.py
```

重新构建 macOS App：

```bash
./scripts/build_macos_app.sh
```

构建独立可分发版：

```bash
python3 -m pip install pyinstaller
./scripts/build_macos_standalone_app.sh
```
