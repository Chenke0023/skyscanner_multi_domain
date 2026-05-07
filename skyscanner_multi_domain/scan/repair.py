from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from skyscanner_multi_domain.models import FlightQuote


AUTO_REPAIR_STATUSES = {
    "page_parse_failed",
    "opencli_parse_failed",
    "page_text_embedded_recovered",
    "opencli_timeout",
    "page_loading",
    "opencli_error",
    "opencli_failed",
    "opencli_not_attempted",
}
NO_REPAIR_STATUSES = {"opencli_no_flights", "page_no_flights", "no_flights"}
CHALLENGE_STATUSES = {"px_challenge", "page_challenge", "captcha_solve_failed"}


@dataclass(frozen=True)
class RepairTask:
    region: str
    route_label: str
    date_label: str
    url: str
    original_status: str
    original_failure_class: str
    recommended_action: str
    automatic: bool = True


@dataclass(frozen=True)
class RepairPlan:
    tasks: list[RepairTask]
    summary: dict[str, Any] = field(default_factory=dict)


def build_repair_plan(
    quotes: list[FlightQuote] | list[dict[str, Any]],
    *,
    include_statuses: set[str] | None = None,
    include_failure_classes: set[str] | None = None,
) -> RepairPlan:
    tasks: list[RepairTask] = []
    skipped_no_flights = 0
    for quote in quotes:
        data = _quote_to_dict(quote)
        if _has_price(data):
            continue
        status = str(data.get("status") or "")
        failure_class = normalize_repair_failure_class(status, str(data.get("failure_class") or ""))
        if status in NO_REPAIR_STATUSES or failure_class == "no_flights":
            skipped_no_flights += 1
            continue
        if include_statuses is not None and status not in include_statuses:
            continue
        if include_failure_classes is not None and failure_class not in include_failure_classes:
            continue
        action, automatic = recommended_repair_action(failure_class, status)
        tasks.append(
            RepairTask(
                region=str(data.get("region") or data.get("region_code") or ""),
                route_label=str(data.get("route") or ""),
                date_label=str(data.get("date") or ""),
                url=str(data.get("source_url") or data.get("link") or ""),
                original_status=status,
                original_failure_class=failure_class,
                recommended_action=action,
                automatic=automatic,
            )
        )
    action_counts: dict[str, int] = {}
    class_counts: dict[str, int] = {}
    for task in tasks:
        action_counts[task.recommended_action] = action_counts.get(task.recommended_action, 0) + 1
        class_counts[task.original_failure_class] = class_counts.get(task.original_failure_class, 0) + 1
    return RepairPlan(
        tasks=tasks,
        summary={
            "total_repair_tasks": len(tasks),
            "automatic_repair_tasks": sum(1 for task in tasks if task.automatic),
            "manual_review_tasks": sum(1 for task in tasks if not task.automatic),
            "skipped_no_flights": skipped_no_flights,
            "by_action": action_counts,
            "by_failure_class": class_counts,
        },
    )


def normalize_repair_failure_class(status: str, failure_class: str = "") -> str:
    normalized = status.lower()
    if failure_class:
        mapped = {
            "parse": "parse_failed",
            "loading": "still_loading",
            "network": "network_error",
            "challenge_px": "challenge",
            "challenge_cf": "challenge",
        }.get(failure_class, failure_class)
        if mapped:
            return mapped
    if normalized in CHALLENGE_STATUSES or "challenge" in normalized:
        return "challenge"
    if normalized in NO_REPAIR_STATUSES:
        return "no_flights"
    if "timeout" in normalized:
        return "timeout"
    if "loading" in normalized:
        return "still_loading"
    if "not_attempted" in normalized:
        return "not_attempted"
    if "empty" in normalized:
        return "empty_shell"
    if "unknown_parse_surface" in normalized:
        return "unknown_parse_surface"
    if "parse" in normalized:
        return "parse_failed"
    if "error" in normalized or "failed" in normalized:
        return "network_error"
    return "other"


def recommended_repair_action(failure_class: str, status: str = "") -> tuple[str, bool]:
    if failure_class == "parse_failed":
        return "retry_with_cdp_dom", True
    if failure_class == "unknown_parse_surface":
        return "retry_with_candidates_and_cdp", True
    if failure_class == "timeout":
        return "retry_with_clean_tab", True
    if failure_class == "empty_shell":
        return "wait_then_reextract", True
    if failure_class == "still_loading":
        return "wait_then_reextract", True
    if failure_class == "not_attempted":
        return "retry_opencli", True
    if failure_class == "challenge":
        return "manual_review", False
    if failure_class == "no_flights":
        return "do_not_retry", False
    return "retry_opencli", True


def repair_tasks_to_dicts(tasks: list[RepairTask]) -> list[dict[str, Any]]:
    return [
        {
            "region": task.region,
            "route_label": task.route_label,
            "date_label": task.date_label,
            "url": task.url,
            "original_status": task.original_status,
            "original_failure_class": task.original_failure_class,
            "recommended_action": task.recommended_action,
            "automatic": task.automatic,
        }
        for task in tasks
    ]


def _quote_to_dict(quote: FlightQuote | dict[str, Any]) -> dict[str, Any]:
    if isinstance(quote, dict):
        return dict(quote)
    return {
        "region": quote.region,
        "domain": quote.domain,
        "price": quote.price,
        "best_price": quote.best_price,
        "cheapest_price": quote.cheapest_price,
        "source_url": quote.source_url,
        "status": quote.status,
    }


def _has_price(quote: dict[str, Any]) -> bool:
    return any(isinstance(quote.get(key), (int, float)) for key in ("price", "best_price", "cheapest_price"))
