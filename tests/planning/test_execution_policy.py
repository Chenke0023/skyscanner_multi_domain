from skyscanner_multi_domain.geo.location_resolver import LocationRecord
from skyscanner_multi_domain.planning.execution_policy import (
    EXACT_POLICY,
    REPAIR_POLICY,
    apply_execution_policy,
)
from skyscanner_multi_domain.planning.search_plan import TripIntent, build_search_plan


def _batches():
    plan = build_search_plan(
        TripIntent("北京", "阿拉木图", "2026-05-20", None, False, False, 1, []),
        [LocationRecord(name="Beijing", code="PEK", kind="airport")],
        [LocationRecord(name="Almaty", code="ALA", kind="airport")],
        ["CN", "HK", "KZ"],
    )
    return plan.batches


def test_exact_policy_runs_every_planned_task() -> None:
    batches = _batches()
    execution_batches, deferred, telemetry = apply_execution_policy(batches, EXACT_POLICY)

    assert execution_batches == batches
    assert deferred == []
    assert telemetry["execution_policy_mode"] == "exact"
    assert telemetry["plan_tasks_executed_total"] == telemetry["plan_tasks_planned_total"]
    assert telemetry["plan_tasks_deferred_count"] == 0
    assert telemetry["early_stop_suggested"] is False


def test_repair_policy_does_not_drop_selected_tasks() -> None:
    batches = _batches()
    execution_batches, deferred, telemetry = apply_execution_policy(batches, REPAIR_POLICY)

    assert execution_batches == batches
    assert deferred == []
    assert telemetry["execution_policy_mode"] == "repair"
    assert telemetry["plan_tasks_remaining_count"] == 0
