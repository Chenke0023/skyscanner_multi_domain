from __future__ import annotations

from skyscanner_multi_domain.models import FlightQuote
from skyscanner_multi_domain.scan.repair import build_repair_plan, repair_tasks_to_dicts


def test_repair_plan_excludes_successful_markets_and_no_flights() -> None:
    quotes = [
        FlightQuote("CN", "domain", 100.0, "CNY", "url", "ok"),
        FlightQuote("HK", "domain", None, "HKD", "url", "page_parse_failed"),
        FlightQuote("SG", "domain", None, "SGD", "url", "opencli_no_flights"),
    ]

    plan = build_repair_plan(quotes)

    assert [task.region for task in plan.tasks] == ["HK"]
    assert plan.summary["skipped_no_flights"] == 1


def test_repair_plan_marks_challenge_manual_review() -> None:
    quotes = [FlightQuote("CN", "domain", None, "CNY", "url", "page_challenge")]

    plan = build_repair_plan(quotes)

    assert plan.tasks[0].recommended_action == "manual_review"
    assert plan.tasks[0].automatic is False
    assert plan.summary["manual_review_tasks"] == 1


def test_repair_plan_filters_status() -> None:
    quotes = [
        FlightQuote("CN", "domain", None, "CNY", "url", "page_parse_failed"),
        FlightQuote("HK", "domain", None, "HKD", "url", "opencli_timeout"),
    ]

    plan = build_repair_plan(quotes, include_statuses={"opencli_timeout"})

    assert [task.region for task in plan.tasks] == ["HK"]
    assert plan.tasks[0].recommended_action == "retry_with_clean_tab"


def test_repair_tasks_are_serializable() -> None:
    plan = build_repair_plan([FlightQuote("CN", "domain", None, "CNY", "url", "opencli_not_attempted")])

    payload = repair_tasks_to_dicts(plan.tasks)

    assert payload[0]["recommended_action"] == "retry_opencli"
    assert payload[0]["automatic"] is True
