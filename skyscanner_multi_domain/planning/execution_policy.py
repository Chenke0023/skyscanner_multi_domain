from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal

from skyscanner_multi_domain.models import FlightQuote
from skyscanner_multi_domain.planning.search_plan import ScanBatch, ScanTask


PolicyMode = Literal["exact", "fast", "repair"]


@dataclass(frozen=True)
class ScanExecutionPolicy:
    mode: PolicyMode = "exact"
    allow_task_deferral: bool = False
    allow_task_skip: bool = False
    allow_early_stop: bool = False
    min_executed_tasks: int = 0
    min_verified_markets: int = 0
    min_high_confidence_prices: int = 0
    stop_when_price_below_cny: float | None = None
    stop_when_spread_over_runner_up_cny: float | None = None
    stop_after_no_improvement_batches: int | None = None
    require_user_visible_explanation: bool = True


EXACT_POLICY = ScanExecutionPolicy(mode="exact")
FAST_POLICY = ScanExecutionPolicy(
    mode="fast",
    allow_task_deferral=True,
    allow_task_skip=False,
    allow_early_stop=False,
    min_executed_tasks=8,
    min_verified_markets=4,
    min_high_confidence_prices=1,
    stop_after_no_improvement_batches=2,
)
REPAIR_POLICY = ScanExecutionPolicy(mode="repair")


@dataclass(frozen=True)
class EarlyStopDecision:
    should_suggest_stop: bool
    can_auto_stop: bool
    reason: str
    evidence: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class TaskDeferralDecision:
    should_defer: bool
    reason: str
    evidence: dict[str, Any] = field(default_factory=dict)


def apply_execution_policy(
    batches: list[ScanBatch],
    policy: ScanExecutionPolicy = EXACT_POLICY,
) -> tuple[list[ScanBatch], list[ScanTask], dict[str, Any]]:
    planned_tasks = [task for batch in batches for task in batch.tasks]
    if policy.mode == "exact" or not policy.allow_task_deferral:
        return list(batches), [], build_execution_policy_telemetry(
            policy=policy,
            planned_tasks=len(planned_tasks),
            executed_tasks=len(planned_tasks),
            deferred_tasks=0,
            skipped_tasks=0,
            reasons=[],
        )

    execution_batches: list[ScanBatch] = []
    deferred_tasks: list[ScanTask] = []
    reasons: list[str] = []
    for batch in batches:
        if batch.phase in {"probe", "quick", "anchor"}:
            execution_batches.append(batch)
            continue
        deferred_tasks.extend(batch.tasks)
        reasons.append(f"deferred_{batch.phase}_batch")

    executed = sum(len(batch.tasks) for batch in execution_batches)
    return execution_batches, deferred_tasks, build_execution_policy_telemetry(
        policy=policy,
        planned_tasks=len(planned_tasks),
        executed_tasks=executed,
        deferred_tasks=len(deferred_tasks),
        skipped_tasks=0,
        reasons=reasons,
    )


def classify_task_deferral_candidate(
    task: ScanTask,
    policy: ScanExecutionPolicy = EXACT_POLICY,
) -> TaskDeferralDecision:
    if policy.mode == "exact" or not policy.allow_task_deferral:
        return TaskDeferralDecision(False, "exact_mode_runs_all_tasks")
    if task.phase in {"probe", "quick", "anchor"}:
        return TaskDeferralDecision(False, "high_priority_task")
    return TaskDeferralDecision(
        True,
        "low_priority_task_deferred_in_fast_mode",
        {"phase": task.phase, "priority": task.priority, "region": task.market.region_code},
    )


def evaluate_early_stop_candidate(
    *,
    policy: ScanExecutionPolicy,
    completed_quotes: list[FlightQuote],
    completed_batches: int,
    remaining_tasks: int,
) -> EarlyStopDecision:
    if policy.mode == "exact" or not policy.allow_early_stop:
        return EarlyStopDecision(False, False, "early_stop_disabled")

    priced = [quote for quote in completed_quotes if quote.price is not None]
    high_confidence = [
        quote
        for quote in priced
        if isinstance(quote.confidence, (int, float)) and quote.confidence >= 0.85
    ]
    verified_markets = {quote.region for quote in priced}
    executed_tasks = len(completed_quotes)
    evidence = {
        "executed_tasks": executed_tasks,
        "verified_markets": len(verified_markets),
        "high_confidence_prices": len(high_confidence),
        "completed_batches": completed_batches,
        "remaining_tasks": remaining_tasks,
    }
    if executed_tasks < policy.min_executed_tasks:
        return EarlyStopDecision(False, False, "minimum_executed_tasks_not_met", evidence)
    if len(verified_markets) < policy.min_verified_markets:
        return EarlyStopDecision(False, False, "minimum_verified_markets_not_met", evidence)
    if len(high_confidence) < policy.min_high_confidence_prices:
        return EarlyStopDecision(False, False, "minimum_high_confidence_prices_not_met", evidence)

    if policy.stop_when_price_below_cny is not None:
        best_price = min((quote.price for quote in priced if quote.price is not None), default=None)
        evidence["best_price"] = best_price
        if best_price is None or best_price > policy.stop_when_price_below_cny:
            return EarlyStopDecision(False, False, "price_threshold_not_met", evidence)

    return EarlyStopDecision(
        True,
        False,
        "fast_mode_stop_candidate_requires_user_confirmation",
        evidence,
    )


def build_execution_policy_telemetry(
    *,
    policy: ScanExecutionPolicy,
    planned_tasks: int,
    executed_tasks: int,
    deferred_tasks: int,
    skipped_tasks: int,
    reasons: list[str],
    early_stop_suggested: bool = False,
    early_stop_triggered: bool = False,
) -> dict[str, Any]:
    return {
        "execution_policy_mode": policy.mode,
        "plan_tasks_planned_total": planned_tasks,
        "plan_tasks_executed_total": executed_tasks,
        "plan_tasks_deferred_count": deferred_tasks,
        "plan_tasks_skipped_count": skipped_tasks,
        "plan_tasks_remaining_count": max(planned_tasks - executed_tasks - skipped_tasks, 0),
        "early_stop_suggested": bool(early_stop_suggested),
        "early_stop_triggered": bool(early_stop_triggered),
        "execution_policy_reasons": list(reasons),
    }
