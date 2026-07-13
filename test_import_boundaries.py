from __future__ import annotations

import ast
import importlib
from pathlib import Path


ROOT = Path(__file__).resolve().parent
ROOT_SHIMS = {
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
}
SHIM_TARGETS_REMOVED = """
The root-level flat compatibility shims (app_paths, date_window, fx_rates,
location_resolver, scan_history, scan_orchestrator, search_plan,
skyscanner_models, skyscanner_page_parser, skyscanner_regions,
transport_cdp, transport_opencli, transport_scrapling, attempt_trace,
skyscanner_neo)
have been removed. Active code now imports from skyscanner_multi_domain.*
directly. ROOT_SHIMS is kept as a deny-list: package code must never
re-introduce imports of these flat root names.
"""
DOCUMENTED_PACKAGE_MODULES = {
    "skyscanner_multi_domain.diagnostics.attempt_trace",
    "skyscanner_multi_domain.geo.location_resolver",
    "skyscanner_multi_domain.geo.regions",
    "skyscanner_multi_domain.models",
    "skyscanner_multi_domain.parsing.page_parser",
    "skyscanner_multi_domain.parsing.price_candidates",
    "skyscanner_multi_domain.planning.execution_policy",
    "skyscanner_multi_domain.planning.date_window",
    "skyscanner_multi_domain.planning.search_plan",
    "skyscanner_multi_domain.pricing.fx_rates",
    "skyscanner_multi_domain.runtime.launchd",
    "skyscanner_multi_domain.runtime.paths",
    "skyscanner_multi_domain.scan.history",
    "skyscanner_multi_domain.scan.orchestrator",
    "skyscanner_multi_domain.scan.output_rows",
    "skyscanner_multi_domain.scan.query_service",
    "skyscanner_multi_domain.scan.repair",
    "skyscanner_multi_domain.scan.result_service",
    "skyscanner_multi_domain.transports.cdp",
}


def _imports_for(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    imports: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imports.update(alias.name.split(".", 1)[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imports.add(node.module.split(".", 1)[0])
    return imports


def _import_modules_for(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    modules: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            modules.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            modules.add(node.module)
    return modules


def test_no_package_module_imports_root_shims() -> None:
    offenders: list[str] = []
    for path in (ROOT / "skyscanner_multi_domain").rglob("*.py"):
        imports = _imports_for(path) & ROOT_SHIMS
        if imports:
            offenders.append(f"{path.relative_to(ROOT)} imports {sorted(imports)}")

    assert offenders == []


def test_removed_flat_root_shim_files_stay_removed() -> None:
    assert [name for name in sorted(ROOT_SHIMS) if (ROOT / f"{name}.py").exists()] == []


def test_desktop_logic_does_not_import_cli() -> None:
    imports = _imports_for(ROOT / "desktop_logic.py")
    assert "cli" not in imports


def test_desktop_ui_service_no_longer_imports_cli() -> None:
    imports = _imports_for(ROOT / "desktop_ui_service.py")
    assert "cli" not in imports
    assert "skyscanner_neo" not in imports
    handoff = (ROOT / "AI_AGENT_HANDOFF.md").read_text(encoding="utf-8")
    todo = (ROOT / "docs" / "todo.md").read_text(encoding="utf-8")
    assert "desktop_ui_service -> cli.SimpleCLI" in handoff
    assert "desktop_ui_service -> cli.SimpleCLI" in todo
    assert "ResultService" in handoff
    assert "ResultService" in todo


def test_retired_package_modules_are_not_importable() -> None:
    retired = (
        "skyscanner_multi_domain.neo",
        "skyscanner_multi_domain.parsing.readiness",
        "skyscanner_multi_domain.scan.fallback_router",
        "skyscanner_multi_domain.scan.url_builder",
        "skyscanner_multi_domain.transports.google_jump",
        "skyscanner_multi_domain.transports.opencli",
        "skyscanner_multi_domain.transports.scrapling",
        "captcha_solver",
        "failure_replay",
    )
    for module_name in retired:
        try:
            importlib.import_module(module_name)
        except ModuleNotFoundError:
            continue
        raise AssertionError(f"retired module remains importable: {module_name}")

def test_simple_cli_does_not_reintroduce_thin_service_wrappers() -> None:
    tree = ast.parse((ROOT / "cli.py").read_text(encoding="utf-8"))
    simple_cli = next(
        node
        for node in tree.body
        if isinstance(node, ast.ClassDef) and node.name == "SimpleCLI"
    )
    methods = {
        node.name
        for node in simple_cli.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    }

    assert methods.isdisjoint(
        {
            "normalize_location",
            "resolve_location",
            "resolve_country",
            "build_country_route_plan",
            "build_expanded_route_plan",
            "build_effective_regions",
            "to_cny",
            "pick_better_row",
            "rows_to_quote_snapshots",
            "save_combined_results",
            "save_results",
            "save_simplified_results",
            "save_window_results",
            "simplify_quotes",
            "sort_simplified_rows",
        }
    )


def test_package_legacy_namespace_stays_removed() -> None:
    assert not (ROOT / "skyscanner_multi_domain" / "legacy").exists()


def test_runtime_paths_importable() -> None:
    paths = importlib.import_module("skyscanner_multi_domain.runtime.paths")
    assert paths.PROJECT_ROOT is not None


def test_primary_entries_importable() -> None:
    for module_name in ("cli", "desktop_logic", "desktop_ui_service", "desktop_webview"):
        importlib.import_module(module_name)


def test_documented_package_modules_exist() -> None:
    for module_name in sorted(DOCUMENTED_PACKAGE_MODULES):
        importlib.import_module(module_name)


def test_root_legacy_tk_entrypoints_stay_removed() -> None:
    assert not (ROOT / "gui.py").exists()
    assert not (ROOT / "legacy").exists()


def test_desktop_webview_does_not_reintroduce_legacy_tk_fallback() -> None:
    path = ROOT / "desktop_webview.py"
    source = path.read_text(encoding="utf-8")
    modules = _import_modules_for(path)

    assert "legacy" not in modules
    assert "legacy.gui" not in modules
    assert "SKYSCANNER_ALLOW_LEGACY_GUI" not in source
