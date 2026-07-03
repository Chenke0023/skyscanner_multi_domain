from __future__ import annotations

import argparse
import json
import re
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
VERSION_FILE = PROJECT_ROOT / "data" / "version.txt"
PYPROJECT_FILE = PROJECT_ROOT / "pyproject.toml"
README_FILE = PROJECT_ROOT / "README.md"
CHANGELOG_FILE = PROJECT_ROOT / "CHANGELOG.md"
WEBUI_PACKAGE_FILE = PROJECT_ROOT / "webui" / "package.json"
WEBUI_DIST_INDEX = PROJECT_ROOT / "webui" / "dist" / "index.html"
BUILD_SCRIPT = PROJECT_ROOT / "scripts" / "build_macos_standalone_app.sh"
SPEC_FILE = PROJECT_ROOT / "Skyscanner 多市场比价.spec"


def read_release_version() -> str:
    version = VERSION_FILE.read_text(encoding="utf-8").strip()
    if not re.fullmatch(r"\d+\.\d+\.\d+", version):
        raise RuntimeError(f"Invalid release version in {VERSION_FILE}: {version!r}")
    return version


def run_release_smoke(*, require_webui_dist: bool = True) -> list[str]:
    version = read_release_version()
    checks: list[str] = []

    pyproject = PYPROJECT_FILE.read_text(encoding="utf-8")
    if f'version = "{version}"' not in pyproject:
        raise RuntimeError(f"{PYPROJECT_FILE} version must match {VERSION_FILE} ({version})")
    checks.append(f"pyproject version {version}")

    package = json.loads(WEBUI_PACKAGE_FILE.read_text(encoding="utf-8"))
    if package.get("version") != version:
        raise RuntimeError(f"{WEBUI_PACKAGE_FILE} version must match {VERSION_FILE} ({version})")
    checks.append(f"webui package version {version}")

    changelog = CHANGELOG_FILE.read_text(encoding="utf-8")
    if f"## {version} " not in changelog:
        raise RuntimeError(f"{CHANGELOG_FILE} must contain a section for {version}")
    checks.append("changelog entry")

    readme = README_FILE.read_text(encoding="utf-8")
    for needle in ("快速安装 / 运行", "scripts/build_macos_standalone_app.sh", "python3 desktop_webview.py"):
        if needle not in readme:
            raise RuntimeError(f"{README_FILE} is missing release/install text: {needle}")
    checks.append("README install path")

    build_script = BUILD_SCRIPT.read_text(encoding="utf-8")
    for needle in ('VERSION_FILE="${PROJECT_ROOT}/data/version.txt"', "CFBundleShortVersionString", "CFBundleVersion"):
        if needle not in build_script:
            raise RuntimeError(f"{BUILD_SCRIPT} is missing bundle version handling: {needle}")
    checks.append("macOS bundle version wiring")

    spec_text = SPEC_FILE.read_text(encoding="utf-8")
    for needle in ("desktop_webview.py", "webui/dist", "collect_submodules('skyscanner_multi_domain')"):
        if needle not in spec_text:
            raise RuntimeError(f"{SPEC_FILE} is missing required PyInstaller entry/data: {needle}")
    checks.append("PyInstaller spec data")

    if require_webui_dist and not WEBUI_DIST_INDEX.exists():
        raise RuntimeError(f"Missing built frontend asset: {WEBUI_DIST_INDEX}")
    if require_webui_dist:
        checks.append("webui dist asset")

    return checks


def main() -> int:
    parser = argparse.ArgumentParser(description="Validate release metadata and macOS app build prerequisites.")
    parser.add_argument("--skip-webui-dist", action="store_true", help="Do not require webui/dist/index.html")
    args = parser.parse_args()
    checks = run_release_smoke(require_webui_dist=not args.skip_webui_dist)
    print("Release smoke passed:")
    for check in checks:
        print(f"- {check}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
