from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal

from skyscanner_multi_domain.runtime.paths import get_price_confirmation_file

ConfirmationStatus = Literal["confirmed", "mismatched"]


@dataclass(frozen=True)
class PriceConfirmationSample:
    status: ConfirmationStatus
    created_at: str
    date: str
    route: str
    region_code: str
    region_name: str
    link: str
    cheapest_cny_price: float | None
    best_cny_price: float | None
    confidence: float | None
    price_source: str
    evidence_text: str
    parser_warnings: list[str]
    note: str = ""


class PriceConfirmationStore:
    def __init__(self, path: Path | None = None) -> None:
        self.path = path or get_price_confirmation_file()

    def append(self, sample: PriceConfirmationSample) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(asdict(sample), ensure_ascii=False, sort_keys=True) + "\n")

    def load(self) -> list[PriceConfirmationSample]:
        if not self.path.exists():
            return []
        samples: list[PriceConfirmationSample] = []
        for line in self.path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            payload = json.loads(line)
            if isinstance(payload, dict):
                samples.append(sample_from_payload(payload))
        return samples

    def summary(self) -> dict[str, Any]:
        samples = self.load()
        confirmed = sum(1 for sample in samples if sample.status == "confirmed")
        mismatched = sum(1 for sample in samples if sample.status == "mismatched")
        return {
            "total": len(samples),
            "confirmed": confirmed,
            "mismatched": mismatched,
            "latest_at": samples[-1].created_at if samples else None,
        }


def sample_from_row(
    row: dict[str, Any],
    *,
    status: ConfirmationStatus,
    note: str = "",
    created_at: str | None = None,
) -> PriceConfirmationSample:
    return PriceConfirmationSample(
        status=status,
        created_at=created_at or datetime.now(timezone.utc).isoformat(),
        date=str(row.get("date") or ""),
        route=str(row.get("route") or ""),
        region_code=str(row.get("region_code") or "").strip().upper(),
        region_name=str(row.get("region_name") or row.get("region_code") or ""),
        link=str(row.get("link") or ""),
        cheapest_cny_price=_optional_float(row.get("cheapest_cny_price")),
        best_cny_price=_optional_float(row.get("best_cny_price")),
        confidence=_optional_float(row.get("confidence")),
        price_source=str(row.get("price_source") or ""),
        evidence_text=str(row.get("evidence_text") or ""),
        parser_warnings=_string_list(row.get("parser_warnings")),
        note=note,
    )


def sample_from_payload(payload: dict[str, Any]) -> PriceConfirmationSample:
    status: ConfirmationStatus = "mismatched" if payload.get("status") == "mismatched" else "confirmed"
    return PriceConfirmationSample(
        status=status,
        created_at=str(payload.get("created_at") or ""),
        date=str(payload.get("date") or ""),
        route=str(payload.get("route") or ""),
        region_code=str(payload.get("region_code") or "").strip().upper(),
        region_name=str(payload.get("region_name") or ""),
        link=str(payload.get("link") or ""),
        cheapest_cny_price=_optional_float(payload.get("cheapest_cny_price")),
        best_cny_price=_optional_float(payload.get("best_cny_price")),
        confidence=_optional_float(payload.get("confidence")),
        price_source=str(payload.get("price_source") or ""),
        evidence_text=str(payload.get("evidence_text") or ""),
        parser_warnings=_string_list(payload.get("parser_warnings")),
        note=str(payload.get("note") or ""),
    )


def _optional_float(value: object) -> float | None:
    if isinstance(value, (int, float)):
        return float(value)
    return None


def _string_list(value: object) -> list[str]:
    if not isinstance(value, list):
        return []
    return [str(item) for item in value if str(item).strip()]
