from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import gui


def test_collect_startup_issues_reports_missing_scrapling_dependency() -> None:
    airport_path = Path("/tmp/airport-codes.csv")
    mappings_path = Path("/tmp/location_mappings.json")

    def fake_exists(path: Path) -> bool:
        return path in {airport_path, mappings_path}

    with patch.object(gui.importlib.util, "find_spec", return_value=None):
        with patch.object(gui, "_find_missing_apify_data_files", return_value=[]):
            with patch.object(gui, "AIRPORT_DATASET_PATH", airport_path):
                with patch.object(gui, "LOCATION_MAPPINGS_PATH", mappings_path):
                    with patch.object(Path, "exists", fake_exists):
                        issues = gui._collect_startup_issues()

    assert "缺少 Scrapling 主抓取依赖，请重新安装项目依赖。" in issues


def test_collect_startup_issues_reports_missing_resource_files() -> None:
    airport_path = Path("/tmp/missing-airport-codes.csv")
    mappings_path = Path("/tmp/missing-location_mappings.json")

    def fake_exists(_path: Path) -> bool:
        return False

    with patch.object(gui.importlib.util, "find_spec", return_value=object()):
        with patch.object(gui, "_find_missing_apify_data_files", return_value=["input-network-definition.zip"]):
            with patch.object(gui, "AIRPORT_DATASET_PATH", airport_path):
                with patch.object(gui, "LOCATION_MAPPINGS_PATH", mappings_path):
                    with patch.object(Path, "exists", fake_exists):
                        issues = gui._collect_startup_issues()

    assert f"缺少机场数据文件：{airport_path}" in issues
    assert f"缺少地点映射文件：{mappings_path}" in issues
    assert any("input-network-definition.zip" in issue for issue in issues)
