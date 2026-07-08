"""
Neo tooling.

Primary scan paths (Scrapling + CDP fallback) live in the package:
- skyscanner_multi_domain/transports/scrapling.py
- skyscanner_multi_domain/transports/cdp.py
- skyscanner_multi_domain/scan/orchestrator.py

This module owns:
- NeoCli wrapper and Neo-based request execution
- Capture file loading, URL rewriting, payload mutation
- doctor / compare CLI subcommands
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any, Optional

import aiohttp

from skyscanner_multi_domain.runtime.paths import PROJECT_ROOT
from skyscanner_multi_domain.models import FlightQuote, RegionConfig
from skyscanner_multi_domain.geo.regions import DEFAULT_REGIONS, REGIONS
from skyscanner_multi_domain.scan import orchestrator as scan_orchestrator
from skyscanner_multi_domain.scan import url_builder
from skyscanner_multi_domain.transports import cdp

DEFAULT_DATE = "2026-04-29"


# ---------------------------------------------------------------------------
# NeoCli
# ---------------------------------------------------------------------------


class NeoCli:
    def __init__(self, project_root: Path):
        self.project_root = project_root
        self.base_cmd = self._resolve_base_cmd()

    def _resolve_base_cmd(self) -> Optional[list[str]]:
        env_bin = os.environ.get("NEO_BIN")
        if env_bin:
            return env_bin.split()

        vendor_tool = self.project_root / "vendor" / "neo" / "tools" / "neo.cjs"
        if vendor_tool.exists():
            return ["node", str(vendor_tool)]

        neo_bin = shutil.which("neo")
        if neo_bin:
            return [neo_bin]

        tool_path = self.project_root / "tools" / "neo.cjs"
        if tool_path.exists():
            return ["node", str(tool_path)]

        return None

    @property
    def available(self) -> bool:
        return self.base_cmd is not None

    def run(self, args: list[str]) -> subprocess.CompletedProcess[str]:
        if not self.base_cmd:
            raise RuntimeError("Neo CLI not found")
        return subprocess.run(
            [*self.base_cmd, *args],
            cwd=self.project_root,
            text=True,
            capture_output=True,
            check=False,
        )


async def execute_raw_request(
    session: aiohttp.ClientSession,
    region: RegionConfig,
    url: str,
    headers: dict[str, str],
    body: Any,
) -> FlightQuote:
    try:
        async with session.post(url, headers=headers, json=body) as response:
            text = await response.text()
            return url_builder.extract_quote(region, url, text, response.status)
    except (aiohttp.ClientError, TimeoutError) as exc:
        return FlightQuote(
            region=region.code,
            domain=region.domain,
            price=None,
            currency=region.currency,
            source_url=url,
            status="request_error",
            error=str(exc),
        )


def execute_neo_request(
    neo: NeoCli,
    region: RegionConfig,
    url: str,
    headers: dict[str, str],
    body: Any,
    tab_pattern: str,
) -> FlightQuote:
    args = [
        "exec",
        url,
        "--method",
        "POST",
        "--body",
        url_builder.compact_json(body),
        "--tab",
        tab_pattern,
        "--auto-headers",
    ]
    for key, value in headers.items():
        if key.lower() in {"authorization", "cookie"}:
            continue
        args.extend(["--header", f"{key}: {value}"])

    result = neo.run(args)
    if result.returncode != 0:
        error = result.stderr.strip() or result.stdout.strip() or "neo exec failed"
        return FlightQuote(
            region=region.code,
            domain=region.domain,
            price=None,
            currency=region.currency,
            source_url=url,
            status="neo_exec_failed",
            error=error,
        )

    stdout = result.stdout.strip()
    lines = stdout.splitlines()
    if not lines or not lines[0].startswith("HTTP "):
        return FlightQuote(
            region=region.code,
            domain=region.domain,
            price=None,
            currency=region.currency,
            source_url=url,
            status="neo_parse_failed",
            error=stdout[:300] or "neo exec returned unexpected output",
        )

    try:
        status_code = int(lines[0].split()[1])
    except (IndexError, ValueError):
        status_code = 0

    separator = lines.index("---") if "---" in lines else 1
    body_text = "\n".join(lines[separator + 1 :])
    return url_builder.extract_quote(region, url, body_text, status_code)


def print_doctor(
    neo: NeoCli,
    capture_file: Optional[Path],
    *,
    verify_session_persistence: bool = False,
    persistence_browser: Optional[str] = None,
) -> None:
    browsers = cdp.detect_browsers()
    cdp_info = cdp.detect_cdp_version()
    extension_path = neo.project_root / "vendor" / "neo" / "extension-dist"

    print("Neo 环境检查")
    print("-" * 40)
    print(f"项目目录: {neo.project_root}")
    print(f"Neo CLI: {'已找到' if neo.available else '未找到'}")
    if neo.available:
        print(f"Neo 命令: {' '.join(neo.base_cmd or [])}")
        result = neo.run(["version"])
        version_text = (result.stdout or result.stderr).strip().splitlines()
        print(f"Neo 版本: {version_text[0] if version_text else 'unknown'}")
    print(f"Chrome: {'已找到' if 'chrome' in browsers else '未找到'}")
    print(f"Edge: {'已找到' if 'edge' in browsers else '未找到'}")
    if cdp_info:
        print(f"CDP 9222: 已连接 ({cdp_info.get('Browser', 'unknown')})")
    else:
        print("CDP 9222: 未连接")
    print(f"Neo 扩展目录: {'存在' if extension_path.exists() else '不存在'}")
    if capture_file:
        print(f"Capture 文件: {'存在' if capture_file.exists() else '不存在'}")
    if verify_session_persistence:
        try:
            ok, message = cdp.verify_browser_session_persistence(
                persistence_browser,
            )
            print(f"Session 持久化: {'通过' if ok else '失败'} ({message})")
        except (OSError, RuntimeError) as exc:
            print(f"Session 持久化: 失败 ({exc})")

    print("\n建议流程:")
    print("1. 在 Edge 打开 edge://extensions 并加载 Neo 扩展目录")
    if extension_path.exists():
        print(f"   扩展目录: {extension_path}")
    print("2. 确保 Edge 以 --remote-debugging-port=9222 运行")
    print("3. 连接 Neo: node vendor/neo/tools/neo.cjs connect 9222")
    print("4. 手动在 Skyscanner 搜一次目标航线")
    print(
        "5. 导出 capture: node vendor/neo/tools/neo.cjs capture export --include-auth > skyscanner-captures.json"
    )
    print("6. 运行 compare 子命令做多地区比价")


async def compare_prices(args: argparse.Namespace) -> int:
    from skyscanner_multi_domain.transports.cdp import compare_via_pages

    project_root = PROJECT_ROOT
    neo = NeoCli(project_root)
    region_codes = [
        code.strip().upper() for code in args.regions.split(",") if code.strip()
    ]
    selected_regions = [REGIONS[code] for code in region_codes if code in REGIONS]
    if not selected_regions:
        print("没有可用的地区代码。", file=sys.stderr)
        return 1

    if args.transport == "page":
        page_quotes = await compare_via_pages(args, selected_regions)
        page_quotes.sort(key=lambda item: (item.price is None, item.price or float("inf")))
        scan_orchestrator.print_quotes(page_quotes)
        winner = next((quote for quote in page_quotes if quote.price is not None), None)
        if winner:
            print(
                f"\n最低价: {winner.price:,.2f} {winner.currency or ''} "
                f"来自 {REGIONS[winner.region].name} ({winner.domain})"
            )
            return 0
        print("\n没有成功从结果页提取到任何价格。")
        return 2

    if args.capture_file:
        captures = url_builder.load_capture_file(Path(args.capture_file))
    else:
        if not neo.available:
            print(
                "未找到 Neo CLI。请先提供 --capture-file，或安装并配置 Neo。",
                file=sys.stderr,
            )
            return 1
        export = neo.run(["capture", "export", "--include-auth"])
        if export.returncode != 0:
            print(export.stderr.strip() or "neo capture export 失败", file=sys.stderr)
            return 1
        captures = json.loads(export.stdout or "[]")

    candidates = url_builder.find_candidate_captures(
        captures, args.origin, args.destination, args.date
    )
    if not candidates:
        print(
            "没有找到匹配该航线的 Neo capture。请先在 Chrome 里手动搜索一次相同航线。",
            file=sys.stderr,
        )
        return 1

    base_capture = candidates[0]
    print("已选取基准 capture:")
    print(f"- URL: {base_capture.get('url')}")
    print(f"- Method: {base_capture.get('method')}")
    print(f"- Timestamp: {base_capture.get('timestamp')}")

    raw_quotes: list[FlightQuote] = []
    if args.transport == "raw":
        timeout = aiohttp.ClientTimeout(total=args.timeout)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            tasks = []
            for region in selected_regions:
                url = url_builder.rewrite_url(
                    str(base_capture.get("url", "")), region, args.date
                )
                body = url_builder.mutate_payload(
                    base_capture.get("requestBody"),
                    args.origin.upper(),
                    args.destination.upper(),
                    args.date,
                    region,
                )
                headers = url_builder.prepare_headers(
                    base_capture.get("requestHeaders") or {},
                    region,
                    url,
                    include_auth=True,
                )
                tasks.append(execute_raw_request(session, region, url, headers, body))
            raw_quotes = await asyncio.gather(*tasks)
    else:
        if not neo.available:
            print("未找到 Neo CLI，无法使用 --transport neo。", file=sys.stderr)
            return 1
        for region in selected_regions:
            url = url_builder.rewrite_url(
                str(base_capture.get("url", "")), region, args.date
            )
            body = url_builder.mutate_payload(
                base_capture.get("requestBody"),
                args.origin.upper(),
                args.destination.upper(),
                args.date,
                region,
            )
            headers = url_builder.prepare_headers(
                base_capture.get("requestHeaders") or {},
                region,
                url,
                include_auth=False,
            )
            quote = execute_neo_request(
                neo=neo,
                region=region,
                url=url,
                headers=headers,
                body=body,
                tab_pattern=args.neo_tab,
            )
            raw_quotes.append(quote)

    raw_quotes.sort(key=lambda item: (item.price is None, item.price or float("inf")))
    scan_orchestrator.print_quotes(raw_quotes)

    winner = next((quote for quote in raw_quotes if quote.price is not None), None)
    if winner:
        print(
            f"\n最低价: {winner.price:,.2f} {winner.currency or ''} "
            f"来自 {REGIONS[winner.region].name} ({winner.domain})"
        )
        if winner.price_path:
            print(f"识别价格路径: {winner.price_path}")
        return 0

    print(
        "\n没有成功提取到任何价格。建议先用 --transport neo，并确保 capture 来自同一路线搜索。"
    )
    return 2


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Compare Skyscanner prices across markets (Scrapling/page primary flow + Neo tools).",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    doctor = subparsers.add_parser("doctor", help="检查 Neo 与本机环境")
    doctor.add_argument("--capture-file", help="可选：已有 Neo export JSON")

    compare = subparsers.add_parser("compare", help="基于 Neo capture 做多地区比价")
    compare.add_argument("--origin", default="PEK", help="出发地 IATA，默认 PEK")
    compare.add_argument("--destination", default="ALA", help="目的地 IATA，默认 ALA")
    compare.add_argument(
        "--date", default=DEFAULT_DATE, help="出发日期 YYYY-MM-DD，默认 2026-04-29"
    )
    compare.add_argument(
        "--regions",
        default=",".join(DEFAULT_REGIONS),
        help=f"地区代码列表，默认 {','.join(DEFAULT_REGIONS)}",
    )
    compare.add_argument(
        "--transport",
        choices=["neo", "raw", "page"],
        default="page",
        help="page: 通过 Edge CDP 直接读取结果页；neo: 通过 neo exec；raw: 直接重放 HTTP 请求",
    )
    compare.add_argument("--capture-file", help="Neo capture export JSON 文件")
    compare.add_argument(
        "--neo-tab", default="skyscanner", help="neo exec 匹配的标签页关键字"
    )
    compare.add_argument("--timeout", type=int, default=30, help="raw 请求超时时间")
    compare.add_argument(
        "--page-wait", type=int, default=8, help="page 模式下打开结果页后的等待秒数"
    )

    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    project_root = PROJECT_ROOT
    neo = NeoCli(project_root)

    if args.command == "doctor":
        print_doctor(neo, Path(args.capture_file) if args.capture_file else None)
        return 0

    if args.command == "compare":
        return asyncio.run(compare_prices(args))

    parser.error("未知命令")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
