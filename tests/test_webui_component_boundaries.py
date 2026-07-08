from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
WEBUI_SRC = ROOT / "webui" / "src"


def _read(relative_path: str) -> str:
    return (WEBUI_SRC / relative_path).read_text(encoding="utf-8")


def test_app_keeps_phase_six_component_boundaries() -> None:
    app_source = _read("App.tsx")
    drawer_source = _read("components/Drawer.tsx")
    result_stream_source = _read("components/ResultStream.tsx")
    required_components = {
        "components/Drawer.tsx": "Drawers",
        "components/QueryCard.tsx": "QueryCard",
        "components/ResultStream.tsx": "ResultStream",
        "components/RawResults.tsx": "RawResults",
        "components/StatusBar.tsx": "StatusBar",
    }

    for component_path, export_name in required_components.items():
        assert (WEBUI_SRC / component_path).exists()
        assert export_name in _read(component_path)

    assert 'from "./components/Drawer"' in app_source
    assert 'from "./components/QueryCard"' in app_source
    assert 'from "./components/ResultStream"' in app_source
    assert 'from "./components/StatusBar"' in app_source
    assert "<QueryCard" in app_source
    assert "<ResultStream" in app_source
    assert "<Drawers" in app_source
    assert "<StatusBar" in app_source
    assert "function HistoryList" in drawer_source
    assert 'from "./RawResults"' in result_stream_source
    assert "<RawResults" in result_stream_source


def test_app_does_not_reabsorb_result_or_drawer_helpers() -> None:
    app_source = _read("App.tsx")
    extracted_helper_names = {
        "DataTable",
        "FetchSummaryCard",
        "FailureReasonPanel",
        "ParserEvidencePanel",
        "HistoryList",
        "formatMoney",
        "fallbackAttemptLabel",
        "priceSourceLabel",
    }

    for helper_name in extracted_helper_names:
        assert f"function {helper_name}" not in app_source
        assert f"const {helper_name}" not in app_source

    assert len(app_source.splitlines()) <= 550
