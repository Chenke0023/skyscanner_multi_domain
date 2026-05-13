"""
Practical CLI for Skyscanner multi-market scans via browser CDP page reads.

Default path:
1. Use the local browser instance on CDP port 9222, preferring Comet.
2. Open each market's result page.
3. Read the rendered page text and extract both the "Best" and "Cheapest" prices.

Example:
  python cli.py page -o 北京 -d 阿拉木图 -t 2026-04-29
"""

from __future__ import annotations

import argparse
import asyncio
import fcntl
import plistlib
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from typing import Optional

from skyscanner_multi_domain.runtime.paths import PROJECT_ROOT, RUNTIME_DIR, get_log_file, get_reports_dir
from skyscanner_multi_domain.planning.date_window import (
    format_trip_date_label,
)
from failure_replay import (
    DEFAULT_FAILURE_DIR,
    build_failure_replay_report,
    render_failure_replay_report,
)
from skyscanner_multi_domain.pricing.fx_rates import FxRateService
from skyscanner_multi_domain.geo.location_resolver import (
    COUNTRY_ROUTE_DEFAULT_AIRPORT_LIMIT,
    CountryRecord,
    LocationRecord,
    LocationResolver,
    ResolvedLocation,
)
from skyscanner_multi_domain.scan.history import (
    ScanHistoryStore,
    annotate_rows_with_history,
    build_delta_summary_lines,
    build_fetch_quality_telemetry,
    build_parser_recovery_telemetry,
    build_snapshot_summary,
    can_reuse_page_for_row,
    classify_failure,
    get_failed_region_codes,
    get_quotes_for_trip_label,
    get_rows_for_trip_label,
    merge_quotes_by_date,
    merge_rows_by_date,
    override_quotes_source_kind,
    override_rows_source_kind,
    source_kind_label,
)
from skyscanner_multi_domain.planning.search_plan import (
    TripIntent,
    build_ordered_trip_dates,
    build_search_plan,
    rank_route_pairs,
    render_search_plan,
)
from skyscanner_multi_domain.scan.output_rows import (
    CombinedQuoteRow,
    QuoteRow,
    SimplifiedQuoteRow,
)
from skyscanner_multi_domain.scan.config import (
    CdpMode,
    ChallengePolicy,
    LowConfidencePolicy,
    ScanConfig,
    TransportMode,
)
from skyscanner_neo import (
    DEFAULT_REGIONS,
    NeoCli,
    REGIONS,
    build_effective_region_codes,
    detect_cdp_version,
    print_doctor,
    quotes_to_dicts,
    run_page_scan,
)


BEST_LABEL = "最佳"
CHEAPEST_LABEL = "最低价"
CLI_REGION_CONCURRENCY = 3
CLI_DATE_WINDOW_CONCURRENCY = 2
CLI_AIRPORT_PAIR_CONCURRENCY = 2
LAUNCHD_LABEL = "com.skyscanner-multi-domain.auto-refresh"
DEFAULT_LAUNCHD_INTERVAL_MINUTES = 600

_PRICE_SOURCE_LABELS: dict[str, str] = {
    "cheapest_block": "Cheapest 区块",
    "best_block": "Best 区块",
    "first_price_fallback": "首个价格 fallback",
    "recovered_best": "恢复解析",
    "manual_confirmed": "人工确认",
    "unpriced": "未取价",
}


def _confidence_label(value: object) -> str:
    if not isinstance(value, (int, float)):
        return "未知"
    if value >= 0.85:
        return "高"
    if value >= 0.6:
        return "中"
    if value >= 0.3:
        return "低"
    return "极低"


def _price_source_label(value: object) -> str:
    if value in (None, "", "unknown"):
        return "未知"
    return _PRICE_SOURCE_LABELS.get(str(value), str(value))


def _warnings_summary(warnings: object) -> str:
    if not isinstance(warnings, (list, tuple)):
        return "-"
    cleaned = [str(item).strip() for item in warnings if str(item).strip()]
    if not cleaned:
        return "-"
    if len(cleaned) == 1:
        return cleaned[0]
    return f"{len(cleaned)} 项警告"


def _failed_reason_counts(rows: list[dict[str, object]]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for row in rows:
        category = row.get("failure_category")
        if not category:
            continue
        reason = str(category).strip() or "unknown"
        counts[reason] = counts.get(reason, 0) + 1
    return counts


def _row_cny_value(row: dict[str, object]) -> float | None:
    cheapest = row.get("cheapest_cny_price")
    if isinstance(cheapest, (int, float)):
        return float(cheapest)
    best = row.get("best_cny_price")
    if isinstance(best, (int, float)):
        return float(best)
    return None


def _row_warning_lines(row: dict[str, object]) -> list[str]:
    warnings = row.get("parser_warnings")
    if not isinstance(warnings, (list, tuple)):
        return []
    return [str(item).strip() for item in warnings if str(item).strip()]


def _build_decision_summary(
    rows: list[dict[str, object]],
    *,
    show_dates: bool = False,
) -> list[str]:
    valid_pairs: list[tuple[dict[str, object], float]] = []
    for row in rows:
        value = _row_cny_value(row)
        if value is not None:
            valid_pairs.append((row, value))

    failed_counts = _failed_reason_counts(rows)

    if not valid_pairs:
        lines = ["## 扫描结论", "", "本次未抓取到任何有效价格，请检查市场可达性后重试。", ""]
        if failed_counts:
            lines.append("失败原因汇总：")
            lines.extend(
                f"- {reason}: {count}"
                for reason, count in sorted(failed_counts.items())
            )
            lines.append("")
        return lines

    valid_pairs.sort(key=lambda item: item[1])
    primary_row, primary_value = valid_pairs[0]
    runner_row: dict[str, object] | None = None
    runner_value: float | None = None
    if len(valid_pairs) > 1:
        runner_row, runner_value = valid_pairs[1]

    policy_mode = str(primary_row.get("execution_policy_mode") or "exact")
    lines = ["## 扫描结论", ""]
    if policy_mode == "fast":
        lines.append("Fast Mode 已启用：本报告不是完整市场全集扫描结果。")
    else:
        lines.append("Exact Mode：已扫描完整计划市场集。")
    lines.extend(["", "### 推荐先验证", ""])
    lines.append(f"- 最低价：¥{primary_value:,.2f}")
    if show_dates:
        lines.append(f"- 日期：{primary_row.get('date') or '-'}")
    lines.append(f"- 航段：{primary_row.get('route') or '-'}")
    lines.append(f"- 市场：{primary_row.get('region_name') or '-'}")
    lines.append(f"- 价格来源：{_price_source_label(primary_row.get('price_source'))}")
    candidate_sources = primary_row.get("candidate_sources")
    if isinstance(candidate_sources, list) and candidate_sources:
        lines.append(f"- 候选来源：{', '.join(str(item) for item in candidate_sources)}")
    fallback_attempts = primary_row.get("fallback_attempts")
    if isinstance(fallback_attempts, list) and fallback_attempts:
        chain = " -> ".join(
            str(item.get("transport") or item.get("status") or "?")
            for item in fallback_attempts
            if isinstance(item, dict)
        )
        lines.append(f"- Fallback：{chain or '无'}")
    else:
        lines.append("- Fallback：无")
    lines.append(f"- 可信度：{_confidence_label(primary_row.get('confidence'))}")
    link = primary_row.get("link")
    if isinstance(link, str) and link:
        lines.append(f"- 链接：[打开结果页]({link})")
    lines.append("")

    if runner_row is not None and runner_value is not None:
        spread = runner_value - primary_value
        lines.extend(["### 备选结果", ""])
        lines.append(f"- 第二低价：¥{runner_value:,.2f}")
        lines.append(f"- 价差：¥{spread:,.2f}")
        if show_dates:
            lines.append(f"- 日期：{runner_row.get('date') or '-'}")
        lines.append(f"- 航段：{runner_row.get('route') or '-'}")
        lines.append(f"- 市场：{runner_row.get('region_name') or '-'}")
        lines.append(f"- 可信度：{_confidence_label(runner_row.get('confidence'))}")
        lines.append("")

    risk_lines = _build_decision_risk_hints(
        rows=rows,
        valid_pairs=valid_pairs,
        primary_row=primary_row,
        primary_value=primary_value,
        runner_row=runner_row,
        runner_value=runner_value,
        failed_counts=failed_counts,
    )
    if risk_lines:
        lines.extend(["### 风险提示", ""])
        lines.extend(f"- {line}" for line in risk_lines)
        lines.append("")
    return lines


_RISKY_FAILURE_TOKENS: tuple[str, ...] = (
    "challenge",
    "loading",
    "network",
    "browser_missing",
    "parse_failed",
    "blocked",
    "timeout",
)


def _build_decision_risk_hints(
    *,
    rows: list[dict[str, object]],
    valid_pairs: list[tuple[dict[str, object], float]],
    primary_row: dict[str, object],
    primary_value: float,
    runner_row: dict[str, object] | None,
    runner_value: float | None,
    failed_counts: dict[str, int],
) -> list[str]:
    hints: list[str] = []
    primary_conf = primary_row.get("confidence")
    primary_source = primary_row.get("price_source")
    runner_conf = runner_row.get("confidence") if runner_row is not None else None

    if isinstance(primary_conf, (int, float)) and primary_conf < 0.6:
        if (
            runner_value is not None
            and isinstance(runner_conf, (int, float))
            and runner_conf >= 0.85
            and primary_value > 0
            and runner_value - primary_value < primary_value * 0.05
        ):
            hints.append("最低价需复核；第二低价可信度更高且价差较小。")
        else:
            hints.append("最低价可信度偏低，建议点开页面复核。")
    if primary_source == "first_price_fallback":
        hints.append("最低价来自首个价格 fallback，必须人工确认。")
    if any(str(row.get("execution_policy_mode") or "exact") == "fast" for row, _ in valid_pairs):
        hints.append("Fast Mode 结果不是完整市场全集扫描，不能等同于全量最低价。")

    for warning in _row_warning_lines(primary_row):
        hints.append(f"解析警告：{warning}")

    risky_hits = {
        reason: count
        for reason, count in failed_counts.items()
        if any(token in reason for token in _RISKY_FAILURE_TOKENS)
    }
    if risky_hits:
        summary = "、".join(
            f"{reason}×{count}" for reason, count in sorted(risky_hits.items())
        )
        hints.append(
            f"{sum(risky_hits.values())} 个市场失败（{summary}），可能存在漏价。"
        )
    challenge_count = sum(
        1
        for row in rows
        if "challenge" in str(row.get("status") or row.get("failure_category") or "").lower()
    )
    if challenge_count:
        hints.append(f"{challenge_count} 个市场出现 challenge，未自动重复尝试，存在覆盖风险。")

    fallback_sources = {"first_price_fallback", "recovered_best"}
    fallback_only = all(
        (row.get("price_source") in fallback_sources)
        or row.get("price_source") in (None, "", "unknown")
        for row, _ in valid_pairs
    )
    if valid_pairs and fallback_only:
        hints.append("所有有效价格均来自 fallback 解析，作为初筛结果，需人工复核。")
    return hints


def _build_warning_detail_section(
    rows: list[dict[str, object]],
    *,
    show_dates: bool = False,
) -> list[str]:
    detail_rows = [row for row in rows if _row_warning_lines(row)]
    if not detail_rows:
        return []
    lines = ["## 解析警告与证据", ""]
    for row in detail_rows:
        header_parts: list[str] = []
        region_name = row.get("region_name") or row.get("region_code")
        if region_name:
            header_parts.append(str(region_name))
        route = row.get("route")
        if route and str(route) != "-":
            header_parts.append(str(route))
        if show_dates:
            date_value = row.get("date")
            if date_value and str(date_value) != "-":
                header_parts.append(str(date_value))
        header = " · ".join(header_parts) if header_parts else "未命名行"
        confidence = _confidence_label(row.get("confidence"))
        source = _price_source_label(row.get("price_source"))
        lines.append(f"- **{header}** — 可信度 {confidence} · 价格来源 {source}")
        for warning in _row_warning_lines(row):
            lines.append(f"  - {warning}")
        evidence = row.get("evidence_text")
        if isinstance(evidence, str) and evidence.strip():
            lines.append(f"  - 证据片段：{evidence.strip()}")
    lines.append("")
    return lines


def _trip_file_token(date: str, return_date: str | None = None) -> str:
    token = date.replace("-", "")
    if return_date:
        token = f"{token}_rt{return_date.replace('-', '')}"
    return token


def _safe_output_token(value: str) -> str:
    cleaned = "".join(ch if ch.isalnum() else "_" for ch in value.strip())
    cleaned = cleaned.strip("_")
    return cleaned or "unknown"


def run_failure_replay_command(args: argparse.Namespace) -> int:
    failure_dir = Path(args.failure_dir).expanduser()
    report = build_failure_replay_report(failure_dir)
    print(render_failure_replay_report(report, show_samples=args.show_samples))
    return 0 if report.total_samples else 1


def _auto_refresh_lock_path() -> Path:
    RUNTIME_DIR.mkdir(parents=True, exist_ok=True)
    return RUNTIME_DIR / "background_auto_refresh.lock"


def _is_ac_power_connected() -> bool:
    result = subprocess.run(
        ["pmset", "-g", "batt"],
        check=False,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        return False
    output = f"{result.stdout}\n{result.stderr}".lower()
    return "'ac power'" in output or "ac power" in output


def _build_args_from_saved_query(
    query_payload: dict[str, object],
    args: argparse.Namespace,
) -> argparse.Namespace:
    identity = query_payload.get("identity") if isinstance(query_payload, dict) else {}
    if not isinstance(identity, dict):
        identity = {}
    manual_regions = identity.get("manual_regions")
    regions = ",".join(str(code).strip().upper() for code in manual_regions if str(code).strip()) if isinstance(manual_regions, list) else ""
    mode = str(identity.get("mode") or "point_to_point")
    origin_input = str(identity.get("origin_input") or identity.get("origin_label") or identity.get("origin_code") or "")
    destination_input = str(
        identity.get("destination_input") or identity.get("destination_label") or identity.get("destination_code") or ""
    )
    origin_is_country = bool(identity.get("origin_is_country"))
    destination_is_country = bool(identity.get("destination_is_country"))
    return argparse.Namespace(
        command="page",
        origin=None if mode == "expanded_route" and origin_is_country else origin_input,
        destination=None if mode == "expanded_route" and destination_is_country else destination_input,
        origin_country=origin_input if mode == "expanded_route" and origin_is_country else None,
        destination_country=destination_input if mode == "expanded_route" and destination_is_country else None,
        country_airport_limit=int(identity.get("airport_limit") or COUNTRY_ROUTE_DEFAULT_AIRPORT_LIMIT),
        date=str(identity.get("date") or ""),
        return_date=str(identity.get("return_date") or "") or None,
        date_window=max(int(identity.get("date_window_days") or 0), 0),
        regions=regions,
        wait=int(getattr(args, "wait", 10)),
        timeout=int(getattr(args, "timeout", 30)),
        transport=str(getattr(args, "transport", "opencli")),
        exact_airport=bool(identity.get("exact_airport")),
        preview_only=False,
        rerun_failed=False,
        show_delta=bool(getattr(args, "show_delta", False)),
        show_plan=False,
        fetch_pipeline=str(getattr(args, "fetch_pipeline", "balanced")),
        save=bool(getattr(args, "save", True)),
    )


class SimpleCLI:
    def __init__(self) -> None:
        self.project_root = PROJECT_ROOT
        self.location_resolver = LocationResolver()
        self.fx_rates = FxRateService()
        self.history_store = ScanHistoryStore()

    def normalize_location(self, value: str, prefer_metro: bool) -> str:
        return self.location_resolver.normalize_location(
            value, prefer_metro=prefer_metro
        )

    def resolve_location(self, value: str, prefer_metro: bool) -> ResolvedLocation:
        return self.location_resolver.resolve_location(value, prefer_metro=prefer_metro)

    def resolve_country(self, value: str) -> CountryRecord:
        return self.location_resolver.resolve_country(value)

    def build_country_route_plan(
        self,
        origin_country_value: str,
        destination_country_value: str,
        *,
        manual_region_codes: list[str] | None = None,
        airport_limit: int = COUNTRY_ROUTE_DEFAULT_AIRPORT_LIMIT,
    ) -> tuple[CountryRecord, CountryRecord, list[LocationRecord], list[LocationRecord], list[str]]:
        origin_country, origin_airports = self.location_resolver.get_country_route_airports(
            origin_country_value,
            limit=airport_limit,
        )
        destination_country, destination_airports = (
            self.location_resolver.get_country_route_airports(
                destination_country_value,
                limit=airport_limit,
            )
        )
        regions = build_effective_region_codes(
            origin_country=origin_country.code,
            destination_country=destination_country.code,
            manual_region_codes=manual_region_codes or [],
        )
        return (
            origin_country,
            destination_country,
            origin_airports,
            destination_airports,
            regions,
        )

    def build_expanded_route_plan(
        self,
        *,
        origin_value: str | None,
        destination_value: str | None,
        origin_is_country: bool,
        destination_is_country: bool,
        prefer_origin_metro: bool,
        manual_region_codes: list[str] | None = None,
        airport_limit: int = COUNTRY_ROUTE_DEFAULT_AIRPORT_LIMIT,
    ) -> tuple[str, str, str, str, list[LocationRecord], list[LocationRecord], list[str]]:
        if origin_is_country:
            if not origin_value:
                raise ValueError("缺少出发国家。")
            origin_country, origin_points = self.location_resolver.get_country_route_airports(
                origin_value,
                limit=airport_limit,
            )
            origin_label = origin_country.name
            origin_file_token = f"{origin_country.code}_ANY"
            origin_region_country = origin_country.code
        else:
            if not origin_value:
                raise ValueError("缺少出发地。")
            origin = self.resolve_location(origin_value, prefer_metro=prefer_origin_metro)
            origin_points = [
                LocationRecord(
                    name=origin.name,
                    code=origin.code,
                    kind=origin.kind,
                    municipality=origin.municipality,
                    country=origin.country,
                )
            ]
            origin_label = origin.query or origin.name or origin.code
            origin_file_token = origin.code
            origin_region_country = origin.country

        if destination_is_country:
            if not destination_value:
                raise ValueError("缺少目的国家。")
            destination_country, destination_points = (
                self.location_resolver.get_country_route_airports(
                    destination_value,
                    limit=airport_limit,
                )
            )
            destination_label = destination_country.name
            destination_file_token = f"{destination_country.code}_ANY"
            destination_region_country = destination_country.code
        else:
            if not destination_value:
                raise ValueError("缺少目的地。")
            destination = self.resolve_location(destination_value, prefer_metro=False)
            destination_points = [
                LocationRecord(
                    name=destination.name,
                    code=destination.code,
                    kind=destination.kind,
                    municipality=destination.municipality,
                    country=destination.country,
                )
            ]
            destination_label = destination.query or destination.name or destination.code
            destination_file_token = destination.code
            destination_region_country = destination.country

        regions = build_effective_region_codes(
            origin_country=origin_region_country,
            destination_country=destination_region_country,
            manual_region_codes=manual_region_codes or [],
        )
        return (
            origin_label,
            destination_label,
            origin_file_token,
            destination_file_token,
            origin_points,
            destination_points,
            regions,
        )

    def build_effective_regions(
        self,
        origin_value: str,
        destination_value: str,
        *,
        prefer_origin_metro: bool,
        manual_region_codes: list[str] | None = None,
    ) -> tuple[ResolvedLocation, ResolvedLocation, list[str]]:
        origin = self.resolve_location(origin_value, prefer_metro=prefer_origin_metro)
        destination = self.resolve_location(destination_value, prefer_metro=False)
        regions = build_effective_region_codes(
            origin_country=origin.country,
            destination_country=destination.country,
            manual_region_codes=manual_region_codes or [],
        )
        return origin, destination, regions

    def print_banner(self) -> None:
        print(
            """
╔═══════════════════════════════════════════════════════════════╗
║      Skyscanner 多市场 CLI（浏览器页面模式）                ║
║      一条命令打开各站点并提取最佳价与最低价                   ║
╚═══════════════════════════════════════════════════════════════╝
            """.strip()
        )

    def to_cny(
        self, price: Optional[float], currency: Optional[str]
    ) -> Optional[float]:
        return self.fx_rates.convert_to_cny(price, currency)

    @staticmethod
    def _query_title(
        origin_label: str,
        destination_label: str,
        date: str,
        return_date: str | None = None,
    ) -> str:
        if return_date:
            return f"{origin_label} -> {destination_label} ({date} / {return_date})"
        return f"{origin_label} -> {destination_label} ({date})"

    def build_point_query_payload(
        self,
        *,
        origin_input: str,
        destination_input: str,
        origin_label: str,
        destination_label: str,
        origin_code: str,
        destination_code: str,
        date: str,
        return_date: str | None,
        date_window_days: int,
        manual_regions: list[str],
        effective_regions: list[str],
        exact_airport: bool,
    ) -> dict[str, object]:
        return {
            "identity": {
                "mode": "point_to_point",
                "origin_input": origin_input,
                "destination_input": destination_input,
                "origin_label": origin_label,
                "destination_label": destination_label,
                "origin_code": origin_code,
                "destination_code": destination_code,
                "date": date,
                "return_date": return_date,
                "date_window_days": int(date_window_days),
                "trip_type": "round_trip" if return_date else "one_way",
                "manual_regions": sorted(code.upper() for code in manual_regions),
                "effective_regions": list(effective_regions),
                "exact_airport": bool(exact_airport),
            },
            "display": {
                "title": self._query_title(origin_label, destination_label, date, return_date),
            },
        }

    def build_expanded_query_payload(
        self,
        *,
        origin_value: str,
        destination_value: str,
        origin_label: str,
        destination_label: str,
        origin_file_token: str,
        destination_file_token: str,
        date: str,
        return_date: str | None,
        date_window_days: int,
        manual_regions: list[str],
        effective_regions: list[str],
        exact_airport: bool,
        origin_is_country: bool,
        destination_is_country: bool,
        airport_limit: int,
    ) -> dict[str, object]:
        return {
            "identity": {
                "mode": "expanded_route",
                "origin_input": origin_value,
                "destination_input": destination_value,
                "origin_label": origin_label,
                "destination_label": destination_label,
                "origin_code": origin_file_token,
                "destination_code": destination_file_token,
                "date": date,
                "return_date": return_date,
                "date_window_days": int(date_window_days),
                "trip_type": "round_trip" if return_date else "one_way",
                "manual_regions": sorted(code.upper() for code in manual_regions),
                "effective_regions": list(effective_regions),
                "exact_airport": bool(exact_airport),
                "origin_is_country": bool(origin_is_country),
                "destination_is_country": bool(destination_is_country),
                "airport_limit": int(airport_limit),
            },
            "display": {
                "title": self._query_title(origin_label, destination_label, date, return_date),
            },
        }

    @staticmethod
    def rows_to_quote_snapshots(rows: list[SimplifiedQuoteRow]) -> list[QuoteRow]:
        snapshots: list[QuoteRow] = []
        for row in rows:
            snapshots.append(
                {
                    "region": row.get("region_code"),
                    "region_name": row.get("region_name"),
                    "price": row.get("cheapest_cny_price"),
                    "best_price": row.get("best_cny_price"),
                    "cheapest_price": row.get("cheapest_cny_price"),
                    "currency": "CNY",
                    "source_url": row.get("link"),
                    "status": row.get("status"),
                    "error": row.get("error"),
                    "source_kind": row.get("source_kind"),
                    "route": row.get("route"),
                    "plan_rank": row.get("plan_rank"),
                    "plan_score": row.get("plan_score"),
                    "plan_phase": row.get("plan_phase"),
                    "plan_reason": row.get("plan_reason"),
                    "route_rank": row.get("route_rank"),
                    "date_rank": row.get("date_rank"),
                    "market_rank": row.get("market_rank"),
                    "confidence": row.get("confidence"),
                    "price_source": row.get("price_source"),
                    "evidence_text": row.get("evidence_text"),
                    "parser_warnings": row.get("parser_warnings") or [],
                    "fallback_attempts": row.get("fallback_attempts") or [],
                    "price_candidates_count": row.get("price_candidates_count") or 0,
                    "selected_candidate_rank": row.get("selected_candidate_rank"),
                    "candidate_sources": row.get("candidate_sources") or [],
                    "readiness": row.get("readiness"),
                    "execution_policy_mode": row.get("execution_policy_mode") or "exact",
                }
            )
        return snapshots

    @staticmethod
    def _group_single_trip(
        trip_label: str,
        rows: list[dict[str, object]],
    ) -> list[tuple[str, list[dict[str, object]]]]:
        return [(trip_label, rows)]

    def _print_delta_summary(self, rows_by_date: list[tuple[str, list[SimplifiedQuoteRow]]]) -> None:
        lines = build_delta_summary_lines(rows_by_date)
        if not lines:
            print("\n本次与上次相比没有新的价格变化。")
            return
        print("\n变化摘要:")
        for line in lines:
            print(f"- {line}")

    def _print_fetch_quality_summary(self, quotes_by_date: list[tuple[str, list[QuoteRow]]]) -> None:
        telemetry = build_fetch_quality_telemetry(quotes_by_date)
        total = int(telemetry.get("fetch_total_regions") or 0)
        if total <= 0:
            return
        found = int(telemetry.get("fetch_price_found_count") or 0)
        opencli_direct = int(telemetry.get("opencli_direct_price_found_count") or 0)
        fallback_rescued = int(telemetry.get("fallback_rescued_count") or 0)
        challenge = int(telemetry.get("fetch_challenge_count") or 0)
        opened = int(telemetry.get("tab_open_total") or 0)
        reused = int(telemetry.get("tab_reuse_total") or 0)
        print(
            "[fetch] final "
            f"{found}/{total} markets found price, "
            f"opencli direct {opencli_direct}, "
            f"fallback rescued {fallback_rescued}, "
            f"challenge {challenge}, "
            f"tabs opened {opened}, reused {reused}"
        )
        parser_telemetry = build_parser_recovery_telemetry(quotes_by_date)
        snapshot_summary = build_snapshot_summary(quotes_by_date)
        candidate_total = int(parser_telemetry.get("price_candidate_total") or 0)
        if candidate_total:
            print(
                "[parse] "
                f"candidates {candidate_total}, "
                f"recovered {int(parser_telemetry.get('candidate_recovered_price_count') or 0)}, "
                f"low confidence {int(parser_telemetry.get('low_confidence_price_count') or 0)}, "
                f"snapshots recommended {int(snapshot_summary.get('snapshot_recommended_count') or 0)}"
            )

    def _sort_simplified_rows(
        self, rows: list[SimplifiedQuoteRow]
    ) -> list[SimplifiedQuoteRow]:
        rows.sort(
            key=lambda item: (
                item["cheapest_cny_price"] is None,
                item["cheapest_cny_price"]
                if isinstance(item["cheapest_cny_price"], (int, float))
                else float("inf"),
                item["best_cny_price"] is None,
                item["best_cny_price"]
                if isinstance(item["best_cny_price"], (int, float))
                else float("inf"),
                str(item.get("route") or ""),
                str(item["region_name"]),
            )
        )
        return rows

    def simplify_quotes(
        self, quotes: list[QuoteRow], *, route_label: str | None = None
    ) -> list[SimplifiedQuoteRow]:
        simplified: list[SimplifiedQuoteRow] = []
        for quote in quotes:
            currency = quote.get("currency")
            if currency is not None and not isinstance(currency, str):
                continue
            region_name = quote.get("region_name")
            source_url = quote.get("source_url")
            if not isinstance(region_name, str) or not isinstance(source_url, str):
                continue

            best_price = quote.get("best_price")
            cheapest_price = quote.get("cheapest_price")

            if best_price is not None and not isinstance(best_price, (int, float)):
                continue
            if cheapest_price is not None and not isinstance(
                cheapest_price, (int, float)
            ):
                continue

            best_numeric = float(best_price) if best_price is not None else None
            cheapest_numeric = (
                float(cheapest_price) if cheapest_price is not None else None
            )
            best_cny = self.to_cny(best_numeric, currency) if currency else None
            cheapest_cny = self.to_cny(cheapest_numeric, currency) if currency else None
            source_kind = str(quote.get("source_kind") or "").strip() or None
            failure_category = None
            failure_action = None
            if best_numeric is None and cheapest_numeric is None:
                failure_category, failure_action = classify_failure(
                    str(quote.get("status") or ""),
                    str(quote.get("error") or ""),
                )

            simplified.append(
                {
                    "region_code": str(quote.get("region") or "-"),
                    "region_name": region_name,
                    "best_display_price": (
                        f"{best_numeric:,.2f} {currency.upper()}"
                        if best_numeric is not None and currency
                        else None
                    ),
                    "best_cny_price": best_cny,
                    "cheapest_display_price": (
                        f"{cheapest_numeric:,.2f} {currency.upper()}"
                        if cheapest_numeric is not None and currency
                        else None
                    ),
                    "cheapest_cny_price": cheapest_cny,
                    "link": source_url,
                    "status": str(quote.get("status") or "-"),
                    "error": str(quote.get("error") or "-"),
                    "route": route_label or "-",
                    "source_kind": source_kind,
                    "source_label": source_kind_label(source_kind),
                    "delta_vs_last_scan": None,
                    "delta_label": "-",
                    "updated_at": None,
                    "failure_category": failure_category,
                    "failure_action": failure_action,
                    "can_reuse_page": can_reuse_page_for_row(
                        {"source_kind": source_kind}
                    ),
                    "plan_rank": quote.get("plan_rank"),
                    "plan_score": quote.get("plan_score"),
                    "plan_phase": quote.get("plan_phase"),
                    "plan_reason": quote.get("plan_reason"),
                    "route_rank": quote.get("route_rank"),
                    "date_rank": quote.get("date_rank"),
                    "market_rank": quote.get("market_rank"),
                    "confidence": quote.get("confidence"),
                    "price_source": quote.get("price_source"),
                    "evidence_text": quote.get("evidence_text"),
                    "parser_warnings": quote.get("parser_warnings") or [],
                    "fallback_attempts": quote.get("fallback_attempts") or [],
                    "price_candidates_count": quote.get("price_candidates_count") or 0,
                    "selected_candidate_rank": quote.get("selected_candidate_rank"),
                    "candidate_sources": quote.get("candidate_sources") or [],
                    "readiness": quote.get("readiness"),
                    "execution_policy_mode": quote.get("execution_policy_mode") or "exact",
                }
            )
        return self._sort_simplified_rows(simplified)

    @staticmethod
    def _format_plan_cell(row: dict[str, object]) -> str:
        plan_rank = row.get("plan_rank")
        plan_phase = row.get("plan_phase")
        plan_reason = str(row.get("plan_reason") or "").strip()
        parts: list[str] = []
        if isinstance(plan_rank, int):
            parts.append(f"#{plan_rank}")
        if plan_phase:
            parts.append(str(plan_phase))
        if plan_reason:
            parts.append(plan_reason)
        return " / ".join(parts) if parts else "-"

    @staticmethod
    def _with_route_plan_metadata(
        rows: list[SimplifiedQuoteRow],
        *,
        route_rank: int,
        route_reason: str,
    ) -> list[SimplifiedQuoteRow]:
        annotated: list[SimplifiedQuoteRow] = []
        for row in rows:
            next_row = dict(row)
            next_row["route_rank"] = route_rank
            existing_reason = str(next_row.get("plan_reason") or "").strip()
            next_row["plan_reason"] = (
                f"{route_reason}；{existing_reason}" if existing_reason else route_reason
            )
            annotated.append(next_row)
        return annotated

    def build_markdown_table(
        self,
        rows: list[SimplifiedQuoteRow],
        origin: str,
        destination: str,
        date: str,
        return_date: str | None = None,
    ) -> str:
        trip_mode = "往返" if return_date else "单程"
        lines = [
            f"# Skyscanner 比价结果",
            "",
            f"- 航线: `{origin} -> {destination}`",
            f"- 行程: `{trip_mode}`",
            f"- 日期: `{format_trip_date_label(date, return_date)}`",
            f"- 生成时间: `{datetime.now().isoformat(timespec='seconds')}`",
            "",
        ]
        if not rows:
            lines.append("暂无可用价格结果。")
            return "\n".join(lines) + "\n"

        lines.extend(_build_decision_summary(rows))
        lines.extend(["## 价格明细", ""])
        lines.extend(
            [
                "| 航段 | 地区 | 来源 | 计划 | 最佳（原币） | 最佳（人民币） | 最低价（原币） | 最低价（人民币） | 可信度 | 价格来源 | 警告 | 较上次变化 | 状态 | 错误 | 链接 |",
                "| --- | --- | --- | --- | ---: | ---: | ---: | ---: | --- | --- | --- | --- | --- | --- | --- |",
            ]
        )
        for row in rows:
            best_cny_text = (
                f"¥{row['best_cny_price']:,.2f}"
                if isinstance(row.get("best_cny_price"), (int, float))
                else "-"
            )
            cheapest_cny_text = (
                f"¥{row['cheapest_cny_price']:,.2f}"
                if isinstance(row.get("cheapest_cny_price"), (int, float))
                else "-"
            )
            lines.append(
                f"| {row.get('route') or '-'} | {row['region_name']} | {row.get('source_label') or '-'} | {self._format_plan_cell(row)} | {row.get('best_display_price') or '-'} | {best_cny_text} | {row.get('cheapest_display_price') or '-'} | {cheapest_cny_text} | {_confidence_label(row.get('confidence'))} | {_price_source_label(row.get('price_source'))} | {_warnings_summary(row.get('parser_warnings'))} | {row.get('delta_label') or '-'} | {row.get('status') or '-'} | {row.get('error') or '-'} | [打开结果页]({row['link']}) |"
            )
        lines.append("")
        lines.extend(_build_warning_detail_section(rows))
        return "\n".join(lines) + "\n"

    def build_combined_markdown_table(
        self,
        rows: list[CombinedQuoteRow],
        origin: str,
        destination: str,
    ) -> str:
        dates: list[str] = [
            date for row in rows if isinstance(date := row.get("date"), str)
        ]
        date_range = f"{min(dates)} ~ {max(dates)}" if dates and all(dates) else "-"
        lines = [
            "# Skyscanner 比价结果（多日期）",
            "",
            f"- 航线: `{origin} -> {destination}`",
            f"- 日期范围: `{date_range}`",
            f"- 生成时间: `{datetime.now().isoformat(timespec='seconds')}`",
            "",
        ]
        if not rows:
            lines.append("暂无可用价格结果。")
            return "\n".join(lines) + "\n"

        lines.extend(_build_decision_summary(rows, show_dates=True))
        lines.extend(["## 价格明细", ""])
        lines.extend(
            [
                "| 日期 | 航段 | 地区 | 来源 | 计划 | 最佳（原币） | 最佳（人民币） | 最低价（原币） | 最低价（人民币） | 可信度 | 价格来源 | 警告 | 较上次变化 | 状态 | 错误 | 链接 |",
                "| --- | --- | --- | --- | --- | ---: | ---: | ---: | ---: | --- | --- | --- | --- | --- | --- | --- |",
            ]
        )
        for row in rows:
            best_cny_text = (
                f"¥{row['best_cny_price']:,.2f}"
                if isinstance(row.get("best_cny_price"), (int, float))
                else "-"
            )
            cheapest_cny_text = (
                f"¥{row['cheapest_cny_price']:,.2f}"
                if isinstance(row.get("cheapest_cny_price"), (int, float))
                else "-"
            )
            link = row.get("link") or "-"
            link_cell = f"[打开结果页]({link})" if link != "-" else "-"
            lines.append(
                "| "
                + " | ".join(
                    [
                        str(row.get("date") or "-"),
                        str(row.get("route") or "-"),
                        str(row.get("region_name") or "-"),
                        str(row.get("source_label") or "-"),
                        self._format_plan_cell(row),
                        str(row.get("best_display_price") or "-"),
                        best_cny_text,
                        str(row.get("cheapest_display_price") or "-"),
                        cheapest_cny_text,
                        _confidence_label(row.get("confidence")),
                        _price_source_label(row.get("price_source")),
                        _warnings_summary(row.get("parser_warnings")),
                        str(row.get("delta_label") or "-"),
                        str(row.get("status") or "-"),
                        str(row.get("error") or "-"),
                        link_cell,
                    ]
                )
                + " |"
            )
        lines.append("")
        lines.extend(_build_warning_detail_section(rows, show_dates=True))
        return "\n".join(lines) + "\n"

    def build_window_markdown_table(
        self,
        rows_by_date: list[tuple[str, list[SimplifiedQuoteRow]]],
        origin: str,
        destination: str,
        start_date: str,
        end_date: str,
        start_return_date: str | None = None,
        end_return_date: str | None = None,
    ) -> str:
        lines = [
            "# Skyscanner 比价结果（日期窗口）",
            "",
            f"- 航线: `{origin} -> {destination}`",
            (
                f"- 日期窗口: `{start_date}` ~ `{end_date}`"
                if not start_return_date or not end_return_date
                else f"- 出发窗口: `{start_date}` ~ `{end_date}`"
            ),
            f"- 生成时间: `{datetime.now().isoformat(timespec='seconds')}`",
            "",
        ]
        if start_return_date and end_return_date:
            lines.insert(3, "- 行程: `往返`")
            lines.insert(5, f"- 返程窗口: `{start_return_date}` ~ `{end_return_date}`")
        total_rows = sum(len(rows) for _, rows in rows_by_date)
        if total_rows == 0:
            lines.append("暂无可用价格结果。")
            return "\n".join(lines) + "\n"

        flattened_rows: list[dict[str, object]] = []
        for date, rows in rows_by_date:
            for row in rows:
                merged = dict(row)
                merged.setdefault("date", date)
                flattened_rows.append(merged)
        lines.extend(_build_decision_summary(flattened_rows, show_dates=True))
        lines.extend(["## 价格明细", ""])
        lines.extend(
            [
                "| 日期 | 航段 | 地区 | 来源 | 最佳（原币） | 最佳（人民币） | 最低价（原币） | 最低价（人民币） | 可信度 | 价格来源 | 警告 | 较上次变化 | 状态 | 错误 | 链接 |",
                "| --- | --- | --- | --- | ---: | ---: | ---: | ---: | --- | --- | --- | --- | --- | --- | --- |",
            ]
        )
        for date, rows in rows_by_date:
            for row in rows:
                best_cny_text = (
                    f"¥{row['best_cny_price']:,.2f}"
                    if isinstance(row.get("best_cny_price"), (int, float))
                    else "-"
                )
                cheapest_cny_text = (
                    f"¥{row['cheapest_cny_price']:,.2f}"
                    if isinstance(row.get("cheapest_cny_price"), (int, float))
                    else "-"
                )
                lines.append(
                    f"| {date} | {row.get('route') or '-'} | {row['region_name']} | {row.get('source_label') or '-'} | {row.get('best_display_price') or '-'} | {best_cny_text} | {row.get('cheapest_display_price') or '-'} | {cheapest_cny_text} | {_confidence_label(row.get('confidence'))} | {_price_source_label(row.get('price_source'))} | {_warnings_summary(row.get('parser_warnings'))} | {row.get('delta_label') or '-'} | {row.get('status') or '-'} | {row.get('error') or '-'} | [打开结果页]({row['link']}) |"
                )
        lines.append("")
        lines.extend(_build_warning_detail_section(flattened_rows, show_dates=True))
        return "\n".join(lines) + "\n"

    def print_quotes(self, rows: list[SimplifiedQuoteRow]) -> None:
        if not rows:
            print("\n暂无可用价格结果。")
            return
        decision_lines = _build_decision_summary(rows)
        if decision_lines:
            print()
            for line in decision_lines:
                print(line)
        print(
            "\n| 航段 | 地区 | 来源 | 最佳（原币） | 最佳（人民币） | 最低价（原币） | 最低价（人民币） | 可信度 | 价格来源 | 警告 | 较上次变化 | 状态 | 错误 | 链接 |"
        )
        print("| --- | --- | --- | ---: | ---: | ---: | ---: | --- | --- | --- | --- | --- | --- | --- |")
        for row in rows:
            best_cny_text = (
                f"¥{row['best_cny_price']:,.2f}"
                if isinstance(row.get("best_cny_price"), (int, float))
                else "-"
            )
            cheapest_cny_text = (
                f"¥{row['cheapest_cny_price']:,.2f}"
                if isinstance(row.get("cheapest_cny_price"), (int, float))
                else "-"
            )
            print(
                f"| {row.get('route') or '-'} | {row['region_name']} | {row.get('source_label') or '-'} | {row.get('best_display_price') or '-'} | {best_cny_text} | {row.get('cheapest_display_price') or '-'} | {cheapest_cny_text} | {_confidence_label(row.get('confidence'))} | {_price_source_label(row.get('price_source'))} | {_warnings_summary(row.get('parser_warnings'))} | {row.get('delta_label') or '-'} | {row.get('status') or '-'} | {row.get('error') or '-'} | {row['link']} |"
            )

    def save_results(
        self,
        quotes: list[QuoteRow],
        origin: str,
        destination: str,
        date: str,
        return_date: str | None = None,
        route_label: str | None = None,
        file_origin_token: str | None = None,
        file_destination_token: str | None = None,
    ) -> Path:
        output_dir = get_reports_dir()
        filename = output_dir / (
            f"edge_page_{_safe_output_token(file_origin_token or origin)}_"
            f"{_safe_output_token(file_destination_token or destination)}_"
            f"{_trip_file_token(date, return_date)}.md"
        )
        rows = self.simplify_quotes(quotes, route_label=route_label)
        payload = self.build_markdown_table(
            rows, origin, destination, date, return_date=return_date
        )
        filename.write_text(payload, encoding="utf-8")
        return filename

    def save_simplified_results(
        self,
        rows: list[SimplifiedQuoteRow],
        origin: str,
        destination: str,
        date: str,
        return_date: str | None = None,
        file_origin_token: str | None = None,
        file_destination_token: str | None = None,
    ) -> Path:
        output_dir = get_reports_dir()
        filename = output_dir / (
            f"edge_page_{_safe_output_token(file_origin_token or origin)}_"
            f"{_safe_output_token(file_destination_token or destination)}_"
            f"{_trip_file_token(date, return_date)}.md"
        )
        payload = self.build_markdown_table(
            rows,
            origin,
            destination,
            date,
            return_date=return_date,
        )
        filename.write_text(payload, encoding="utf-8")
        return filename

    def save_combined_results(
        self,
        rows: list[CombinedQuoteRow],
        origin: str,
        destination: str,
        date: str,
        return_date: str | None = None,
        file_origin_token: str | None = None,
        file_destination_token: str | None = None,
    ) -> Path:
        output_dir = get_reports_dir()
        filename = (
            output_dir
            / (
                f"edge_page_{_safe_output_token(file_origin_token or origin)}_"
                f"{_safe_output_token(file_destination_token or destination)}_"
                f"{_trip_file_token(date, return_date)}_combined.md"
            )
        )
        payload = self.build_combined_markdown_table(rows, origin, destination)
        filename.write_text(payload, encoding="utf-8")
        return filename

    def save_window_results(
        self,
        rows_by_date: list[tuple[str, list[SimplifiedQuoteRow]]],
        origin: str,
        destination: str,
        start_date: str,
        end_date: str,
        start_return_date: str | None = None,
        end_return_date: str | None = None,
        file_origin_token: str | None = None,
        file_destination_token: str | None = None,
    ) -> Path:
        output_dir = get_reports_dir()
        start_stamp = _trip_file_token(start_date, start_return_date)
        end_stamp = _trip_file_token(end_date, end_return_date)
        filename = (
            output_dir
            / (
                f"edge_page_{_safe_output_token(file_origin_token or origin)}_"
                f"{_safe_output_token(file_destination_token or destination)}_"
                f"{start_stamp}_{end_stamp}_summary.md"
            )
        )
        payload = self.build_window_markdown_table(
            rows_by_date,
            origin,
            destination,
            start_date,
            end_date,
            start_return_date=start_return_date,
            end_return_date=end_return_date,
        )
        filename.write_text(payload, encoding="utf-8")
        return filename

    @staticmethod
    def _display_price_value(value: str | float | None) -> float:
        if not isinstance(value, str) or not value or value == "-":
            return float("inf")
        try:
            return float(value.replace(",", "").split()[0])
        except (IndexError, ValueError):
            return float("inf")

    def _row_selection_key(self, row: SimplifiedQuoteRow) -> tuple[float, float, float, float]:
        cheapest_cny = row.get("cheapest_cny_price")
        best_cny = row.get("best_cny_price")
        cheapest_native = self._display_price_value(row.get("cheapest_display_price"))
        best_native = self._display_price_value(row.get("best_display_price"))
        return (
            float(cheapest_cny) if isinstance(cheapest_cny, (int, float)) else float("inf"),
            float(best_cny) if isinstance(best_cny, (int, float)) else float("inf"),
            cheapest_native,
            best_native,
        )

    def _pick_better_row(
        self,
        current: SimplifiedQuoteRow | None,
        candidate: SimplifiedQuoteRow,
    ) -> SimplifiedQuoteRow:
        if current is None:
            return candidate
        if self._row_selection_key(candidate) < self._row_selection_key(current):
            return candidate
        return current

    async def _run_point_to_point_page_command(
        self,
        args: argparse.Namespace,
        *,
        manual_regions: list[str],
        config: ScanConfig,
    ) -> int:
        if not args.origin or not args.destination:
            print("参数错误: 点对点模式下必须同时提供 --origin 和 --destination。")
            return 2

        origin, destination, regions = self.build_effective_regions(
            args.origin,
            args.destination,
            prefer_origin_metro=not args.exact_airport,
            manual_region_codes=manual_regions,
        )
        date_window_days = max(int(getattr(args, "date_window", 0)), 0)
        try:
            trip_dates = build_ordered_trip_dates(
                args.date,
                args.return_date,
                date_window_days,
            )
        except ValueError as exc:
            print(f"日期参数错误: {exc}")
            return 2
        query_payload = self.build_point_query_payload(
            origin_input=args.origin,
            destination_input=args.destination,
            origin_label=origin.query or origin.name or origin.code,
            destination_label=destination.query or destination.name or destination.code,
            origin_code=origin.code,
            destination_code=destination.code,
            date=args.date,
            return_date=args.return_date,
            date_window_days=date_window_days,
            manual_regions=manual_regions,
            effective_regions=regions,
            exact_airport=bool(args.exact_airport),
        )
        latest_record = self.history_store.get_latest_scan(query_payload)
        rerun_failed = bool(getattr(args, "rerun_failed", False))
        preview_only = bool(getattr(args, "preview_only", False))
        show_delta = bool(getattr(args, "show_delta", False))
        show_plan = bool(getattr(args, "show_plan", False))
        if rerun_failed and latest_record is None:
            print("未找到历史记录，`--rerun-failed` 本次退化为全量扫描。")

        print(f"本次实际地区: {', '.join(regions)}")

        if show_plan:
            plan = build_search_plan(
                TripIntent(
                    origin_input=args.origin,
                    destination_input=args.destination,
                    depart_date=args.date,
                    return_date=args.return_date,
                    origin_is_country=False,
                    destination_is_country=False,
                    date_window=date_window_days,
                    user_regions=manual_regions,
                ),
                [
                    LocationRecord(
                        name=origin.name,
                        code=origin.code,
                        kind=origin.kind,
                        municipality=origin.municipality,
                        country=origin.country,
                    )
                ],
                [
                    LocationRecord(
                        name=destination.name,
                        code=destination.code,
                        kind=destination.kind,
                        municipality=destination.municipality,
                        country=destination.country,
                    )
                ],
                regions,
                latest_record.rows_by_date if latest_record is not None else None,
                origin_country=origin.country,
                destination_country=destination.country,
            )
            print()
            print(render_search_plan(plan), end="")
            return 0

        if preview_only:
            preview_record = self.history_store.get_cached_preview(query_payload)
            if preview_record is None:
                print("最近 6 小时内没有可用预览缓存。")
                return 1
            cached_rows_by_date = override_rows_source_kind(
                preview_record.rows_by_date,
                "cached",
                updated_at=preview_record.created_at,
            )
            rows_by_date: list[tuple[str, list[SimplifiedQuoteRow]]] = []
            any_rows = False
            any_winner = False
            for current_date, current_return_date in trip_dates:
                trip_label = format_trip_date_label(current_date, current_return_date)
                rows = self._sort_simplified_rows(
                    get_rows_for_trip_label(cached_rows_by_date, trip_label)
                )
                rows_by_date.append((trip_label, rows))
                print(f"\n日期: {trip_label}（预览缓存）")
                self.print_quotes(rows)
                if rows:
                    any_rows = True
                if any(
                    isinstance(row.get("best_cny_price"), (int, float))
                    or isinstance(row.get("cheapest_cny_price"), (int, float))
                    for row in rows
                ):
                    any_winner = True
            if show_delta:
                self._print_delta_summary(rows_by_date)
            return 0 if any_winner else (1 if not any_rows else 2)

        async def scan_trip(
            trip_index: int,
            current_date: str,
            current_return_date: str | None,
        ) -> tuple[int, str, str, str | None, list[SimplifiedQuoteRow], list[QuoteRow]]:
            trip_label = format_trip_date_label(current_date, current_return_date)
            route_label = f"{origin.code} -> {destination.code}"
            selected_regions = list(regions)
            current_rerun_scope = "all"
            if rerun_failed and latest_record is not None:
                failed_region_codes = get_failed_region_codes(
                    latest_record.quotes_by_date,
                    trip_label=trip_label,
                )
                if failed_region_codes:
                    failed_region_set = {code.upper() for code in failed_region_codes}
                    selected_regions = [
                        code for code in regions if code.upper() in failed_region_set
                    ]
                    current_rerun_scope = "selected_regions"
                else:
                    selected_regions = []

            if rerun_failed and latest_record is not None and not selected_regions:
                rows = self._sort_simplified_rows(
                    get_rows_for_trip_label(
                        override_rows_source_kind(
                            latest_record.rows_by_date,
                            "cached",
                            updated_at=latest_record.created_at,
                        ),
                        trip_label,
                    )
                )
                quote_snapshots = get_quotes_for_trip_label(
                    override_quotes_source_kind(
                        latest_record.quotes_by_date,
                        "cached",
                    ),
                    trip_label,
                )
                return (
                    trip_index,
                    trip_label,
                    current_date,
                    current_return_date,
                    rows,
                    quote_snapshots,
                )

            printed_stages: set[str] = set()

            async def on_progress(progress_payload: dict[str, object]) -> None:
                stage = str(progress_payload.get("stage") or "").strip().lower()
                if not stage:
                    return
                if stage in {"plan_batch_start", "plan_batch_complete"}:
                    batch_id = progress_payload.get("plan_batch_id")
                    batch_count = progress_payload.get("plan_batch_count")
                    phase = str(progress_payload.get("active_plan_phase") or "-")
                    reason = str(progress_payload.get("plan_batch_reason") or "").strip()
                    key = f"{stage}:{batch_id}:{phase}"
                    if key in printed_stages:
                        return
                    printed_stages.add(key)
                    prefix = "[plan] batch" if stage == "plan_batch_start" else "[plan] completed batch"
                    suffix = f": {reason}" if reason else ""
                    completed = progress_payload.get("completed_regions") or []
                    completed_text = ""
                    if stage == "plan_batch_complete" and isinstance(completed, list):
                        completed_text = f", completed regions: {', '.join(str(code) for code in completed)}"
                    print(f"{prefix} {batch_id}/{batch_count} {phase}{suffix}{completed_text}")
                    return
                if stage in printed_stages:
                    return
                printed_stages.add(stage)
                stage_text = {
                    "preview_cache": f"{trip_label}: 预览缓存已展示",
                    "quick_live": f"{trip_label}: 快速实扫结果已返回",
                    "background_live": f"{trip_label}: 后台补全结果已刷新",
                }.get(stage)
                if stage_text:
                    print(stage_text)

            quotes = await run_page_scan(
                origin=origin.code,
                destination=destination.code,
                date=current_date,
                region_codes=regions,
                return_date=current_return_date,
                page_wait=args.wait,
                timeout=args.timeout,
                transport=args.transport,
                scan_mode="preview_first",
                rerun_scope=current_rerun_scope,
                selected_region_codes=selected_regions,
                region_concurrency=CLI_REGION_CONCURRENCY,
                query_payload=query_payload,
                on_progress=on_progress,
                fetch_pipeline=getattr(args, "fetch_pipeline", "balanced"),
                config=config,
            )
            if not quotes:
                return (
                    trip_index,
                    trip_label,
                    current_date,
                    current_return_date,
                    [],
                    [],
                )

            live_quote_dicts = quotes_to_dicts(quotes)
            live_rows_by_date = annotate_rows_with_history(
                self._group_single_trip(
                    trip_label,
                    self.simplify_quotes(live_quote_dicts, route_label=route_label),
                ),
                latest_record.rows_by_date if latest_record else None,
            )
            live_quotes_by_date = [(trip_label, live_quote_dicts)]

            if rerun_failed and latest_record is not None:
                cached_rows_by_date = override_rows_source_kind(
                    self._group_single_trip(
                        trip_label,
                        get_rows_for_trip_label(latest_record.rows_by_date, trip_label),
                    ),
                    "cached",
                    updated_at=latest_record.created_at,
                )
                cached_quotes_by_date = override_quotes_source_kind(
                    self._group_single_trip(
                        trip_label,
                        get_quotes_for_trip_label(latest_record.quotes_by_date, trip_label),
                    ),
                    "cached",
                )
                merged_rows_by_date = merge_rows_by_date(
                    cached_rows_by_date,
                    live_rows_by_date,
                )
                merged_quotes_by_date = merge_quotes_by_date(
                    cached_quotes_by_date,
                    live_quotes_by_date,
                )
                rows = self._sort_simplified_rows(
                    get_rows_for_trip_label(merged_rows_by_date, trip_label)
                )
                quote_snapshots = get_quotes_for_trip_label(merged_quotes_by_date, trip_label)
            else:
                rows = self._sort_simplified_rows(
                    get_rows_for_trip_label(live_rows_by_date, trip_label)
                )
                quote_snapshots = live_quote_dicts

            return (
                trip_index,
                trip_label,
                current_date,
                current_return_date,
                rows,
                quote_snapshots,
            )

        date_semaphore = asyncio.Semaphore(CLI_DATE_WINDOW_CONCURRENCY)

        async def run_trip_with_limit(
            trip_index: int,
            current_date: str,
            current_return_date: str | None,
        ) -> tuple[int, str, str, str | None, list[SimplifiedQuoteRow], list[QuoteRow]]:
            async with date_semaphore:
                return await scan_trip(trip_index, current_date, current_return_date)

        trip_results = await asyncio.gather(
            *(
                run_trip_with_limit(index, current_date, current_return_date)
                for index, (current_date, current_return_date) in enumerate(trip_dates)
            )
        )
        trip_results.sort(key=lambda item: item[0])

        rows_by_date: list[tuple[str, list[SimplifiedQuoteRow]]] = []
        quote_snapshots_by_date: list[tuple[str, list[QuoteRow]]] = []
        any_rows = False
        any_winner = False

        for _, trip_label, current_date, current_return_date, rows, quote_snapshots in trip_results:
            print(f"\n日期: {trip_label}")
            if rerun_failed and latest_record is not None and not rows:
                print("没有返回任何结果。检查地区代码或浏览器/CDP 环境。")
            rows_by_date.append((trip_label, rows))
            quote_snapshots_by_date.append((trip_label, quote_snapshots))
            self._print_fetch_quality_summary([(trip_label, quote_snapshots)])
            if rows:
                any_rows = True

            self.print_quotes(rows)

            best_winner = next(
                (
                    row
                    for row in rows
                    if isinstance(row.get("best_cny_price"), (int, float))
                ),
                None,
            )
            cheapest_winner = next(
                (
                    row
                    for row in rows
                    if isinstance(row.get("cheapest_cny_price"), (int, float))
                ),
                None,
            )
            if best_winner:
                any_winner = True
                print(
                    f"最佳: ¥{best_winner['best_cny_price']:,.2f} 来自 {best_winner['region_name']}"
                )
            if cheapest_winner:
                any_winner = True
                print(
                    f"最低价: ¥{cheapest_winner['cheapest_cny_price']:,.2f} 来自 {cheapest_winner['region_name']}"
                )
            elif rows:
                print("已提取市场价格，但人民币换算暂不可用。")
            else:
                print("未能成功提取任何市场价格。")

            if args.save:
                saved = self.save_simplified_results(
                    rows,
                    origin.code,
                    destination.code,
                    current_date,
                    return_date=current_return_date,
                )
                print(f"结果已保存到: {saved}")

        if rows_by_date:
            self.history_store.record_scan(
                query_payload,
                rows_by_date,
                quote_snapshots_by_date,
                scan_mode="failed_only" if rerun_failed else "preview_first",
            )

        if args.save and rows_by_date:
            start_date, start_return_date = trip_dates[0]
            end_date, end_return_date = trip_dates[-1]
            summary_path = self.save_window_results(
                rows_by_date,
                origin.code,
                destination.code,
                start_date,
                end_date,
                start_return_date=start_return_date,
                end_return_date=end_return_date,
            )
            print(f"窗口汇总已保存到: {summary_path}")

        if not args.exact_airport and args.origin in {"北京", "beijing", "BEIJING"}:
            print(
                "提示: 本次默认使用 BJSA（北京任意机场）。如需严格 PEK，请加 --exact-airport 或直接传 PEK。"
            )
        if show_delta:
            self._print_delta_summary(rows_by_date)
        if not any_rows:
            return 1
        return 0 if any_winner else 2

    async def _run_expanded_route_page_command(
        self,
        args: argparse.Namespace,
        *,
        manual_regions: list[str],
        config: ScanConfig,
    ) -> int:
        airport_limit = max(
            int(
                getattr(
                    args,
                    "country_airport_limit",
                    COUNTRY_ROUTE_DEFAULT_AIRPORT_LIMIT,
                )
            ),
            1,
        )
        try:
            (
                origin_label,
                destination_label,
                origin_file_token,
                destination_file_token,
                origin_points,
                destination_points,
                regions,
            ) = self.build_expanded_route_plan(
                origin_value=getattr(args, "origin_country", None) or getattr(args, "origin", None),
                destination_value=getattr(args, "destination_country", None) or getattr(args, "destination", None),
                origin_is_country=bool(getattr(args, "origin_country", None)),
                destination_is_country=bool(getattr(args, "destination_country", None)),
                prefer_origin_metro=not getattr(args, "exact_airport", False),
                manual_region_codes=manual_regions,
                airport_limit=airport_limit,
            )
        except ValueError as exc:
            print(f"扩展模式参数错误: {exc}")
            return 2

        mode_label = []
        mode_label.append("国家" if getattr(args, "origin_country", None) else "地点")
        mode_label.append("国家" if getattr(args, "destination_country", None) else "地点")
        print(f"扩展模式: {'-'.join(mode_label)} {origin_label} -> {destination_label}")
        print(
            "出发候选机场: "
            + ", ".join(
                f"{airport.code}({airport.municipality or airport.name})"
                for airport in origin_points
            )
        )
        print(
            "目的候选机场: "
            + ", ".join(
                f"{airport.code}({airport.municipality or airport.name})"
                for airport in destination_points
            )
        )
        print(f"本次实际地区: {', '.join(regions)}")

        date_window_days = max(int(getattr(args, "date_window", 0)), 0)
        try:
            trip_dates = build_ordered_trip_dates(
                args.date,
                args.return_date,
                date_window_days,
            )
        except ValueError as exc:
            print(f"日期参数错误: {exc}")
            return 2
        query_payload = self.build_expanded_query_payload(
            origin_value=getattr(args, "origin_country", None) or getattr(args, "origin", None) or "",
            destination_value=getattr(args, "destination_country", None)
            or getattr(args, "destination", None)
            or "",
            origin_label=origin_label,
            destination_label=destination_label,
            origin_file_token=origin_file_token,
            destination_file_token=destination_file_token,
            date=args.date,
            return_date=args.return_date,
            date_window_days=date_window_days,
            manual_regions=manual_regions,
            effective_regions=regions,
            exact_airport=bool(getattr(args, "exact_airport", False)),
            origin_is_country=bool(getattr(args, "origin_country", None)),
            destination_is_country=bool(getattr(args, "destination_country", None)),
            airport_limit=airport_limit,
        )
        latest_record = self.history_store.get_latest_scan(query_payload)
        rerun_failed = bool(getattr(args, "rerun_failed", False))
        preview_only = bool(getattr(args, "preview_only", False))
        show_delta = bool(getattr(args, "show_delta", False))
        show_plan = bool(getattr(args, "show_plan", False))
        if rerun_failed and latest_record is None:
            print("未找到历史记录，`--rerun-failed` 本次退化为全量扫描。")

        if show_plan:
            plan = build_search_plan(
                TripIntent(
                    origin_input=getattr(args, "origin_country", None)
                    or getattr(args, "origin", None)
                    or "",
                    destination_input=getattr(args, "destination_country", None)
                    or getattr(args, "destination", None)
                    or "",
                    depart_date=args.date,
                    return_date=args.return_date,
                    origin_is_country=bool(getattr(args, "origin_country", None)),
                    destination_is_country=bool(getattr(args, "destination_country", None)),
                    date_window=date_window_days,
                    user_regions=manual_regions,
                ),
                origin_points,
                destination_points,
                regions,
                latest_record.rows_by_date if latest_record is not None else None,
                origin_country=origin_file_token.removesuffix("_ANY")
                if origin_file_token.endswith("_ANY")
                else "",
                destination_country=destination_file_token.removesuffix("_ANY")
                if destination_file_token.endswith("_ANY")
                else "",
            )
            print()
            print(render_search_plan(plan), end="")
            return 0

        if preview_only:
            preview_record = self.history_store.get_cached_preview(query_payload)
            if preview_record is None:
                print("最近 6 小时内没有可用预览缓存。")
                return 1
            cached_rows_by_date = override_rows_source_kind(
                preview_record.rows_by_date,
                "cached",
                updated_at=preview_record.created_at,
            )
            rows_by_date: list[tuple[str, list[SimplifiedQuoteRow]]] = []
            any_rows = False
            any_winner = False
            for current_date, current_return_date in trip_dates:
                trip_label = format_trip_date_label(current_date, current_return_date)
                rows = self._sort_simplified_rows(
                    get_rows_for_trip_label(cached_rows_by_date, trip_label)
                )
                rows_by_date.append((trip_label, rows))
                print(f"\n日期: {trip_label}（预览缓存）")
                self.print_quotes(rows)
                if rows:
                    any_rows = True
                if any(
                    isinstance(row.get("best_cny_price"), (int, float))
                    or isinstance(row.get("cheapest_cny_price"), (int, float))
                    for row in rows
                ):
                    any_winner = True
            if show_delta:
                self._print_delta_summary(rows_by_date)
            return 0 if any_winner else (1 if not any_rows else 2)

        pair_routes = rank_route_pairs(
            origin_points,
            destination_points,
            latest_record.rows_by_date if latest_record is not None else None,
        )
        pair_count = len(pair_routes)

        async def scan_trip(
            trip_index: int,
            current_date: str,
            current_return_date: str | None,
        ) -> tuple[int, str, str, str | None, list[SimplifiedQuoteRow], list[QuoteRow]]:
            trip_label = format_trip_date_label(current_date, current_return_date)
            selected_regions = list(regions)
            rerun_scope = "all"

            if rerun_failed and latest_record is not None:
                failed_region_codes = get_failed_region_codes(
                    latest_record.quotes_by_date,
                    trip_label=trip_label,
                )
                if failed_region_codes:
                    failed_region_set = {code.upper() for code in failed_region_codes}
                    selected_regions = [
                        code for code in regions if code.upper() in failed_region_set
                    ]
                    rerun_scope = "selected_regions"
                else:
                    selected_regions = []

            if rerun_failed and latest_record is not None and not selected_regions:
                rows = self._sort_simplified_rows(
                    get_rows_for_trip_label(
                        override_rows_source_kind(
                            latest_record.rows_by_date,
                            "cached",
                            updated_at=latest_record.created_at,
                        ),
                        trip_label,
                    )
                )
                quote_snapshots = get_quotes_for_trip_label(
                    override_quotes_source_kind(
                        latest_record.quotes_by_date,
                        "cached",
                    ),
                    trip_label,
                )
                return (
                    trip_index,
                    trip_label,
                    current_date,
                    current_return_date,
                    rows,
                    quote_snapshots,
                )

            pair_semaphore = asyncio.Semaphore(CLI_AIRPORT_PAIR_CONCURRENCY)

            async def scan_pair(
                pair_index: int,
                origin_airport: AirportCandidate,
                destination_airport: AirportCandidate,
            ) -> tuple[int, list[SimplifiedQuoteRow]]:
                route_label = f"{origin_airport.code} -> {destination_airport.code}"
                printed_stages: set[str] = set()

                async def on_progress(progress_payload: dict[str, object]) -> None:
                    stage = str(progress_payload.get("stage") or "").strip().lower()
                    if not stage:
                        return
                    if stage in {"plan_batch_start", "plan_batch_complete"}:
                        batch_id = progress_payload.get("plan_batch_id")
                        batch_count = progress_payload.get("plan_batch_count")
                        phase = str(progress_payload.get("active_plan_phase") or "-")
                        reason = str(progress_payload.get("plan_batch_reason") or "").strip()
                        key = f"{stage}:{batch_id}:{phase}"
                        if key in printed_stages:
                            return
                        printed_stages.add(key)
                        prefix = "[plan] batch" if stage == "plan_batch_start" else "[plan] completed batch"
                        suffix = f": {reason}" if reason else ""
                        completed = progress_payload.get("completed_regions") or []
                        completed_text = ""
                        if stage == "plan_batch_complete" and isinstance(completed, list):
                            completed_text = f", completed regions: {', '.join(str(code) for code in completed)}"
                        print(f"{prefix} {batch_id}/{batch_count} {phase}{suffix}{completed_text}")
                        return
                    if stage in printed_stages:
                        return
                    printed_stages.add(stage)
                    stage_text = {
                        "preview_cache": f"{trip_label} / {route_label}: 预览缓存已展示",
                        "quick_live": f"{trip_label} / {route_label}: 快速实扫结果已返回",
                        "background_live": f"{trip_label} / {route_label}: 后台补全结果已刷新",
                    }.get(stage)
                    if stage_text:
                        print(stage_text)

                async with pair_semaphore:
                    quotes = await run_page_scan(
                        origin=origin_airport.code,
                        destination=destination_airport.code,
                        date=current_date,
                        region_codes=regions,
                        return_date=current_return_date,
                        page_wait=args.wait,
                        timeout=args.timeout,
                        transport=args.transport,
                        scan_mode="preview_first",
                        rerun_scope=rerun_scope,
                        selected_region_codes=selected_regions,
                        region_concurrency=CLI_REGION_CONCURRENCY,
                        query_payload=query_payload,
                        on_progress=on_progress,
                        fetch_pipeline=getattr(args, "fetch_pipeline", "balanced"),
                        config=config,
                    )
                if not quotes:
                    return pair_index, []
                route_reason = f"路线候选排序 {pair_index + 1}"
                return (
                    pair_index,
                    self._with_route_plan_metadata(
                        self.simplify_quotes(
                            quotes_to_dicts(quotes),
                            route_label=route_label,
                        ),
                        route_rank=pair_index + 1,
                        route_reason=route_reason,
                    ),
                )

            pair_results = await asyncio.gather(
                *(
                    scan_pair(pair_index, origin_airport, destination_airport)
                    for pair_index, (origin_airport, destination_airport) in enumerate(
                        pair_routes
                    )
                )
            )
            pair_results.sort(key=lambda item: item[0])

            best_rows_by_region: dict[str, SimplifiedQuoteRow] = {}
            for _, pair_rows in pair_results:
                for row in pair_rows:
                    region_name = str(row.get("region_name") or "-")
                    best_rows_by_region[region_name] = self._pick_better_row(
                        best_rows_by_region.get(region_name),
                        row,
                    )

            live_rows_by_date = annotate_rows_with_history(
                self._group_single_trip(
                    trip_label,
                    self._sort_simplified_rows(list(best_rows_by_region.values())),
                ),
                latest_record.rows_by_date if latest_record else None,
            )
            if rerun_failed and latest_record is not None:
                cached_rows_by_date = override_rows_source_kind(
                    self._group_single_trip(
                        trip_label,
                        get_rows_for_trip_label(latest_record.rows_by_date, trip_label),
                    ),
                    "cached",
                    updated_at=latest_record.created_at,
                )
                merged_rows_by_date = merge_rows_by_date(
                    cached_rows_by_date,
                    live_rows_by_date,
                )
                rows = self._sort_simplified_rows(
                    get_rows_for_trip_label(merged_rows_by_date, trip_label)
                )
            else:
                rows = self._sort_simplified_rows(
                    get_rows_for_trip_label(live_rows_by_date, trip_label)
                )
            return (
                trip_index,
                trip_label,
                current_date,
                current_return_date,
                rows,
                self.rows_to_quote_snapshots(rows),
            )

        date_semaphore = asyncio.Semaphore(CLI_DATE_WINDOW_CONCURRENCY)

        async def run_trip_with_limit(
            trip_index: int,
            current_date: str,
            current_return_date: str | None,
        ) -> tuple[int, str, str, str | None, list[SimplifiedQuoteRow], list[QuoteRow]]:
            async with date_semaphore:
                return await scan_trip(trip_index, current_date, current_return_date)

        trip_results = await asyncio.gather(
            *(
                run_trip_with_limit(index, current_date, current_return_date)
                for index, (current_date, current_return_date) in enumerate(trip_dates)
            )
        )
        trip_results.sort(key=lambda item: item[0])

        rows_by_date: list[tuple[str, list[SimplifiedQuoteRow]]] = []
        quote_snapshots_by_date: list[tuple[str, list[QuoteRow]]] = []
        any_rows = False
        any_winner = False

        for _, trip_label, current_date, current_return_date, rows, quote_snapshots in trip_results:
            print(f"\n日期: {trip_label}，共 {pair_count} 个候选航段")
            if rerun_failed and latest_record is not None:
                failed_region_codes = get_failed_region_codes(
                    latest_record.quotes_by_date,
                    trip_label=trip_label,
                )
                if failed_region_codes:
                    print(f"仅重跑上次失败市场: {', '.join(failed_region_codes)}")
                else:
                    print("上次该日期没有失败市场，直接复用已有结果。")

            rows_by_date.append((trip_label, rows))
            quote_snapshots_by_date.append((trip_label, quote_snapshots))
            self._print_fetch_quality_summary([(trip_label, quote_snapshots)])
            if rows:
                any_rows = True

            self.print_quotes(rows)

            best_winner = next(
                (
                    row
                    for row in rows
                    if isinstance(row.get("best_cny_price"), (int, float))
                ),
                None,
            )
            cheapest_winner = next(
                (
                    row
                    for row in rows
                    if isinstance(row.get("cheapest_cny_price"), (int, float))
                ),
                None,
            )
            if best_winner:
                any_winner = True
                print(
                    "最佳: ¥{price:,.2f} 来自 {region}，航段 {route}".format(
                        price=float(best_winner["best_cny_price"]),
                        region=best_winner["region_name"],
                        route=best_winner.get("route") or "-",
                    )
                )
            if cheapest_winner:
                any_winner = True
                print(
                    "最低价: ¥{price:,.2f} 来自 {region}，航段 {route}".format(
                        price=float(cheapest_winner["cheapest_cny_price"]),
                        region=cheapest_winner["region_name"],
                        route=cheapest_winner.get("route") or "-",
                    )
                )
            elif rows:
                print("已提取市场价格，但人民币换算暂不可用。")
            else:
                print("未能从候选机场组合里提取出有效价格。")

            if args.save:
                saved = self.save_simplified_results(
                    rows,
                    origin_label,
                    destination_label,
                    current_date,
                    return_date=current_return_date,
                    file_origin_token=origin_file_token,
                    file_destination_token=destination_file_token,
                )
                print(f"结果已保存到: {saved}")

        if rows_by_date:
            self.history_store.record_scan(
                query_payload,
                rows_by_date,
                quote_snapshots_by_date,
                scan_mode="failed_only" if rerun_failed else "preview_first",
            )

        if args.save and rows_by_date:
            start_date, start_return_date = trip_dates[0]
            end_date, end_return_date = trip_dates[-1]
            summary_path = self.save_window_results(
                rows_by_date,
                origin_label,
                destination_label,
                start_date,
                end_date,
                start_return_date=start_return_date,
                end_return_date=end_return_date,
                file_origin_token=origin_file_token,
                file_destination_token=destination_file_token,
            )
            print(f"窗口汇总已保存到: {summary_path}")

        if show_delta:
            self._print_delta_summary(rows_by_date)
        if not any_rows:
            return 1
        return 0 if any_winner else 2

    async def run_page_command(self, args: argparse.Namespace) -> int:
        manual_regions = [
            code.strip().upper() for code in args.regions.split(",") if code.strip()
        ]
        config = self._build_scan_config(args)
        if getattr(args, "origin_country", None) or getattr(
            args, "destination_country", None
        ):
            return await self._run_expanded_route_page_command(
                args,
                manual_regions=manual_regions,
                config=config,
            )
        return await self._run_point_to_point_page_command(
            args,
            manual_regions=manual_regions,
            config=config,
        )

    @staticmethod
    def _build_scan_config(args: argparse.Namespace) -> ScanConfig:
        manual_tabs: dict[str, str] = {}
        manual_tabs_json = getattr(args, "manual_tabs_json", None)
        if manual_tabs_json:
            try:
                import json
                manual_tabs = json.loads(Path(manual_tabs_json).read_text(encoding="utf-8"))
            except Exception:
                pass

        # ScanConfig.transport is the strict-mode override.  Default is AUTO,
        # which preserves the legacy --transport flag's "primary + fallback"
        # semantic.  Setting --transport-mode opencli/cdp/scrapling forces a
        # single transport with fallback disabled.
        transport_mode_raw = getattr(args, "transport_mode", None) or "auto"
        try:
            transport_mode = TransportMode(transport_mode_raw)
        except ValueError:
            transport_mode = TransportMode.AUTO

        return ScanConfig(
            transport=transport_mode,
            cdp_mode=CdpMode(getattr(args, "cdp_mode", "attach")),
            cdp_host=getattr(args, "cdp_host", "http://localhost:9222"),
            keep_tabs=bool(getattr(args, "keep_tabs", False)),
            manual_tabs=manual_tabs,
            low_confidence_policy=LowConfidencePolicy(
                getattr(args, "low_confidence_policy", "fallback")
            ),
            rankable_confidence=float(getattr(args, "rankable_confidence", 0.80)),
            review_confidence=float(getattr(args, "review_confidence", 0.50)),
            challenge_policy=ChallengePolicy(
                getattr(args, "challenge_policy", "stop")
            ),
            trace_dir=getattr(args, "trace_dir", "traces") or None,
            no_trace=bool(getattr(args, "no_trace", False)),
            failure_log_dir=getattr(args, "failure_log_dir", "failures") or None,
            debug_page_text=bool(getattr(args, "debug_page_text", False)),
            output=getattr(args, "output", "table"),
            output_file=getattr(args, "output_file", None),
            show_attempts=bool(getattr(args, "show_attempts", False)),
            show_low_confidence=bool(getattr(args, "show_low_confidence", False)),
        )

    def interactive_page(self) -> int:
        self.print_banner()
        origin = input("出发地（如 北京 / PEK）: ").strip()
        destination = input("目的地（如 阿拉木图 / ALA）: ").strip()
        date = input("日期（YYYY-MM-DD）: ").strip()
        date_window_raw = input("日期窗口 ±天数（默认 3）: ").strip()
        regions = input(
            f"额外地区代码（默认会自动包含 {','.join(DEFAULT_REGIONS)}）: "
        ).strip()
        args = argparse.Namespace(
            origin=origin,
            destination=destination,
            origin_country=None,
            destination_country=None,
            date=date,
            return_date=None,
            regions=regions,
            wait=10,
            timeout=30,
            save=True,
            date_window=int(date_window_raw) if date_window_raw else 3,
            exact_airport=False,
            country_airport_limit=COUNTRY_ROUTE_DEFAULT_AIRPORT_LIMIT,
            transport="opencli",
            fetch_pipeline="balanced",
            preview_only=False,
            rerun_failed=False,
            show_delta=False,
        )
        return asyncio.run(self.run_page_command(args))


async def run_auto_refresh_once_command(args: argparse.Namespace) -> int:
    lock_file = _auto_refresh_lock_path().open("a+", encoding="utf-8")
    try:
        fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        print("后台自动复扫已在运行，本次跳过。")
        lock_file.close()
        return 0
    try:
        if bool(getattr(args, "only_on_ac_power", False)) and not _is_ac_power_connected():
            print("当前未接入电源，后台自动复扫跳过。")
            return 0
        store = ScanHistoryStore()
        due_configs = store.get_due_auto_refresh_configs(
            limit=max(int(getattr(args, "limit", 1)), 1),
            auto_refresh_mode="background",
        )
        if not due_configs:
            print("没有到期的后台自动复扫任务。")
            return 0
        if bool(getattr(args, "dry_run", False)):
            for config in due_configs:
                print(f"DRY RUN: {config.title}")
            return 0
        failures = 0
        cli = SimpleCLI()
        for config in due_configs:
            print(f"后台自动复扫触发: {config.title}")
            store.mark_alert_auto_refreshed(config.query_payload)
            scan_args = _build_args_from_saved_query(config.query_payload, args)
            result = await cli.run_page_command(scan_args)
            if result != 0:
                failures += 1
                print(f"后台自动复扫失败: {config.title} (exit={result})")
        return 1 if failures else 0
    finally:
        try:
            fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)
        finally:
            lock_file.close()


def _launch_agent_path() -> Path:
    return Path.home() / "Library" / "LaunchAgents" / f"{LAUNCHD_LABEL}.plist"


def install_auto_refresh_launchd(args: argparse.Namespace) -> int:
    interval_seconds = max(int(getattr(args, "interval_minutes", DEFAULT_LAUNCHD_INTERVAL_MINUTES)), 1) * 60
    plist_path = _launch_agent_path()
    plist_path.parent.mkdir(parents=True, exist_ok=True)
    stdout_log = get_log_file("background_auto_refresh.out.log")
    stderr_log = get_log_file("background_auto_refresh.err.log")
    program_args = [
        sys.executable,
        str(PROJECT_ROOT / "cli.py"),
        "auto-refresh-once",
        "--limit",
        str(max(int(getattr(args, "limit", 1)), 1)),
    ]
    if not bool(getattr(args, "save", True)):
        program_args.append("--no-save")
    if bool(getattr(args, "only_on_ac_power", False)):
        program_args.append("--only-on-ac-power")
    plist = {
        "Label": LAUNCHD_LABEL,
        "ProgramArguments": program_args,
        "WorkingDirectory": str(PROJECT_ROOT),
        "StartInterval": interval_seconds,
        "RunAtLoad": bool(getattr(args, "run_at_load", True)),
        "StandardOutPath": str(stdout_log),
        "StandardErrorPath": str(stderr_log),
        "EnvironmentVariables": {
            "PATH": "/usr/local/bin:/opt/homebrew/bin:/usr/bin:/bin:/usr/sbin:/sbin",
        },
    }
    with plist_path.open("wb") as handle:
        plistlib.dump(plist, handle, sort_keys=False)
    subprocess.run(["launchctl", "unload", str(plist_path)], check=False, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    subprocess.run(["launchctl", "load", str(plist_path)], check=False)
    print(f"已安装后台自动复扫 launchd: {plist_path}")
    print(f"调度间隔: {interval_seconds // 60} 分钟；日志: {stdout_log}")
    return 0


def uninstall_auto_refresh_launchd() -> int:
    plist_path = _launch_agent_path()
    if plist_path.exists():
        subprocess.run(["launchctl", "unload", str(plist_path)], check=False)
        plist_path.unlink()
        print(f"已卸载后台自动复扫 launchd: {plist_path}")
    else:
        print("未找到后台自动复扫 launchd 配置。")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Skyscanner 多市场 CLI。默认推荐浏览器页面模式（Comet 优先）。",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例:
  python cli.py doctor
  python cli.py page -o 北京 -d 阿拉木图 -t 2026-04-29
  python cli.py page -o 北京 -d 阿拉木图 -t 2026-04-29 --date-window 0
  python cli.py page -o PEK -d ALA -t 2026-04-29 --exact-airport
  python cli.py page -o 北京 -d 阿拉木图 -t 2026-04-29 -r CN,UK,SG,HK
        """,
    )

    subparsers = parser.add_subparsers(dest="command")

    doctor = subparsers.add_parser("doctor", help="检查浏览器/CDP/Neo 环境")
    doctor.add_argument("--capture-file", help="可选：检查某个 Neo export 文件是否存在")
    doctor.add_argument(
        "--verify-session-persistence",
        action="store_true",
        help="实际启动并重启浏览器，验证同一 profile 的 cookie 是否保留",
    )
    doctor.add_argument(
        "--persistence-browser",
        choices=["comet", "edge", "chrome"],
        help="指定要验证 session 持久化的浏览器",
    )

    replay = subparsers.add_parser(
        "replay-failures",
        help="回放 logs/failures 并统计各市场 parser 稳定性",
    )
    replay.add_argument(
        "--failure-dir",
        default=str(DEFAULT_FAILURE_DIR),
        help="失败样本目录，默认指向运行时 logs/failures",
    )
    replay.add_argument(
        "--show-samples",
        dest="show_samples",
        action="store_true",
        default=True,
        help="打印逐样本回放详情",
    )
    replay.add_argument(
        "--no-show-samples",
        dest="show_samples",
        action="store_false",
        help="仅打印汇总统计",
    )

    auto_once = subparsers.add_parser(
        "auto-refresh-once",
        help="检查并执行一次到期的后台自动复扫任务",
    )
    auto_once.add_argument("--limit", type=int, default=1, help="本次最多执行多少条后台复扫配置")
    auto_once.add_argument("--wait", type=int, default=10, help="打开结果页后的等待秒数")
    auto_once.add_argument("--timeout", type=int, default=30, help="HTTP/CDP 超时")
    auto_once.add_argument(
        "--transport",
        choices=["scrapling", "page", "opencli", "cdp_structured"],
        default="opencli",
        help="后台复扫使用的抓取传输",
    )
    auto_once.add_argument(
        "--fetch-pipeline",
        choices=["fast", "balanced", "session_heavy"],
        default="balanced",
        help="Scrapling 抓取策略链",
    )
    auto_once.add_argument("--show-delta", action="store_true", help="扫描结束后打印变化摘要")
    auto_once.add_argument("--dry-run", action="store_true", help="只打印到期任务，不执行扫描")
    auto_once.add_argument("--only-on-ac-power", action="store_true", help="仅在接入电源时运行后台复扫")
    auto_once.add_argument("--save", dest="save", action="store_true", default=True, help="保存 Markdown 结果")
    auto_once.add_argument("--no-save", dest="save", action="store_false", help="不保存 Markdown 结果")

    install_auto = subparsers.add_parser(
        "install-auto-refresh",
        help="安装 macOS launchd 后台自动复扫调度",
    )
    install_auto.add_argument(
        "--interval-minutes",
        type=int,
        default=DEFAULT_LAUNCHD_INTERVAL_MINUTES,
        help="launchd 检查间隔，默认 600 分钟",
    )
    install_auto.add_argument("--limit", type=int, default=1, help="每次调度最多执行多少条后台复扫配置")
    install_auto.add_argument("--run-at-load", dest="run_at_load", action="store_true", default=True)
    install_auto.add_argument("--no-run-at-load", dest="run_at_load", action="store_false")
    install_auto.add_argument("--only-on-ac-power", action="store_true", help="写入 launchd 后仅接电时执行复扫")
    install_auto.add_argument("--save", dest="save", action="store_true", default=True)
    install_auto.add_argument("--no-save", dest="save", action="store_false")

    subparsers.add_parser(
        "uninstall-auto-refresh",
        help="卸载 macOS launchd 后台自动复扫调度",
    )

    page = subparsers.add_parser("page", help="打开各市场结果页并抽取最佳价和最低价")
    page.add_argument(
        "-o", "--origin", help="出发地（中文、IATA 或 metro code）"
    )
    page.add_argument(
        "-d", "--destination", help="目的地（中文或 IATA）"
    )
    page.add_argument(
        "--origin-country",
        help="国家模式：出发国家（中文、英文或 ISO 国家码）",
    )
    page.add_argument(
        "--destination-country",
        help="国家模式：目的国家（中文、英文或 ISO 国家码）",
    )
    page.add_argument(
        "--country-airport-limit",
        type=int,
        default=COUNTRY_ROUTE_DEFAULT_AIRPORT_LIMIT,
        help="国家模式下每国最多展开多少个候选机场（默认 5）",
    )
    page.add_argument("-t", "--date", required=True, help="出发日期 YYYY-MM-DD")
    page.add_argument("--return-date", help="返程日期 YYYY-MM-DD（不传则按单程处理）")
    page.add_argument(
        "--date-window",
        type=int,
        default=3,
        help="日期前后扫窗天数（默认 ±3 天；往返时保持停留天数不变）",
    )
    page.add_argument(
        "-r",
        "--regions",
        default="",
        help="额外地区代码，逗号分隔，会叠加到智能默认地区上",
    )
    page.add_argument("--wait", type=int, default=10, help="打开结果页后的等待秒数")
    page.add_argument("--timeout", type=int, default=30, help="HTTP/CDP 超时")
    page.add_argument(
        "--transport",
        choices=["scrapling", "page", "opencli", "cdp_structured"],
        default="opencli",
        help="opencli: 使用 opencli 浏览器自动化（默认）；cdp_structured: 实验性结构化 CDP；page: 通过浏览器 CDP 读取结果页；scrapling: legacy 直接抓取页面文本",
    )
    page.add_argument(
        "--exact-airport",
        action="store_true",
        help="关闭城市 metro code 映射，例如北京不再转成 BJSA",
    )
    page.add_argument(
        "--preview-only",
        action="store_true",
        help="仅显示最近 6 小时内的本地预览缓存，不发起实时扫描",
    )
    page.add_argument(
        "--rerun-failed",
        action="store_true",
        help="只重跑上次失败的市场，其余成功市场直接复用历史结果",
    )
    page.add_argument(
        "--show-delta",
        action="store_true",
        help="扫描结束后额外打印相对上次的变化摘要",
    )
    page.add_argument(
        "--show-plan",
        action="store_true",
        help="打印 SearchPlan 扫描计划并退出，不发起实时扫描",
    )
    page.add_argument(
        "--fetch-pipeline",
        choices=["fast", "balanced", "session_heavy"],
        default="balanced",
        help="Scrapling 抓取策略链: fast=快速直连, balanced=CDP复用+Stealth+验证码 (默认), session_heavy=完整浏览器会话链",
    )
    page.add_argument(
        "--save",
        dest="save",
        action="store_true",
        default=True,
        help="保存 Markdown 结果",
    )
    page.add_argument(
        "--no-save", dest="save", action="store_false", help="不保存 Markdown 结果"
    )

    # ── P7: Transport mode ────────────────────────────────────────────
    page.add_argument(
        "--transport-mode",
        choices=["auto", "opencli", "cdp", "scrapling"],
        default="auto",
        help=(
            "传输模式严格控制: auto=按 --transport 选择并允许 fallback (默认), "
            "opencli/cdp/scrapling=只跑该传输，禁用 fallback"
        ),
    )

    # ── P7: CDP mode ──────────────────────────────────────────────────
    page.add_argument(
        "--cdp-mode",
        choices=["attach", "managed", "manual"],
        default="attach",
        help="CDP 浏览器连接模式: attach=连接已有浏览器 (默认), managed=启动独立浏览器, manual=手动 Tab 映射",
    )
    page.add_argument(
        "--cdp-host",
        default="http://localhost:9222",
        help="CDP 浏览器调试端口地址 (默认 http://localhost:9222)",
    )
    page.add_argument(
        "--keep-tabs",
        action="store_true",
        help="调试用: owned tabs 不自动关闭",
    )
    page.add_argument(
        "--manual-tabs-json",
        default=None,
        help="手动 Tab 映射 JSON 文件路径 (cdp-mode=manual 时需要)",
    )

    # ── P7: Confidence / trust policy ──────────────────────────────────
    page.add_argument(
        "--low-confidence-policy",
        choices=["fallback", "show", "hide", "accept-review"],
        default="fallback",
        help="低置信度价格处理策略: fallback=触发回退 (默认), show=展示但不参与排序, hide=仅写入 trace, accept-review=接受但标记需人工复核",
    )
    page.add_argument(
        "--rankable-confidence",
        type=float,
        default=0.80,
        help="可参与排序的最低置信度阈值 (默认 0.80)",
    )
    page.add_argument(
        "--review-confidence",
        type=float,
        default=0.50,
        help="触发人工复核标记的置信度阈值 (默认 0.50)",
    )

    # ── P7: Challenge policy ──────────────────────────────────────────
    page.add_argument(
        "--challenge-policy",
        choices=["stop", "manual"],
        default="stop",
        help="遇到验证码时的策略: stop=终止不绕过 (默认), manual=输出需要用户处理的 URL/Tab 信息",
    )

    # ── P7: Trace / debug ─────────────────────────────────────────────
    page.add_argument(
        "--trace-dir",
        default="traces",
        help="Trace JSONL 输出目录 (默认 traces/)",
    )
    page.add_argument(
        "--no-trace",
        action="store_true",
        help="禁用 trace JSONL 输出",
    )
    page.add_argument(
        "--failure-log-dir",
        default="failures",
        help="Failure log 输出目录 (默认 failures/)",
    )
    page.add_argument(
        "--debug-page-text",
        action="store_true",
        help="在 failure log 中保留完整 page text (默认截断)",
    )

    # ── P7: Output format ─────────────────────────────────────────────
    page.add_argument(
        "--output",
        choices=["table", "json", "jsonl"],
        default="table",
        help="输出格式: table=终端表格 (默认), json=结构化 JSON, jsonl=每行一个 quote",
    )
    page.add_argument(
        "--output-file",
        default=None,
        help="结果输出文件路径 (--output json 或 jsonl 时使用)",
    )
    page.add_argument(
        "--show-attempts",
        action="store_true",
        help="展示每个 region 的完整 fallback attempt 链路",
    )
    page.add_argument(
        "--show-low-confidence",
        action="store_true",
        help="展示低置信度价格 (默认隐藏)",
    )

    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    cli = SimpleCLI()

    if args.command is None:
        return cli.interactive_page()

    if args.command == "doctor":
        neo = NeoCli(cli.project_root)
        print_doctor(
            neo,
            Path(args.capture_file) if args.capture_file else None,
            verify_session_persistence=args.verify_session_persistence,
            persistence_browser=args.persistence_browser,
        )
        cdp_info = detect_cdp_version()
        if cdp_info:
            print(f"\n当前 CDP 浏览器: {cdp_info.get('Browser', 'unknown')}")
        return 0

    if args.command == "replay-failures":
        return run_failure_replay_command(args)

    if args.command == "auto-refresh-once":
        return asyncio.run(run_auto_refresh_once_command(args))

    if args.command == "install-auto-refresh":
        return install_auto_refresh_launchd(args)

    if args.command == "uninstall-auto-refresh":
        return uninstall_auto_refresh_launchd()

    if args.command == "page":
        return asyncio.run(cli.run_page_command(args))

    parser.error("未知命令")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
