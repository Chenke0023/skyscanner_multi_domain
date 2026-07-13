from __future__ import annotations

import ast
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
EXCLUDED_PARTS = {
    ".git",
    ".mypy_cache",
    ".pytest_cache",
    ".ruff_cache",
    "build",
    "dist",
    "htmlcov",
    "node_modules",
    "vendor",
    "webui",
}
ALLOWED_BROAD_EXCEPTION_BOUNDARIES = {
    ("desktop_ui_service.py", "_worker_boundary"),
}


class BroadExceptionVisitor(ast.NodeVisitor):
    def __init__(self, relative_path: str) -> None:
        self.relative_path = relative_path
        self.scope: list[str] = []
        self.boundaries: list[tuple[str, str, int]] = []

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        self.scope.append(node.name)
        self.generic_visit(node)
        self.scope.pop()

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        self.scope.append(node.name)
        self.generic_visit(node)
        self.scope.pop()

    def visit_ExceptHandler(self, node: ast.ExceptHandler) -> None:
        if _is_broad_exception_type(node.type):
            self.boundaries.append((self.relative_path, self.scope[-1] if self.scope else "<module>", node.lineno))
        self.generic_visit(node)


def _is_broad_exception_type(node: ast.expr | None) -> bool:
    if node is None:
        return True
    if isinstance(node, ast.Name):
        return node.id in {"Exception", "BaseException"}
    if isinstance(node, ast.Attribute):
        return node.attr in {"Exception", "BaseException"}
    if isinstance(node, ast.Tuple):
        return any(_is_broad_exception_type(item) for item in node.elts)
    return False


def _is_in_scope(path: Path) -> bool:
    relative_parts = path.relative_to(ROOT).parts
    return relative_parts[0] != "legacy" and not (set(relative_parts) & EXCLUDED_PARTS)


def test_package_modules_stay_in_exception_audit_scope() -> None:
    assert _is_in_scope(ROOT / "skyscanner_multi_domain" / "models.py")
    assert not _is_in_scope(ROOT / "legacy" / "gui.py")


def test_only_documented_broad_exception_boundaries_remain() -> None:
    found: list[tuple[str, str, int]] = []
    for path in ROOT.rglob("*.py"):
        if not _is_in_scope(path):
            continue
        relative_path = path.relative_to(ROOT).as_posix()
        visitor = BroadExceptionVisitor(relative_path)
        visitor.visit(ast.parse(path.read_text(encoding="utf-8"), filename=str(path)))
        found.extend(visitor.boundaries)

    unexpected = [
        f"{relative_path}:{lineno} in {scope}"
        for relative_path, scope, lineno in found
        if (relative_path, scope) not in ALLOWED_BROAD_EXCEPTION_BOUNDARIES
    ]
    missing = sorted(
        f"{relative_path} in {scope}"
        for relative_path, scope in ALLOWED_BROAD_EXCEPTION_BOUNDARIES
        if not any((found_path, found_scope) == (relative_path, scope) for found_path, found_scope, _ in found)
    )

    assert unexpected == []
    assert missing == []


def test_broad_exception_allow_list_is_named_in_todo() -> None:
    todo = (ROOT / "docs" / "todo.md").read_text(encoding="utf-8")
    for relative_path, scope in ALLOWED_BROAD_EXCEPTION_BOUNDARIES:
        assert relative_path in todo
        assert scope in todo


def test_broad_exception_visitor_detects_broad_handler_forms() -> None:
    tree = ast.parse(
        """
import builtins

def tuple_boundary():
    try:
        pass
    except (ValueError, Exception):
        pass

def bare_boundary():
    try:
        pass
    except:
        pass

def attribute_boundary():
    try:
        pass
    except builtins.Exception:
        pass
"""
    )
    visitor = BroadExceptionVisitor("example.py")
    visitor.visit(tree)

    assert visitor.boundaries == [
        ("example.py", "tuple_boundary", 7),
        ("example.py", "bare_boundary", 13),
        ("example.py", "attribute_boundary", 19),
    ]
