#!/usr/bin/env python3
"""Test inventory diagnostic — understand test file shape before refactoring."""

from __future__ import annotations

import ast
from pathlib import Path
from typing import Any

TEST_DIR = Path(__file__).parent.parent


def iter_test_files(root: Path = TEST_DIR) -> list[Path]:
    test_files = [*root.glob("test_*.py"), *root.glob("tests/**/test_*.py")]
    return sorted(path for path in test_files if "vendor" not in path.relative_to(root).parts)


def _display_path(path: Path) -> str:
    try:
        return path.relative_to(TEST_DIR).as_posix()
    except ValueError:
        return path.name


def parse_test_file(path: Path) -> dict[str, Any]:
    src = path.read_text(encoding="utf-8")

    stats: dict[str, Any] = {
        "file": _display_path(path),
        "lines": len(src.splitlines()),
        "tests": 0,
        "classes": 0,
        "patch_calls": 0,
        "async_calls": 0,
        "import_root_shim": 0,
        "import_package": 0,
        "sleep_calls": 0,
        "temp_dir_calls": 0,
        "sys_modules_patch": 0,
    }

    try:
        tree = ast.parse(src)
    except SyntaxError:
        return stats

    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name.startswith("test_"):
            stats["tests"] += 1
        if isinstance(node, ast.ClassDef) and node.name.endswith("Tests"):
            stats["classes"] += 1

    stats["patch_calls"] = src.count("patch(") + src.count("patch.object(") + src.count("patch.dict(")
    stats["async_calls"] = src.count("async def test_") + src.count("AsyncMock")
    stats["import_root_shim"] = (
        src.count("from skyscanner_neo")
        + src.count("import skyscanner_neo")
        + src.count("from transport_")
        + src.count("from scan_history")
        + src.count("from test_")  # cross-test imports
    )
    stats["import_package"] = src.count("from skyscanner_multi_domain")
    stats["sleep_calls"] = src.count("time.sleep")
    stats["temp_dir_calls"] = src.count("TemporaryDirectory")
    stats["sys_modules_patch"] = src.count("sys.modules")

    return stats


def print_table(rows: list[dict[str, Any]]) -> None:
    header = f"{'file':<36} {'tests':>5} {'cls':>4} {'lines':>6} {'patch':>6} {'async':>5} {'sys_mod':>8} {'sleep':>6} {'td':>4}"
    print(header)
    print("-" * len(header))
    for r in rows:
        print(
            f"{r['file']:<36} "
            f"{r['tests']:>5} "
            f"{r['classes']:>4} "
            f"{r['lines']:>6} "
            f"{r['patch_calls']:>6} "
            f"{r['async_calls']:>5} "
            f"{r['sys_modules_patch']:>8} "
            f"{r['sleep_calls']:>6} "
            f"{r['temp_dir_calls']:>4}"
        )
    total_lines = sum(r["lines"] for r in rows)
    total_tests = sum(r["tests"] for r in rows)
    print(f"\nTotal: {total_tests} tests across {len(rows)} files, {total_lines} lines")


def main() -> None:
    rows = []
    for p in iter_test_files():
        stats = parse_test_file(p)
        rows.append(stats)

    print_table(rows)

    # Flag fat files
    print("\n# Files > 400 lines or > 200 patch calls:")
    for r in rows:
        if r["lines"] > 400 or r["patch_calls"] > 200:
            print(f"  {r['file']}: {r['lines']} lines, {r['patch_calls']} patch calls")


if __name__ == "__main__":
    main()
