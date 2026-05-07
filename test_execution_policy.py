from __future__ import annotations

from skyscanner_multi_domain.models import FlightQuote
from skyscanner_multi_domain.planning.execution_policy import (
    EXACT_POLICY,
    FAST_POLICY,
    ScanExecutionPolicy,
    apply_execution_policy,
    classify_task_deferral_candidate,
    evaluate_early_stop_candidate,
)
from skyscanner_multi_domain.planning.search_plan import (
    DateCandidate,
    MarketCandidate,
    RouteCandidate,
    ScanBatch,
    ScanTask,
)


def _task(region: str, phase: str, priority: float) -> ScanTask:
    return ScanTask(
        route=RouteCandidate("PEK", "ALA", "PEK", "ALA", 1, "route", 1.0, 1.0, {}),
        date=DateCandidate("2026-05-20", None, 0, "anchor", "date", 1.0, {}),
        market=MarketCandidate(region, 1, "market", 1.0, 0.5, 1.0, {}),
        priority=priority,
        phase=phase,
        reason="test",
    )


def test_execution_policy_exact_runs_all_tasks() -> None:
    batches = [
        ScanBatch(1, "probe", [_task("CN", "probe", 0.9)], "probe"),
        ScanBatch(2, "expand", [_task("UK", "expand", 0.4)], "expand"),
    ]

    execution_batches, deferred, telemetry = apply_execution_policy(batches, EXACT_POLICY)

    assert execution_batches == batches
    assert deferred == []
    assert telemetry["execution_policy_mode"] == "exact"
    assert telemetry["plan_tasks_executed_total"] == 2
    assert telemetry["plan_tasks_deferred_count"] == 0


def test_execution_policy_fast_defers_low_rank_tasks() -> None:
    batches = [
        ScanBatch(1, "probe", [_task("CN", "probe", 0.9)], "probe"),
        ScanBatch(2, "expand", [_task("UK", "expand", 0.4)], "expand"),
    ]

    execution_batches, deferred, telemetry = apply_execution_policy(batches, FAST_POLICY)

    assert [batch.phase for batch in execution_batches] == ["probe"]
    assert [task.market.region_code for task in deferred] == ["UK"]
    assert telemetry["plan_tasks_deferred_count"] == 1
    assert telemetry["plan_tasks_skipped_count"] == 0


def test_execution_policy_early_stop_suggestion_requires_minimum_coverage() -> None:
    policy = ScanExecutionPolicy(
        mode="fast",
        allow_early_stop=True,
        min_executed_tasks=2,
        min_verified_markets=2,
        min_high_confidence_prices=1,
    )
    quote = FlightQuote("CN", "domain", 100.0, "CNY", "url", "ok", confidence=0.9)

    decision = evaluate_early_stop_candidate(
        policy=policy,
        completed_quotes=[quote],
        completed_batches=1,
        remaining_tasks=3,
    )

    assert decision.should_suggest_stop is False
    assert decision.reason == "minimum_executed_tasks_not_met"


def test_execution_policy_early_stop_is_suggestion_not_auto_stop() -> None:
    policy = ScanExecutionPolicy(
        mode="fast",
        allow_early_stop=True,
        min_executed_tasks=2,
        min_verified_markets=2,
        min_high_confidence_prices=1,
    )
    quotes = [
        FlightQuote("CN", "domain", 100.0, "CNY", "url", "ok", confidence=0.9),
        FlightQuote("HK", "domain", 120.0, "HKD", "url", "ok", confidence=0.7),
    ]

    decision = evaluate_early_stop_candidate(
        policy=policy,
        completed_quotes=quotes,
        completed_batches=2,
        remaining_tasks=3,
    )

    assert decision.should_suggest_stop is True
    assert decision.can_auto_stop is False


def test_execution_policy_task_deferral_is_explained() -> None:
    decision = classify_task_deferral_candidate(_task("UK", "expand", 0.4), FAST_POLICY)

    assert decision.should_defer is True
    assert decision.reason == "low_priority_task_deferred_in_fast_mode"
    assert decision.evidence["region"] == "UK"
