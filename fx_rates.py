from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

import requests

from app_paths import get_fx_cache_file

FALLBACK_RATES_TO_CNY = {
    "CNY": 1.0,
    "USD": 6.91,
    "GBP": 9.29,
    "SGD": 5.44,
    "HKD": 0.88,
    "EUR": 7.49,
    "JPY": 0.046,
    "KZT": 0.013,
    "SEK": 0.68,
    "KRW": 0.0051,
    "IDR": 0.00043,
    "CHF": 8.55,
    "AUD": 4.55,
    "CAD": 5.08,
    "INR": 0.083,
    "MXN": 0.40,
    "RUB": 0.075,
    "BRL": 1.40,
}
FX_CACHE_TTL_HOURS = 24
FX_API_URL = "https://open.er-api.com/v6/latest/CNY"


@dataclass(frozen=True)
class FxSnapshot:
    rates_to_cny: dict[str, float]
    fetched_at: datetime | None
    source: str
    is_stale: bool


class FxRateService:
    def __init__(self) -> None:
        self._snapshot: FxSnapshot | None = None

    def convert_to_cny(self, amount: float | None, currency: str | None) -> float | None:
        if amount is None or not currency:
            return None
        normalized = currency.strip().upper()
        rate = self.get_rates_to_cny().get(normalized)
        if rate is None:
            return None
        return round(amount * rate, 2)

    def get_rates_to_cny(self) -> dict[str, float]:
        return self.get_snapshot().rates_to_cny

    def get_snapshot(self) -> FxSnapshot:
        if self._snapshot is not None:
            return self._snapshot

        cache_payload = self._load_cache()
        if cache_payload is not None and not self._is_stale(cache_payload):
            self._snapshot = self._snapshot_from_payload(cache_payload, source="cache", is_stale=False)
            return self._snapshot

        fresh_payload = self._fetch_remote_rates()
        if fresh_payload is not None:
            self._write_cache(fresh_payload)
            self._snapshot = self._snapshot_from_payload(fresh_payload, source="remote", is_stale=False)
            return self._snapshot

        if cache_payload is not None:
            self._snapshot = self._snapshot_from_payload(cache_payload, source="stale-cache", is_stale=True)
            return self._snapshot

        self._snapshot = FxSnapshot(
            rates_to_cny=dict(FALLBACK_RATES_TO_CNY),
            fetched_at=None,
            source="fallback",
            is_stale=True,
        )
        return self._snapshot

    def _fetch_remote_rates(self) -> dict[str, Any] | None:
        try:
            response = requests.get(FX_API_URL, timeout=8)
            response.raise_for_status()
            payload = response.json()
        except (requests.RequestException, ValueError):
            return None

        rates = payload.get("rates")
        if not isinstance(rates, dict):
            return None

        rates_to_cny: dict[str, float] = {"CNY": 1.0}
        for currency, value in rates.items():
            if not isinstance(currency, str) or not isinstance(value, (int, float)) or value <= 0:
                continue
            rates_to_cny[currency.upper()] = round(1 / float(value), 8)

        if len(rates_to_cny) <= 1:
            return None

        return {
            "fetched_at": datetime.now(timezone.utc).isoformat(),
            "rates_to_cny": rates_to_cny,
        }

    def _load_cache(self) -> dict[str, Any] | None:
        cache_file = get_fx_cache_file()
        if not cache_file.exists():
            return None
        try:
            return json.loads(cache_file.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None

    def _write_cache(self, payload: dict[str, Any]) -> None:
        cache_file = get_fx_cache_file()
        cache_file.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True),
            encoding="utf-8",
        )

    def _is_stale(self, payload: dict[str, Any]) -> bool:
        fetched_at = self._parse_timestamp(payload.get("fetched_at"))
        if fetched_at is None:
            return True
        return datetime.now(timezone.utc) - fetched_at > timedelta(hours=FX_CACHE_TTL_HOURS)

    def _snapshot_from_payload(
        self, payload: dict[str, Any], *, source: str, is_stale: bool
    ) -> FxSnapshot:
        rates_raw = payload.get("rates_to_cny")
        rates: dict[str, float] = dict(FALLBACK_RATES_TO_CNY)
        if isinstance(rates_raw, dict):
            for currency, value in rates_raw.items():
                if not isinstance(currency, str) or not isinstance(value, (int, float)) or value <= 0:
                    continue
                rates[currency.upper()] = float(value)
        return FxSnapshot(
            rates_to_cny=rates,
            fetched_at=self._parse_timestamp(payload.get("fetched_at")),
            source=source,
            is_stale=is_stale,
        )

    def _parse_timestamp(self, value: Any) -> datetime | None:
        if not isinstance(value, str) or not value.strip():
            return None
        try:
            return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(timezone.utc)
        except ValueError:
            return None
