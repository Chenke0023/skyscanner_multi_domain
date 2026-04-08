"""
Practical CLI for Skyscanner multi-market scans via Edge page reads.

Default path:
1. Use the local Edge instance on CDP port 9222.
2. Open each market's result page.
3. Read the rendered page text and extract both the "Best" and "Cheapest" prices.

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
from date_window import build_date_window
from fx_rates import FxRateService
from location_resolver import LocationResolver, ResolvedLocation
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
CombinedQuoteRow = dict[str, str | float | None]


BEST_LABEL = "最佳"
CHEAPEST_LABEL = "最低价"


class SimpleCLI:
    def __init__(self) -> None:
        self.project_root = PROJECT_ROOT
        self.location_resolver = LocationResolver()
        self.fx_rates = FxRateService()

    def normalize_location(self, value: str, prefer_metro: bool) -> str:
        return self.location_resolver.normalize_location(
            value, prefer_metro=prefer_metro
        )

    def resolve_location(self, value: str, prefer_metro: bool) -> ResolvedLocation:
        return self.location_resolver.resolve_location(value, prefer_metro=prefer_metro)

    def build_effective_regions(
        self,
        origin_value: str,
        destination_value: str,
        *,
        prefer_origin_metro: bool,
        manual_region_codes: list[str] | None = None,
    ) -> tuple[ResolvedLocation, ResolvedLocation, list[str]]:
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
║      一条命令打开各站点并提取最佳价与最低价                   ║
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
            currency = quote.get("currency")
            if currency is not None and not isinstance(currency, str):
                continue
            region_name = quote.get("region_name")
            source_url = quote.get("source_url")
            if not isinstance(region_name, str) or not isinstance(source_url, str):
                continue

            best_price = quote.get("best_price")
            cheapest_price = quote.get("cheapest_price")

            if best_price is not None and not isinstance(best_price, (int, float)):
                continue
            if cheapest_price is not None and not isinstance(
                cheapest_price, (int, float)
            ):
                continue
            if best_price is None and cheapest_price is None:
                continue

            best_numeric = float(best_price) if best_price is not None else None
            cheapest_numeric = (
                float(cheapest_price) if cheapest_price is not None else None
            )
            best_cny = self.to_cny(best_numeric, currency) if currency else None
            cheapest_cny = self.to_cny(cheapest_numeric, currency) if currency else None

            simplified.append(
                {
                    "region_name": region_name,
                    "best_display_price": (
                        f"{best_numeric:,.2f} {currency.upper()}"
                        if best_numeric is not None and currency
                        else None
                    ),
                    "best_cny_price": best_cny,
                    "cheapest_display_price": (
                        f"{cheapest_numeric:,.2f} {currency.upper()}"
                        if cheapest_numeric is not None and currency
                        else None
                    ),
                    "cheapest_cny_price": cheapest_cny,
                    "link": source_url,
                    "status": str(quote.get("status") or "-"),
                    "error": str(quote.get("error") or "-"),
                }
            )
        simplified.sort(
            key=lambda item: (
                item["cheapest_cny_price"] is None,
                item["cheapest_cny_price"]
                if isinstance(item["cheapest_cny_price"], (int, float))
                else float("inf"),
                item["best_cny_price"] is None,
                item["best_cny_price"]
                if isinstance(item["best_cny_price"], (int, float))
                else float("inf"),
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
                "| 地区 | 最佳（原币） | 最佳（人民币） | 最低价（原币） | 最低价（人民币） | 状态 | 错误 | 链接 |",
                "| --- | ---: | ---: | ---: | ---: | --- | --- | --- |",
            ]
        )
        for row in rows:
            best_cny_text = (
                f"¥{row['best_cny_price']:,.2f}"
                if isinstance(row.get("best_cny_price"), (int, float))
                else "-"
            )
            cheapest_cny_text = (
                f"¥{row['cheapest_cny_price']:,.2f}"
                if isinstance(row.get("cheapest_cny_price"), (int, float))
                else "-"
            )
            lines.append(
                f"| {row['region_name']} | {row.get('best_display_price') or '-'} | {best_cny_text} | {row.get('cheapest_display_price') or '-'} | {cheapest_cny_text} | {row.get('status') or '-'} | {row.get('error') or '-'} | [打开结果页]({row['link']}) |"
            )
        return "\n".join(lines) + "\n"

    def build_combined_markdown_table(
        self,
        rows: list[CombinedQuoteRow],
        origin: str,
        destination: str,
    ) -> str:
        dates: list[str] = [
            date for row in rows if isinstance(date := row.get("date"), str)
        ]
        date_range = f"{min(dates)} ~ {max(dates)}" if dates and all(dates) else "-"
        lines = [
            "# Skyscanner 比价结果（多日期）",
            "",
            f"- 航线: `{origin} -> {destination}`",
            f"- 日期范围: `{date_range}`",
            f"- 生成时间: `{datetime.now().isoformat(timespec='seconds')}`",
            "",
        ]
        if not rows:
            lines.append("暂无可用价格结果。")
            return "\n".join(lines) + "\n"

        lines.extend(
            [
                "| 日期 | 地区 | 最佳（原币） | 最佳（人民币） | 最低价（原币） | 最低价（人民币） | 状态 | 错误 | 链接 |",
                "| --- | --- | ---: | ---: | ---: | ---: | --- | --- | --- |",
            ]
        )
        for row in rows:
            best_cny_text = (
                f"¥{row['best_cny_price']:,.2f}"
                if isinstance(row.get("best_cny_price"), (int, float))
                else "-"
            )
            cheapest_cny_text = (
                f"¥{row['cheapest_cny_price']:,.2f}"
                if isinstance(row.get("cheapest_cny_price"), (int, float))
                else "-"
            )
            link = row.get("link") or "-"
            link_cell = f"[打开结果页]({link})" if link != "-" else "-"
            lines.append(
                "| "
                + " | ".join(
                    [
                        str(row.get("date") or "-"),
                        str(row.get("region_name") or "-"),
                        str(row.get("best_display_price") or "-"),
                        best_cny_text,
                        str(row.get("cheapest_display_price") or "-"),
                        cheapest_cny_text,
                        str(row.get("status") or "-"),
                        str(row.get("error") or "-"),
                        link_cell,
                    ]
                )
                + " |"
            )
        return "\n".join(lines) + "\n"

    def build_window_markdown_table(
        self,
        rows_by_date: list[tuple[str, list[SimplifiedQuoteRow]]],
        origin: str,
        destination: str,
        start_date: str,
        end_date: str,
    ) -> str:
        lines = [
            "# Skyscanner 比价结果（日期窗口）",
            "",
            f"- 航线: `{origin} -> {destination}`",
            f"- 日期窗口: `{start_date}` ~ `{end_date}`",
            f"- 生成时间: `{datetime.now().isoformat(timespec='seconds')}`",
            "",
        ]
        total_rows = sum(len(rows) for _, rows in rows_by_date)
        if total_rows == 0:
            lines.append("暂无可用价格结果。")
            return "\n".join(lines) + "\n"

        lines.extend(
            [
                "| 日期 | 地区 | 最佳（原币） | 最佳（人民币） | 最低价（原币） | 最低价（人民币） | 状态 | 错误 | 链接 |",
                "| --- | --- | ---: | ---: | ---: | ---: | --- | --- | --- |",
            ]
        )
        for date, rows in rows_by_date:
            for row in rows:
                best_cny_text = (
                    f"¥{row['best_cny_price']:,.2f}"
                    if isinstance(row.get("best_cny_price"), (int, float))
                    else "-"
                )
                cheapest_cny_text = (
                    f"¥{row['cheapest_cny_price']:,.2f}"
                    if isinstance(row.get("cheapest_cny_price"), (int, float))
                    else "-"
                )
                lines.append(
                    f"| {date} | {row['region_name']} | {row.get('best_display_price') or '-'} | {best_cny_text} | {row.get('cheapest_display_price') or '-'} | {cheapest_cny_text} | {row.get('status') or '-'} | {row.get('error') or '-'} | [打开结果页]({row['link']}) |"
                )
        return "\n".join(lines) + "\n"

    def print_quotes(self, rows: list[SimplifiedQuoteRow]) -> None:
        if not rows:
            print("\n暂无可用价格结果。")
            return
        print(
            "\n| 地区 | 最佳（原币） | 最佳（人民币） | 最低价（原币） | 最低价（人民币） | 状态 | 错误 | 链接 |"
        )
        print("| --- | ---: | ---: | ---: | ---: | --- | --- | --- |")
        for row in rows:
            best_cny_text = (
                f"¥{row['best_cny_price']:,.2f}"
                if isinstance(row.get("best_cny_price"), (int, float))
                else "-"
            )
            cheapest_cny_text = (
                f"¥{row['cheapest_cny_price']:,.2f}"
                if isinstance(row.get("cheapest_cny_price"), (int, float))
                else "-"
            )
            print(
                f"| {row['region_name']} | {row.get('best_display_price') or '-'} | {best_cny_text} | {row.get('cheapest_display_price') or '-'} | {cheapest_cny_text} | {row.get('status') or '-'} | {row.get('error') or '-'} | {row['link']} |"
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

    def save_combined_results(
        self,
        rows: list[CombinedQuoteRow],
        origin: str,
        destination: str,
        date: str,
    ) -> Path:
        output_dir = get_reports_dir()
        filename = (
            output_dir
            / f"edge_page_{origin}_{destination}_{date.replace('-', '')}_combined.md"
        )
        payload = self.build_combined_markdown_table(rows, origin, destination)
        filename.write_text(payload, encoding="utf-8")
        return filename

    def save_window_results(
        self,
        rows_by_date: list[tuple[str, list[SimplifiedQuoteRow]]],
        origin: str,
        destination: str,
        start_date: str,
        end_date: str,
    ) -> Path:
        output_dir = get_reports_dir()
        start_stamp = start_date.replace("-", "")
        end_stamp = end_date.replace("-", "")
        filename = (
            output_dir
            / f"edge_page_{origin}_{destination}_{start_stamp}_{end_stamp}_summary.md"
        )
        payload = self.build_window_markdown_table(
            rows_by_date, origin, destination, start_date, end_date
        )
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

        date_window_days = max(int(getattr(args, "date_window", 0)), 0)
        date_list = build_date_window(args.date, date_window_days)
        rows_by_date: list[tuple[str, list[SimplifiedQuoteRow]]] = []
        any_rows = False
        any_winner = False

        for date in date_list:
            print(f"\n日期: {date}")
            quotes = await run_page_scan(
                origin=origin.code,
                destination=destination.code,
                date=date,
                region_codes=regions,
                page_wait=args.wait,
                timeout=args.timeout,
                transport=args.transport,
            )
            if not quotes:
                print("没有返回任何结果。检查地区代码或 Edge/CDP 环境。")
                rows_by_date.append((date, []))
                continue

            quote_dicts = quotes_to_dicts(quotes)
            rows = self.simplify_quotes(quote_dicts)
            rows_by_date.append((date, rows))
            if rows:
                any_rows = True

            self.print_quotes(rows)

            best_winner = next(
                (
                    row
                    for row in rows
                    if isinstance(row.get("best_cny_price"), (int, float))
                ),
                None,
            )
            cheapest_winner = next(
                (
                    row
                    for row in rows
                    if isinstance(row.get("cheapest_cny_price"), (int, float))
                ),
                None,
            )
            if best_winner:
                any_winner = True
                print(
                    f"最佳: ¥{best_winner['best_cny_price']:,.2f} 来自 {best_winner['region_name']}"
                )
            if cheapest_winner:
                any_winner = True
                print(
                    f"最低价: ¥{cheapest_winner['cheapest_cny_price']:,.2f} 来自 {cheapest_winner['region_name']}"
                )
            elif rows:
                print("已提取市场价格，但人民币换算暂不可用。")
            else:
                print("未能成功提取任何市场价格。")

            if args.save:
                saved = self.save_results(
                    quote_dicts, origin.code, destination.code, date
                )
                print(f"结果已保存到: {saved}")

        if args.save and rows_by_date:
            start_date = date_list[0]
            end_date = date_list[-1]
            summary_path = self.save_window_results(
                rows_by_date, origin.code, destination.code, start_date, end_date
            )
            print(f"窗口汇总已保存到: {summary_path}")

        if not args.exact_airport and args.origin in {"北京", "beijing", "BEIJING"}:
            print(
                "提示: 本次默认使用 BJSA（北京任意机场）。如需严格 PEK，请加 --exact-airport 或直接传 PEK。"
            )
        if not any_rows:
            return 1
        return 0 if any_winner else 2

    def interactive_page(self) -> int:
        self.print_banner()
        origin = input("出发地（如 北京 / PEK）: ").strip()
        destination = input("目的地（如 阿拉木图 / ALA）: ").strip()
        date = input("日期（YYYY-MM-DD）: ").strip()
        date_window_raw = input("日期窗口 ±天数（默认 3）: ").strip()
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
            date_window=int(date_window_raw) if date_window_raw else 3,
            exact_airport=False,
            transport="scrapling",
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
  python cli.py page -o 北京 -d 阿拉木图 -t 2026-04-29 --date-window 0
  python cli.py page -o PEK -d ALA -t 2026-04-29 --exact-airport
  python cli.py page -o 北京 -d 阿拉木图 -t 2026-04-29 -r CN,UK,SG,HK
        """,
    )

    subparsers = parser.add_subparsers(dest="command")

    doctor = subparsers.add_parser("doctor", help="检查 Edge/CDP/Neo 环境")
    doctor.add_argument("--capture-file", help="可选：检查某个 Neo export 文件是否存在")

    page = subparsers.add_parser("page", help="打开各市场结果页并抽取最佳价和最低价")
    page.add_argument(
        "-o", "--origin", required=True, help="出发地（中文、IATA 或 metro code）"
    )
    page.add_argument(
        "-d", "--destination", required=True, help="目的地（中文或 IATA）"
    )
    page.add_argument("-t", "--date", required=True, help="出发日期 YYYY-MM-DD")
    page.add_argument(
        "--date-window",
        type=int,
        default=3,
        help="日期前后扫窗天数（默认 ±3 天）",
    )
    page.add_argument(
        "-r",
        "--regions",
        default="",
        help="额外地区代码，逗号分隔，会叠加到智能默认地区上",
    )
    page.add_argument("--wait", type=int, default=10, help="打开结果页后的等待秒数")
    page.add_argument("--timeout", type=int, default=30, help="HTTP/CDP 超时")
    page.add_argument(
        "--transport",
        choices=["scrapling", "page"],
        default="scrapling",
        help="scrapling: 直接抓取页面文本；page: 通过 Edge CDP 读取结果页",
    )
    page.add_argument(
        "--exact-airport",
        action="store_true",
        help="关闭城市 metro code 映射，例如北京不再转成 BJSA",
    )
    page.add_argument(
        "--save",
        dest="save",
        action="store_true",
        default=True,
        help="保存 Markdown 结果",
    )
    page.add_argument(
        "--no-save", dest="save", action="store_false", help="不保存 Markdown 结果"
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
