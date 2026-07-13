from __future__ import annotations

import os
from pathlib import Path


BROWSER_BINARY_CANDIDATES = {
    "comet": Path("/Applications/Comet.app/Contents/MacOS/Comet"),
    "chrome": Path("/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"),
    "edge": Path("/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge"),
}

BROWSER_ORDER = ("chrome", "edge", "comet")


def is_launchable_browser_binary(path: Path) -> bool:
    try:
        return path.exists() and path.is_file() and os.access(path, os.X_OK)
    except OSError:
        return False


def detect_browser_binaries() -> dict[str, Path]:
    return {
        name: path
        for name, path in BROWSER_BINARY_CANDIDATES.items()
        if is_launchable_browser_binary(path)
    }


def ordered_browser_names(preferred_browser: str | None = None) -> tuple[str, ...]:
    if not preferred_browser:
        return BROWSER_ORDER
    preferred = preferred_browser.strip().lower()
    return (preferred,) if preferred else BROWSER_ORDER


def first_launchable_browser(preferred_browser: str | None = None) -> tuple[str, Path] | None:
    browsers = detect_browser_binaries()
    for browser_name in ordered_browser_names(preferred_browser):
        binary = browsers.get(browser_name)
        if binary is not None:
            return browser_name, binary
    return None


def is_browser_launch_error(exc: BaseException) -> bool:
    message = str(exc).lower()
    return any(token in message for token in (
        "browser-unavailable",
        "no launchable browser",
        "未检测到浏览器",
        "未找到可启动",
        "browser window not found",
    ))
