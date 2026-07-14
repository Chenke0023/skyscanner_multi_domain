"""Result service for quote simplification and report persistence.

This module owns result-processing behavior shared by the CLI and desktop UI.
It keeps non-CLI callers from depending on ``cli.SimpleCLI`` for formatting,
currency conversion, row ranking, and markdown report output.
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

from skyscanner_multi_domain.planning.date_window import format_trip_date_label
from skyscanner_multi_domain.pricing.fx_rates import FxRateService
from skyscanner_multi_domain.runtime.paths import get_reports_dir
from skyscanner_multi_domain.scan.history import (
    can_reuse_page_for_row,
    classify_failure,
    source_kind_label,
)
from skyscanner_multi_domain.scan.output_rows import (
    CombinedQuoteRow,
    QuoteRow,
    SimplifiedQuoteRow,
)

_PRICE_SOURCE_LABELS: dict[str, str] = {
    "cheapest_block": "Cheapest 区块",
    "best_block": "Best 区块",
    "first_price_fallback": "首个价格弱匹配",
    "recovered_best": "恢复解析",
    "manual_confirmed": "人工确认",
    "unpriced": "未取价",
}


def confidence_label(value: object) -> str:
    if not isinstance(value, (int, float)):
        return "未知"
    if value >= 0.85:
        return "高"
    if value >= 0.6:
        return "中"
    if value >= 0.3:
        return "低"
    return "极低"


def price_source_label(value: object) -> str:
    if value in (None, "", "unknown"):
        return "未知"
    return _PRICE_SOURCE_LABELS.get(str(value), str(value))


def warnings_summary(warnings: object) -> str:
    if not isinstance(warnings, (list, tuple)):
        return "-"
    cleaned = [str(item).strip() for item in warnings if str(item).strip()]
    if not cleaned:
        return "-"
    if len(cleaned) == 1:
        return cleaned[0]
    return f"{len(cleaned)} 项警告"


def format_itinerary_legs(legs: object, *, separator: str = "；") -> str:
    if not isinstance(legs, list):
        return "-"
    parts: list[str] = []
    for leg in legs[:2]:
        if not isinstance(leg, dict):
            continue
        direction = "去程" if str(leg.get("direction") or "") != "return" else "返程"
        details: list[str] = []
        departure = str(leg.get("departure_time") or "").strip()
        arrival = str(leg.get("arrival_time") or "").strip()
        if departure and arrival:
            details.append(f"{departure}–{arrival}")
        stops = leg.get("stop_count")
        if isinstance(stops, int) and not isinstance(stops, bool):
            details.append("直飞" if stops == 0 else f"经停{stops}次")
        duration = leg.get("duration_minutes")
        if isinstance(duration, int) and duration > 0:
            hours, minutes = divmod(duration, 60)
            if hours and minutes:
                details.append(f"{hours}小时{minutes}分")
            elif hours:
                details.append(f"{hours}小时")
            else:
                details.append(f"{minutes}分钟")
        if details:
            parts.append(f"{direction} " + " · ".join(details))
    return separator.join(parts) if parts else "-"


def _failed_reason_counts(rows: list[dict[str, object]]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for row in rows:
        category = row.get("failure_category")
        if not category:
            continue
        reason = str(category).strip() or "unknown"
        counts[reason] = counts.get(reason, 0) + 1
    return counts


def _row_is_decision_eligible(row: dict[str, object]) -> bool:
    if row.get("decision_eligible") is False or row.get("rankable") is False:
        return False
    if str(row.get("price_source") or "").strip() == "first_price_fallback":
        return False
    confidence = row.get("confidence")
    if (
        row.get("rankable") is None
        and isinstance(confidence, (int, float))
        and not isinstance(confidence, bool)
        and float(confidence) < 0.80
    ):
        return False
    return True


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
        hints.append("最低价来自首个价格弱匹配，必须人工确认。")
    for warning in _row_warning_lines(primary_row):
        hints.append(f"解析警告：{warning}")

    risky_hits = {
        reason: count
        for reason, count in failed_counts.items()
        if any(token in reason for token in _RISKY_FAILURE_TOKENS)
    }
    if risky_hits:
        summary = "、".join(f"{reason}×{count}" for reason, count in sorted(risky_hits.items()))
        hints.append(f"{sum(risky_hits.values())} 个市场失败（{summary}），可能存在漏价。")
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
        hints.append("所有有效价格均来自弱匹配/恢复解析，作为初筛结果，需人工复核。")
    return hints


def build_decision_summary(
    rows: list[dict[str, object]],
    *,
    show_dates: bool = False,
) -> list[str]:
    valid_pairs: list[tuple[dict[str, object], float]] = []
    for row in rows:
        if not _row_is_decision_eligible(row):
            continue
        value = _row_cny_value(row)
        if value is not None:
            valid_pairs.append((row, value))

    failed_counts = _failed_reason_counts(rows)

    if not valid_pairs:
        lines = ["## 扫描结论", "", "本次未抓取到任何有效价格，请检查市场可达性后重试。", ""]
        if failed_counts:
            lines.append("失败原因汇总：")
            lines.extend(f"- {reason}: {count}" for reason, count in sorted(failed_counts.items()))
            lines.append("")
        return lines

    valid_pairs.sort(key=lambda item: item[1])
    primary_row, primary_value = valid_pairs[0]
    runner_row: dict[str, object] | None = None
    runner_value: float | None = None
    if len(valid_pairs) > 1:
        runner_row, runner_value = valid_pairs[1]

    lines = ["## 扫描结论", "", "已扫描完整计划市场集。", "", "### 推荐先验证", ""]
    lines.append(f"- 最低价：¥{primary_value:,.2f}")
    if show_dates:
        lines.append(f"- 日期：{primary_row.get('date') or '-'}")
    lines.append(f"- 航段：{primary_row.get('route') or '-'}")
    lines.append(f"- 市场：{primary_row.get('region_name') or '-'}")
    lines.append(f"- 价格来源：{price_source_label(primary_row.get('price_source'))}")
    candidate_sources = primary_row.get("candidate_sources")
    if isinstance(candidate_sources, list) and candidate_sources:
        lines.append(f"- 候选来源：{', '.join(str(item) for item in candidate_sources)}")
    lines.append(f"- 可信度：{confidence_label(primary_row.get('confidence'))}")
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
        lines.append(f"- 可信度：{confidence_label(runner_row.get('confidence'))}")
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


def build_warning_detail_section(
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
        confidence = confidence_label(row.get("confidence"))
        source = price_source_label(row.get("price_source"))
        lines.append(f"- **{header}** - 可信度 {confidence} · 价格来源 {source}")
        for warning in _row_warning_lines(row):
            lines.append(f"  - {warning}")
        evidence = row.get("evidence_text")
        if isinstance(evidence, str) and evidence.strip():
            lines.append(f"  - 证据片段：{evidence.strip()}")
    lines.append("")
    return lines


def trip_file_token(date: str, return_date: str | None = None) -> str:
    token = date.replace("-", "")
    if return_date:
        token = f"{token}_rt{return_date.replace('-', '')}"
    return token


def safe_output_token(value: str) -> str:
    cleaned = "".join(ch if ch.isalnum() else "_" for ch in value.strip())
    cleaned = cleaned.strip("_")
    return cleaned or "unknown"


class ResultService:
    """Simplify scan quotes, rank rows, and write markdown report files."""

    def __init__(self, fx_rates: FxRateService | None = None) -> None:
        self.fx_rates = fx_rates or FxRateService()

    def to_cny(self, price: float | None, currency: str | None) -> float | None:
        return self.fx_rates.convert_to_cny(price, currency)

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
                    "price_candidates_count": row.get("price_candidates_count") or 0,
                    "selected_candidate_rank": row.get("selected_candidate_rank"),
                    "candidate_sources": row.get("candidate_sources") or [],
                    "itinerary_legs": row.get("itinerary_legs") or [],
                    "readiness": row.get("readiness"),
                    "rankable": row.get("rankable"),
                    "decision_eligible": row.get("decision_eligible"),
                    "result_visibility": row.get("result_visibility"),
                    "requires_manual_review": bool(row.get("requires_manual_review")),
                }
            )
        return snapshots

    @staticmethod
    def _group_single_trip(
        trip_label: str,
        rows: list[dict[str, object]],
    ) -> list[tuple[str, list[dict[str, object]]]]:
        return [(trip_label, rows)]

    def sort_simplified_rows(
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
            if cheapest_price is not None and not isinstance(cheapest_price, (int, float)):
                continue

            observed_best = float(best_price) if best_price is not None else None
            observed_cheapest = float(cheapest_price) if cheapest_price is not None else None
            rankable = quote.get("rankable")
            price_source = str(quote.get("price_source") or "").strip()
            confidence = quote.get("confidence")
            weak_fallback = price_source == "first_price_fallback"
            below_rankable_threshold = (
                isinstance(confidence, (int, float))
                and not isinstance(confidence, bool)
                and float(confidence) < 0.80
            )
            decision_eligible = not (
                rankable is False
                or weak_fallback
                or (rankable is None and below_rankable_threshold)
            )

            # Keep weak observations in the raw quote snapshot, but never turn them
            # into comparable prices. This prevents an arbitrary first text price
            # (for example a calendar/ad amount) from becoming the UI's minimum.
            best_numeric = observed_best if decision_eligible else None
            cheapest_numeric = observed_cheapest if decision_eligible else None
            best_cny = self.to_cny(best_numeric, currency) if currency else None
            cheapest_cny = self.to_cny(cheapest_numeric, currency) if currency else None
            source_kind = str(quote.get("source_kind") or "").strip() or None
            failure_category = None
            failure_action = None
            display_error = str(quote.get("error") or "-")
            if best_numeric is None and cheapest_numeric is None:
                excluded_observation = (
                    (observed_best is not None or observed_cheapest is not None)
                    and not decision_eligible
                )
                if excluded_observation:
                    display_error = "低可信度价格已排除，不参与最低价排名"
                    failure_category = "未能读取价格"
                    failure_action = "打开页面确认实际票价，或重新扫描该市场"
                else:
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
                    "error": display_error,
                    "route": route_label or "-",
                    "source_kind": source_kind,
                    "source_label": source_kind_label(source_kind),
                    "delta_vs_last_scan": None,
                    "delta_label": "-",
                    "updated_at": None,
                    "failure_category": failure_category,
                    "failure_action": failure_action,
                    "can_reuse_page": can_reuse_page_for_row({"source_kind": source_kind}),
                    "plan_rank": quote.get("plan_rank"),
                    "plan_score": quote.get("plan_score"),
                    "plan_phase": quote.get("plan_phase"),
                    "plan_reason": quote.get("plan_reason"),
                    "route_rank": quote.get("route_rank"),
                    "date_rank": quote.get("date_rank"),
                    "market_rank": quote.get("market_rank"),
                    "confidence": quote.get("confidence"),
                    "price_source": quote.get("price_source"),
                    "rankable": rankable,
                    "decision_eligible": decision_eligible,
                    "result_visibility": quote.get("result_visibility"),
                    "requires_manual_review": bool(quote.get("requires_manual_review")),
                    "excluded_price_display": (
                        f"{(observed_cheapest if observed_cheapest is not None else observed_best):,.2f} {currency.upper()}"
                        if not decision_eligible
                        and (observed_cheapest is not None or observed_best is not None)
                        and currency
                        else None
                    ),
                    "evidence_text": quote.get("evidence_text"),
                    "parser_warnings": quote.get("parser_warnings") or [],
                    "price_candidates_count": quote.get("price_candidates_count") or 0,
                    "selected_candidate_rank": quote.get("selected_candidate_rank"),
                    "candidate_sources": quote.get("candidate_sources") or [],
                    "itinerary_legs": quote.get("itinerary_legs") or [],
                    "readiness": quote.get("readiness"),
                }
            )
        return self.sort_simplified_rows(simplified)

    @staticmethod
    def format_plan_cell(row: dict[str, object]) -> str:
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
    def with_route_plan_metadata(
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
            "# Skyscanner 比价结果",
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

        lines.extend(build_decision_summary(rows))
        lines.extend(["## 价格明细", ""])
        lines.extend(
            [
                "| 航段 | 地区 | 来源 | 计划 | 最佳（原币） | 最佳（人民币） | 最低价（原币） | 最低价（人民币） | 最低价行程 | 可信度 | 价格来源 | 警告 | 较上次变化 | 状态 | 错误 | 链接 |",
                "| --- | --- | --- | --- | ---: | ---: | ---: | ---: | --- | --- | --- | --- | --- | --- | --- | --- |",
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
                f"| {row.get('route') or '-'} | {row['region_name']} | {row.get('source_label') or '-'} | {self.format_plan_cell(row)} | {row.get('best_display_price') or '-'} | {best_cny_text} | {row.get('cheapest_display_price') or '-'} | {cheapest_cny_text} | {format_itinerary_legs(row.get('itinerary_legs'), separator='<br>')} | {confidence_label(row.get('confidence'))} | {price_source_label(row.get('price_source'))} | {warnings_summary(row.get('parser_warnings'))} | {row.get('delta_label') or '-'} | {row.get('status') or '-'} | {row.get('error') or '-'} | [打开结果页]({row['link']}) |"
            )
        lines.append("")
        lines.extend(build_warning_detail_section(rows))
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

        lines.extend(build_decision_summary(rows, show_dates=True))
        lines.extend(["## 价格明细", ""])
        lines.extend(
            [
                "| 日期 | 航段 | 地区 | 来源 | 计划 | 最佳（原币） | 最佳（人民币） | 最低价（原币） | 最低价（人民币） | 最低价行程 | 可信度 | 价格来源 | 警告 | 较上次变化 | 状态 | 错误 | 链接 |",
                "| --- | --- | --- | --- | --- | ---: | ---: | ---: | ---: | --- | --- | --- | --- | --- | --- | --- | --- |",
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
                        self.format_plan_cell(row),
                        str(row.get("best_display_price") or "-"),
                        best_cny_text,
                        str(row.get("cheapest_display_price") or "-"),
                        cheapest_cny_text,
                        format_itinerary_legs(row.get("itinerary_legs"), separator="<br>"),
                        confidence_label(row.get("confidence")),
                        price_source_label(row.get("price_source")),
                        warnings_summary(row.get("parser_warnings")),
                        str(row.get("delta_label") or "-"),
                        str(row.get("status") or "-"),
                        str(row.get("error") or "-"),
                        link_cell,
                    ]
                )
                + " |"
            )
        lines.append("")
        lines.extend(build_warning_detail_section(rows, show_dates=True))
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
        lines.extend(build_decision_summary(flattened_rows, show_dates=True))
        lines.extend(["## 价格明细", ""])
        lines.extend(
            [
                "| 日期 | 航段 | 地区 | 来源 | 最佳（原币） | 最佳（人民币） | 最低价（原币） | 最低价（人民币） | 最低价行程 | 可信度 | 价格来源 | 警告 | 较上次变化 | 状态 | 错误 | 链接 |",
                "| --- | --- | --- | --- | ---: | ---: | ---: | ---: | --- | --- | --- | --- | --- | --- | --- | --- |",
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
                    f"| {date} | {row.get('route') or '-'} | {row['region_name']} | {row.get('source_label') or '-'} | {row.get('best_display_price') or '-'} | {best_cny_text} | {row.get('cheapest_display_price') or '-'} | {cheapest_cny_text} | {format_itinerary_legs(row.get('itinerary_legs'), separator='<br>')} | {confidence_label(row.get('confidence'))} | {price_source_label(row.get('price_source'))} | {warnings_summary(row.get('parser_warnings'))} | {row.get('delta_label') or '-'} | {row.get('status') or '-'} | {row.get('error') or '-'} | [打开结果页]({row['link']}) |"
                )
        lines.append("")
        lines.extend(build_warning_detail_section(flattened_rows, show_dates=True))
        return "\n".join(lines) + "\n"

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
        rows = self.simplify_quotes(quotes, route_label=route_label)
        return self.save_simplified_results(
            rows,
            origin,
            destination,
            date,
            return_date=return_date,
            file_origin_token=file_origin_token,
            file_destination_token=file_destination_token,
        )

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
            f"edge_page_{safe_output_token(file_origin_token or origin)}_"
            f"{safe_output_token(file_destination_token or destination)}_"
            f"{trip_file_token(date, return_date)}.md"
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
        filename = output_dir / (
            f"edge_page_{safe_output_token(file_origin_token or origin)}_"
            f"{safe_output_token(file_destination_token or destination)}_"
            f"{trip_file_token(date, return_date)}_combined.md"
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
        start_stamp = trip_file_token(start_date, start_return_date)
        end_stamp = trip_file_token(end_date, end_return_date)
        filename = output_dir / (
            f"edge_page_{safe_output_token(file_origin_token or origin)}_"
            f"{safe_output_token(file_destination_token or destination)}_"
            f"{start_stamp}_{end_stamp}_summary.md"
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
    def display_price_value(value: object) -> float:
        if not isinstance(value, str) or not value or value == "-":
            return float("inf")
        try:
            return float(value.replace(",", "").split()[0])
        except (IndexError, ValueError):
            return float("inf")

    def row_selection_key(self, row: SimplifiedQuoteRow) -> tuple[float, float, float, float]:
        cheapest_cny = row.get("cheapest_cny_price")
        best_cny = row.get("best_cny_price")
        cheapest_display = row.get("cheapest_display_price")
        best_display = row.get("best_display_price")
        cheapest_native = self.display_price_value(
            cheapest_display if isinstance(cheapest_display, (str, float)) else None
        )
        best_native = self.display_price_value(
            best_display if isinstance(best_display, (str, float)) else None
        )
        return (
            float(cheapest_cny) if isinstance(cheapest_cny, (int, float)) else float("inf"),
            float(best_cny) if isinstance(best_cny, (int, float)) else float("inf"),
            cheapest_native,
            best_native,
        )

    def pick_better_row(
        self,
        current: SimplifiedQuoteRow | None,
        candidate: SimplifiedQuoteRow,
    ) -> SimplifiedQuoteRow:
        if current is None:
            return candidate
        if self.row_selection_key(candidate) < self.row_selection_key(current):
            return candidate
        return current
