"""Configuration for the direct CDP scanner."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum


class TransportMode(str, Enum):
    PAGE = "page"
    CDP_STRUCTURED = "cdp_structured"


class CdpMode(str, Enum):
    ATTACH = "attach"
    MANAGED = "managed"
    MANUAL = "manual"


class LowConfidencePolicy(str, Enum):
    SHOW = "show"
    HIDE = "hide"
    ACCEPT_REVIEW = "accept-review"


class ChallengePolicy(str, Enum):
    STOP = "stop"
    MANUAL = "manual"


@dataclass
class ScanConfig:
    transport: TransportMode = TransportMode.PAGE
    cdp_mode: CdpMode = CdpMode.ATTACH
    cdp_host: str = "http://localhost:9222"
    keep_tabs: bool = False
    manual_tabs: dict[str, str] = field(default_factory=dict)
    low_confidence_policy: LowConfidencePolicy = LowConfidencePolicy.ACCEPT_REVIEW
    rankable_confidence: float = 0.80
    review_confidence: float = 0.50
    challenge_policy: ChallengePolicy = ChallengePolicy.STOP
    trace_dir: str | None = None
    no_trace: bool = False
    failure_log_dir: str | None = None
    debug_page_text: bool = False
    output: str = "table"
    output_file: str | None = None
    show_attempts: bool = False
    show_low_confidence: bool = False
