from __future__ import annotations

from pathlib import Path

from scripts import test_inventory


def test_parse_test_file_counts_async_tests_and_relative_path(
    tmp_path: Path,
    monkeypatch,
) -> None:
    test_file = tmp_path / "tests" / "tools" / "test_sample.py"
    test_file.parent.mkdir(parents=True)
    test_file.write_text(
        """
from unittest.mock import AsyncMock, patch


class SampleTests:
    def test_sync(self):
        with patch("module.name"):
            pass


async def test_async():
    pass
""",
        encoding="utf-8",
    )
    monkeypatch.setattr(test_inventory, "TEST_DIR", tmp_path)

    stats = test_inventory.parse_test_file(test_file)

    assert stats["file"] == "tests/tools/test_sample.py"
    assert stats["tests"] == 2
    assert stats["classes"] == 1
    assert stats["patch_calls"] == 1
    assert stats["async_calls"] == 2


def test_parse_test_file_counts_package_imports_separately_from_removed_root_shims(
    tmp_path: Path,
    monkeypatch,
) -> None:
    test_file = tmp_path / "test_scan.py"
    test_file.write_text(
        """
from skyscanner_multi_domain.scan.orchestrator import run_page_scan
from transport_cdp import detect_cdp_version


def test_sample():
    assert run_page_scan
""",
        encoding="utf-8",
    )
    monkeypatch.setattr(test_inventory, "TEST_DIR", tmp_path)

    stats = test_inventory.parse_test_file(test_file)

    assert stats["import_root_shim"] == 1
    assert stats["import_package"] == 1


def test_parse_test_file_counts_removed_root_shim_import(
    tmp_path: Path,
    monkeypatch,
) -> None:
    test_file = tmp_path / "test_removed_root.py"
    test_file.write_text(
        """
from scan_history import ScanHistoryStore


def test_sample():
    assert ScanHistoryStore
""",
        encoding="utf-8",
    )
    monkeypatch.setattr(test_inventory, "TEST_DIR", tmp_path)

    stats = test_inventory.parse_test_file(test_file)

    assert stats["import_root_shim"] == 1
    assert stats["import_package"] == 0


def test_parse_test_file_handles_syntax_error(tmp_path: Path, monkeypatch) -> None:
    test_file = tmp_path / "test_broken.py"
    test_file.write_text("def test_broken(:\n", encoding="utf-8")
    monkeypatch.setattr(test_inventory, "TEST_DIR", tmp_path)

    stats = test_inventory.parse_test_file(test_file)

    assert stats["file"] == "test_broken.py"
    assert stats["tests"] == 0


def test_iter_test_files_discovers_root_and_nested_tests_without_vendor(tmp_path: Path) -> None:
    root_test = tmp_path / "test_root.py"
    nested_test = tmp_path / "tests" / "tools" / "test_nested.py"
    vendor_test = tmp_path / "tests" / "vendor" / "test_vendor.py"
    nested_test.parent.mkdir(parents=True)
    vendor_test.parent.mkdir(parents=True)
    for path in (root_test, nested_test, vendor_test):
        path.write_text("def test_sample():\n    pass\n", encoding="utf-8")

    assert test_inventory.iter_test_files(tmp_path) == [root_test, nested_test]
