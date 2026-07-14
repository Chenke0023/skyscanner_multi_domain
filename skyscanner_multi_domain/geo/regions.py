from __future__ import annotations

from typing import Iterable

from skyscanner_multi_domain.geo.countries import (
    get_country_currency,
    get_country_display_name,
    iter_cldr_country_codes,
)
from skyscanner_multi_domain.models import RegionConfig


BASELINE_REGIONS = ("CN", "HK")
DEFAULT_REGIONS = list(BASELINE_REGIONS)
MARKET_RANK_BASELINE_REGIONS = ("CN", "HK", "SG", "UK")
GENERIC_SKYSCANNER_DOMAIN = "https://www.skyscanner.com"
REGION_CODE_ALIASES = {
    "GB": "UK",
}

CURATED_REGIONS: dict[str, RegionConfig] = {
    "CN": RegionConfig("CN", "中国", "https://www.skyscanner.cn", "zh-CN", "CNY"),
    "UK": RegionConfig("UK", "英国", "https://www.skyscanner.co.uk", "en-GB", "GBP"),
    "SG": RegionConfig("SG", "新加坡", "https://www.skyscanner.sg", "en-SG", "SGD"),
    "HK": RegionConfig("HK", "香港", "https://www.skyscanner.com.hk", "zh-HK", "HKD"),
    "KZ": RegionConfig("KZ", "哈萨克斯坦", "https://www.skyscanner.kz", "ru-RU", "KZT"),
    "JP": RegionConfig("JP", "日本", "https://www.skyscanner.jp", "ja-JP", "JPY"),
    "DE": RegionConfig("DE", "德国", "https://www.skyscanner.de", "de-DE", "EUR"),
    "KR": RegionConfig("KR", "韩国", "https://www.skyscanner.co.kr", "ko-KR", "KRW"),
    "SE": RegionConfig("SE", "瑞典", "https://www.skyscanner.se", "sv-SE", "SEK"),
    "ID": RegionConfig("ID", "印度尼西亚", "https://www.skyscanner.co.id", "id-ID", "IDR"),
    "FR": RegionConfig("FR", "法国", "https://www.skyscanner.fr", "fr-FR", "EUR"),
    "IT": RegionConfig("IT", "意大利", "https://www.skyscanner.it", "it-IT", "EUR"),
    "ES": RegionConfig("ES", "西班牙", "https://www.skyscanner.es", "es-ES", "EUR"),
    "NL": RegionConfig("NL", "荷兰", "https://www.skyscanner.nl", "nl-NL", "EUR"),
    "PT": RegionConfig("PT", "葡萄牙", "https://www.skyscanner.pt", "pt-PT", "EUR"),
    "IE": RegionConfig("IE", "爱尔兰", "https://www.skyscanner.ie", "en-IE", "EUR"),
    "CH": RegionConfig("CH", "瑞士", "https://www.skyscanner.ch", "de-CH", "CHF"),
    "AT": RegionConfig("AT", "奥地利", "https://www.skyscanner.at", "de-AT", "EUR"),
    "AU": RegionConfig("AU", "澳大利亚", "https://www.skyscanner.com.au", "en-AU", "AUD"),
    "BR": RegionConfig("BR", "巴西", "https://www.skyscanner.com.br", "pt-BR", "BRL"),
    "CA": RegionConfig("CA", "加拿大", "https://www.skyscanner.ca", "en-CA", "CAD"),
    "IN": RegionConfig("IN", "印度", "https://www.skyscanner.co.in", "en-IN", "INR"),
    "MX": RegionConfig("MX", "墨西哥", "https://www.skyscanner.com.mx", "es-MX", "MXN"),
    "PK": RegionConfig("PK", "巴基斯坦", "https://www.skyscanner.pk", "en-PK", "PKR"),
    "RU": RegionConfig("RU", "俄罗斯", "https://ru.skyscanner.com", "ru-RU", "RUB"),
}

REGION_HOST_ALIASES = {
    "CN": {"www.skyscanner.cn", "www.tianxun.com"},
    "UK": {"www.skyscanner.co.uk", "www.skyscanner.net"},
    "SG": {"www.skyscanner.sg", "www.skyscanner.com.sg"},
    "HK": {"www.skyscanner.com.hk"},
    "KZ": {"www.skyscanner.kz", "www.skyscanner.net"},
    "PK": {"www.skyscanner.pk", "www.skyscanner.com.pk"},
}


def normalize_region_code(code: str) -> str:
    normalized = code.strip().upper()
    return REGION_CODE_ALIASES.get(normalized, normalized)


def _build_regions() -> dict[str, RegionConfig]:
    regions = dict(CURATED_REGIONS)
    for country_code in iter_cldr_country_codes():
        region_code = normalize_region_code(country_code)
        if not region_code or region_code in regions:
            continue
        regions[region_code] = RegionConfig(
            region_code,
            get_country_display_name(country_code),
            GENERIC_SKYSCANNER_DOMAIN,
            f"en-{country_code}",
            get_country_currency(country_code),
        )
    return dict(sorted(regions.items()))


REGIONS: dict[str, RegionConfig] = _build_regions()


def _build_country_to_region_codes() -> dict[str, tuple[str, ...]]:
    mapping: dict[str, tuple[str, ...]] = {}
    for country_code in iter_cldr_country_codes():
        region_code = normalize_region_code(country_code)
        if region_code in REGIONS:
            mapping[country_code] = (region_code,)
    mapping["GB"] = ("UK",)
    return mapping


COUNTRY_TO_REGION_CODES: dict[str, tuple[str, ...]] = _build_country_to_region_codes()


def get_selected_regions(region_codes: list[str]) -> list[RegionConfig]:
    return [REGIONS[code] for code in dedupe_region_codes(region_codes)]


def dedupe_region_codes(region_codes: Iterable[str]) -> list[str]:
    seen: set[str] = set()
    ordered: list[str] = []
    for code in region_codes:
        normalized = normalize_region_code(code)
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
