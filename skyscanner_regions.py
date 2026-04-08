from __future__ import annotations

from typing import Iterable

from skyscanner_models import RegionConfig


REGIONS: dict[str, RegionConfig] = {
    "CN": RegionConfig("CN", "中国", "https://www.skyscanner.cn", "zh-CN", "CNY"),
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

BASELINE_REGIONS = ("CN", "HK", "SG", "UK")
COUNTRY_TO_REGION_CODES: dict[str, tuple[str, ...]] = {
    "CN": ("CN",),
    "HK": ("HK",),
    "SG": ("SG",),
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
REGION_HOST_ALIASES = {
    "CN": {"www.skyscanner.cn", "www.tianxun.com"},
    "UK": {"www.skyscanner.co.uk", "www.skyscanner.net"},
    "SG": {"www.skyscanner.sg", "www.skyscanner.com.sg"},
    "HK": {"www.skyscanner.com.hk"},
    "KZ": {"www.skyscanner.kz", "www.skyscanner.net"},
}


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
