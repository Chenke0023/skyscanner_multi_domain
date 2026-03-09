from __future__ import annotations

import csv
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

from app_paths import PROJECT_ROOT

AIRPORT_DATASET_PATH = PROJECT_ROOT / "data" / "airport-codes.csv"
VALID_AIRPORT_TYPES = {"large_airport", "medium_airport", "small_airport"}
MAX_LOCATION_SUGGESTIONS = 8

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

MANUAL_AIRPORT_CODES = {
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
    "吉隆坡": "KUL",
    "kuala lumpur": "KUL",
    "曼谷": "BKK",
    "bangkok": "BKK",
    "大阪": "KIX",
    "osaka": "KIX",
    "东京羽田": "HND",
    "haneda": "HND",
    "东京成田": "NRT",
    "narita": "NRT",
    "大阪关西": "KIX",
    "关西": "KIX",
    "kansai": "KIX",
    "大阪伊丹": "ITM",
    "伊丹": "ITM",
    "itami": "ITM",
    "名古屋": "NGO",
    "nagoya": "NGO",
    "札幌": "CTS",
    "sapporo": "CTS",
    "福冈": "FUK",
    "fukuoka": "FUK",
    "冲绳": "OKA",
    "那霸": "OKA",
    "okinawa": "OKA",
    "naha": "OKA",
    "首尔仁川": "ICN",
    "仁川": "ICN",
    "incheon": "ICN",
    "首尔金浦": "GMP",
    "金浦": "GMP",
    "gimpo": "GMP",
    "釜山": "PUS",
    "busan": "PUS",
    "济州": "CJU",
    "jeju": "CJU",
    "马尼拉": "MNL",
    "manila": "MNL",
    "宿务": "CEB",
    "cebu": "CEB",
    "长滩岛": "MPH",
    "boracay": "MPH",
    "卡利博": "KLO",
    "kalibo": "KLO",
    "胡志明市": "SGN",
    "胡志明": "SGN",
    "ho chi minh city": "SGN",
    "saigon": "SGN",
    "河内": "HAN",
    "hanoi": "HAN",
    "岘港": "DAD",
    "da nang": "DAD",
    "芽庄": "CXR",
    "nha trang": "CXR",
    "富国岛": "PQC",
    "phu quoc": "PQC",
    "曼谷素万那普": "BKK",
    "素万那普": "BKK",
    "suvarnabhumi": "BKK",
    "曼谷廊曼": "DMK",
    "廊曼": "DMK",
    "don muang": "DMK",
    "普吉": "HKT",
    "phuket": "HKT",
    "清迈": "CNX",
    "chiang mai": "CNX",
    "清莱": "CEI",
    "chiang rai": "CEI",
    "芭提雅": "UTP",
    "pattaya": "UTP",
    "甲米": "KBV",
    "krabi": "KBV",
    "苏梅": "USM",
    "koh samui": "USM",
    "samui": "USM",
    "吉隆坡第二机场": "KUL",
    "klia": "KUL",
    "亚庇": "BKI",
    "kota kinabalu": "BKI",
    "槟城": "PEN",
    "penang": "PEN",
    "兰卡威": "LGK",
    "langkawi": "LGK",
    "古晋": "KCH",
    "kuching": "KCH",
    "新山": "JHB",
    "johor bahru": "JHB",
    "雅加达苏加诺哈达": "CGK",
    "苏加诺哈达": "CGK",
    "jakarta soekarno hatta": "CGK",
    "巴厘岛": "DPS",
    "bali": "DPS",
    "登巴萨": "DPS",
    "denpasar": "DPS",
    "泗水": "SUB",
    "surabaya": "SUB",
    "日惹": "YIA",
    "yogyakarta": "YIA",
    "龙目岛": "LOP",
    "lombok": "LOP",
    "斯里巴加湾": "BWN",
    "bandar seri begawan": "BWN",
    "金边": "PNH",
    "phnom penh": "PNH",
    "暹粒": "SAI",
    "siem reap": "SAI",
    "万象": "VTE",
    "vientiane": "VTE",
    "仰光": "RGN",
    "yangon": "RGN",
    "加德满都": "KTM",
    "kathmandu": "KTM",
    "科伦坡": "CMB",
    "colombo": "CMB",
    "马累": "MLE",
    "male": "MLE",
    "达卡": "DAC",
    "dhaka": "DAC",
    "德里": "DEL",
    "new delhi": "DEL",
    "新德里": "DEL",
    "孟买": "BOM",
    "mumbai": "BOM",
    "班加罗尔": "BLR",
    "bangalore": "BLR",
    "bengaluru": "BLR",
    "金奈": "MAA",
    "chennai": "MAA",
    "海得拉巴": "HYD",
    "hyderabad": "HYD",
    "加尔各答": "CCU",
    "kolkata": "CCU",
    "果阿": "GOI",
    "goa": "GOI",
    "迪拜": "DXB",
    "dubai": "DXB",
    "阿布扎比": "AUH",
    "abu dhabi": "AUH",
    "多哈": "DOH",
    "doha": "DOH",
    "利雅得": "RUH",
    "riyadh": "RUH",
    "吉达": "JED",
    "jeddah": "JED",
    "伊斯坦布尔": "IST",
    "istanbul": "IST",
    "阿姆斯特丹": "AMS",
    "amsterdam": "AMS",
    "巴黎": "CDG",
    "paris": "CDG",
    "法兰克福": "FRA",
    "frankfurt": "FRA",
    "慕尼黑": "MUC",
    "munich": "MUC",
    "米兰": "MXP",
    "milan": "MXP",
    "罗马": "FCO",
    "rome": "FCO",
    "巴塞罗那": "BCN",
    "barcelona": "BCN",
    "马德里": "MAD",
    "madrid": "MAD",
    "苏黎世": "ZRH",
    "zurich": "ZRH",
    "日内瓦": "GVA",
    "geneva": "GVA",
    "维也纳": "VIE",
    "vienna": "VIE",
    "布拉格": "PRG",
    "prague": "PRG",
    "雅典": "ATH",
    "athens": "ATH",
    "里斯本": "LIS",
    "lisbon": "LIS",
    "都柏林": "DUB",
    "dublin": "DUB",
    "布达佩斯": "BUD",
    "budapest": "BUD",
    "哥本哈根": "CPH",
    "copenhagen": "CPH",
    "赫尔辛基": "HEL",
    "helsinki": "HEL",
    "斯德哥尔摩": "ARN",
    "stockholm": "ARN",
    "奥斯陆": "OSL",
    "oslo": "OSL",
    "莫斯科": "SVO",
    "moscow": "SVO",
    "洛杉矶": "LAX",
    "los angeles": "LAX",
    "旧金山": "SFO",
    "san francisco": "SFO",
    "西雅图": "SEA",
    "seattle": "SEA",
    "芝加哥": "ORD",
    "chicago": "ORD",
    "波士顿": "BOS",
    "boston": "BOS",
    "温哥华": "YVR",
    "vancouver": "YVR",
    "多伦多": "YYZ",
    "toronto": "YYZ",
    "悉尼": "SYD",
    "sydney": "SYD",
    "布里斯班": "BNE",
    "brisbane": "BNE",
    "黄金海岸": "OOL",
    "gold coast": "OOL",
    "珀斯": "PER",
    "perth": "PER",
    "阿德莱德": "ADL",
    "adelaide": "ADL",
    "凯恩斯": "CNS",
    "cairns": "CNS",
    "墨尔本": "MEL",
    "melbourne": "MEL",
    "奥克兰": "AKL",
    "auckland": "AKL",
    "皇后镇": "ZQN",
    "queenstown": "ZQN",
    "基督城": "CHC",
    "christchurch": "CHC",
    "阿拉木图": "ALA",
    "almaty": "ALA",
    "塔什干": "TAS",
    "tashkent": "TAS",
    "阿斯塔纳": "NQZ",
    "astana": "NQZ",
    "乌兰巴托": "UBN",
    "ulaanbaatar": "UBN",
    "开罗": "CAI",
    "cairo": "CAI",
    "卡萨布兰卡": "CMN",
    "casablanca": "CMN",
    "马拉喀什": "RAK",
    "marrakech": "RAK",
    "毛里求斯": "MRU",
    "mauritius": "MRU",
    "塞舌尔": "SEZ",
    "seychelles": "SEZ",
    "合肥": "HFE",
    "hefei": "HFE",
    "雅加达": "JKT",
    "jakarta": "JKT",
    "伦敦": "LHR",
    "london": "LHR",
    "纽约": "JFK",
    "new york": "JFK",
}


@dataclass(frozen=True)
class LocationRecord:
    name: str
    code: str
    kind: str
    municipality: str = ""
    country: str = ""
    search_name: str = ""


class LocationResolver:
    def __init__(self) -> None:
        self._records = load_airport_records()
        self._airport_code_set = {item.code for item in self._records if item.kind == "airport"}
        self._metro_code_set = set(METRO_CODES.values())
        self._manual_lookup = {key.lower(): value for key, value in MANUAL_AIRPORT_CODES.items()}
        self._metro_lookup = {key.lower(): value for key, value in METRO_CODES.items()}

    def normalize_location(self, value: str, prefer_metro: bool) -> str:
        raw = value.strip()
        if not raw:
            raise ValueError("地点不能为空。")

        upper = raw.upper()
        if prefer_metro and upper in self._metro_code_set:
            return upper
        if upper in self._airport_code_set:
            return upper

        lookup = raw.lower()
        if prefer_metro and lookup in self._metro_lookup:
            return self._metro_lookup[lookup]
        if lookup in self._manual_lookup:
            return self._manual_lookup[lookup]

        suggestions = self.search_locations(raw, prefer_metro=prefer_metro, limit=1)
        if suggestions:
            return suggestions[0].code

        raise ValueError(
            f"无法识别地点“{raw}”。请使用常见城市名、机场名，或直接输入 IATA/城市代码（例如 PEK、ALA、JKT、BJSA）。"
        )

    def search_locations(
        self, query: str, *, prefer_metro: bool, limit: int = MAX_LOCATION_SUGGESTIONS
    ) -> list[LocationRecord]:
        raw = query.strip()
        if not raw:
            return []
        lowered = raw.lower()
        ranked: list[tuple[int, int, int, str, LocationRecord]] = []
        seen_codes: set[tuple[str, str]] = set()

        for record in self._records:
            if record.kind == "metro" and not prefer_metro:
                continue
            score = self._score(lowered, record)
            if score is None:
                continue
            dedupe_key = (record.code, record.kind)
            if dedupe_key in seen_codes:
                continue
            seen_codes.add(dedupe_key)
            kind_rank = 0 if record.kind == "metro" and prefer_metro else 1
            ranked.append((score, kind_rank, len(record.name), record.name, record))

        ranked.sort(key=lambda item: item[:4])
        return [record for *_meta, record in ranked[:limit]]

    def describe_code_kind(self, code: str) -> str:
        return "城市代码" if code.upper() in self._metro_code_set else "机场代码"

    def _score(self, lowered: str, record: LocationRecord) -> int | None:
        if lowered == record.code.lower() or lowered == record.search_name:
            return 0
        if record.search_name.startswith(lowered) or record.code.lower().startswith(lowered):
            return 1
        search_blob = " ".join(
            part.lower() for part in [record.name, record.municipality, record.country, record.code] if part
        )
        if lowered in search_blob:
            return 2
        return None


@lru_cache(maxsize=1)
def load_airport_records() -> list[LocationRecord]:
    records: list[LocationRecord] = []
    seen: set[tuple[str, str, str]] = set()

    for alias, code in METRO_CODES.items():
        key = (alias.lower(), code, "metro")
        if key in seen:
            continue
        seen.add(key)
        records.append(
            LocationRecord(
                name=alias,
                code=code,
                kind="metro",
                search_name=alias.lower(),
            )
        )

    if AIRPORT_DATASET_PATH.exists():
        with AIRPORT_DATASET_PATH.open("r", encoding="utf-8", newline="") as f:
            reader = csv.DictReader(f)
            for row in reader:
                airport_type = (row.get("type") or "").strip()
                iata_code = (row.get("iata_code") or "").strip().upper()
                if not iata_code or airport_type not in VALID_AIRPORT_TYPES:
                    continue
                name = (row.get("name") or "").strip()
                municipality = (row.get("municipality") or "").strip()
                country = (row.get("iso_country") or "").strip()
                for alias in [name, municipality]:
                    alias = alias.strip()
                    if not alias:
                        continue
                    key = (alias.lower(), iata_code, "airport")
                    if key in seen:
                        continue
                    seen.add(key)
                    records.append(
                        LocationRecord(
                            name=alias,
                            code=iata_code,
                            kind="airport",
                            municipality=municipality,
                            country=country,
                            search_name=alias.lower(),
                        )
                    )

    for alias, code in MANUAL_AIRPORT_CODES.items():
        key = (alias.lower(), code, "airport")
        if key in seen:
            continue
        seen.add(key)
        records.append(
            LocationRecord(
                name=alias,
                code=code,
                kind="airport",
                search_name=alias.lower(),
            )
        )

    return records
