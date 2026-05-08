"""Market/domain wait policy — dynamically adjusts timeouts based on history.

Does NOT change the task set (no early stopping, no market skipping).
Only adjusts wait durations and max region time per domain.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

# ── Data structures ──────────────────────────────────────────────────────────


@dataclass
class WaitPolicy:
    initial_wait: int
    max_region_time: int
    extract_wait_steps: list[int] = field(default_factory=lambda: [0, 8, 15])
    reason: str = "default"

    @property
    def adjusted(self) -> bool:
        return self.reason != "default"


# ── Defaults ─────────────────────────────────────────────────────────────────

DEFAULT_INITIAL_WAIT = 10
DEFAULT_MAX_REGION_TIME = 45
DEFAULT_EXTRACT_WAIT_STEPS = [0, 8, 15]

# ── Thresholds for adjustment ────────────────────────────────────────────────

SLOW_DOMAIN_LOADING_RATE_THRESHOLD = 0.3   # >30% loading/timeout → slow
SLOW_DOMAIN_CHALLENGE_RATE_THRESHOLD = 0.4  # >40% challenge → risky
SLOW_DOMAIN_MIN_SAMPLES = 3                 # need at least 3 samples to adjust


# ── Public API ───────────────────────────────────────────────────────────────


def build_wait_policy(
    *,
    region_code: str,
    domain: str,
    history_telemetry: dict[str, Any] | None = None,
    default_page_wait: int = DEFAULT_INITIAL_WAIT,
) -> WaitPolicy:
    """Build a WaitPolicy for a region, optionally informed by history telemetry.

    The policy only adjusts wait times — it never skips or prunes tasks.
    """
    if history_telemetry is None:
        return WaitPolicy(
            initial_wait=default_page_wait,
            max_region_time=DEFAULT_MAX_REGION_TIME,
            extract_wait_steps=list(DEFAULT_EXTRACT_WAIT_STEPS),
            reason="default (no history)",
        )

    ht = history_telemetry
    domain_stats = _get_domain_stats(ht, domain)
    if domain_stats is None:
        return WaitPolicy(
            initial_wait=default_page_wait,
            max_region_time=DEFAULT_MAX_REGION_TIME,
            extract_wait_steps=list(DEFAULT_EXTRACT_WAIT_STEPS),
            reason="default (insufficient domain history)",
        )

    total = domain_stats.get("total_attempts", 0)
    if total < SLOW_DOMAIN_MIN_SAMPLES:
        return WaitPolicy(
            initial_wait=default_page_wait,
            max_region_time=DEFAULT_MAX_REGION_TIME,
            extract_wait_steps=list(DEFAULT_EXTRACT_WAIT_STEPS),
            reason=f"default (< {SLOW_DOMAIN_MIN_SAMPLES} domain samples)",
        )

    loading_rate = domain_stats.get("loading_timeout_rate", 0.0)
    challenge_rate = domain_stats.get("challenge_rate", 0.0)

    # Slow domain: increase wait and max time
    if loading_rate > SLOW_DOMAIN_LOADING_RATE_THRESHOLD:
        return WaitPolicy(
            initial_wait=default_page_wait + 10,
            max_region_time=75,
            extract_wait_steps=[0, 15, 30],
            reason=f"slow domain (loading/timeout rate {loading_rate:.0%})",
        )

    # Challenge-heavy domain: don't increase retries, mark as risky
    if challenge_rate > SLOW_DOMAIN_CHALLENGE_RATE_THRESHOLD:
        return WaitPolicy(
            initial_wait=default_page_wait,
            max_region_time=DEFAULT_MAX_REGION_TIME,
            extract_wait_steps=list(DEFAULT_EXTRACT_WAIT_STEPS),
            reason=f"challenge-heavy domain ({challenge_rate:.0%}), no extra wait",
        )

    # Fast domain: keep defaults
    return WaitPolicy(
        initial_wait=default_page_wait,
        max_region_time=DEFAULT_MAX_REGION_TIME,
        extract_wait_steps=list(DEFAULT_EXTRACT_WAIT_STEPS),
        reason="default (normal domain performance)",
    )


def normalize_domain(value: str) -> str:
    """Normalize a domain string to bare host without scheme or www. prefix.

    Handles inputs like:
      - "https://www.skyscanner.sg/path"
      - "www.skyscanner.sg"
      - "skyscanner.sg"
      - "www.skyscanner.com.sg/transport/flights/bjs/ala/260520/"
        (no scheme — path is stripped, host is extracted)
    """
    from urllib.parse import urlparse

    if "://" not in value:
        # No scheme — could be "www.example.com" or "www.example.com/path"
        # Strip leading www. and any path component
        host = value.split("/")[0]
        return host.removeprefix("www.")

    parsed = urlparse(value)
    host = parsed.netloc or parsed.path.split("/")[0]
    return host.removeprefix("www.")


def _get_domain_stats(telemetry: dict[str, Any], domain: str) -> dict[str, Any] | None:
    """Extract per-domain statistics from telemetry."""
    per_domain = telemetry.get("per_domain")
    if not isinstance(per_domain, dict):
        return None
    normalized = normalize_domain(domain)
    stats = per_domain.get(normalized)
    if not isinstance(stats, dict):
        # Try fuzzy match
        for key, value in per_domain.items():
            if isinstance(key, str) and normalized in key:
                return value if isinstance(value, dict) else None
    return stats


def collect_domain_telemetry_from_rows(
    rows_by_date: list[tuple[str, list[dict[str, Any]]]],
) -> dict[str, Any]:
    """Build per-domain telemetry from historical scan rows."""
    per_domain: dict[str, dict[str, int]] = {}
    for _trip_label, rows in rows_by_date:
        for row in rows:
            if not isinstance(row, dict):
                continue
            domain_url = str(row.get("link") or row.get("source_url") or "")
            if not domain_url:
                continue
            from urllib.parse import urlparse
            domain = urlparse(domain_url).netloc.replace("www.", "")
            if not domain:
                continue

            stats = per_domain.setdefault(domain, {
                "total_attempts": 0,
                "success_count": 0,
                "loading_timeout_count": 0,
                "challenge_count": 0,
                "no_flights_count": 0,
            })
            stats["total_attempts"] += 1

            status = str(row.get("status") or "").lower()
            has_price = isinstance(row.get("cheapest_cny_price"), (int, float)) or isinstance(
                row.get("best_cny_price"), (int, float),
            )

            if has_price:
                stats["success_count"] += 1
            if "challenge" in status or "captcha" in status:
                stats["challenge_count"] += 1
            if "loading" in status or "timeout" in status:
                stats["loading_timeout_count"] += 1
            if "no_flights" in status:
                stats["no_flights_count"] += 1

    # Compute rates
    result: dict[str, Any] = {"per_domain": {}}
    for domain, stats in per_domain.items():
        total = max(stats["total_attempts"], 1)
        result["per_domain"][domain] = {
            **stats,
            "success_rate": stats["success_count"] / total,
            "loading_timeout_rate": stats["loading_timeout_count"] / total,
            "challenge_rate": stats["challenge_count"] / total,
        }
    return result
