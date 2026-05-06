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
    "skyscanner_models",
    "skyscanner_page_parser",
    "skyscanner_regions",
    "transport_cdp",
    "transport_opencli",
    "transport_scrapling",
}
SHIM_TARGETS = {
    "app_paths": "skyscanner_multi_domain.runtime.paths",
    "attempt_trace": "skyscanner_multi_domain.diagnostics.attempt_trace",
    "date_window": "skyscanner_multi_domain.planning.date_window",
    "fx_rates": "skyscanner_multi_domain.pricing.fx_rates",
    "location_resolver": "skyscanner_multi_domain.geo.location_resolver",
    "scan_history": "skyscanner_multi_domain.scan.history",
    "scan_orchestrator": "skyscanner_multi_domain.scan.orchestrator",
    "search_plan": "skyscanner_multi_domain.planning.search_plan",
    "skyscanner_models": "skyscanner_multi_domain.models",
    "skyscanner_page_parser": "skyscanner_multi_domain.parsing.page_parser",
    "skyscanner_regions": "skyscanner_multi_domain.geo.regions",
    "transport_cdp": "skyscanner_multi_domain.transports.cdp",
    "transport_opencli": "skyscanner_multi_domain.transports.opencli",
    "transport_scrapling": "skyscanner_multi_domain.transports.scrapling",
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


def test_no_package_module_imports_root_shims() -> None:
    offenders: list[str] = []
    for path in (ROOT / "skyscanner_multi_domain").rglob("*.py"):
        imports = _imports_for(path) & ROOT_SHIMS
        if imports:
            offenders.append(f"{path.relative_to(ROOT)} imports {sorted(imports)}")

    assert offenders == []


def test_desktop_logic_does_not_import_cli() -> None:
    imports = _imports_for(ROOT / "desktop_logic.py")
    assert "cli" not in imports


def test_root_shims_import_same_module_object_and_keep_public_all() -> None:
    for shim_name, target_name in SHIM_TARGETS.items():
        shim = importlib.import_module(shim_name)
        target = importlib.import_module(target_name)
        assert shim is target
        assert all(not name.startswith("_") for name in getattr(shim, "__all__", ()))
