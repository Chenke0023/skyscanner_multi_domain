from __future__ import annotations

from dataclasses import dataclass
from typing import Optional


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
