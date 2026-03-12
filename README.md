# Skyscanner 多市场比价

这是一个基于本机 Edge + CDP 的 Skyscanner 多市场比价工具。

当前维护的是一条明确的运行路径：

- GUI 入口：`gui.py`
- CLI 入口：`cli.py`
- 扫描编排：`skyscanner_neo.py`
- 地区配置：`skyscanner_regions.py`
- 页面解析：`skyscanner_page_parser.py`
- 数据模型：`skyscanner_models.py`
- App 构建脚本：`scripts/build_macos_app.sh`
- Neo 依赖：`vendor/neo`
- 交接文档：`AI_AGENT_HANDOFF.md`

## 启动方式

GUI：

```bash
python3 gui.py
```

CLI：

```bash
python3 cli.py page -o 北京 -d 阿拉木图 -t 2026-04-29
python3 cli.py page -o 北京 -d 阿拉木图 -t 2026-04-29 --date-window 0
```

如果已经构建过 macOS App，也可以双击打开 `Skyscanner 多市场比价.app`。

## 当前功能

- 自动连接或拉起带 `9222` 调试端口的 Edge
- 按路线智能拼出实际比较地区（基线地区 + 出发/目的地所属市场 + 手动追加地区）
- 支持日期窗口扫描（默认 `±3` 天，`--date-window 0` 表示只扫单日）
- 读取真实结果页的 `document.body.innerText`
- 同时提取 Best / Cheapest
- 按汇率统一换算为人民币
- 保存 Markdown 报告，便于直接对比

## 输出与运行时路径

项目内输出：

- 报告：`outputs/reports/`
- 日志：`logs/`

运行时状态目录：

- 浏览器 profile：`$XDG_STATE_HOME/skyscanner_multi_domain/browser-profiles/`
- 汇率缓存：`$XDG_STATE_HOME/skyscanner_multi_domain/fx_rates_cache.json`

如果没有设置 `XDG_STATE_HOME`，默认会落到：

- `~/.local/state/skyscanner_multi_domain/`

首次运行时，如果旧 profile 仍在 `outputs/*-cdp-profile/`，程序会尝试迁移到状态目录。

## 报告格式

当前 Markdown 表格列为：

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

如果启用日期窗口，还会额外生成一个窗口汇总文件，例如：

- `outputs/reports/edge_page_BJSA_ALA_20260426_20260502_summary.md`

## 默认地区逻辑

当前基线默认地区为：

- `CN`
- `HK`
- `SG`
- `US`
- `UK`

`JP` / `KR` 不在默认基线里，但仍可手动追加。

## 开发与验证

安装依赖：

```bash
pip install -r requirements.txt
```

运行测试：

```bash
python3 -m pytest -q test_skyscanner_neo.py
python3 -m pytest -q test_date_window.py
```

重新构建 macOS App：

```bash
./scripts/build_macos_app.sh
```
