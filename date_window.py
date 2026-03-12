from __future__ import annotations

from datetime import datetime, timedelta


DATE_FMT = "%Y-%m-%d"


def build_date_window(center_date: str, window_days: int) -> list[str]:
    if window_days < 0:
        raise ValueError("window_days must be >= 0")

    base = datetime.strptime(center_date, DATE_FMT).date()
    return [
        (base + timedelta(days=offset)).strftime(DATE_FMT)
        for offset in range(-window_days, window_days + 1)
    ]
