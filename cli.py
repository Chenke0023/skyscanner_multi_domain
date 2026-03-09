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
from skyscanner_neo import (
    DEFAULT_REGIONS,
    NeoCli,
    REGIONS,
    detect_cdp_version,
    print_doctor,
    quotes_to_dicts,
    run_page_scan,
)


AIRPORT_CODES = {
    "北京": "PEK",
    "beijing": "PEK",
    "上海": "PVG",
    "shanghai": "PVG",
    "广州": "CAN",
    "guangzhou": "CAN",
    "深圳": "SZX",
    "shenzhen": "SZX",
    "成都": "CTU",
    "chengdu": "CTU",
    "杭州": "HGH",
    "hangzhou": "HGH",
    "西安": "XIY",
    "xian": "XIY",
    "重庆": "CKG",
    "chongqing": "CKG",
    "香港": "HKG",
    "hong kong": "HKG",
    "台北": "TPE",
    "taipei": "TPE",
    "东京": "NRT",
    "tokyo": "NRT",
    "首尔": "ICN",
    "seoul": "ICN",
    "新加坡": "SIN",
    "singapore": "SIN",
    "阿拉木图": "ALA",
    "almaty": "ALA",
    "雅加达": "JKT",
    "jakarta": "JKT",
    "伦敦": "LHR",
    "london": "LHR",
    "纽约": "JFK",
    "new york": "JFK",
}

METRO_CODES = {
    "北京": "BJSA",
    "beijing": "BJSA",
    "上海": "SHAA",
    "shanghai": "SHAA",
    "伦敦": "LOND",
    "london": "LOND",
    "纽约": "NYCA",
    "new york": "NYCA",
}

FX_TO_CNY = {
    "CNY": 1.0,
    "USD": 6.91,
    "GBP": 9.29,
    "SGD": 5.44,
    "HKD": 0.88,
    "EUR": 7.49,
    "JPY": 0.046,
    "KZT": 0.013,
}


QuoteRow = dict[str, object]
SimplifiedQuoteRow = dict[str, str | float]


class SimpleCLI:
    def __init__(self) -> None:
        self.project_root = PROJECT_ROOT

    def normalize_location(self, value: str, prefer_metro: bool) -> str:
        raw = value.strip()
        if not raw:
            raise ValueError("地点不能为空。")
        upper = raw.upper()
        if upper in set(METRO_CODES.values()) | set(AIRPORT_CODES.values()):
            return upper
        if len(upper) in {3, 4} and upper.isascii() and upper.isalpha():
            return upper
        lookup = raw.lower()
        if prefer_metro and raw in METRO_CODES:
            return METRO_CODES[raw]
        if prefer_metro and lookup in METRO_CODES:
            return METRO_CODES[lookup]
        if raw in AIRPORT_CODES:
            return AIRPORT_CODES[raw]
        if lookup in AIRPORT_CODES:
            return AIRPORT_CODES[lookup]
        raise ValueError(
            f"无法识别地点“{raw}”。请使用常见城市名，或直接输入 IATA/城市代码（例如 PEK、ALA、JKT、BJSA）。"
        )

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
        if price is None or not currency or currency not in FX_TO_CNY:
            return None
        return round(price * FX_TO_CNY[currency], 2)

    def simplify_quotes(self, quotes: list[QuoteRow]) -> list[SimplifiedQuoteRow]:
        simplified: list[SimplifiedQuoteRow] = []
        for quote in quotes:
            price = quote.get("price")
            currency = quote.get("currency")
            if price is not None and not isinstance(price, (int, float)):
                continue
            if currency is not None and not isinstance(currency, str):
                continue
            cny_price = self.to_cny(
                float(price) if price is not None else None, currency
            )
            if cny_price is None:
                continue
            region_name = quote.get("region_name")
            source_url = quote.get("source_url")
            if not isinstance(region_name, str) or not isinstance(source_url, str):
                continue
            simplified.append(
                {
                    "region_name": region_name,
                    "cny_price": cny_price,
                    "link": source_url,
                }
            )
        simplified.sort(key=lambda item: item["cny_price"])
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
                "| 地区 | 价格（人民币） | 链接 |",
                "| --- | ---: | --- |",
            ]
        )
        for row in rows:
            lines.append(
                f"| {row['region_name']} | ¥{row['cny_price']:,.2f} | [打开结果页]({row['link']}) |"
            )
        return "\n".join(lines) + "\n"

    def print_quotes(self, rows: list[SimplifiedQuoteRow]) -> None:
        if not rows:
            print("\n暂无可用价格结果。")
            return
        print("\n| 地区 | 价格（人民币） | 链接 |")
        print("| --- | ---: | --- |")
        for row in rows:
            print(
                f"| {row['region_name']} | ¥{row['cny_price']:,.2f} | {row['link']} |"
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
        origin = self.normalize_location(
            args.origin, prefer_metro=not args.exact_airport
        )
        destination = self.normalize_location(args.destination, prefer_metro=False)
        regions = [
            code.strip().upper() for code in args.regions.split(",") if code.strip()
        ]

        quotes = await run_page_scan(
            origin=origin,
            destination=destination,
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

        winner = rows[0] if rows else None
        if winner:
            print(f"\n最低价: ¥{winner['cny_price']:,.2f} 来自 {winner['region_name']}")
        else:
            print("\n未能成功提取任何市场价格。")

        if args.save:
            saved = self.save_results(quote_dicts, origin, destination, args.date)
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
            f"地区代码（默认 {','.join(DEFAULT_REGIONS)}）: "
        ).strip() or ",".join(DEFAULT_REGIONS)
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
        "-r", "--regions", default=",".join(DEFAULT_REGIONS), help="地区代码，逗号分隔"
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
