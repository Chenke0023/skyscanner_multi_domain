from __future__ import annotations

from datetime import date, datetime, timedelta


DATE_FMT = "%Y-%m-%d"


def parse_iso_date(value: str) -> date:
    return datetime.strptime(value, DATE_FMT).date()


def format_iso_date(value: date) -> str:
    return value.strftime(DATE_FMT)


def build_date_window(center_date: str, window_days: int) -> list[str]:
    if window_days < 0:
        raise ValueError("window_days must be >= 0")

    base = parse_iso_date(center_date)
    return [
        format_iso_date(base + timedelta(days=offset))
        for offset in range(-window_days, window_days + 1)
    ]


def build_round_trip_date_window(
    departure_date: str, return_date: str, window_days: int
) -> list[tuple[str, str]]:
    departure = parse_iso_date(departure_date)
    inbound = parse_iso_date(return_date)
    stay_length = (inbound - departure).days
    if stay_length < 0:
        raise ValueError("return_date must be >= departure_date")

    date_pairs: list[tuple[str, str]] = []
    for current_departure in build_date_window(departure_date, window_days):
        shifted_departure = parse_iso_date(current_departure)
        shifted_return = shifted_departure + timedelta(days=stay_length)
        date_pairs.append((current_departure, format_iso_date(shifted_return)))
    return date_pairs


def format_trip_date_label(departure_date: str, return_date: str | None = None) -> str:
    if not return_date:
        return departure_date
    return f"{departure_date} -> {return_date}"
