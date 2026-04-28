from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional
import uuid


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
    debug_log_path: Optional[str] = None
    source_kind: Optional[str] = None


# ── AttemptTrace ──────────────────────────────────────────────────────────────

@dataclass
class AttemptTrace:
    run_id: str
    route_key: str
    region: str
    transport: str
    attempt_index: int
    source_kind: str
    used_cdp_cookies: bool
    used_profile_dir: bool
    wait_ms: int
    load_dom: bool
    network_idle: bool
    page_text_len: int
    page_url: str
    status: str
    parser_stage: Optional[str] = None
    failure_reason: Optional[str] = None
    elapsed_ms: int = 0
    price: Optional[float] = None
    currency: Optional[str] = None
    timestamp: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat(timespec="seconds"))

    def to_dict(self) -> dict:
        result = {
            "run_id": self.run_id,
            "route_key": self.route_key,
            "region": self.region,
            "transport": self.transport,
            "attempt_index": self.attempt_index,
            "source_kind": self.source_kind,
            "used_cdp_cookies": self.used_cdp_cookies,
            "used_profile_dir": self.used_profile_dir,
            "wait_ms": self.wait_ms,
            "load_dom": self.load_dom,
            "network_idle": self.network_idle,
            "page_text_len": self.page_text_len,
            "page_url": self.page_url,
            "status": self.status,
            "parser_stage": self.parser_stage,
            "failure_reason": self.failure_reason,
            "elapsed_ms": self.elapsed_ms,
            "price": self.price,
            "currency": self.currency,
            "timestamp": self.timestamp,
        }
        return result


def new_run_id() -> str:
    return uuid.uuid4().hex[:12]