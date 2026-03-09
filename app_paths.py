from __future__ import annotations

import shutil
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parent
OUTPUTS_DIR = PROJECT_ROOT / "outputs"
REPORTS_DIR = OUTPUTS_DIR / "reports"
LOGS_DIR = PROJECT_ROOT / "logs"
DATA_DIR = PROJECT_ROOT / "data"
BROWSER_PROFILES_DIR = DATA_DIR / "browser-profiles"
LEGACY_BROWSER_PROFILES_ROOT = OUTPUTS_DIR


def ensure_runtime_dirs() -> None:
    REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    LOGS_DIR.mkdir(parents=True, exist_ok=True)
    BROWSER_PROFILES_DIR.mkdir(parents=True, exist_ok=True)


def get_reports_dir() -> Path:
    ensure_runtime_dirs()
    return REPORTS_DIR


def get_log_file(name: str) -> Path:
    ensure_runtime_dirs()
    return LOGS_DIR / name


def get_browser_profile_dir(browser_name: str) -> Path:
    ensure_runtime_dirs()
    target = BROWSER_PROFILES_DIR / f"{browser_name}-cdp-profile"
    legacy = LEGACY_BROWSER_PROFILES_ROOT / f"{browser_name}-cdp-profile"

    if target.exists():
        return target
    if not legacy.exists():
        return target

    try:
        shutil.move(str(legacy), str(target))
        return target
    except OSError:
        return legacy
