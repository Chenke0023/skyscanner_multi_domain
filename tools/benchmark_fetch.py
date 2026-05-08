#!/usr/bin/env python3
"""Benchmark fetch tool — measures end-to-end fetch performance and quality.

Usage:
  python tools/benchmark_fetch.py \\
    --origin 北京 \\
    --destination 阿拉木图 \\
    --date 2026-05-20 \\
    --date-window 0 \\
    --runs 3 \\
    --json

Output: JSON benchmark report with wall time, price rates, tab metrics.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import statistics
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any

# Ensure the project root is on sys.path
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from skyscanner_multi_domain.runtime.paths import RUNTIME_DIR
from skyscanner_multi_domain.models import FlightQuote, RegionConfig
from skyscanner_multi_domain.geo.location_resolver import LocationResolver, LocationRecord
from skyscanner_multi_domain.geo.regions import (
    REGIONS, DEFAULT_REGIONS, build_effective_region_codes,
)
from skyscanner_multi_domain.scan.orchestrator import build_search_url, run_page_scan
from skyscanner_multi_domain.scan.fallback_router import build_fallback_telemetry


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Benchmark Skyscanner fetch performance")
    parser.add_argument("--origin", default="北京", help="Origin (Chinese/IATA)")
    parser.add_argument("--destination", default="阿拉木图", help="Destination (Chinese/IATA)")
    parser.add_argument("--date", default="2026-05-20", help="Date YYYY-MM-DD")
    parser.add_argument("--return-date", help="Return date YYYY-MM-DD")
    parser.add_argument("--date-window", type=int, default=0, help="Date window ±days")
    parser.add_argument("--regions", default="", help="Extra region codes (comma-separated)")
    parser.add_argument("--runs", type=int, default=3, help="Number of benchmark runs")
    parser.add_argument("--wait", type=int, default=10, help="Page wait seconds")
    parser.add_argument("--timeout", type=int, default=30, help="HTTP/CDP timeout")
    parser.add_argument("--transport", choices=["scrapling", "page", "opencli"], default="opencli")
    parser.add_argument("--fetch-pipeline", choices=["fast", "balanced", "session_heavy"], default="balanced")
    parser.add_argument("--json", action="store_true", help="Output JSON only")
    parser.add_argument("--save", action="store_true", help="Save benchmark results to file")
    return parser.parse_args()


def _resolve_regions(args: argparse.Namespace) -> tuple[LocationRecord, LocationRecord, list[str], list[RegionConfig]]:
    resolver = LocationResolver()
    origin = resolver.resolve_location(args.origin, prefer_metro=True)
    destination = resolver.resolve_location(args.destination, prefer_metro=False)

    manual_regions = [c.strip().upper() for c in args.regions.split(",") if c.strip()]
    region_codes = build_effective_region_codes(
        origin_country=origin.country,
        destination_country=destination.country,
        manual_region_codes=manual_regions,
    )
    selected_regions = [REGIONS[code] for code in region_codes if code in REGIONS]
    return (
        LocationRecord(name=origin.name, code=origin.code, kind=origin.kind,
                       municipality=origin.municipality, country=origin.country),
        LocationRecord(name=destination.name, code=destination.code, kind=destination.kind,
                       municipality=destination.municipality, country=destination.country),
        region_codes,
        selected_regions,
    )


async def _single_run(
    args: argparse.Namespace,
    origin: LocationRecord,
    destination: LocationRecord,
    region_codes: list[str],
    selected_regions: list[RegionConfig],
    run_index: int,
) -> dict[str, Any]:
    start = time.monotonic()
    try:
        quotes = await run_page_scan(
            origin=origin.code,
            destination=destination.code,
            date=args.date,
            region_codes=region_codes,
            return_date=args.return_date,
            page_wait=args.wait,
            timeout=args.timeout,
            transport=args.transport,
            scan_mode="full_scan",
            allow_browser_fallback=False,
            fetch_pipeline=args.fetch_pipeline,
        )
    except Exception as exc:
        wall_ms = int((time.monotonic() - start) * 1000)
        return {
            "run": run_index + 1,
            "wall_time_ms": wall_ms,
            "error": str(exc),
            "fetch_price_found_count": 0,
            "fetch_total_regions": len(selected_regions),
            "fetch_price_found_rate": 0.0,
            "opencli_direct_price_found_count": 0,
            "fallback_rescued_count": 0,
            "fetch_challenge_count": 0,
            "tab_open_total": 0,
            "tab_reuse_total": 0,
            "extract_attempt_total": 0,
            "fallback_telemetry": {},
        }

    wall_ms = int((time.monotonic() - start) * 1000)
    total = len(quotes)
    found = sum(1 for q in quotes if q.price is not None)
    opencli_direct = sum(1 for q in quotes if q.source_kind == "opencli" and q.price is not None)
    fallback_rescued = sum(
        1 for q in quotes
        if q.price is not None and q.fallback_attempts
    )
    challenge = sum(
        1 for q in quotes
        if "challenge" in (q.status or "").lower()
    )
    tab_open_total = sum(q.tab_open_count for q in quotes)
    tab_reuse_total = sum(q.reused_tab_count for q in quotes)
    extract_attempt_total = sum(q.extract_attempt_count for q in quotes)
    fb_telemetry = build_fallback_telemetry(quotes)

    return {
        "run": run_index + 1,
        "wall_time_ms": wall_ms,
        "fetch_price_found_count": found,
        "fetch_total_regions": total,
        "fetch_price_found_rate": found / max(total, 1),
        "opencli_direct_price_found_count": opencli_direct,
        "opencli_direct_price_found_rate": opencli_direct / max(total, 1),
        "fallback_rescued_count": fallback_rescued,
        "fallback_rescue_rate": fallback_rescued / max(total, 1),
        "fetch_challenge_count": challenge,
        "fetch_challenge_rate": challenge / max(total, 1),
        "tab_open_total": tab_open_total,
        "tab_reuse_total": tab_reuse_total,
        "extract_attempt_total": extract_attempt_total,
        "fallback_telemetry": fb_telemetry,
    }


def _print_run_summary(run: dict[str, Any], *, verbose: bool = False) -> None:
    print(
        f"  Run {run['run']}: "
        f"{run['wall_time_ms'] / 1000:.1f}s, "
        f"price rate {run['fetch_price_found_rate']:.0%} "
        f"({run['fetch_price_found_count']}/{run['fetch_total_regions']}), "
        f"opencli direct {run['opencli_direct_price_found_count']}, "
        f"fallback rescued {run['fallback_rescued_count']}, "
        f"challenge {run['fetch_challenge_count']}, "
        f"tabs opened {run['tab_open_total']}, reused {run['tab_reuse_total']}"
    )


def _compute_aggregate(runs: list[dict[str, Any]]) -> dict[str, Any]:
    wall_times = [r["wall_time_ms"] for r in runs]
    price_rates = [r["fetch_price_found_rate"] for r in runs]
    opencli_rates = [r.get("opencli_direct_price_found_rate", 0) for r in runs]
    rescue_rates = [r.get("fallback_rescue_rate", 0) for r in runs]
    challenge_rates = [r.get("fetch_challenge_rate", 0) for r in runs]
    tab_opens = [r["tab_open_total"] for r in runs]
    tab_reuses = [r["tab_reuse_total"] for r in runs]

    return {
        "runs": len(runs),
        "avg_wall_time_ms": statistics.mean(wall_times),
        "min_wall_time_ms": min(wall_times),
        "max_wall_time_ms": max(wall_times),
        "stdev_wall_time_ms": statistics.stdev(wall_times) if len(wall_times) > 1 else 0,
        "avg_fetch_price_found_rate": statistics.mean(price_rates),
        "avg_opencli_direct_price_found_rate": statistics.mean(opencli_rates),
        "avg_fallback_rescue_rate": statistics.mean(rescue_rates),
        "avg_challenge_rate": statistics.mean(challenge_rates),
        "avg_tab_open_total": statistics.mean(tab_opens),
        "avg_tab_reuse_total": statistics.mean(tab_reuses),
        "run_details": runs,
    }


def _save_benchmark(report: dict[str, Any], args: argparse.Namespace) -> Path:
    bench_dir = RUNTIME_DIR / "benchmarks"
    bench_dir.mkdir(parents=True, exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    origin_token = args.origin.replace(" ", "_")
    dest_token = args.destination.replace(" ", "_")
    filename = bench_dir / f"bench_{origin_token}_{dest_token}_{args.date.replace('-', '')}_{ts}.json"
    filename.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return filename


async def main() -> int:
    args = _parse_args()
    origin, destination, region_codes, selected_regions = _resolve_regions(args)

    if not selected_regions:
        print("No regions selected.", file=sys.stderr)
        return 1

    if not args.json:
        print(f"Benchmark: {origin.code} -> {destination.code} on {args.date}")
        print(f"Regions: {', '.join(r.code for r in selected_regions)}")
        print(f"Transport: {args.transport}, pipeline: {args.fetch_pipeline}")
        print(f"Runs: {args.runs}\n")

    runs: list[dict[str, Any]] = []
    for i in range(args.runs):
        run = await _single_run(args, origin, destination, region_codes, selected_regions, i)
        runs.append(run)
        if not args.json:
            _print_run_summary(run)
        # Brief pause between runs
        if i < args.runs - 1:
            await asyncio.sleep(2)

    report = {
        "benchmark": {
            "origin": origin.code,
            "destination": destination.code,
            "date": args.date,
            "return_date": args.return_date,
            "regions": region_codes,
            "transport": args.transport,
            "fetch_pipeline": args.fetch_pipeline,
            "timestamp": datetime.now().isoformat(timespec="seconds"),
        },
        "aggregate": _compute_aggregate(runs),
    }

    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        agg = report["aggregate"]
        print(f"\nAggregate ({agg['runs']} runs):")
        print(f"  Wall time: avg {agg['avg_wall_time_ms'] / 1000:.1f}s "
              f"(min {agg['min_wall_time_ms'] / 1000:.1f}s, "
              f"max {agg['max_wall_time_ms'] / 1000:.1f}s)")
        print(f"  Price found rate: {agg['avg_fetch_price_found_rate']:.0%}")
        print(f"  OpenCLI direct rate: {agg['avg_opencli_direct_price_found_rate']:.0%}")
        print(f"  Fallback rescue rate: {agg['avg_fallback_rescue_rate']:.0%}")
        print(f"  Challenge rate: {agg['avg_challenge_rate']:.0%}")
        print(f"  Tabs: opened avg {agg['avg_tab_open_total']:.0f}, reused avg {agg['avg_tab_reuse_total']:.0f}")

    if args.save:
        path = _save_benchmark(report, args)
        if not args.json:
            print(f"\nSaved to: {path}")

    # Exit code: 0 if any run found prices, 1 otherwise
    has_prices = any(r["fetch_price_found_count"] > 0 for r in runs)
    return 0 if has_prices else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
