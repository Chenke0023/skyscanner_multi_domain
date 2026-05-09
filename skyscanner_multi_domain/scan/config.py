"""Scan configuration: transport mode, CDP mode, confidence policy, challenge policy.

A single ScanConfig object carries all user-facing choices through the
orchestrator, planner, and trust policy.  CLI flags map to this dataclass;
nothing in the scan pipeline reads CLI flags directly.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum


class TransportMode(str, Enum):
    AUTO = "auto"
    OPENCLI = "opencli"
    CDP = "cdp"
    SCRAPLING = "scrapling"


class CdpMode(str, Enum):
    ATTACH = "attach"
    MANAGED = "managed"
    MANUAL = "manual"


class LowConfidencePolicy(str, Enum):
    FALLBACK = "fallback"
    SHOW = "show"
    HIDE = "hide"
    ACCEPT_REVIEW = "accept-review"


class ChallengePolicy(str, Enum):
    STOP = "stop"
    MANUAL = "manual"


@dataclass
class ScanConfig:
    """All user-facing scan options in one place.

    Defaults match the existing production behavior so existing callers
    (desktop UI, background refresh) are unaffected until they opt in.
    """

    # ── Transport ─────────────────────────────────────────────────────────
    transport: TransportMode = TransportMode.AUTO

    # ── CDP ───────────────────────────────────────────────────────────────
    cdp_mode: CdpMode = CdpMode.ATTACH
    cdp_host: str = "http://localhost:9222"
    keep_tabs: bool = False
    manual_tabs: dict[str, str] = field(default_factory=dict)

    # ── Confidence / trust policy ─────────────────────────────────────────
    low_confidence_policy: LowConfidencePolicy = LowConfidencePolicy.FALLBACK
    rankable_confidence: float = 0.80
    review_confidence: float = 0.50

    # ── Challenge ─────────────────────────────────────────────────────────
    challenge_policy: ChallengePolicy = ChallengePolicy.STOP

    # ── Trace / debug ─────────────────────────────────────────────────────
    trace_dir: str | None = "traces"
    no_trace: bool = False
    failure_log_dir: str | None = "failures"
    debug_page_text: bool = False

    # ── Output ────────────────────────────────────────────────────────────
    output: str = "table"
    output_file: str | None = None
    show_attempts: bool = False
    show_low_confidence: bool = False