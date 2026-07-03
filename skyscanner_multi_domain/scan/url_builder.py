from __future__ import annotations

import json
import re
from datetime import datetime
from pathlib import Path
from typing import Any
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse

from skyscanner_multi_domain.geo.regions import RegionConfig
from skyscanner_multi_domain.models import FlightQuote
from skyscanner_multi_domain.parsing.page_parser import first_currency, parse_float

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


def load_capture_file(path: Path) -> list[dict[str, Any]]:
    data = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(data, list):
        return [item for item in data if isinstance(item, dict)]
    if isinstance(data, dict):
        return [data]
    raise ValueError("Capture file must contain a JSON object or array")


def find_candidate_captures(
    captures: list[dict[str, Any]],
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
                    source_destination = str(destination_place.get("iata") or "").upper() or None
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
                elif isinstance(leg_date, str) and re.fullmatch(r"\d{4}-\d{2}-\d{2}", leg_date):
                    source_iso_date = leg_date
            if source_origin and source_destination and source_iso_date:
                break
        if source_origin and source_destination and source_iso_date:
            break

    if source_iso_date:
        _, _, source_short_date = parse_date(source_iso_date)

    for path in (["query", "market"], ["market"], ["marketCode"], ["context", "market"]):
        nested_set(body, path, region.code)

    for path in (["query", "locale"], ["locale"], ["context", "locale"]):
        nested_set(body, path, region.locale)

    for path in (["query", "currency"], ["currency"], ["context", "currency"]):
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
        text = re.sub(rf"\b{re.escape(source_origin)}\b", origin, text, flags=re.IGNORECASE)
    if source_destination:
        text = re.sub(rf"\b{re.escape(source_destination)}\b", destination, text, flags=re.IGNORECASE)
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


def pick_currency(data: Any) -> str | None:
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


def collect_price_candidates(data: Any, path: str = "") -> list[tuple[float, str | None, str]]:
    candidates: list[tuple[float, str | None, str]] = []

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
                    candidates.append((nested_price, nested_currency, f"{key_path}.amount"))
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
