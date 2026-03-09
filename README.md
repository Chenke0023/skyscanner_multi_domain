# Skyscanner 多市场比价

当前目录已经清理为只保留可运行的版本：

- 图形界面：`gui.py`
- 命令行：`cli.py`
- 核心抓取逻辑：`skyscanner_neo.py`
- macOS 应用：`Skyscanner 多市场比价.app`
- App 构建脚本：`scripts/build_macos_app.sh`
- Neo 依赖：`vendor/neo`
- AI 交接文档：`AI_AGENT_HANDOFF.md`

## 启动方式

直接双击：

`Skyscanner 多市场比价.app`

也可以运行：

```bash
cd /path/to/skyscanner_multi_domain
python3 gui.py
```

## 当前功能

- 自动连接或拉起带 `9222` 调试端口的 Edge
- 打开多个 Skyscanner 市场页面做比价
- 支持常见中文城市名到机场码/城市码映射
- 输出统一折算为人民币
- 结果保存为 Markdown 表格，便于直接对比

## 输出文件

结果和运行数据已按用途分开：

- Markdown 比价结果保存在 `outputs/reports/`
- GUI 日志保存在 `logs/`
- Edge 专用浏览器 profile 保存在 `data/browser-profiles/`

例如：

- `outputs/reports/edge_page_BJSA_ALA_20260429.md`

表格只保留：

- 地区
- 价格（人民币）
- 结果链接

## 重新构建 App

如果修改了界面，可以重新生成 `.app`：

```bash
cd /path/to/skyscanner_multi_domain
./scripts/build_macos_app.sh
```

## 依赖

```bash
pip install -r requirements.txt
```
