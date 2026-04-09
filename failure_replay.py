from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import dataclass
import json
from pathlib import Path
from typing import Any, Optional

from app_paths import LOGS_DIR
from skyscanner_models import FlightQuote, RegionConfig
from skyscanner_page_parser import (
    PARSER_REPLAYABLE_FAILURE_STATUSES,
    extract_page_quote_with_diagnostics,
)
from skyscanner_regions import REGIONS


DEFAULT_FAILURE_DIR = LOGS_DIR / "failures"


@dataclass(frozen=True)
class FailureSample:
    path: Path
    timestamp: str
    transport: str
    route: str
    region: str
    domain: str
    status: str
    error: str
    source_url: str
    extra: dict[str, Any]
    page_text_excerpt: str


@dataclass(frozen=True)
class FailureReplayResult:
    sample: FailureSample
    replayable: bool
    quote: Optional[FlightQuote]
    diagnostics: Optional[dict[str, Any]]
    success: bool
    used_fallback: bool
    matched_expected_status: bool
    failure_stage: str
    failure_reason: str


@dataclass(frozen=True)
class RegionReplayStat:
    region: str
    sample_count: int
    replayable_count: int
    success_count: int
    fallback_count: int
    top_failure_reason: str
    non_replayable_count: int


@dataclass(frozen=True)
class FailureReplayReport:
    failure_dir: Path
    total_samples: int
    replayable_samples: int
    non_replayable_samples: int
    successful_replays: int
    fallback_replays: int
    results: tuple[FailureReplayResult, ...]
    region_stats: tuple[RegionReplayStat, ...]


def _normalize_excerpt(value: str) -> str:
    cleaned = value.strip()
    if cleaned == "(empty)":
        return ""
    return cleaned


def load_failure_sample(path: Path) -> FailureSample:
    lines = path.read_text(encoding="utf-8").splitlines()
    headers: dict[str, str] = {}
    extra: dict[str, Any] = {}
    page_lines: list[str] = []
    in_excerpt = False

    for line in lines:
        if line == "--- page_text_excerpt ---":
            in_excerpt = True
            continue
        if in_excerpt:
            page_lines.append(line)
            continue
        if not line.strip():
            continue
        if line.startswith("extra: "):
            try:
                payload = json.loads(line[len("extra: ") :])
                if isinstance(payload, dict):
                    extra = payload
            except json.JSONDecodeError:
                extra = {"raw": line[len("extra: ") :]}
            continue
        key, _, value = line.partition(":")
        headers[key.strip()] = value.strip()

    return FailureSample(
        path=path,
        timestamp=headers.get("timestamp", ""),
        transport=headers.get("transport", ""),
        route=headers.get("route", ""),
        region=headers.get("region", ""),
        domain=headers.get("domain", ""),
        status=headers.get("status", ""),
        error=headers.get("error", ""),
        source_url=headers.get("source_url", ""),
        extra=extra,
        page_text_excerpt=_normalize_excerpt("\n".join(page_lines)),
    )


def load_failure_samples(failure_dir: Path = DEFAULT_FAILURE_DIR) -> list[FailureSample]:
    if not failure_dir.exists():
        return []
    return [load_failure_sample(path) for path in sorted(failure_dir.glob("*.log"))]


def _resolve_region_config(sample: FailureSample) -> RegionConfig:
    known = REGIONS.get(sample.region)
    if known is not None:
        return known
    locale = str(sample.extra.get("locale") or "")
    currency = str(sample.extra.get("currency") or "")
    return RegionConfig(
        code=sample.region or "UNKNOWN",
        name=sample.region or "UNKNOWN",
        domain=sample.domain,
        locale=locale,
        currency=currency,
    )


def replay_failure_sample(sample: FailureSample) -> FailureReplayResult:
    if sample.status not in PARSER_REPLAYABLE_FAILURE_STATUSES:
        failure_reason = sample.error or sample.status or "metadata_only_failure"
        return FailureReplayResult(
            sample=sample,
            replayable=False,
            quote=None,
            diagnostics=None,
            success=False,
            used_fallback=False,
            matched_expected_status=False,
            failure_stage="transport",
            failure_reason=failure_reason,
        )

    region = _resolve_region_config(sample)
    quote, diagnostics = extract_page_quote_with_diagnostics(
        region,
        sample.source_url or sample.domain,
        sample.page_text_excerpt,
    )
    diagnostics_dict = {
        "final_status": diagnostics.final_status,
        "validation_outcome": diagnostics.validation_outcome,
        "failure_stage": diagnostics.failure_stage,
        "failure_reason": diagnostics.failure_reason,
        "used_fallback": diagnostics.used_fallback,
        "state": diagnostics.state.state,
        "scope_strategy": diagnostics.state.scope_strategy,
    }
    failure_stage = diagnostics.failure_stage or "none"
    failure_reason = (
        diagnostics.failure_reason
        or quote.error
        or sample.error
        or sample.status
        or "unknown"
    )
    return FailureReplayResult(
        sample=sample,
        replayable=True,
        quote=quote,
        diagnostics=diagnostics_dict,
        success=quote.price is not None,
        used_fallback=diagnostics.used_fallback,
        matched_expected_status=quote.status == sample.status,
        failure_stage=failure_stage,
        failure_reason=failure_reason,
    )


def build_failure_replay_report(
    failure_dir: Path = DEFAULT_FAILURE_DIR,
) -> FailureReplayReport:
    samples = load_failure_samples(failure_dir)
    results = [replay_failure_sample(sample) for sample in samples]
    by_region: dict[str, list[FailureReplayResult]] = defaultdict(list)
    for result in results:
        by_region[result.sample.region].append(result)

    region_stats: list[RegionReplayStat] = []
    for region, grouped in by_region.items():
        replayable_count = sum(1 for result in grouped if result.replayable)
        success_count = sum(1 for result in grouped if result.success)
        fallback_count = sum(
            1 for result in grouped if result.replayable and result.used_fallback
        )
        non_replayable_count = sum(1 for result in grouped if not result.replayable)
        failure_reason_counts = Counter(
            result.failure_reason
            for result in grouped
            if (result.replayable and not result.success) or not result.replayable
        )
        top_failure_reason = (
            failure_reason_counts.most_common(1)[0][0]
            if failure_reason_counts
            else "-"
        )
        region_stats.append(
            RegionReplayStat(
                region=region,
                sample_count=len(grouped),
                replayable_count=replayable_count,
                success_count=success_count,
                fallback_count=fallback_count,
                top_failure_reason=top_failure_reason,
                non_replayable_count=non_replayable_count,
            )
        )

    region_stats.sort(
        key=lambda stat: (
            (stat.success_count / stat.replayable_count)
            if stat.replayable_count
            else -1.0,
            -stat.sample_count,
            stat.region,
        )
    )
    return FailureReplayReport(
        failure_dir=failure_dir,
        total_samples=len(results),
        replayable_samples=sum(1 for result in results if result.replayable),
        non_replayable_samples=sum(1 for result in results if not result.replayable),
        successful_replays=sum(1 for result in results if result.success),
        fallback_replays=sum(1 for result in results if result.used_fallback),
        results=tuple(results),
        region_stats=tuple(region_stats),
    )


def _percent(numerator: int, denominator: int) -> str:
    if denominator <= 0:
        return "-"
    return f"{(numerator / denominator) * 100:.1f}%"


def render_failure_replay_report(
    report: FailureReplayReport, *, show_samples: bool = True
) -> str:
    lines = [
        "# 失败样本回放集",
        "",
        f"- 样本目录: `{report.failure_dir}`",
        f"- 总样本: `{report.total_samples}`",
        f"- 可回放 parser 样本: `{report.replayable_samples}`",
        f"- 元数据样本: `{report.non_replayable_samples}`",
        f"- 回放成功数: `{report.successful_replays}`",
        f"- 回放 fallback 数: `{report.fallback_replays}`",
        "",
        "| 市场 | 样本 | 可回放 | 成功率 | fallback 率 | 元数据样本 | 主要失败原因 |",
        "| --- | ---: | ---: | ---: | ---: | ---: | --- |",
    ]
    for stat in report.region_stats:
        lines.append(
            "| "
            + " | ".join(
                [
                    stat.region,
                    str(stat.sample_count),
                    str(stat.replayable_count),
                    _percent(stat.success_count, stat.replayable_count),
                    _percent(stat.fallback_count, stat.replayable_count),
                    str(stat.non_replayable_count),
                    stat.top_failure_reason,
                ]
            )
            + " |"
        )

    if show_samples and report.results:
        lines.extend(
            [
                "",
                "## 样本详情",
                "",
                "| 文件 | 市场 | 原始状态 | 回放结果 | 阶段 | 是否命中预期 |",
                "| --- | --- | --- | --- | --- | --- |",
            ]
        )
        for result in report.results:
            replay_status = result.quote.status if result.quote is not None else "-"
            lines.append(
                "| "
                + " | ".join(
                    [
                        result.sample.path.name,
                        result.sample.region,
                        result.sample.status,
                        replay_status,
                        result.failure_stage,
                        "是" if result.matched_expected_status else "否",
                    ]
                )
                + " |"
            )

    return "\n".join(lines) + "\n"
