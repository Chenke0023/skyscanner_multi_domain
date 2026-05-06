from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta
from typing import Iterable, Literal, Sequence

from date_window import format_iso_date, parse_iso_date
from location_resolver import LocationRecord
from skyscanner_regions import (
    BASELINE_REGIONS,
    COUNTRY_TO_REGION_CODES,
    REGIONS,
    dedupe_region_codes,
)


SearchMode = Literal["fast", "balanced", "deep"]
DatePhase = Literal["anchor", "edge", "nearby", "full"]
TaskPhase = Literal["probe", "expand", "verify", "deep"]
RowsByDate = list[tuple[str, list[dict[str, object]]]]


@dataclass(frozen=True)
class TripIntent:
    origin_input: str
    destination_input: str
    depart_date: str
    return_date: str | None
    origin_is_country: bool
    destination_is_country: bool
    date_window: int
    user_regions: list[str]
    mode: SearchMode = "balanced"


@dataclass(frozen=True)
class RouteCandidate:
    origin_code: str
    destination_code: str
    origin_label: str
    destination_label: str
    rank: int
    reason: str
    confidence: float
    score: float


@dataclass(frozen=True)
class DateCandidate:
    depart_date: str
    return_date: str | None
    offset: int
    phase: DatePhase
    reason: str
    score: float


@dataclass(frozen=True)
class MarketCandidate:
    region_code: str
    rank: int
    reason: str
    reliability: float
    historical_win_rate: float
    score: float


@dataclass(frozen=True)
class ScanTask:
    route: RouteCandidate
    date: DateCandidate
    market: MarketCandidate
    priority: float
    phase: TaskPhase
    reason: str


@dataclass(frozen=True)
class SearchStats:
    route_success_rate: dict[str, float]
    route_win_rate: dict[str, float]
    market_success_rate: dict[str, float]
    market_win_rate: dict[str, float]


def collect_search_stats(previous_rows_by_date: RowsByDate | None) -> SearchStats:
    route_total: dict[str, int] = {}
    route_success: dict[str, int] = {}
    route_wins: dict[str, int] = {}
    market_total: dict[str, int] = {}
    market_success: dict[str, int] = {}
    market_wins: dict[str, int] = {}

    for _trip_label, rows in previous_rows_by_date or []:
        priced_rows: list[dict[str, object]] = []
        for row in rows:
            route = str(row.get("route") or "").strip()
            market = str(row.get("region_code") or row.get("region_name") or "").strip().upper()
            has_price = _has_price(row)
            if route:
                route_total[route] = route_total.get(route, 0) + 1
                if has_price:
                    route_success[route] = route_success.get(route, 0) + 1
            if market:
                market_total[market] = market_total.get(market, 0) + 1
                if has_price:
                    market_success[market] = market_success.get(market, 0) + 1
            if has_price:
                priced_rows.append(row)

        if not priced_rows:
            continue
        winner = min(priced_rows, key=_price_key)
        winning_route = str(winner.get("route") or "").strip()
        winning_market = str(
            winner.get("region_code") or winner.get("region_name") or ""
        ).strip().upper()
        if winning_route:
            route_wins[winning_route] = route_wins.get(winning_route, 0) + 1
        if winning_market:
            market_wins[winning_market] = market_wins.get(winning_market, 0) + 1

    return SearchStats(
        route_success_rate=_rates(route_success, route_total),
        route_win_rate=_rates(route_wins, route_total),
        market_success_rate=_rates(market_success, market_total),
        market_win_rate=_rates(market_wins, market_total),
    )


def build_date_candidates(
    depart_date: str,
    return_date: str | None,
    window_days: int,
) -> list[DateCandidate]:
    if window_days < 0:
        raise ValueError("window_days must be >= 0")

    ordered_offsets: list[tuple[int, DatePhase, str, float]] = [
        (0, "anchor", "用户目标日期", 1.0),
    ]
    if window_days > 0:
        ordered_offsets.extend(
            [
                (-window_days, "edge", "窗口最早日期，用于检测提前出发是否明显便宜", 0.82),
                (window_days, "edge", "窗口最晚日期，用于检测推迟出发是否明显便宜", 0.82),
            ]
        )
    if window_days >= 1:
        ordered_offsets.extend(
            [
                (-1, "nearby", "目标日前一天", 0.72),
                (1, "nearby", "目标日后一天", 0.72),
            ]
        )
    for offset in range(2, window_days):
        ordered_offsets.extend(
            [
                (-offset, "full", f"窗口内剩余日期 {offset} 天前", 0.55 - offset * 0.01),
                (offset, "full", f"窗口内剩余日期 {offset} 天后", 0.55 - offset * 0.01),
            ]
        )

    departure = parse_iso_date(depart_date)
    stay_length: int | None = None
    if return_date:
        inbound = parse_iso_date(return_date)
        stay_length = (inbound - departure).days
        if stay_length < 0:
            raise ValueError("return_date must be >= departure_date")

    candidates: list[DateCandidate] = []
    seen_offsets: set[int] = set()
    for offset, phase, reason, score in ordered_offsets:
        if offset in seen_offsets or abs(offset) > window_days:
            continue
        seen_offsets.add(offset)
        current_departure = departure + timedelta(days=offset)
        current_return = (
            format_iso_date(current_departure + timedelta(days=stay_length))
            if stay_length is not None
            else None
        )
        candidates.append(
            DateCandidate(
                depart_date=format_iso_date(current_departure),
                return_date=current_return,
                offset=offset,
                phase=phase,
                reason=reason,
                score=max(score, 0.1),
            )
        )
    return candidates


def build_ordered_trip_dates(
    depart_date: str,
    return_date: str | None,
    window_days: int,
) -> list[tuple[str, str | None]]:
    return [
        (candidate.depart_date, candidate.return_date)
        for candidate in build_date_candidates(depart_date, return_date, window_days)
    ]


def build_market_candidates(
    region_codes: Iterable[str],
    previous_rows_by_date: RowsByDate | None = None,
    *,
    origin_country: str = "",
    destination_country: str = "",
    manual_region_codes: Iterable[str] = (),
) -> list[MarketCandidate]:
    ordered_codes = dedupe_region_codes(region_codes)
    stats = collect_search_stats(previous_rows_by_date)
    route_relevant = set()
    for country in (origin_country, destination_country):
        if country:
            route_relevant.update(COUNTRY_TO_REGION_CODES.get(country.upper(), ()))
    manual_regions = {code.upper() for code in manual_region_codes}

    candidates: list[MarketCandidate] = []
    for index, code in enumerate(ordered_codes):
        baseline_score = 1.0 if code in BASELINE_REGIONS else 0.0
        route_score = 1.0 if code in route_relevant else 0.0
        manual_score = 1.0 if code in manual_regions else 0.0
        success_rate = stats.market_success_rate.get(code, 0.5)
        win_rate = stats.market_win_rate.get(code, 0.0)
        usability_score = _currency_usability_score(code)
        position_score = 1.0 / (index + 1)
        score = (
            0.24 * baseline_score
            + 0.32 * route_score
            + 0.18 * win_rate
            + 0.14 * success_rate
            + 0.08 * usability_score
            + 0.05 * manual_score
            + 0.03 * position_score
        )
        candidates.append(
            MarketCandidate(
                region_code=code,
                rank=0,
                reason=_market_reason(code, baseline_score, route_score, manual_score, win_rate),
                reliability=success_rate,
                historical_win_rate=win_rate,
                score=score,
            )
        )

    ranked = sorted(candidates, key=lambda item: (-item.score, item.region_code))
    return [
        MarketCandidate(
            region_code=candidate.region_code,
            rank=index + 1,
            reason=candidate.reason,
            reliability=candidate.reliability,
            historical_win_rate=candidate.historical_win_rate,
            score=candidate.score,
        )
        for index, candidate in enumerate(ranked)
    ]


def rank_region_codes(
    region_codes: Iterable[str],
    previous_rows_by_date: RowsByDate | None = None,
    *,
    origin_country: str = "",
    destination_country: str = "",
    manual_region_codes: Iterable[str] = (),
) -> list[str]:
    return [
        candidate.region_code
        for candidate in build_market_candidates(
            region_codes,
            previous_rows_by_date,
            origin_country=origin_country,
            destination_country=destination_country,
            manual_region_codes=manual_region_codes,
        )
    ]


def build_route_candidates(
    origin_points: Sequence[LocationRecord],
    destination_points: Sequence[LocationRecord],
    previous_rows_by_date: RowsByDate | None = None,
) -> list[RouteCandidate]:
    stats = collect_search_stats(previous_rows_by_date)
    raw_candidates: list[RouteCandidate] = []
    for origin_index, origin in enumerate(origin_points):
        for destination_index, destination in enumerate(destination_points):
            route_label = f"{origin.code} -> {destination.code}"
            priority_score = (
                _point_position_score(origin_index) + _point_position_score(destination_index)
            ) / 2
            airport_type_score = (
                _airport_type_score(origin.airport_type)
                + _airport_type_score(destination.airport_type)
            ) / 2
            success_score = stats.route_success_rate.get(route_label, 0.5)
            win_score = stats.route_win_rate.get(route_label, 0.0)
            score = (
                0.42 * priority_score
                + 0.22 * airport_type_score
                + 0.20 * success_score
                + 0.16 * win_score
            )
            raw_candidates.append(
                RouteCandidate(
                    origin_code=origin.code,
                    destination_code=destination.code,
                    origin_label=origin.municipality or origin.name or origin.code,
                    destination_label=destination.municipality
                    or destination.name
                    or destination.code,
                    rank=0,
                    reason=_route_reason(origin_index, destination_index, success_score, win_score),
                    confidence=min(0.95, 0.45 + 0.35 * success_score + 0.20 * priority_score),
                    score=score,
                )
            )
    ranked = sorted(
        raw_candidates,
        key=lambda item: (-item.score, item.origin_code, item.destination_code),
    )
    return [
        RouteCandidate(
            origin_code=candidate.origin_code,
            destination_code=candidate.destination_code,
            origin_label=candidate.origin_label,
            destination_label=candidate.destination_label,
            rank=index + 1,
            reason=candidate.reason,
            confidence=candidate.confidence,
            score=candidate.score,
        )
        for index, candidate in enumerate(ranked)
    ]


def rank_route_pairs(
    origin_points: Sequence[LocationRecord],
    destination_points: Sequence[LocationRecord],
    previous_rows_by_date: RowsByDate | None = None,
    *,
    limit: int | None = None,
) -> list[tuple[LocationRecord, LocationRecord]]:
    origin_by_code = {point.code: point for point in origin_points}
    destination_by_code = {point.code: point for point in destination_points}
    pairs: list[tuple[LocationRecord, LocationRecord]] = []
    for candidate in build_route_candidates(
        origin_points,
        destination_points,
        previous_rows_by_date,
    ):
        origin = origin_by_code.get(candidate.origin_code)
        destination = destination_by_code.get(candidate.destination_code)
        if origin is None or destination is None:
            continue
        pairs.append((origin, destination))
        if limit is not None and len(pairs) >= limit:
            break
    return pairs


def build_scan_tasks(
    routes: Sequence[RouteCandidate],
    dates: Sequence[DateCandidate],
    markets: Sequence[MarketCandidate],
) -> list[ScanTask]:
    tasks: list[ScanTask] = []
    for route in routes:
        for date in dates:
            for market in markets:
                priority = route.score * 0.4 + date.score * 0.25 + market.score * 0.35
                tasks.append(
                    ScanTask(
                        route=route,
                        date=date,
                        market=market,
                        priority=priority,
                        phase=_task_phase(route, date, market),
                        reason=f"{route.reason}；{date.reason}；{market.reason}",
                    )
                )
    return sorted(tasks, key=lambda item: -item.priority)


def _has_price(row: dict[str, object]) -> bool:
    return any(
        isinstance(row.get(key), (int, float))
        for key in ("cheapest_cny_price", "best_cny_price", "price")
    )


def _price_key(row: dict[str, object]) -> tuple[float, float]:
    cheapest = row.get("cheapest_cny_price")
    best = row.get("best_cny_price")
    price = row.get("price")
    return (
        float(cheapest)
        if isinstance(cheapest, (int, float))
        else float(price)
        if isinstance(price, (int, float))
        else float("inf"),
        float(best) if isinstance(best, (int, float)) else float("inf"),
    )


def _rates(successes: dict[str, int], totals: dict[str, int]) -> dict[str, float]:
    return {
        key: successes.get(key, 0) / total
        for key, total in totals.items()
        if total > 0
    }


def _currency_usability_score(region_code: str) -> float:
    currency = REGIONS.get(region_code).currency if region_code in REGIONS else ""
    return {
        "CNY": 1.0,
        "HKD": 0.9,
        "SGD": 0.86,
        "KZT": 0.82,
        "GBP": 0.72,
        "EUR": 0.7,
        "JPY": 0.68,
        "KRW": 0.68,
        "USD": 0.68,
    }.get(currency, 0.55)


def _market_reason(
    code: str,
    baseline_score: float,
    route_score: float,
    manual_score: float,
    win_rate: float,
) -> str:
    reasons: list[str] = []
    if baseline_score:
        reasons.append("baseline 市场")
    if route_score:
        reasons.append("与出发地/目的地相关")
    if manual_score:
        reasons.append("用户手动追加")
    if win_rate > 0:
        reasons.append(f"历史低价胜率 {win_rate:.0%}")
    return "，".join(reasons) if reasons else "候选对照市场"


def _airport_type_score(airport_type: str) -> float:
    return {
        "large_airport": 1.0,
        "medium_airport": 0.78,
        "small_airport": 0.45,
    }.get(airport_type, 0.65)


def _point_position_score(index: int) -> float:
    return 1.0 / (index + 1)


def _route_reason(
    origin_index: int,
    destination_index: int,
    success_score: float,
    win_score: float,
) -> str:
    reasons = [f"机场候选排序 {origin_index + 1}/{destination_index + 1}"]
    if success_score > 0.5:
        reasons.append(f"历史成功率 {success_score:.0%}")
    if win_score > 0:
        reasons.append(f"历史低价胜率 {win_score:.0%}")
    return "，".join(reasons)


def _task_phase(
    route: RouteCandidate,
    date: DateCandidate,
    market: MarketCandidate,
) -> TaskPhase:
    if route.rank <= 2 and date.phase in {"anchor", "edge"} and market.rank <= 4:
        return "probe"
    if date.phase == "full" or route.rank > 5:
        return "deep"
    if market.rank <= 4:
        return "verify"
    return "expand"
