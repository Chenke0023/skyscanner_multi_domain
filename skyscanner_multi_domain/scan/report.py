"""Scan result output formatters: table (terminal), json, jsonl.

Each formatter takes a list of FlightQuote and optional metadata and
returns a string.  No side effects — callers decide where to print/write.
"""

from __future__ import annotations

import json
from dataclasses import asdict
from typing import Any


def _best_rankable(quotes: list[Any]) -> Any | None:
    """Return the cheapest rankable quote, or None."""
    priced = [
        q for q in quotes
        if q.price is not None and getattr(q, "rankable", None) is not False
    ]
    if not priced:
        return None
    return min(priced, key=lambda q: q.price or float("inf"))


def _attempt_summary(quote: Any) -> str:
    """Human-readable attempt chain summary."""
    history = getattr(quote, "attempt_history", None) or []
    if not history:
        return "-"
    transports = [h.get("transport", "?") for h in history]
    return " → ".join(transports)


def _attempt_count(quote: Any) -> int:
    history = getattr(quote, "attempt_history", None) or []
    return len(history) if history else 1


def _confidence_str(quote: Any) -> str:
    conf = getattr(quote, "confidence", None)
    if conf is None:
        return "-"
    return f"{conf:.2f}"


def _rankable_str(quote: Any) -> str:
    rankable = getattr(quote, "rankable", None)
    if rankable is None:
        return "-"
    return "yes" if rankable else "no"


def format_table(
    quotes: list[Any],
    *,
    scan_id: str = "",
    route_label: str = "",
    show_attempts: bool = False,
    show_low_confidence: bool = False,
) -> str:
    """Render a terminal-friendly table.

    Filters out debug_only quotes unless show_low_confidence is True.
    """
    visible = [
        q for q in quotes
        if show_low_confidence or getattr(q, "result_visibility", None) != "debug_only"
    ]

    lines: list[str] = []
    if route_label:
        lines.append(f"Route: {route_label}")
    if scan_id:
        lines.append(f"Scan ID: {scan_id}")
    lines.append("")

    if not visible:
        lines.append("(no results)")
        return "\n".join(lines)

    header = f"{'Region':<8}{'Price':<12}{'Conf':<8}{'Rankable':<10}{'Transport':<12}{'Attempts':<10}{'Status':<18}"
    lines.append(header)
    lines.append("-" * len(header))

    for q in visible:
        price_text = f"{q.price:,.2f}" if q.price is not None else "-"
        currency = getattr(q, "currency", None) or ""
        transport = getattr(q, "source_kind", None) or "-"
        lines.append(
            f"{q.region:<8}{price_text:<12}{_confidence_str(q):<8}"
            f"{_rankable_str(q):<10}{transport:<12}"
            f"{_attempt_count(q):<10}{q.status:<18}"
        )

    best = _best_rankable(quotes)
    if best and best.price is not None:
        currency = getattr(best, "currency", "") or ""
        transport = getattr(best, "source_kind", "") or ""
        fallback_text = ""
        count = _attempt_count(best)
        if count > 1:
            fallback_text = f", after {count - 1} fallback(s)"

        lines.append("")
        lines.append(
            f"Best rankable: {best.region} {best.price:,.2f} {currency}"
            f" via {transport}{fallback_text}"
        )

    if show_attempts:
        lines.append("")
        lines.append("─" * 72)
        lines.append("Attempt Details")
        lines.append("─" * 72)
        for q in visible:
            history = getattr(q, "attempt_history", None) or []
            if not history:
                continue
            lines.append(f"\n{q.region}:")
            for h in history:
                status = h.get("status", "?")
                action = h.get("action", "?")
                reason = h.get("reason", "")
                attempt_idx = h.get("attempt_index", "?")
                transport = h.get("transport", "?")
                elapsed = h.get("elapsed_ms", "")
                elapsed_str = f" {elapsed}ms" if elapsed else ""
                lines.append(
                    f"  {attempt_idx}. {transport:<14} {status:<22} {action:<22}"
                    f"{reason[:40]}{elapsed_str}"
                )

    return "\n".join(lines)


def format_json(
    quotes: list[Any],
    *,
    scan_id: str = "",
    route_label: str = "",
) -> str:
    """Render a structured JSON object suitable for program consumption."""
    best = _best_rankable(quotes)

    result: dict[str, Any] = {
        "scan_id": scan_id,
        "route_label": route_label,
        "best_rankable": None,
        "quotes": [],
    }

    if best is not None:
        result["best_rankable"] = {
            "region": best.region,
            "price": best.price,
            "currency": getattr(best, "currency", None),
            "confidence": getattr(best, "confidence", None),
            "transport": getattr(best, "source_kind", None),
            "attempts": _attempt_count(best),
        }

    for q in quotes:
        entry: dict[str, Any] = {
            "region": q.region,
            "domain": getattr(q, "domain", ""),
            "price": q.price,
            "currency": getattr(q, "currency", None),
            "source_url": getattr(q, "source_url", ""),
            "status": q.status,
            "confidence": getattr(q, "confidence", None),
            "rankable": getattr(q, "rankable", None),
            "result_visibility": getattr(q, "result_visibility", None),
            "source_kind": getattr(q, "source_kind", None),
            "error": getattr(q, "error", None),
            "attempt_history": getattr(q, "attempt_history", []),
            "parser_warnings": getattr(q, "parser_warnings", []),
        }
        result["quotes"].append(entry)

    return json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True)


def format_jsonl(quotes: list[Any], *, scan_id: str = "") -> str:
    """Render one JSON object per line."""
    lines: list[str] = []
    for q in quotes:
        entry: dict[str, Any] = {
            "scan_id": scan_id,
            "region": q.region,
            "domain": getattr(q, "domain", ""),
            "price": q.price,
            "currency": getattr(q, "currency", None),
            "status": q.status,
            "confidence": getattr(q, "confidence", None),
            "rankable": getattr(q, "rankable", None),
            "source_kind": getattr(q, "source_kind", None),
            "attempt_count": _attempt_count(q),
        }
        lines.append(json.dumps(entry, ensure_ascii=False, sort_keys=True))
    return "\n".join(lines)


def format_output(
    quotes: list[Any],
    *,
    output: str = "table",
    scan_id: str = "",
    route_label: str = "",
    show_attempts: bool = False,
    show_low_confidence: bool = False,
) -> str:
    """Dispatch to the right formatter based on output mode."""
    if output == "json":
        return format_json(quotes, scan_id=scan_id, route_label=route_label)
    if output == "jsonl":
        return format_jsonl(quotes, scan_id=scan_id)
    return format_table(
        quotes,
        scan_id=scan_id,
        route_label=route_label,
        show_attempts=show_attempts,
        show_low_confidence=show_low_confidence,
    )