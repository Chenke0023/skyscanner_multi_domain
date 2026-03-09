"""
Practical CLI for Skyscanner multi-market scans via Edge page reads.

Default path:
1. Use the local Edge instance on CDP port 9222.
2. Open each market's result page.
3. Read the rendered page text and extract the "Cheapest" price.

Example:
  python cli.py page -o 北京 -d 阿拉木图 -t 2026-04-29
"""

from __future__ import annotations

import argparse
import asyncio
from datetime import datetime
from pathlib import Path
from typing import Optional

from app_paths import PROJECT_ROOT, get_reports_dir
from fx_rates import FxRateService
from location_resolver import LocationResolver
from skyscanner_neo import (
    DEFAULT_REGIONS,
    NeoCli,
    REGIONS,
    build_effective_region_codes,
    detect_cdp_version,
    print_doctor,
    quotes_to_dicts,
    run_page_scan,
)


QuoteRow = dict[str, object]
SimplifiedQuoteRow = dict[str, str | float | None]


class SimpleCLI:
    def __init__(self) -> None:
        self.project_root = PROJECT_ROOT
        self.location_resolver = LocationResolver()
        self.fx_rates = FxRateService()

    def normalize_location(self, value: str, prefer_metro: bool) -> str:
        return self.location_resolver.normalize_location(value, prefer_metro=prefer_metro)

    def resolve_location(self, value: str, prefer_metro: bool):
        return self.location_resolver.resolve_location(value, prefer_metro=prefer_metro)

    def build_effective_regions(
        self,
        origin_value: str,
        destination_value: str,
        *,
        prefer_origin_metro: bool,
        manual_region_codes: list[str] | None = None,
    ) -> tuple[object, object, list[str]]:
        origin = self.resolve_location(origin_value, prefer_metro=prefer_origin_metro)
        destination = self.resolve_location(destination_value, prefer_metro=False)
        regions = build_effective_region_codes(
            origin_country=origin.country,
            destination_country=destination.country,
            manual_region_codes=manual_region_codes or [],
        )
        return origin, destination, regions

    def print_banner(self) -> None:
        print(
            """
╔═══════════════════════════════════════════════════════════════╗
║      Skyscanner 多市场 CLI（Edge 页面模式）                  ║
║      一条命令打开各站点并提取最低价                           ║
╚═══════════════════════════════════════════════════════════════╝
            """.strip()
        )

    def to_cny(
        self, price: Optional[float], currency: Optional[str]
    ) -> Optional[float]:
        return self.fx_rates.convert_to_cny(price, currency)

    def simplify_quotes(self, quotes: list[QuoteRow]) -> list[SimplifiedQuoteRow]:
        simplified: list[SimplifiedQuoteRow] = []
        for quote in quotes:
            price = quote.get("price")
            currency = quote.get("currency")
            if price is not None and not isinstance(price, (int, float)):
                continue
            if currency is not None and not isinstance(currency, str):
                continue
            region_name = quote.get("region_name")
            source_url = quote.get("source_url")
            if not isinstance(region_name, str) or not isinstance(source_url, str):
                continue
            numeric_price = float(price) if price is not None else None
            cny_price = self.to_cny(numeric_price, currency)
            if numeric_price is None or not currency:
                continue
            simplified.append(
                {
                    "region_name": region_name,
                    "display_price": f"{numeric_price:,.2f} {currency.upper()}",
                    "cny_price": cny_price,
                    "link": source_url,
                }
            )
        simplified.sort(
            key=lambda item: (
                item["cny_price"] is None,
                item["cny_price"] if isinstance(item["cny_price"], (int, float)) else float("inf"),
                str(item["region_name"]),
            )
        )
        return simplified

    def build_markdown_table(
        self,
        rows: list[SimplifiedQuoteRow],
        origin: str,
        destination: str,
        date: str,
    ) -> str:
        lines = [
            f"# Skyscanner 比价结果",
            "",
            f"- 航线: `{origin} -> {destination}`",
            f"- 日期: `{date}`",
            f"- 生成时间: `{datetime.now().isoformat(timespec='seconds')}`",
            "",
        ]
        if not rows:
            lines.append("暂无可用价格结果。")
            return "\n".join(lines) + "\n"

        lines.extend(
            [
                "| 地区 | 原币价格 | 价格（人民币） | 链接 |",
                "| --- | ---: | ---: | --- |",
            ]
        )
        for row in rows:
            cny_text = (
                f"¥{row['cny_price']:,.2f}"
                if isinstance(row.get("cny_price"), (int, float))
                else "待换算"
            )
            lines.append(
                f"| {row['region_name']} | {row['display_price']} | {cny_text} | [打开结果页]({row['link']}) |"
            )
        return "\n".join(lines) + "\n"

    def print_quotes(self, rows: list[SimplifiedQuoteRow]) -> None:
        if not rows:
            print("\n暂无可用价格结果。")
            return
        print("\n| 地区 | 原币价格 | 价格（人民币） | 链接 |")
        print("| --- | ---: | ---: | --- |")
        for row in rows:
            cny_text = (
                f"¥{row['cny_price']:,.2f}"
                if isinstance(row.get("cny_price"), (int, float))
                else "待换算"
            )
            print(
                f"| {row['region_name']} | {row['display_price']} | {cny_text} | {row['link']} |"
            )

    def save_results(
        self,
        quotes: list[QuoteRow],
        origin: str,
        destination: str,
        date: str,
    ) -> Path:
        output_dir = get_reports_dir()
        filename = (
            output_dir / f"edge_page_{origin}_{destination}_{date.replace('-', '')}.md"
        )
        rows = self.simplify_quotes(quotes)
        payload = self.build_markdown_table(rows, origin, destination, date)
        filename.write_text(payload, encoding="utf-8")
        return filename

    async def run_page_command(self, args: argparse.Namespace) -> int:
        manual_regions = [
            code.strip().upper() for code in args.regions.split(",") if code.strip()
        ]
        origin, destination, regions = self.build_effective_regions(
            args.origin,
            args.destination,
            prefer_origin_metro=not args.exact_airport,
            manual_region_codes=manual_regions,
        )
        print(f"本次实际地区: {', '.join(regions)}")

        quotes = await run_page_scan(
            origin=origin.code,
            destination=destination.code,
            date=args.date,
            region_codes=regions,
            page_wait=args.wait,
            timeout=args.timeout,
        )
        if not quotes:
            print("没有返回任何结果。检查地区代码或 Edge/CDP 环境。")
            return 1

        quote_dicts = quotes_to_dicts(quotes)
        rows = self.simplify_quotes(quote_dicts)
        self.print_quotes(rows)

        winner = next(
            (row for row in rows if isinstance(row.get("cny_price"), (int, float))),
            None,
        )
        if winner:
            print(f"\n最低价: ¥{winner['cny_price']:,.2f} 来自 {winner['region_name']}")
        elif rows:
            print("\n已提取市场价格，但人民币换算暂不可用。")
        else:
            print("\n未能成功提取任何市场价格。")

        if args.save:
            saved = self.save_results(quote_dicts, origin.code, destination.code, args.date)
            print(f"结果已保存到: {saved}")

        if not args.exact_airport and args.origin in {"北京", "beijing", "BEIJING"}:
            print(
                "提示: 本次默认使用 BJSA（北京任意机场）。如需严格 PEK，请加 --exact-airport 或直接传 PEK。"
            )
        return 0 if winner else 2

    def interactive_page(self) -> int:
        self.print_banner()
        origin = input("出发地（如 北京 / PEK）: ").strip()
        destination = input("目的地（如 阿拉木图 / ALA）: ").strip()
        date = input("日期（YYYY-MM-DD）: ").strip()
        regions = input(
            f"额外地区代码（默认会自动包含 {','.join(DEFAULT_REGIONS)}）: "
        ).strip()
        args = argparse.Namespace(
            origin=origin,
            destination=destination,
            date=date,
            regions=regions,
            wait=10,
            timeout=30,
            save=True,
            exact_airport=False,
        )
        return asyncio.run(self.run_page_command(args))


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Skyscanner 多市场 CLI。默认推荐 Edge 页面模式。",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例:
  python cli.py doctor
  python cli.py page -o 北京 -d 阿拉木图 -t 2026-04-29
  python cli.py page -o PEK -d ALA -t 2026-04-29 --exact-airport
  python cli.py page -o 北京 -d 阿拉木图 -t 2026-04-29 -r CN,US,UK,SG,HK
        """,
    )

    subparsers = parser.add_subparsers(dest="command")

    doctor = subparsers.add_parser("doctor", help="检查 Edge/CDP/Neo 环境")
    doctor.add_argument("--capture-file", help="可选：检查某个 Neo export 文件是否存在")

    page = subparsers.add_parser("page", help="打开各市场结果页并抽取最低价")
    page.add_argument(
        "-o", "--origin", required=True, help="出发地（中文、IATA 或 metro code）"
    )
    page.add_argument(
        "-d", "--destination", required=True, help="目的地（中文或 IATA）"
    )
    page.add_argument("-t", "--date", required=True, help="出发日期 YYYY-MM-DD")
    page.add_argument(
        "-r", "--regions", default="", help="额外地区代码，逗号分隔，会叠加到智能默认地区上"
    )
    page.add_argument("--wait", type=int, default=10, help="打开结果页后的等待秒数")
    page.add_argument("--timeout", type=int, default=30, help="HTTP/CDP 超时")
    page.add_argument(
        "--exact-airport",
        action="store_true",
        help="关闭城市 metro code 映射，例如北京不再转成 BJSA",
    )
    page.add_argument(
        "--save", dest="save", action="store_true", default=True, help="保存 JSON 结果"
    )
    page.add_argument(
        "--no-save", dest="save", action="store_false", help="不保存 JSON 结果"
    )

    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    cli = SimpleCLI()

    if args.command is None:
        return cli.interactive_page()

    if args.command == "doctor":
        neo = NeoCli(cli.project_root)
        print_doctor(neo, Path(args.capture_file) if args.capture_file else None)
        cdp_info = detect_cdp_version()
        if cdp_info:
            print(f"\n当前 CDP 浏览器: {cdp_info.get('Browser', 'unknown')}")
        return 0

    if args.command == "page":
        return asyncio.run(cli.run_page_command(args))

    parser.error("未知命令")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
