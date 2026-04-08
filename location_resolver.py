from __future__ import annotations

import csv
import json
from dataclasses import dataclass
from functools import lru_cache

from app_paths import DATA_DIR

AIRPORT_DATASET_PATH = DATA_DIR / "airport-codes.csv"
LOCATION_MAPPINGS_PATH = DATA_DIR / "location_mappings.json"
VALID_AIRPORT_TYPES = {"large_airport", "medium_airport", "small_airport"}
MAX_LOCATION_SUGGESTIONS = 8


@dataclass(frozen=True)
class LocationMappings:
    metro_codes: dict[str, str]
    metro_code_countries: dict[str, str]
    airport_aliases: dict[str, str]
    airport_code_countries: dict[str, str]


@dataclass(frozen=True)
class LocationRecord:
    name: str
    code: str
    kind: str
    municipality: str = ""
    country: str = ""
    search_name: str = ""


@dataclass(frozen=True)
class ResolvedLocation:
    query: str
    code: str
    kind: str
    name: str
    municipality: str = ""
    country: str = ""


class LocationResolver:
    def __init__(self) -> None:
        self._records = load_airport_records()
        self._airport_code_set = {item.code for item in self._records if item.kind == "airport"}
        self._metro_code_set = set(METRO_CODES.values())
        self._manual_lookup = {key.lower(): value for key, value in MANUAL_AIRPORT_CODES.items()}
        self._metro_lookup = {key.lower(): value for key, value in METRO_CODES.items()}
        self._records_by_code: dict[str, list[LocationRecord]] = {}
        for record in self._records:
            self._records_by_code.setdefault(record.code, []).append(record)

    def normalize_location(self, value: str, prefer_metro: bool) -> str:
        return self.resolve_location(value, prefer_metro=prefer_metro).code

    def resolve_location(self, value: str, prefer_metro: bool) -> ResolvedLocation:
        raw = value.strip()
        if not raw:
            raise ValueError("地点不能为空。")

        upper = raw.upper()
        if prefer_metro and upper in self._metro_code_set:
            return self._build_resolved_location(raw, upper, kind="metro")
        if upper in self._airport_code_set:
            return self._build_resolved_location(raw, upper, kind="airport")

        lookup = raw.lower()
        if prefer_metro and lookup in self._metro_lookup:
            return self._build_resolved_location(raw, self._metro_lookup[lookup], kind="metro")
        if lookup in self._manual_lookup:
            return self._build_resolved_location(raw, self._manual_lookup[lookup], kind="airport")

        suggestions = self.search_locations(raw, prefer_metro=prefer_metro, limit=1)
        if suggestions:
            record = suggestions[0]
            return ResolvedLocation(
                query=raw,
                code=record.code,
                kind=record.kind,
                name=record.name,
                municipality=record.municipality,
                country=record.country,
            )

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

    def _build_resolved_location(self, query: str, code: str, *, kind: str) -> ResolvedLocation:
        record = self._pick_record_for_code(code, kind=kind)
        if record is not None:
            return ResolvedLocation(
                query=query,
                code=record.code,
                kind=kind,
                name=record.name,
                municipality=record.municipality,
                country=record.country,
            )
        if kind == "metro":
            return ResolvedLocation(
                query=query,
                code=code,
                kind=kind,
                name=query,
                country=METRO_CODE_COUNTRIES.get(code, ""),
            )
        return ResolvedLocation(
            query=query,
            code=code,
            kind=kind,
            name=query,
            country=MANUAL_AIRPORT_COUNTRIES.get(code, ""),
        )

    def _pick_record_for_code(self, code: str, *, kind: str) -> LocationRecord | None:
        records = self._records_by_code.get(code, [])
        preferred = [record for record in records if record.kind == kind]
        if not preferred:
            return None
        with_country = [record for record in preferred if record.country]
        if with_country:
            return sorted(with_country, key=lambda record: (len(record.name), record.name))[0]
        return sorted(preferred, key=lambda record: (len(record.name), record.name))[0]

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
def load_location_mappings() -> LocationMappings:
    if not LOCATION_MAPPINGS_PATH.exists():
        raise FileNotFoundError(f"未找到地点映射文件: {LOCATION_MAPPINGS_PATH}")

    raw_data = json.loads(LOCATION_MAPPINGS_PATH.read_text(encoding="utf-8"))
    if not isinstance(raw_data, dict):
        raise ValueError(f"地点映射文件格式错误: {LOCATION_MAPPINGS_PATH}")

    return LocationMappings(
        metro_codes=_normalize_mapping(raw_data.get("metro_codes", {}), value_upper=True),
        metro_code_countries=_normalize_mapping(
            raw_data.get("metro_code_countries", {}), key_upper=True, value_upper=True
        ),
        airport_aliases=_normalize_mapping(raw_data.get("airport_aliases", {}), value_upper=True),
        airport_code_countries=_normalize_mapping(
            raw_data.get("airport_code_countries", {}), key_upper=True, value_upper=True
        ),
    )


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
                country=METRO_CODE_COUNTRIES.get(code, ""),
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
                country=MANUAL_AIRPORT_COUNTRIES.get(code, ""),
                search_name=alias.lower(),
            )
        )

    return records


def _normalize_mapping(
    raw_mapping: object, *, key_upper: bool = False, value_upper: bool = False
) -> dict[str, str]:
    if not isinstance(raw_mapping, dict):
        raise ValueError(f"地点映射段落必须是对象: {raw_mapping!r}")

    normalized: dict[str, str] = {}
    for raw_key, raw_value in raw_mapping.items():
        key = str(raw_key).strip()
        value = str(raw_value).strip()
        if not key or not value:
            continue
        if key_upper:
            key = key.upper()
        if value_upper:
            value = value.upper()
        normalized[key] = value
    return normalized


_LOCATION_MAPPINGS = load_location_mappings()
METRO_CODES = _LOCATION_MAPPINGS.metro_codes
METRO_CODE_COUNTRIES = _LOCATION_MAPPINGS.metro_code_countries
MANUAL_AIRPORT_CODES = _LOCATION_MAPPINGS.airport_aliases
MANUAL_AIRPORT_COUNTRIES = _LOCATION_MAPPINGS.airport_code_countries
