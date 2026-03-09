"""
Skyscanner multi-domain comparison powered by Neo captures.

This script does two related jobs:
1. Validate whether Neo is available and whether this machine is ready to use it.
2. Reuse a captured Skyscanner search request, rewrite market/locale/currency
   for multiple regions, then compare the best and cheapest prices returned by each region.

Recommended workflow:
1. Install Neo and load its Chrome extension.
2. Open a real Skyscanner page in Chrome and manually search the same route once.
3. Export captures with auth:
      neo capture export --include-auth > skyscanner-captures.json
4. Run:
      python skyscanner_neo.py compare --capture-file skyscanner-captures.json

If Neo is connected to Chrome, the script can also call `neo exec` directly.
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
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable, Optional
from urllib.error import URLError
from urllib.request import urlopen
from urllib.parse import parse_qsl, quote, urlencode, urlparse, urlunparse
import time

import aiohttp

from app_paths import PROJECT_ROOT, get_browser_profile_dir


@dataclass(frozen=True)
class RegionConfig:
    code: str
    name: str
    domain: str
    locale: str
    currency: str


@dataclass
class FlightQuote:
    region: str
    domain: str
    price: Optional[float]
    currency: Optional[str]
    source_url: str
    status: str
    price_path: Optional[str] = None
    best_price: Optional[float] = None
    best_price_path: Optional[str] = None
    cheapest_price: Optional[float] = None
    cheapest_price_path: Optional[str] = None
    error: Optional[str] = None


REGIONS: dict[str, RegionConfig] = {
    "CN": RegionConfig("CN", "中国", "https://www.skyscanner.cn", "zh-CN", "CNY"),
    "US": RegionConfig("US", "美国", "https://www.skyscanner.com", "en-US", "USD"),
    "UK": RegionConfig("UK", "英国", "https://www.skyscanner.co.uk", "en-GB", "GBP"),
    "SG": RegionConfig("SG", "新加坡", "https://www.skyscanner.sg", "en-SG", "SGD"),
    "HK": RegionConfig("HK", "香港", "https://www.skyscanner.com.hk", "zh-HK", "HKD"),
    "KZ": RegionConfig("KZ", "哈萨克斯坦", "https://www.skyscanner.kz", "ru-RU", "KZT"),
    "JP": RegionConfig("JP", "日本", "https://www.skyscanner.jp", "ja-JP", "JPY"),
    "DE": RegionConfig("DE", "德国", "https://www.skyscanner.de", "de-DE", "EUR"),
    "KR": RegionConfig("KR", "韩国", "https://www.skyscanner.co.kr", "ko-KR", "KRW"),
    "SE": RegionConfig("SE", "瑞典", "https://www.skyscanner.se", "sv-SE", "SEK"),
    "ID": RegionConfig(
        "ID", "印度尼西亚", "https://www.skyscanner.co.id", "id-ID", "IDR"
    ),
    "FR": RegionConfig("FR", "法国", "https://www.skyscanner.fr", "fr-FR", "EUR"),
    "IT": RegionConfig("IT", "意大利", "https://www.skyscanner.it", "it-IT", "EUR"),
    "ES": RegionConfig("ES", "西班牙", "https://www.skyscanner.es", "es-ES", "EUR"),
    "NL": RegionConfig("NL", "荷兰", "https://www.skyscanner.nl", "nl-NL", "EUR"),
    "PT": RegionConfig("PT", "葡萄牙", "https://www.skyscanner.pt", "pt-PT", "EUR"),
    "IE": RegionConfig("IE", "爱尔兰", "https://www.skyscanner.ie", "en-IE", "EUR"),
    "CH": RegionConfig("CH", "瑞士", "https://www.skyscanner.ch", "de-CH", "CHF"),
    "AT": RegionConfig("AT", "奥地利", "https://www.skyscanner.at", "de-AT", "EUR"),
    "AU": RegionConfig(
        "AU", "澳大利亚", "https://www.skyscanner.com.au", "en-AU", "AUD"
    ),
    "BR": RegionConfig("BR", "巴西", "https://www.skyscanner.com.br", "pt-BR", "BRL"),
    "CA": RegionConfig("CA", "加拿大", "https://www.skyscanner.ca", "en-CA", "CAD"),
    "IN": RegionConfig("IN", "印度", "https://www.skyscanner.co.in", "en-IN", "INR"),
    "MX": RegionConfig("MX", "墨西哥", "https://www.skyscanner.com.mx", "es-MX", "MXN"),
    "RU": RegionConfig("RU", "俄罗斯", "https://ru.skyscanner.com", "ru-RU", "RUB"),
}

BASELINE_REGIONS = ("CN", "HK", "SG", "US", "UK")
COUNTRY_TO_REGION_CODES: dict[str, tuple[str, ...]] = {
    "CN": ("CN",),
    "HK": ("HK",),
    "SG": ("SG",),
    "US": ("US",),
    "JP": ("JP",),
    "KR": ("KR",),
    "GB": ("UK",),
    "KZ": ("KZ",),
    "DE": ("DE",),
    "SE": ("SE",),
    "ID": ("ID",),
    "FR": ("FR",),
    "IT": ("IT",),
    "ES": ("ES",),
    "NL": ("NL",),
    "PT": ("PT",),
    "IE": ("IE",),
    "CH": ("CH",),
    "AT": ("AT",),
    "AU": ("AU",),
    "BR": ("BR",),
    "CA": ("CA",),
    "IN": ("IN",),
    "MX": ("MX",),
    "RU": ("RU",),
}
DEFAULT_REGIONS = list(BASELINE_REGIONS)
DEFAULT_DATE = "2026-04-29"
DATE_PATH_HINTS = {"date", "outbounddate", "departdate", "departuredate"}
PRICE_KEYS = {"price", "amount", "rawprice", "formattedprice", "totalprice"}
CURRENCY_KEYS = {"currency", "currencycode", "unit", "curr", "symbol"}
URL_HINTS = ("search", "flight", "flights", "conductor", "live")
CHALLENGE_HINTS = (
    "verify you are human",
    "verify you're human",
    "security check",
    "complete the challenge",
    "captcha",
    "press and hold",
    "are you a robot",
    "人机验证",
    "验证你是人类",
    "安全检查",
)
LOADING_HINTS = (
    "searching for the best flights",
    "searching flights",
    "looking for the best flights",
    "正在搜索",
    "搜索中",
    "查找最优惠",
    "loading",
)
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

CDP_HTTP = "http://localhost:9222"
PROFILE_CACHE_PATHS = (
    "BrowserMetrics",
    "component_crx_cache",
    "GraphiteDawnCache",
    "GrShaderCache",
    "ShaderCache",
    "Default/Cache",
    "Default/Code Cache",
    "Default/GPUCache",
    "Default/DawnGraphiteCache",
    "Default/DawnWebGPUCache",
    "Default/blob_storage",
)
REGION_HOST_ALIASES = {
    "CN": {"www.skyscanner.cn", "www.tianxun.com"},
    "US": {"www.skyscanner.com"},
    "UK": {"www.skyscanner.co.uk", "www.skyscanner.net"},
    "SG": {"www.skyscanner.sg", "www.skyscanner.com.sg"},
    "HK": {"www.skyscanner.com.hk"},
    "KZ": {"www.skyscanner.kz", "www.skyscanner.net"},
}


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


def parse_float(value: Any) -> Optional[float]:
    if isinstance(value, (int, float)):
        return float(value)
    if not isinstance(value, str):
        return None
    text = value.strip()
    if not text:
        return None
    cleaned = re.sub(r"[^\d,\.\s\u00a0]", "", text)
    cleaned = cleaned.replace("\u00a0", " ").strip()
    if not cleaned:
        return None
    if "," in cleaned and "." in cleaned:
        if cleaned.rfind(",") > cleaned.rfind("."):
            cleaned = cleaned.replace(".", "")
            cleaned = cleaned.replace(",", ".")
        else:
            cleaned = cleaned.replace(",", "")
    elif "." in cleaned:
        parts = [part for part in cleaned.split(".") if part]
        if len(parts) > 1 and len(parts[-1]) in {1, 2}:
            cleaned = "".join(parts[:-1]) + "." + parts[-1]
        elif len(parts) > 1 and all(part.isdigit() for part in parts):
            cleaned = "".join(parts)
    elif "," in cleaned:
        parts = [part for part in cleaned.split(",") if part]
        if len(parts) > 1 and len(parts[-1]) in {1, 2}:
            cleaned = "".join(parts[:-1]) + "." + parts[-1]
        else:
            cleaned = "".join(parts)
    cleaned = cleaned.replace(" ", "")
    if cleaned.isdigit():
        match_text = cleaned
    else:
        match = re.search(r"\d+(?:\.\d+)?", cleaned)
        if not match:
            return None
        match_text = match.group(0)
    try:
        return float(match_text)
    except ValueError:
        return None


def first_currency(value: Any) -> Optional[str]:
    if isinstance(value, str):
        cleaned = value.strip()
        if 1 <= len(cleaned) <= 6:
            return cleaned.upper()
    return None


CURRENCY_TOKENS = (
    "HK$",
    "US$",
    "CA$",
    "A$",
    "S$",
    "€",
    "£",
    "¥",
    "$",
    "₩",
    "₹",
    "₽",
    "₺",
    "₫",
    "CHF",
    "SEK",
    "NOK",
    "DKK",
    "ISK",
    "PLN",
    "CZK",
    "HUF",
    "RON",
    "BGN",
    "HRK",
    "RSD",
    "AED",
    "QAR",
    "SAR",
    "KWD",
    "BHD",
    "OMR",
    "JPY",
    "CNY",
    "HKD",
    "USD",
    "GBP",
    "SGD",
    "KRW",
    "IDR",
    "EUR",
    "AUD",
    "CAD",
    "INR",
    "BRL",
    "MXN",
    "RUB",
    "KZT",
)
BEST_LABELS = (
    "Best",
    "Best option",
    "Best flight",
    "Recommended",
    "最佳",
    "最佳选项",
    "最优",
    "推荐",
    "推荐排序",
    "Bäst",
    "추천순",
    "おすすめ",
    "おすすめ順",
    "おすすめのフライト",
    "Am besten",
    "Mejor",
    "Migliore",
    "Melhor",
    "Beste",
    "Le meilleur",
    "Najlepsze",
    "Cel mai bun",
    "Лучший",
    "Лучшее",
    "Terbaik",
)
CHEAPEST_LABELS = (
    "Cheapest",
    "最便宜",
    "Billigast",
    "최저가",
    "最安値",
    "Günstigste",
    "Más barato",
    "Più economico",
    "Mais barato",
    "Goedkoopste",
    "Le moins cher",
    "Najtańsze",
    "Cel mai ieftin",
    "Самый дешевый",
    "Самые дешевые",
    "Дешевле всего",
    "Termurah",
)
SORT_LABELS = tuple(dict.fromkeys((*BEST_LABELS, *CHEAPEST_LABELS)))
LABEL_PRICE_SCAN_LINES = 10
PRICE_PREFIX_HINTS = (
    "总费用为",
    "費用總計",
    "总费用",
    "價格低至",
    "价格低至",
    "最低只要",
    "起",
)
SORT_SECTION_HINTS = (
    "Visa resultat efter",
    "搜索结果显示方式",
    "搜尋結果顯示方式",
    "검색 결과 표시 기준",
    "検索結果の表示順",
    "Show results by",
    "Display results by",
    "Mostrar resultados por",
    "Mostra risultati per",
    "Afficher les résultats par",
    "Ergebnisse anzeigen nach",
    "Показать результаты по",
    "Tampilkan hasil berdasarkan",
)
REGION_BEST_LABELS: dict[str, tuple[str, ...]] = {
    "SE": ("Bäst",),
    "KR": ("추천순",),
    "JP": ("おすすめ", "おすすめ順"),
    "CN": ("综合最佳", "最优", "最佳"),
    "HK": ("最優", "最佳"),
    "SG": ("综合最佳", "最优", "最佳"),
    "DE": ("Am besten", "Beste"),
    "FR": ("Le meilleur",),
    "ES": ("Mejor",),
    "IT": ("Migliore",),
    "PT": ("Melhor",),
    "NL": ("Beste",),
    "PL": ("Najlepsze",),
    "RO": ("Cel mai bun",),
    "ID": ("Terbaik",),
    "KZ": ("Лучший", "Лучшее"),
    "RU": ("Лучший", "Лучшее"),
}
REGION_CHEAPEST_LABELS: dict[str, tuple[str, ...]] = {
    "SE": ("Billigast",),
    "KR": ("최저가",),
    "JP": ("最安値",),
    "CN": ("最便宜",),
    "HK": ("最便宜",),
    "SG": ("最便宜",),
    "DE": ("Günstigste",),
    "FR": ("Le moins cher",),
    "ES": ("Más barato",),
    "IT": ("Più economico",),
    "PT": ("Mais barato",),
    "NL": ("Goedkoopste",),
    "PL": ("Najtańsze",),
    "RO": ("Cel mai ieftin",),
    "ID": ("Termurah",),
    "KZ": ("Самый дешевый", "Самые дешевые", "Дешевле всего"),
    "RU": ("Самый дешевый", "Самые дешевые", "Дешевле всего"),
}


def parse_price_text(text: str) -> Optional[tuple[str, float]]:
    token_pattern = "|".join(
        re.escape(token) for token in sorted(CURRENCY_TOKENS, key=len, reverse=True)
    )
    amount_pattern = r"\d[\d\s\u00a0,.]*"

    prefix_match = re.search(
        rf"({token_pattern})[ \t\u00a0]*({amount_pattern})",
        text,
        re.IGNORECASE,
    )
    if prefix_match:
        amount = parse_float(prefix_match.group(2))
        if amount is not None:
            return prefix_match.group(1), amount

    suffix_match = re.search(
        rf"({amount_pattern})[ \t\u00a0]*({token_pattern})",
        text,
        re.IGNORECASE,
    )
    if suffix_match:
        amount = parse_float(suffix_match.group(1))
        if amount is not None:
            return suffix_match.group(2), amount

    return None


def deep_copy_json(value: Any) -> Any:
    return json.loads(json.dumps(value))


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


def detect_browsers() -> dict[str, Path]:
    candidates = {
        "chrome": Path("/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"),
        "edge": Path("/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge"),
    }
    return {name: path for name, path in candidates.items() if path.exists()}


def profile_dir_for(browser_name: str) -> Path:
    return get_browser_profile_dir(browser_name)


def detect_cdp_version(port: int = 9222) -> Optional[dict[str, Any]]:
    try:
        with urlopen(f"http://localhost:{port}/json/version", timeout=2) as response:
            return json.loads(response.read().decode("utf-8"))
    except (URLError, OSError, json.JSONDecodeError, TimeoutError):
        return None


def wait_for_cdp(
    port: int = 9222, timeout: float = 12.0, interval: float = 0.5
) -> Optional[dict[str, Any]]:
    deadline = time.time() + timeout
    while time.time() < deadline:
        info = detect_cdp_version(port)
        if info:
            return info
        time.sleep(interval)
    return None


def prune_browser_profile(profile_dir: Path) -> tuple[int, list[str]]:
    removed: list[str] = []
    for rel_path in PROFILE_CACHE_PATHS:
        target = profile_dir / rel_path
        if not target.exists():
            continue
        try:
            if target.is_dir():
                shutil.rmtree(target)
            else:
                target.unlink()
            removed.append(rel_path)
        except OSError:
            continue
    return len(removed), removed


def launch_browser_with_cdp(
    port: int = 9222, start_url: str = "https://www.skyscanner.com"
) -> str:
    browsers = detect_browsers()
    for browser_name in ("edge", "chrome"):
        binary = browsers.get(browser_name)
        if not binary:
            continue

        profile_dir = profile_dir_for(browser_name)
        profile_dir.mkdir(parents=True, exist_ok=True)
        removed_count, _ = prune_browser_profile(profile_dir)
        command = [
            str(binary),
            f"--remote-debugging-port={port}",
            f"--user-data-dir={profile_dir}",
            "--no-first-run",
            "--no-default-browser-check",
            "--new-window",
            start_url,
        ]
        try:
            subprocess.Popen(
                command,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                stdin=subprocess.DEVNULL,
                start_new_session=True,
            )
            if removed_count:
                return f"已清理 {removed_count} 处缓存并自动启动 {browser_name.capitalize()}，调试端口 {port}"
            return f"已尝试自动启动 {browser_name.capitalize()}，调试端口 {port}"
        except OSError as exc:
            return f"找到 {browser_name.capitalize()}，但启动失败: {exc}"

    return "没有找到可自动启动的 Edge 或 Chrome"


def ensure_cdp_ready(
    port: int = 9222,
    auto_launch: bool = True,
    wait_timeout: float = 12.0,
    start_url: str = "https://www.skyscanner.com",
) -> dict[str, Any]:
    cdp_info = detect_cdp_version(port)
    if cdp_info:
        return cdp_info

    launch_note = None
    if auto_launch:
        launch_note = launch_browser_with_cdp(port=port, start_url=start_url)
        cdp_info = wait_for_cdp(port=port, timeout=wait_timeout)
        if cdp_info:
            return cdp_info

    raise RuntimeError(
        "未检测到 Edge 调试端口 9222。"
        + (f" {launch_note}。" if launch_note else "")
        + " 请关闭已打开的浏览器后重试，或手动启动带 --remote-debugging-port=9222 的 Edge。"
    )


def load_capture_file(path: Path) -> list[dict[str, Any]]:
    data = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(data, list):
        return [item for item in data if isinstance(item, dict)]
    if isinstance(data, dict):
        return [data]
    raise ValueError("Capture file must contain a JSON object or array")


def build_search_url(
    region: RegionConfig, origin: str, destination: str, travel_date: str
) -> str:
    _, _, short_date = parse_date(travel_date)
    return (
        f"{region.domain}/transport/flights/{origin.lower()}/{destination.lower()}/{short_date}/"
        "?adultsv2=1&cabinclass=economy&childrenv2=&ref=home&rtn=0"
        "&preferdirects=false&outboundaltsenabled=false&inboundaltsenabled=false"
    )


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


def print_quotes(quotes: list[FlightQuote]) -> None:
    print("\n" + "=" * 96)
    print(f"{'地区':<8}{'价格':<14}{'货币':<8}{'状态':<18}{'来源':<48}")
    print("-" * 96)
    for quote in quotes:
        region_name = REGIONS.get(
            quote.region, RegionConfig(quote.region, quote.region, quote.domain, "", "")
        ).name
        price_text = f"{quote.price:,.2f}" if quote.price is not None else "-"
        print(
            f"{region_name:<8}{price_text:<14}{(quote.currency or '-'): <8}"
            f"{quote.status:<18}{quote.source_url[:48]:<48}"
        )
    print("=" * 96)

    failures = [quote for quote in quotes if quote.price is None]
    if failures:
        print("\n失败详情:")
        for quote in failures:
            print(f"[{quote.region}] {quote.error or quote.status}")


def get_selected_regions(region_codes: list[str]) -> list[RegionConfig]:
    return [REGIONS[code] for code in region_codes if code in REGIONS]


def dedupe_region_codes(region_codes: Iterable[str]) -> list[str]:
    seen: set[str] = set()
    ordered: list[str] = []
    for code in region_codes:
        normalized = code.strip().upper()
        if not normalized or normalized in seen or normalized not in REGIONS:
            continue
        seen.add(normalized)
        ordered.append(normalized)
    return ordered


def build_effective_region_codes(
    origin_country: str = "",
    destination_country: str = "",
    manual_region_codes: Iterable[str] = (),
) -> list[str]:
    route_regions: list[str] = []
    if origin_country:
        route_regions.extend(COUNTRY_TO_REGION_CODES.get(origin_country.upper(), ()))
    if destination_country:
        route_regions.extend(
            COUNTRY_TO_REGION_CODES.get(destination_country.upper(), ())
        )
    return dedupe_region_codes(
        [*BASELINE_REGIONS, *route_regions, *manual_region_codes]
    )


def quotes_to_dicts(quotes: list[FlightQuote]) -> list[dict[str, Any]]:
    return [
        {
            "region": quote.region,
            "region_name": REGIONS.get(
                quote.region,
                RegionConfig(quote.region, quote.region, quote.domain, "", ""),
            ).name,
            "domain": quote.domain,
            "price": quote.price,
            "currency": quote.currency,
            "source_url": quote.source_url,
            "status": quote.status,
            "price_path": quote.price_path,
            "best_price": quote.best_price,
            "best_price_path": quote.best_price_path,
            "cheapest_price": quote.cheapest_price,
            "cheapest_price_path": quote.cheapest_price_path,
            "error": quote.error,
        }
        for quote in quotes
    ]


async def cdp_open_tab(session: aiohttp.ClientSession, url: str) -> dict[str, Any]:
    target_url = f"{CDP_HTTP}/json/new?{quote(url, safe=':/?&=%')}"
    async with session.put(target_url) as response:
        response.raise_for_status()
        return await response.json()


async def cdp_list_tabs(session: aiohttp.ClientSession) -> list[dict[str, Any]]:
    async with session.get(f"{CDP_HTTP}/json/list") as response:
        response.raise_for_status()
        return await response.json()


async def cdp_eval(ws_url: str, expression: str) -> Any:
    timeout = aiohttp.ClientTimeout(total=20)
    async with aiohttp.ClientSession(timeout=timeout) as session:
        async with session.ws_connect(ws_url) as ws:
            await ws.send_json(
                {
                    "id": 1,
                    "method": "Runtime.evaluate",
                    "params": {
                        "expression": expression,
                        "returnByValue": True,
                        "awaitPromise": True,
                    },
                }
            )
            async for message in ws:
                if message.type != aiohttp.WSMsgType.TEXT:
                    continue
                payload = json.loads(message.data)
                if payload.get("id") != 1:
                    continue
                result = payload.get("result", {}).get("result", {})
                if "value" in result:
                    return result["value"]
                raise RuntimeError(json.dumps(payload, ensure_ascii=False))
    raise RuntimeError("CDP evaluate failed")


def find_page_hint(page_text: str, hints: tuple[str, ...]) -> Optional[str]:
    lower_text = page_text.lower()
    for hint in hints:
        if hint in lower_text:
            return hint
    return None


def get_flight_results_scope(page_text: str) -> str:
    for hint in SORT_SECTION_HINTS:
        index = page_text.find(hint)
        if index >= 0:
            start = max(index - 120, 0)
            return page_text[start : start + 3200]

    lower_text = page_text.lower()
    label_indexes = [
        index
        for label in SORT_LABELS
        for index in [lower_text.find(label.lower())]
        if index >= 0
    ]
    if label_indexes:
        start = max(min(label_indexes) - 120, 0)
        return page_text[start : start + 3200]

    return page_text[:6000]


def match_sort_label(
    line_text: str, labels: tuple[str, ...]
) -> Optional[tuple[str, int]]:
    stripped = line_text.strip()
    lowered = stripped.lower()
    for label in sorted(labels, key=len, reverse=True):
        label_lower = label.lower()
        if lowered == label_lower:
            return label, 3
        if lowered.startswith(label_lower):
            return label, 2
        if lowered.lstrip("•- ").startswith(label_lower):
            return label, 1
    return None


def extract_labeled_page_price(
    page_text: str, labels: tuple[str, ...]
) -> Optional[tuple[float, str, str]]:
    candidates = extract_labeled_page_price_candidates(page_text, labels)
    if not candidates:
        return None
    price, currency, label = candidates[0]
    return price, currency, label


def extract_labeled_page_price_candidates(
    page_text: str, labels: tuple[str, ...]
) -> list[tuple[float, str, str]]:
    lines = page_text.splitlines()
    candidates: list[tuple[int, int, int, int, float, str, str]] = []

    for index, raw_line in enumerate(lines):
        matched = match_sort_label(raw_line, labels)
        if not matched:
            continue

        label, score = matched
        block_parts: list[str] = []
        suffix = raw_line.strip()[len(label) :].strip(" :-\t")
        if suffix:
            block_parts.append(suffix)

        distance = 99
        hint_score = 0
        for offset in range(1, LABEL_PRICE_SCAN_LINES + 1):
            next_index = index + offset
            if next_index >= len(lines):
                break
            next_line = lines[next_index].strip()
            if not next_line:
                continue
            if match_sort_label(next_line, SORT_LABELS):
                break
            if next_line in SORT_SECTION_HINTS:
                continue
            block_parts.append(next_line)
            if any(hint in next_line for hint in PRICE_PREFIX_HINTS):
                hint_score = 1
            if distance == 99 and parse_price_text(next_line):
                distance = offset
                break

        if distance == 99:
            distance = 0 if suffix and parse_price_text(suffix) else 99
        if any(hint in part for part in block_parts for hint in PRICE_PREFIX_HINTS):
            hint_score = 1

        parsed = parse_price_text("\n".join(block_parts))
        if not parsed:
            continue

        currency, price = parsed
        candidates.append((score, hint_score, distance, index, price, currency, label))

    if not candidates:
        return []

    candidates.sort(key=lambda item: (-item[0], -item[1], item[2], item[3]))
    return [(price, currency, label) for _, _, _, _, price, currency, label in candidates]


def best_candidates_for_region(
    scoped_text: str, region: RegionConfig
) -> list[tuple[float, str, str]]:
    region_labels = REGION_BEST_LABELS.get(region.code, ()) or BEST_LABELS
    primary = extract_labeled_page_price_candidates(scoped_text, region_labels)
    if region_labels == BEST_LABELS:
        return primary

    fallback = extract_labeled_page_price_candidates(scoped_text, BEST_LABELS)
    merged: list[tuple[float, str, str]] = []
    seen: set[tuple[float, str, str]] = set()
    for candidate in [*primary, *fallback]:
        if candidate in seen:
            continue
        seen.add(candidate)
        merged.append(candidate)
    return merged


def extract_page_quote(
    region: RegionConfig, source_url: str, page_text: str
) -> FlightQuote:
    challenge_hint = find_page_hint(page_text, CHALLENGE_HINTS)
    if challenge_hint:
        return FlightQuote(
            region=region.code,
            domain=region.domain,
            price=None,
            currency=region.currency,
            source_url=source_url,
            status="page_challenge",
            error=f"页面仍停留在人机验证/安全检查: {challenge_hint}",
        )

    loading_hint = find_page_hint(page_text, LOADING_HINTS)
    if loading_hint:
        return FlightQuote(
            region=region.code,
            domain=region.domain,
            price=None,
            currency=region.currency,
            source_url=source_url,
            status="page_loading",
            error=f"页面仍在加载结果: {loading_hint}",
        )

    currency = region.currency
    scoped_text = get_flight_results_scope(page_text)

    best_labels = REGION_BEST_LABELS.get(region.code, ()) or BEST_LABELS
    cheapest_labels = REGION_CHEAPEST_LABELS.get(region.code, ()) or CHEAPEST_LABELS
    best_candidates = best_candidates_for_region(scoped_text, region)
    best_match = best_candidates[0] if best_candidates else None
    if best_match is None and best_labels != BEST_LABELS:
        best_match = extract_labeled_page_price(scoped_text, BEST_LABELS)
    cheapest_match = extract_labeled_page_price(scoped_text, cheapest_labels)
    if cheapest_match is None and cheapest_labels != CHEAPEST_LABELS:
        cheapest_match = extract_labeled_page_price(scoped_text, CHEAPEST_LABELS)
    if best_match or cheapest_match:
        best_price = best_match[0] if best_match else None
        cheapest_price = cheapest_match[0] if cheapest_match else None
        best_label = best_match[2] if best_match else None
        cheapest_label = cheapest_match[2] if cheapest_match else None
        inconsistency_error = None
        status = "page_text"
        if (
            best_price is not None
            and cheapest_price is not None
            and best_price < cheapest_price
        ):
            recovered = next(
                (
                    candidate
                    for candidate in best_candidates
                    if candidate[0] >= cheapest_price
                ),
                None,
            )
            if recovered is not None:
                best_price, _, best_label = recovered
                status = "page_text_recovered_best"
                inconsistency_error = (
                    "Best 初始候选低于 Cheapest，已切换到后续 Best 候选"
                )
            else:
                inconsistency_error = (
                    "Best 价格低于 Cheapest，页面文本匹配可能错位，已忽略 Best"
                )
                best_price = None
                best_label = None
                status = "page_text_inconsistent"
        elif best_price is not None and cheapest_price is None:
            status = "page_text_best_only"
        elif cheapest_price is not None and best_price is None:
            status = "page_text_cheapest_only"
        primary_price = cheapest_price if cheapest_price is not None else best_price
        primary_label = cheapest_label if cheapest_price is not None else best_label
        return FlightQuote(
            region=region.code,
            domain=region.domain,
            price=primary_price,
            currency=currency,
            source_url=source_url,
            status=status,
            price_path=(
                f"document.body.innerText -> {primary_label}"
                if primary_label is not None
                else None
            ),
            best_price=best_price,
            best_price_path=(
                f"document.body.innerText -> {best_label}"
                if best_label is not None
                else None
            ),
            cheapest_price=cheapest_price,
            cheapest_price_path=(
                f"document.body.innerText -> {cheapest_label}"
                if cheapest_label is not None
                else None
            ),
            error=inconsistency_error,
        )

    fallback = parse_price_text(scoped_text)
    if fallback:
        return FlightQuote(
            region=region.code,
            domain=region.domain,
            price=fallback[1],
            currency=currency,
            source_url=source_url,
            status="page_text_fallback",
            price_path="document.body.innerText -> first price",
            cheapest_price=fallback[1],
            cheapest_price_path="document.body.innerText -> first price",
        )

    return FlightQuote(
        region=region.code,
        domain=region.domain,
        price=None,
        currency=currency,
        source_url=source_url,
        status="page_parse_failed",
        error="页面正文未识别到 Best/Cheapest 价格",
    )


async def compare_via_pages(
    args: argparse.Namespace, selected_regions: list[RegionConfig]
) -> list[FlightQuote]:
    total_wait = max(args.timeout, args.page_wait + 60, 45)
    timeout = aiohttp.ClientTimeout(total=total_wait + 15)
    async with aiohttp.ClientSession(timeout=timeout) as session:
        for region in selected_regions:
            url = build_search_url(region, args.origin, args.destination, args.date)
            await cdp_open_tab(session, url)

        await asyncio.sleep(args.page_wait)
        deadline = time.monotonic() + max(total_wait - args.page_wait, 10)
        poll_interval = 2.0
        latest_quotes: dict[str, FlightQuote] = {}
        pending_regions = {region.code: region for region in selected_regions}

        while pending_regions:
            tabs = await cdp_list_tabs(session)
            next_pending: dict[str, RegionConfig] = {}

            for region in pending_regions.values():
                expected_path = urlparse(
                    build_search_url(region, args.origin, args.destination, args.date)
                ).path
                allowed_hosts = REGION_HOST_ALIASES.get(
                    region.code, {urlparse(region.domain).netloc}
                )
                candidates = [
                    tab
                    for tab in tabs
                    if tab.get("type") == "page"
                    and urlparse(str(tab.get("url", ""))).netloc in allowed_hosts
                    and urlparse(str(tab.get("url", ""))).path == expected_path
                ]
                if not candidates:
                    latest_quotes[region.code] = FlightQuote(
                        region=region.code,
                        domain=region.domain,
                        price=None,
                        currency=region.currency,
                        source_url=build_search_url(
                            region, args.origin, args.destination, args.date
                        ),
                        status="page_missing",
                        error="CDP 列表中未找到对应结果页",
                    )
                    if time.monotonic() < deadline:
                        next_pending[region.code] = region
                    continue

                parsed_quote: Optional[FlightQuote] = None
                last_error: Optional[FlightQuote] = None
                for page in candidates:
                    ws_url = str(page.get("webSocketDebuggerUrl", ""))
                    if not ws_url:
                        last_error = FlightQuote(
                            region=region.code,
                            domain=region.domain,
                            price=None,
                            currency=region.currency,
                            source_url=str(page.get("url", "")),
                            status="page_missing_ws",
                            error="结果页没有 webSocketDebuggerUrl",
                        )
                        continue

                    payload = await cdp_eval(
                        ws_url,
                        "({title: document.title, url: location.href, text: (document.body ? document.body.innerText : '').slice(0, 12000)})",
                    )
                    quote = extract_page_quote(
                        region,
                        payload.get("url", str(page.get("url", ""))),
                        payload.get("text", ""),
                    )
                    if quote.price is not None:
                        parsed_quote = quote
                        break
                    last_error = quote

                latest_quotes[region.code] = (
                    parsed_quote
                    or last_error
                    or FlightQuote(
                        region=region.code,
                        domain=region.domain,
                        price=None,
                        currency=region.currency,
                        source_url=build_search_url(
                            region, args.origin, args.destination, args.date
                        ),
                        status="page_parse_failed",
                        error="页面正文未识别到 Best/Cheapest 价格",
                    )
                )

                if (
                    latest_quotes[region.code].price is None
                    and time.monotonic() < deadline
                ):
                    next_pending[region.code] = region

            if not next_pending:
                break
            pending_regions = next_pending
            await asyncio.sleep(poll_interval)

    ordered_quotes: list[FlightQuote] = []
    for region in selected_regions:
        quote = latest_quotes.get(region.code)
        if quote is not None:
            ordered_quotes.append(quote)
    return ordered_quotes


async def run_page_scan(
    origin: str,
    destination: str,
    date: str,
    region_codes: list[str],
    page_wait: int = 8,
    timeout: int = 30,
) -> list[FlightQuote]:
    selected_regions = get_selected_regions(region_codes)
    if not selected_regions:
        return []
    ensure_cdp_ready(
        start_url=build_search_url(selected_regions[0], origin, destination, date)
    )
    args = argparse.Namespace(
        origin=origin,
        destination=destination,
        date=date,
        page_wait=page_wait,
        timeout=timeout,
    )
    quotes = await compare_via_pages(args, selected_regions)
    quotes.sort(key=lambda item: (item.price is None, item.price or float("inf")))
    return quotes


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


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Use Neo captures to compare Skyscanner prices across markets.",
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
