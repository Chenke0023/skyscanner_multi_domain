from __future__ import annotations

import os
import shutil
import sys
from pathlib import Path


APP_SLUG = "skyscanner_multi_domain"
SOURCE_ROOT = Path(__file__).resolve().parent


def _resolve_resource_root() -> Path:
    if getattr(sys, "frozen", False):
        return Path(getattr(sys, "_MEIPASS", SOURCE_ROOT))
    return SOURCE_ROOT


def _resolve_runtime_root() -> Path:
    override = os.environ.get("SKYSCANNER_APP_HOME")
    if override:
        return Path(override).expanduser()

    if getattr(sys, "frozen", False):
        return Path.home() / "Library" / "Application Support" / APP_SLUG

    return SOURCE_ROOT


PROJECT_ROOT = _resolve_resource_root()
APP_HOME_DIR = _resolve_runtime_root()
OUTPUTS_DIR = APP_HOME_DIR / "outputs"
REPORTS_DIR = OUTPUTS_DIR / "reports"
LOGS_DIR = APP_HOME_DIR / "logs"
DATA_DIR = PROJECT_ROOT / "data"
RUNTIME_DIR = APP_HOME_DIR / "runtime"
BROWSER_PROFILES_DIR = RUNTIME_DIR / "browser-profiles"
LEGACY_BROWSER_PROFILE_ROOTS = (
    SOURCE_ROOT / "outputs",
    SOURCE_ROOT / "data" / "browser-profiles",
)


def ensure_runtime_dirs() -> None:
    REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    LOGS_DIR.mkdir(parents=True, exist_ok=True)
    RUNTIME_DIR.mkdir(parents=True, exist_ok=True)
    BROWSER_PROFILES_DIR.mkdir(parents=True, exist_ok=True)


def get_reports_dir() -> Path:
    ensure_runtime_dirs()
    return REPORTS_DIR


def get_log_file(name: str) -> Path:
    ensure_runtime_dirs()
    return LOGS_DIR / name


def get_failure_log_file(name: str) -> Path:
    d = LOGS_DIR / "failures"
    d.mkdir(parents=True, exist_ok=True)
    return d / name


def get_fx_cache_file() -> Path:
    ensure_runtime_dirs()
    return RUNTIME_DIR / "fx_rates_cache.json"


def get_gui_state_file() -> Path:
    ensure_runtime_dirs()
    return RUNTIME_DIR / "gui_last_query.json"


def get_browser_profile_dir(browser_name: str) -> Path:
    ensure_runtime_dirs()
    target = BROWSER_PROFILES_DIR / f"{browser_name}-cdp-profile"
    if target.exists():
        return target

    for legacy_root in LEGACY_BROWSER_PROFILE_ROOTS:
        legacy = legacy_root / f"{browser_name}-cdp-profile"
        if not legacy.exists():
            continue
        try:
            shutil.move(str(legacy), str(target))
            return target
        except OSError:
            return legacy

    return target
