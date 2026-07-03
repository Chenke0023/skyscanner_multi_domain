"""launchd helpers for background auto-refresh scheduling."""

from __future__ import annotations

import argparse
import plistlib
import subprocess
import sys
from pathlib import Path

from skyscanner_multi_domain.runtime.paths import PROJECT_ROOT, get_log_file

LAUNCHD_LABEL = "com.skyscanner-multi-domain.auto-refresh"
DEFAULT_LAUNCHD_INTERVAL_MINUTES = 600


def launch_agent_path() -> Path:
    return Path.home() / "Library" / "LaunchAgents" / f"{LAUNCHD_LABEL}.plist"


def install_auto_refresh_launchd(args: argparse.Namespace) -> int:
    interval_seconds = max(int(getattr(args, "interval_minutes", DEFAULT_LAUNCHD_INTERVAL_MINUTES)), 1) * 60
    plist_path = launch_agent_path()
    plist_path.parent.mkdir(parents=True, exist_ok=True)
    stdout_log = get_log_file("background_auto_refresh.out.log")
    stderr_log = get_log_file("background_auto_refresh.err.log")
    program_args = [
        sys.executable,
        str(PROJECT_ROOT / "cli.py"),
        "auto-refresh-once",
        "--limit",
        str(max(int(getattr(args, "limit", 1)), 1)),
    ]
    if not bool(getattr(args, "save", True)):
        program_args.append("--no-save")
    if bool(getattr(args, "only_on_ac_power", False)):
        program_args.append("--only-on-ac-power")
    plist = {
        "Label": LAUNCHD_LABEL,
        "ProgramArguments": program_args,
        "WorkingDirectory": str(PROJECT_ROOT),
        "StartInterval": interval_seconds,
        "RunAtLoad": bool(getattr(args, "run_at_load", True)),
        "StandardOutPath": str(stdout_log),
        "StandardErrorPath": str(stderr_log),
        "EnvironmentVariables": {
            "PATH": "/usr/local/bin:/opt/homebrew/bin:/usr/bin:/bin:/usr/sbin:/sbin",
        },
    }
    with plist_path.open("wb") as handle:
        plistlib.dump(plist, handle, sort_keys=False)
    subprocess.run(
        ["launchctl", "unload", str(plist_path)],
        check=False,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    subprocess.run(["launchctl", "load", str(plist_path)], check=False)
    print(f"已安装后台自动复扫 launchd: {plist_path}")
    print(f"调度间隔: {interval_seconds // 60} 分钟；日志: {stdout_log}")
    return 0


def uninstall_auto_refresh_launchd() -> int:
    plist_path = launch_agent_path()
    if plist_path.exists():
        subprocess.run(["launchctl", "unload", str(plist_path)], check=False)
        plist_path.unlink()
        print(f"已卸载后台自动复扫 launchd: {plist_path}")
    else:
        print("未找到后台自动复扫 launchd 配置。")
    return 0
