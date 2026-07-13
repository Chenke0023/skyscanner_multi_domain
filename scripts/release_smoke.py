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
GITIGNORE_FILE = PROJECT_ROOT / ".gitignore"
WEBUI_PACKAGE_FILE = PROJECT_ROOT / "webui" / "package.json"
WEBUI_DIST_INDEX = PROJECT_ROOT / "webui" / "dist" / "index.html"
WEBUI_SOURCE_DIR = PROJECT_ROOT / "webui" / "src"
BUILD_SCRIPT = PROJECT_ROOT / "scripts" / "build_macos_standalone_app.sh"
SOURCE_APP_SCRIPT = PROJECT_ROOT / "scripts" / "build_macos_app.sh"
LAUNCH_GUI_SCRIPT = PROJECT_ROOT / "scripts" / "launch_gui.sh"
CI_WORKFLOW_FILE = PROJECT_ROOT / ".github" / "workflows" / "ci.yml"
LEGACY_TK_ENTRYPOINTS = (
    PROJECT_ROOT / "gui.py",
    PROJECT_ROOT / "legacy",
    PROJECT_ROOT / "docs" / "legacy_tk_policy.md",
    PROJECT_ROOT / "tests" / "legacy",
)
REMOVED_PACKAGE_LEGACY_NAMESPACES = (
    PROJECT_ROOT / "skyscanner_multi_domain" / "legacy",
)
REMOVED_FEATURE_PATHS = tuple(
    PROJECT_ROOT / path
    for path in (
        "captcha_solver.py",
        "failure_replay.py",
        "skyscanner_multi_domain/neo.py",
        "skyscanner_multi_domain/diagnostics/snapshots.py",
        "skyscanner_multi_domain/parsing/readiness.py",
        "skyscanner_multi_domain/scan/fallback_router.py",
        "skyscanner_multi_domain/scan/url_builder.py",
        "skyscanner_multi_domain/transports/google_jump.py",
        "skyscanner_multi_domain/transports/opencli.py",
        "skyscanner_multi_domain/transports/scrapling.py",
        "vendor/neo",
        "vendor/ohmycaptcha",
    )
)
REMOVED_FLAT_ROOT_SHIMS = tuple(
    PROJECT_ROOT / f"{name}.py"
    for name in (
        "app_paths",
        "attempt_trace",
        "date_window",
        "fx_rates",
        "location_resolver",
        "scan_history",
        "scan_orchestrator",
        "search_plan",
        "skyscanner_neo",
        "skyscanner_models",
        "skyscanner_page_parser",
        "skyscanner_regions",
        "transport_cdp",
        "transport_opencli",
        "transport_scrapling",
    )
)
LEGACY_TK_FORBIDDEN_TEXT = (
    (PROJECT_ROOT / "desktop_webview.py", "SKYSCANNER_ALLOW_LEGACY_GUI"),
    (PROJECT_ROOT / "desktop_webview.py", "from legacy"),
    (PROJECT_ROOT / "desktop_webview.py", "legacy.gui"),
    (PROJECT_ROOT / "scripts" / "build_macos_standalone_app.sh", "test_gui_features"),
    (PROJECT_ROOT / "scripts" / "build_macos_standalone_app.sh", "test_gui_startup"),
)
REMOVED_ROOT_SHIM_FORBIDDEN_TEXT = (
    (PYPROJECT_FILE, "skyscanner_neo.py"),
    (BUILD_SCRIPT, "skyscanner_neo"),
)
CI_GATE_NEEDLES = (
    "python -m ruff check .",
    "python -m mypy",
    "npm ci",
    "npm test -- --run",
    "npm run build",
    "python scripts/release_smoke.py",
    "python -m pytest -q",
)
BUILD_SIZE_GUARD_NEEDLES = (
    "APP_SIZE_LIMIT=$((150 * 1024 * 1024))",
    "ZIP_SIZE_LIMIT=$((60 * 1024 * 1024))",
    "print_largest_components",
    "FORBIDDEN_PATTERN=",
    'find "${DIST_DIR}" -maxdepth 1 -type f -name "*.zip" -delete',
    'rm -rf "${DIST_DIR}/${APP_NAME}"',
    '"${DIST_DIR}/${APP_NAME}-Standalone"',
    'RELEASE_ZIP="${DIST_DIR}/skyscanner-multi-domain-v${VERSION}-macos-arm64.zip"',
    '/usr/bin/ditto -c -k --keepParent "${APP_BUNDLE}" "${RELEASE_ZIP}"',
    'rm -rf "${PROJECT_ROOT}/build/${APP_NAME}"',
    '"${PROJECT_ROOT}/build/${APP_NAME}-Standalone"',
)
BUILD_RUNTIME_SMOKE_NEEDLES = (
    "SKYSCANNER_GUI_SMOKE_TEST=1",
    'SKYSCANNER_APP_HOME="${SMOKE_HOME}"',
    'chmod 555 "${SMOKE_READONLY}"',
    '"${SMOKE_OUTPUT}" != *"smoke-ok"*',
    '"${SMOKE_HOME}/traces"',
    '"${SMOKE_READONLY}/traces"',
)
DESKTOP_RUNTIME_GUARD_NEEDLES = (
    "_run_desktop_smoke_test",
    "browser-unavailable: no launchable browser",
    "browser-unavailable error was not normalized",
)
SOURCE_LINKED_RUNTIME_NEEDLES = (
    'APP_HOME="${SKYSCANNER_APP_HOME:-${HOME}/Library/Application Support/skyscanner_multi_domain}"',
    'export SKYSCANNER_APP_HOME="${APP_HOME}"',
    'LOG_DIR="${APP_HOME}/logs"',
)
CLI_RUNTIME_GUARD_NEEDLES = (
    "get_traces_dir",
    'trace_dir_arg = getattr(args, "trace_dir", None)',
    "str(get_traces_dir())",
    'default=None,\n        help="Trace JSONL 输出目录 (默认运行时 traces 目录)"',
    'failure_log_dir=getattr(args, "failure_log_dir", None) or None',
    'help="Failure log 输出目录 (默认运行时 logs/failures)"',
)
GENERATED_IGNORE_NEEDLES = (
    ".mypy_cache/",
    ".pytest_cache/",
    "build/",
    "dist/",
    "/runtime/",
    "webui/node_modules/",
    "webui/dist/",
    "webui/.mypy_cache/",
)
WEBUI_STYLE_FORBIDDEN_PATTERNS = (
    ("handwritten SVG", re.compile(r"<svg\b")),
    ("Tailwind tracking utility", re.compile(r"\btracking-")),
    ("decorative loading orbit", re.compile(r"loading-orbit|orbitPulse")),
    ("gradient background", re.compile(r"radial-gradient|linear-gradient")),
    ("viewport-scaled font size", re.compile(r"font-size:[^;]*vw|clamp\(")),
    ("oversized border radius", re.compile(r"border-radius:\s*(?:1[7-9]|[2-9]\d)px|rounded-(?:2xl|3xl)|rounded-\[")),
)


def _iter_webui_source_files(source_dir: Path = WEBUI_SOURCE_DIR) -> list[Path]:
    if not source_dir.exists():
        return []
    return sorted(
        path
        for path in source_dir.rglob("*")
        if path.suffix in {".css", ".ts", ".tsx"}
    )


def collect_webui_style_guardrail_violations(source_dir: Path = WEBUI_SOURCE_DIR) -> list[str]:
    violations: list[str] = []
    for path in _iter_webui_source_files(source_dir):
        relative_path = path.relative_to(PROJECT_ROOT) if path.is_relative_to(PROJECT_ROOT) else path
        source = path.read_text(encoding="utf-8")
        for label, pattern in WEBUI_STYLE_FORBIDDEN_PATTERNS:
            if pattern.search(source):
                violations.append(f"{relative_path}: {label}")
        for match in re.finditer(r"letter-spacing\s*:\s*([^;\"']+)", source):
            if match.group(1).strip() != "0":
                violations.append(f"{relative_path}: non-zero letter spacing")
    return violations


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
    if "SKYSCANNER_ALLOW_LEGACY_GUI" in readme:
        raise RuntimeError(f"{README_FILE} must not document the removed legacy Tk fallback")
    checks.append("README install path")

    gitignore = GITIGNORE_FILE.read_text(encoding="utf-8")
    for needle in GENERATED_IGNORE_NEEDLES:
        if needle not in gitignore:
            raise RuntimeError(f"{GITIGNORE_FILE} must ignore generated growth path: {needle}")
    checks.append("generated file ignore rules")

    existing_legacy = [path for path in LEGACY_TK_ENTRYPOINTS if path.exists()]
    if existing_legacy:
        raise RuntimeError(f"Legacy Tk entrypoints must stay removed: {existing_legacy}")
    for path, forbidden_text in LEGACY_TK_FORBIDDEN_TEXT:
        if forbidden_text in path.read_text(encoding="utf-8"):
            raise RuntimeError(f"{path} must not reference removed legacy Tk fallback text: {forbidden_text}")
    checks.append("legacy Tk entrypoints removed")

    existing_flat_shims = [path for path in REMOVED_FLAT_ROOT_SHIMS if path.exists()]
    if existing_flat_shims:
        raise RuntimeError(f"Flat root compatibility shims must stay removed: {existing_flat_shims}")
    checks.append("flat root shims removed")
    for path, forbidden_text in REMOVED_ROOT_SHIM_FORBIDDEN_TEXT:
        if forbidden_text in path.read_text(encoding="utf-8"):
            raise RuntimeError(f"{path} must not reference removed root shim: {forbidden_text}")
    checks.append("removed root shim references")

    existing_package_legacy = [path for path in REMOVED_PACKAGE_LEGACY_NAMESPACES if path.exists()]
    if existing_package_legacy:
        raise RuntimeError(f"Package legacy namespaces must stay removed: {existing_package_legacy}")
    checks.append("package legacy namespace removed")

    existing_retired = [path for path in REMOVED_FEATURE_PATHS if path.exists()]
    if existing_retired:
        raise RuntimeError(f"Retired feature paths must stay removed: {existing_retired}")
    checks.append("retired feature paths removed")

    build_script = BUILD_SCRIPT.read_text(encoding="utf-8")
    for needle in ('VERSION_FILE="${PROJECT_ROOT}/data/version.txt"', "CFBundleShortVersionString", "CFBundleVersion"):
        if needle not in build_script:
            raise RuntimeError(f"{BUILD_SCRIPT} is missing bundle version handling: {needle}")
    source_app_script = SOURCE_APP_SCRIPT.read_text(encoding="utf-8")
    for needle in ('VERSION_FILE="${PROJECT_ROOT}/data/version.txt"', "<string>${VERSION}</string>"):
        if needle not in source_app_script:
            raise RuntimeError(f"{SOURCE_APP_SCRIPT} is missing bundle version handling: {needle}")
    checks.append("macOS bundle version wiring")

    for needle in (
        "desktop_webview.py",
        '--add-data "webui/dist:webui/dist"',
        "--hidden-import webview.platforms.cocoa",
    ):
        if needle not in build_script:
            raise RuntimeError(f"{BUILD_SCRIPT} is missing required PyInstaller entry/data: {needle}")
    if "--collect-submodules skyscanner_multi_domain" in build_script:
        raise RuntimeError(f"{BUILD_SCRIPT} must not collect the whole application package")
    checks.append("minimal PyInstaller module set")

    for needle in BUILD_SIZE_GUARD_NEEDLES:
        if needle not in build_script:
            raise RuntimeError(f"{BUILD_SCRIPT} is missing generated-size guardrail: {needle}")
    checks.append("release artifact size guardrails")

    for needle in BUILD_RUNTIME_SMOKE_NEEDLES:
        if needle not in build_script:
            raise RuntimeError(f"{BUILD_SCRIPT} is missing packaged runtime smoke guardrail: {needle}")
    checks.append("packaged runtime smoke guardrails")

    desktop_webview = (PROJECT_ROOT / "desktop_webview.py").read_text(encoding="utf-8")
    for needle in DESKTOP_RUNTIME_GUARD_NEEDLES:
        if needle not in desktop_webview:
            raise RuntimeError(f"desktop_webview.py is missing runtime smoke guardrail: {needle}")
    checks.append("desktop runtime smoke guardrails")

    launch_gui = LAUNCH_GUI_SCRIPT.read_text(encoding="utf-8")
    for needle in SOURCE_LINKED_RUNTIME_NEEDLES:
        if needle not in launch_gui:
            raise RuntimeError(f"{LAUNCH_GUI_SCRIPT} is missing source-linked runtime guardrail: {needle}")
    checks.append("source-linked runtime guardrails")

    cli_source = (PROJECT_ROOT / "cli.py").read_text(encoding="utf-8")
    for needle in CLI_RUNTIME_GUARD_NEEDLES:
        if needle not in cli_source:
            raise RuntimeError(f"cli.py is missing runtime path guardrail: {needle}")
    checks.append("CLI runtime path guardrails")

    ci_workflow = CI_WORKFLOW_FILE.read_text(encoding="utf-8")
    for needle in CI_GATE_NEEDLES:
        if needle not in ci_workflow:
            raise RuntimeError(f"{CI_WORKFLOW_FILE} is missing CI gate: {needle}")
    checks.append("CI test gates")

    webui_style_violations = collect_webui_style_guardrail_violations()
    if webui_style_violations:
        raise RuntimeError(f"WebUI style guardrails failed: {webui_style_violations}")
    checks.append("webui style guardrails")

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
