from __future__ import annotations

import argparse
from collections import Counter
import json
from pathlib import Path
import sys
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from skyscanner_multi_domain.geo.regions import REGIONS
from skyscanner_multi_domain.models import RegionConfig
from skyscanner_multi_domain.parsing.page_parser import extract_page_quote


def load_snapshots(snapshot_dir: Path) -> list[dict[str, Any]]:
    if not snapshot_dir.exists():
        return []
    snapshots: list[dict[str, Any]] = []
    for path in sorted(snapshot_dir.glob("*.json")):
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if isinstance(payload, dict):
            payload["_path"] = str(path)
            snapshots.append(payload)
    return snapshots


def replay_snapshot(snapshot: dict[str, Any]) -> dict[str, Any]:
    region_code = str(snapshot.get("region") or "UNKNOWN")
    known = REGIONS.get(region_code)
    region = known or RegionConfig(
        code=region_code,
        name=region_code,
        domain=str(snapshot.get("domain") or ""),
        locale="",
        currency=str(snapshot.get("currency") or "USD"),
    )
    page_text = "\n".join(
        str(part or "")
        for part in (
            snapshot.get("page_text_head"),
            "\n".join(str(item) for item in snapshot.get("marker_contexts") or []),
            snapshot.get("page_text_tail"),
        )
        if part
    )
    quote = extract_page_quote(region, str(snapshot.get("url") or ""), page_text)
    return {
        "path": snapshot.get("_path"),
        "region": region_code,
        "status": snapshot.get("status"),
        "replay_status": quote.status,
        "price": quote.price,
        "currency": quote.currency,
        "confidence": quote.confidence,
        "price_source": quote.price_source,
        "recovered": quote.price is not None,
    }


def build_report(snapshots: list[dict[str, Any]]) -> dict[str, Any]:
    results = [replay_snapshot(snapshot) for snapshot in snapshots]
    confidence = Counter(_confidence_bucket(result.get("confidence")) for result in results if result.get("recovered"))
    return {
        "total_snapshots": len(results),
        "recovered_price_count": sum(1 for result in results if result["recovered"]),
        "still_failed_count": sum(1 for result in results if not result["recovered"]),
        "confidence": {
            "high": confidence["high"],
            "medium": confidence["medium"],
            "low": confidence["low"],
            "unknown": confidence["unknown"],
        },
        "results": results,
    }


def render_text_report(report: dict[str, Any]) -> str:
    confidence = report["confidence"]
    return "\n".join(
        [
            f"[replay] total {report['total_snapshots']} snapshots",
            f"[replay] recovered price: {report['recovered_price_count']}",
            f"[replay] still failed: {report['still_failed_count']}",
            "[replay] confidence high/medium/low: "
            f"{confidence['high']}/{confidence['medium']}/{confidence['low']}",
        ]
    )


def filter_snapshots(
    snapshots: list[dict[str, Any]],
    *,
    status: str | None,
    region: str | None,
) -> list[dict[str, Any]]:
    filtered = snapshots
    if status:
        filtered = [snapshot for snapshot in filtered if str(snapshot.get("status") or "") == status]
    if region:
        region_upper = region.upper()
        filtered = [snapshot for snapshot in filtered if str(snapshot.get("region") or "").upper() == region_upper]
    return filtered


def _confidence_bucket(value: Any) -> str:
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        return "unknown"
    if value >= 0.85:
        return "high"
    if value >= 0.65:
        return "medium"
    return "low"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Replay OpenCLI parser snapshots.")
    parser.add_argument("snapshot_dir", nargs="?", default="logs/snapshots/opencli")
    parser.add_argument("--status")
    parser.add_argument("--region")
    parser.add_argument("--json", action="store_true", dest="as_json")
    parser.add_argument("--write-fixtures", action="store_true")
    args = parser.parse_args(argv)

    snapshots = filter_snapshots(
        load_snapshots(Path(args.snapshot_dir)),
        status=args.status,
        region=args.region,
    )
    report = build_report(snapshots)
    if args.write_fixtures:
        fixture_dir = Path(args.snapshot_dir) / "fixtures"
        fixture_dir.mkdir(parents=True, exist_ok=True)
        (fixture_dir / "replay_report.json").write_text(
            json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True),
            encoding="utf-8",
        )
    if args.as_json:
        print(json.dumps(report, ensure_ascii=False, sort_keys=True))
    else:
        print(render_text_report(report))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
