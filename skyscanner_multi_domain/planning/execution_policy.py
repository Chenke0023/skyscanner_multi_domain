from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal

from skyscanner_multi_domain.planning.search_plan import ScanBatch, ScanTask


PolicyMode = Literal["exact", "repair"]


@dataclass(frozen=True)
class ScanExecutionPolicy:
    """Internal execution context.

    Normal searches always execute every planned task. ``repair`` only labels
    failure-only reruns; it does not defer work or expose a user search level.
    """

    mode: PolicyMode = "exact"


EXACT_POLICY = ScanExecutionPolicy(mode="exact")
REPAIR_POLICY = ScanExecutionPolicy(mode="repair")


def apply_execution_policy(
    batches: list[ScanBatch],
    policy: ScanExecutionPolicy = EXACT_POLICY,
) -> tuple[list[ScanBatch], list[ScanTask], dict[str, Any]]:
    planned_tasks = [task for batch in batches for task in batch.tasks]
    return list(batches), [], build_execution_policy_telemetry(
        policy=policy,
        planned_tasks=len(planned_tasks),
        executed_tasks=len(planned_tasks),
        deferred_tasks=0,
        skipped_tasks=0,
        reasons=[],
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
