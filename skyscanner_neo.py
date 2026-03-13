"""
Skyscanner Neo compatibility layer and legacy capture-based tooling.

Primary scan paths (Scrapling + CDP fallback) have moved to:
- transport_scrapling.py
- transport_cdp.py
- scan_orchestrator.py

This module retains:
- NeoCli wrapper and Neo-based request execution
- Capture file loading, URL rewriting, payload mutation
- doctor / compare CLI subcommands
- Re-exports for backward compatibility
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
import shutil
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Iterable, Optional
from urllib.parse import parse_qsl, quote, urlencode, urlparse, urlunparse

import aiohttp

from app_paths import PROJECT_ROOT
from skyscanner_models import FlightQuote, RegionConfig
from skyscanner_page_parser import (
    PAGE_TEXT_CAPTURE_LIMIT,
    extract_page_quote,
    first_currency,
    parse_float,
)
from skyscanner_regions import (
    DEFAULT_REGIONS,
    REGIONS,
    build_effective_region_codes,
    get_selected_regions,
)

# ---------------------------------------------------------------------------
# Re-exports for backward compatibility
# ---------------------------------------------------------------------------
from scan_orchestrator import (  # noqa: F401
    build_search_url,
    print_quotes,
    quotes_to_dicts,
    run_page_scan,
)
# PLACEHOLDER_REEXPORT_TAIL
from scan_orchestrator import _persist_failure_log  # noqa: F401
from transport_cdp import (  # noqa: F401
    detect_browsers,
    detect_cdp_version,
    ensure_cdp_ready,
    launch_browser_with_cdp,
    prune_browser_profile,
    wait_for_cdp,
)
from transport_scrapling import (  # noqa: F401
    _check_captcha_in_page,
    _extract_scrapling_page_text,
    compare_via_scrapling,
)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

DEFAULT_DATE = "2026-04-29"
DATE_PATH_HINTS = {"date", "outbounddate", "departdate", "departuredate"}
PRICE_KEYS = {"price", "amount", "rawprice", "formattedprice", "totalprice"}
CURRENCY_KEYS = {"currency", "currencycode", "unit", "curr", "symbol"}
URL_HINTS = ("search", "flight", "flights", "conductor", "live")
SAFE_FORWARD_HEADERS = {
    "accept",
    "accept-language",
    "content-type",
    "x-client",
    "x-device",
    "x-locale",
    "x-market",
    "x-platform",
    "x-skyscanner-channelid",
    "x-skyscanner-traveller-context",
}
DROP_HEADERS = {
    "authority",
    "content-length",
    "cookie",
    "host",
    "origin",
    "referer",
    "sec-ch-ua",
    "sec-ch-ua-mobile",
    "sec-ch-ua-platform",
    "sec-fetch-dest",
    "sec-fetch-mode",
    "sec-fetch-site",
}


# ---------------------------------------------------------------------------
# JSON / nested helpers
# ---------------------------------------------------------------------------


def compact_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def nested_get(container: Any, path: list[str]) -> Any:
    current = container
    for part in path:
        if not isinstance(current, dict) or part not in current:
            return None
        current = current[part]
    return current


def nested_set(container: Any, path: list[str], value: Any) -> bool:
    current = container
    for part in path[:-1]:
        if not isinstance(current, dict) or part not in current:
            return False
        current = current[part]
    if not isinstance(current, dict) or path[-1] not in current:
        return False
    current[path[-1]] = value
    return True


def stringify(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    try:
        return compact_json(value)
    except TypeError:
        return str(value)


def parse_date(date_str: str) -> tuple[datetime, str, str]:
    parsed = datetime.strptime(date_str, "%Y-%m-%d")
    return parsed, parsed.strftime("%Y-%m-%d"), parsed.strftime("%y%m%d")


def replace_date_tokens(
    text: str,
    target_iso_date: str,
    target_short_date: str,
    *,
    source_iso_date: str | None = None,
    source_short_date: str | None = None,
) -> str:
    if source_iso_date:
        text = text.replace(source_iso_date, target_iso_date)
    if source_short_date:
        text = text.replace(source_short_date, target_short_date)
    text = re.sub(r"(?<=/)\d{6}(?=/|$)", target_short_date, text)
    return re.sub(r"\b\d{4}-\d{2}-\d{2}\b", target_iso_date, text)


def deep_copy_json(value: Any) -> Any:
    return json.loads(json.dumps(value))


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


# --- PLACEHOLDER_NEO_TAIL ---


# ---------------------------------------------------------------------------
# Capture file & URL rewriting
# ---------------------------------------------------------------------------


def load_capture_file(path: Path) -> list[dict[str, Any]]:
    data = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(data, list):
        return [item for item in data if isinstance(item, dict)]
    if isinstance(data, dict):
        return [data]
    raise ValueError("Capture file must contain a JSON object or array")


def find_candidate_captures(
    captures: Iterable[dict[str, Any]],
    origin: str,
    destination: str,
    travel_date: str,
) -> list[dict[str, Any]]:
    _, iso_date, short_date = parse_date(travel_date)
    origin = origin.upper()
    destination = destination.upper()
    candidates: list[tuple[int, int, dict[str, Any]]] = []

    for capture in captures:
        url = str(capture.get("url", ""))
        url_lower = url.lower()
        method = str(capture.get("method", "GET")).upper()
        body_text = stringify(capture.get("requestBody")).lower()
        response_status = int(capture.get("responseStatus") or 0)
        score = 0

        if "skyscanner" in url_lower:
            score += 3
        if any(hint in url_lower for hint in URL_HINTS):
            score += 4
        if method == "POST":
            score += 3
        if origin.lower() in body_text:
            score += 3
        if destination.lower() in body_text:
            score += 3
        if iso_date.lower() in body_text or short_date.lower() in body_text:
            score += 3
        if response_status == 200:
            score += 2

        if score >= 9:
            candidates.append((score, int(capture.get("timestamp") or 0), capture))

    candidates.sort(key=lambda item: (item[0], item[1]), reverse=True)
    return [capture for _, _, capture in candidates]


def rewrite_url(url: str, region: RegionConfig, travel_date: str) -> str:
    _, iso_date, short_date = parse_date(travel_date)
    current = urlparse(url)
    target = urlparse(region.domain)
    query = dict(parse_qsl(current.query, keep_blank_values=True))

    source_iso_date = None
    source_short_date = None
    iso_match = re.search(r"\b\d{4}-\d{2}-\d{2}\b", url)
    if iso_match:
        source_iso_date = iso_match.group(0)
    short_match = re.search(r"(?<=/)\d{6}(?=/|$)", current.path)
    if short_match:
        source_short_date = short_match.group(0)

    for key in list(query):
        lower = key.lower()
        if lower == "market":
            query[key] = region.code
        elif lower == "locale":
            query[key] = region.locale
        elif lower == "currency":
            query[key] = region.currency
        elif lower in DATE_PATH_HINTS:
            query[key] = iso_date
        elif isinstance(query[key], str):
            query[key] = replace_date_tokens(
                query[key],
                iso_date,
                short_date,
                source_iso_date=source_iso_date,
                source_short_date=source_short_date,
            )

    replaced_path = replace_date_tokens(
        current.path,
        iso_date,
        short_date,
        source_iso_date=source_iso_date,
        source_short_date=source_short_date,
    )
    new_query = urlencode(query, doseq=True)
    return urlunparse(
        (
            target.scheme,
            target.netloc,
            replaced_path,
            current.params,
            new_query,
            current.fragment,
        )
    )


# --- PLACEHOLDER_MUTATE ---


def mutate_payload(
    payload: Any,
    origin: str,
    destination: str,
    travel_date: str,
    region: RegionConfig,
) -> Any:
    if payload is None:
        return None

    body = deep_copy_json(payload)
    parsed_date, iso_date, short_date = parse_date(travel_date)

    source_origin = None
    source_destination = None
    source_iso_date = None
    source_short_date = None

    for legs_path in (["query", "queryLegs"], ["queryLegs"], ["legs"]):
        legs = nested_get(body, legs_path)
        if not isinstance(legs, list):
            continue
        for leg in legs:
            if not isinstance(leg, dict):
                continue
            if source_origin is None:
                origin_place = leg.get("originPlaceId")
                if isinstance(origin_place, dict):
                    source_origin = str(origin_place.get("iata") or "").upper() or None
                elif isinstance(leg.get("origin"), str):
                    source_origin = leg["origin"].upper()
            if source_destination is None:
                destination_place = leg.get("destinationPlaceId")
                if isinstance(destination_place, dict):
                    source_destination = (
                        str(destination_place.get("iata") or "").upper() or None
                    )
                elif isinstance(leg.get("destination"), str):
                    source_destination = leg["destination"].upper()
            if source_iso_date is None:
                leg_date = leg.get("date")
                if isinstance(leg_date, dict):
                    year = leg_date.get("year")
                    month = leg_date.get("month")
                    day = leg_date.get("day")
                    if all(isinstance(part, int) for part in (year, month, day)):
                        source_iso_date = f"{year:04d}-{month:02d}-{day:02d}"
                elif isinstance(leg_date, str) and re.fullmatch(
                    r"\d{4}-\d{2}-\d{2}", leg_date
                ):
                    source_iso_date = leg_date
            if source_origin and source_destination and source_iso_date:
                break
        if source_origin and source_destination and source_iso_date:
            break

    if source_iso_date:
        _, _, source_short_date = parse_date(source_iso_date)

    for path in (
        ["query", "market"],
        ["market"],
        ["marketCode"],
        ["context", "market"],
    ):
        nested_set(body, path, region.code)

    for path in (
        ["query", "locale"],
        ["locale"],
        ["context", "locale"],
    ):
        nested_set(body, path, region.locale)

    for path in (
        ["query", "currency"],
        ["currency"],
        ["context", "currency"],
    ):
        nested_set(body, path, region.currency)

    for legs_path in (["query", "queryLegs"], ["queryLegs"], ["legs"]):
        legs = nested_get(body, legs_path)
        if isinstance(legs, list):
            for leg in legs:
                if not isinstance(leg, dict):
                    continue
                if isinstance(leg.get("originPlaceId"), dict):
                    leg["originPlaceId"]["iata"] = origin
                if isinstance(leg.get("destinationPlaceId"), dict):
                    leg["destinationPlaceId"]["iata"] = destination
                if "origin" in leg and isinstance(leg["origin"], str):
                    leg["origin"] = origin
                if "destination" in leg and isinstance(leg["destination"], str):
                    leg["destination"] = destination
                if isinstance(leg.get("date"), dict):
                    leg["date"]["year"] = parsed_date.year
                    leg["date"]["month"] = parsed_date.month
                    leg["date"]["day"] = parsed_date.day
                elif "date" in leg:
                    leg["date"] = iso_date

    text = compact_json(body)
    if source_origin:
        text = re.sub(
            rf"\b{re.escape(source_origin)}\b", origin, text, flags=re.IGNORECASE
        )
    if source_destination:
        text = re.sub(
            rf"\b{re.escape(source_destination)}\b",
            destination,
            text,
            flags=re.IGNORECASE,
        )
    text = replace_date_tokens(
        text,
        iso_date,
        short_date,
        source_iso_date=source_iso_date,
        source_short_date=source_short_date,
    )

    return json.loads(text)


# --- PLACEHOLDER_HEADERS ---


def prepare_headers(
    capture_headers: dict[str, Any],
    region: RegionConfig,
    source_url: str,
    include_auth: bool,
) -> dict[str, str]:
    parsed = urlparse(source_url)
    headers: dict[str, str] = {}

    for key, value in capture_headers.items():
        lower = key.lower()
        if lower in DROP_HEADERS:
            continue
        if lower in {"authorization", "cookie"} and not include_auth:
            continue
        if lower in SAFE_FORWARD_HEADERS or lower.startswith("x-"):
            headers[key] = str(value)

    headers["Origin"] = f"{parsed.scheme}://{parsed.netloc}"
    headers["Referer"] = region.domain
    headers.setdefault("Accept", "application/json, text/plain, */*")
    headers.setdefault("Content-Type", "application/json")
    return headers


def pick_currency(data: Any) -> Optional[str]:
    if isinstance(data, dict):
        for key, value in data.items():
            if key.lower() in CURRENCY_KEYS:
                currency = first_currency(value)
                if currency:
                    return currency
        for value in data.values():
            currency = pick_currency(value)
            if currency:
                return currency
    elif isinstance(data, list):
        for item in data:
            currency = pick_currency(item)
            if currency:
                return currency
    return None


def collect_price_candidates(
    data: Any, path: str = ""
) -> list[tuple[float, Optional[str], str]]:
    candidates: list[tuple[float, Optional[str], str]] = []

    if isinstance(data, dict):
        currency = pick_currency(data)
        for key, value in data.items():
            key_path = f"{path}.{key}" if path else key
            lower = key.lower()
            if lower in PRICE_KEYS:
                price = parse_float(value)
                if price is not None and price > 0:
                    candidates.append((price, currency, key_path))
            if isinstance(value, dict) and lower == "price":
                nested_price = parse_float(value.get("amount"))
                nested_currency = pick_currency(value) or currency
                if nested_price is not None and nested_price > 0:
                    candidates.append(
                        (nested_price, nested_currency, f"{key_path}.amount")
                    )
            candidates.extend(collect_price_candidates(value, key_path))

    elif isinstance(data, list):
        for index, item in enumerate(data):
            item_path = f"{path}[{index}]" if path else f"[{index}]"
            candidates.extend(collect_price_candidates(item, item_path))

    return candidates


def extract_quote(
    region: RegionConfig,
    source_url: str,
    response_text: str,
    status_code: int,
) -> FlightQuote:
    try:
        payload = json.loads(response_text)
    except json.JSONDecodeError:
        payload = {"raw": response_text}

    candidates = collect_price_candidates(payload)
    candidates = [item for item in candidates if item[0] > 0]
    candidates.sort(key=lambda item: item[0])

    if candidates:
        price, currency, price_path = candidates[0]
        return FlightQuote(
            region=region.code,
            domain=region.domain,
            price=price,
            currency=currency or region.currency,
            source_url=source_url,
            status=f"http_{status_code}",
            price_path=price_path,
        )

    return FlightQuote(
        region=region.code,
        domain=region.domain,
        price=None,
        currency=region.currency,
        source_url=source_url,
        status=f"http_{status_code}",
        error="响应中未识别到价格字段",
    )


# --- PLACEHOLDER_EXEC ---


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
            return extract_quote(region, url, text, response.status)
    except Exception as exc:
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
        compact_json(body),
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
    return extract_quote(region, url, body_text, status_code)


# --- PLACEHOLDER_DOCTOR ---


def print_doctor(neo: NeoCli, capture_file: Optional[Path]) -> None:
    browsers = detect_browsers()
    cdp_info = detect_cdp_version()
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
    from transport_cdp import compare_via_pages

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
        quotes = await compare_via_pages(args, selected_regions)
        quotes.sort(key=lambda item: (item.price is None, item.price or float("inf")))
        print_quotes(quotes)
        winner = next((quote for quote in quotes if quote.price is not None), None)
        if winner:
            print(
                f"\n最低价: {winner.price:,.2f} {winner.currency or ''} "
                f"来自 {REGIONS[winner.region].name} ({winner.domain})"
            )
            return 0
        print("\n没有成功从结果页提取到任何价格。")
        return 2

    if args.capture_file:
        captures = load_capture_file(Path(args.capture_file))
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

    candidates = find_candidate_captures(
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

    quotes: list[FlightQuote] = []
    if args.transport == "raw":
        timeout = aiohttp.ClientTimeout(total=args.timeout)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            tasks = []
            for region in selected_regions:
                url = rewrite_url(str(base_capture.get("url", "")), region, args.date)
                body = mutate_payload(
                    base_capture.get("requestBody"),
                    args.origin.upper(),
                    args.destination.upper(),
                    args.date,
                    region,
                )
                headers = prepare_headers(
                    base_capture.get("requestHeaders") or {},
                    region,
                    url,
                    include_auth=True,
                )
                tasks.append(execute_raw_request(session, region, url, headers, body))
            quotes = await asyncio.gather(*tasks)
    else:
        if not neo.available:
            print("未找到 Neo CLI，无法使用 --transport neo。", file=sys.stderr)
            return 1
        for region in selected_regions:
            url = rewrite_url(str(base_capture.get("url", "")), region, args.date)
            body = mutate_payload(
                base_capture.get("requestBody"),
                args.origin.upper(),
                args.destination.upper(),
                args.date,
                region,
            )
            headers = prepare_headers(
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
            quotes.append(quote)

    quotes.sort(key=lambda item: (item.price is None, item.price or float("inf")))
    print_quotes(quotes)

    winner = next((quote for quote in quotes if quote.price is not None), None)
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


# --- PLACEHOLDER_CLI ---


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Compare Skyscanner prices across markets (Scrapling/page primary flow + legacy Neo tools).",
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
