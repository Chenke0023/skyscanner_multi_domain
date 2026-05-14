from __future__ import annotations

import argparse
import asyncio
import json

from skyscanner_multi_domain.models import FlightQuote, RegionConfig
from skyscanner_multi_domain.transports.cdp_structured import (
    _capture_structured_artifacts,
    _classify_failure_reason,
    _classify_page_state,
    _write_diagnostics,
)


class FakeResult:
    def __init__(self, region, source_url, evidences=None):
        self.confidence = 0.0
        self.conflict_reason = None
        self.evidences = evidences or []
        self.decision_trace = []
        quote = FlightQuote(
            region=region.code,
            domain=region.domain,
            price=None,
            currency=region.currency,
            source_url=source_url,
            status="no_quote",
        )
        quote.fetch_metadata["evidence_ranking"] = []
        quote.fetch_metadata["rejected_candidates"] = []
        quote.decision_trace = []
        self.final_quote = quote


def test_write_diagnostics_persists_full_artifact_set(tmp_path, monkeypatch) -> None:
    def fake_failure_log_file(name: str):
        return tmp_path / "logs" / "failures" / name

    monkeypatch.setattr(
        "skyscanner_multi_domain.transports.cdp_structured.get_failure_log_file",
        fake_failure_log_file,
    )
    region = RegionConfig(
        code="HK",
        name="Hong Kong",
        domain="https://www.skyscanner.com.hk",
        locale="zh-HK",
        currency="HKD",
    )
    result = FakeResult(region, "https://example.test/search")

    diagnostic_dir = _write_diagnostics(
        args=argparse.Namespace(origin="PEK", destination="HKG", date="2026-06-01", return_date=None),
        region=region,
        capture={
            "pageHealth": {"readyState": "complete"},
            "domCards": [],
            "hydrationScripts": [],
            "pageText": "sample",
            "failureStage": "dom_eval",
            "stageErrors": [{"stage": "dom_eval", "error": "boom"}],
        },
        result=result,
        network_candidates=[],
        screenshot_initial=b"png-bytes-initial",
        screenshot_final=b"png-bytes-final",
        failure_stage="dom_eval",
        page_state={"final_url": "https://example.test/search", "ready_state": "complete", "body_text_length": 100},
        navigation_trace={"final_url": "https://example.test/search"},
        state_timeline=[{"state": "loading_skeleton"}, {"state": "results_visible"}],
    )

    artifact_dir = tmp_path / "logs" / "failures" / "cdp_structured" / "PEK_HKG_20260601" / "HK"
    assert str(artifact_dir) == diagnostic_dir
    for name in (
        "meta.json",
        "page_health.json",
        "network_candidates.json",
        "network_candidates_enriched.json",
        "hydration_candidates.json",
        "hydration_candidates_enriched.json",
        "dom_cards.json",
        "page_text.txt",
        "final_decision.json",
        "screenshot_initial.png",
        "screenshot_final.png",
        "page_state.json",
        "navigation_trace.json",
    ):
        assert (artifact_dir / name).exists(), f"Missing: {name}"
    meta = json.loads((artifact_dir / "meta.json").read_text(encoding="utf-8"))
    assert meta["failure_stage"] == "dom_eval"
    assert json.loads((artifact_dir / "page_health.json").read_text(encoding="utf-8"))["readyState"] == "complete"
    final_decision = json.loads((artifact_dir / "final_decision.json").read_text(encoding="utf-8"))
    assert final_decision["decision_trace"] == []
    assert final_decision["failure_stage"] == "dom_eval"
    assert (artifact_dir / "screenshot_initial.png").read_bytes() == b"png-bytes-initial"
    assert (artifact_dir / "screenshot_final.png").read_bytes() == b"png-bytes-final"
    page_state = json.loads((artifact_dir / "page_state.json").read_text(encoding="utf-8"))
    assert page_state["body_text_length"] == 100


def test_capture_structured_artifacts_records_eval_failure_stage(monkeypatch) -> None:
    async def fake_cdp_eval(ws_url: str, expression: str, *, max_retries: int = 0):
        if "document.title" in expression:
            return {"url": "https://example.test/search", "readyState": "complete"}
        if "innerText" in expression and "querySelectorAll" not in expression:
            return "page text"
        if "candidateSelector" in expression:
            raise RuntimeError("Execution context was destroyed")
        return []

    monkeypatch.setattr(
        "skyscanner_multi_domain.transports.cdp_structured.cdp_eval",
        fake_cdp_eval,
    )
    trace: list[str] = []

    capture, failure_stage, page_state = asyncio.run(
        _capture_structured_artifacts("ws://example", "https://example.test/search", trace)
    )

    assert failure_stage == "dom_eval"
    assert capture["failureStage"] == "dom_eval"
    assert capture["pageText"] == "page text"
    assert capture["stageErrors"][0]["stage"] == "dom_eval"
    assert any(item.startswith("dom_eval:failed") for item in trace)


class TestClassifyPageState:
    def test_challenge(self) -> None:
        state = {"has_challenge": True, "has_currency_text": False}
        assert _classify_page_state(state) == "challenge"

    def test_loading_skeleton(self) -> None:
        state = {"has_loading_skeleton": True, "has_result_cards": False, "has_currency_text": False}
        assert _classify_page_state(state) == "loading_skeleton"

    def test_search_form_or_incomplete_params(self) -> None:
        state = {"has_search_form": True, "has_result_cards": False}
        assert _classify_page_state(state) == "search_form_or_incomplete_params"

    def test_no_results(self) -> None:
        state = {"has_no_results": True, "has_currency_text": False, "has_result_cards": False}
        assert _classify_page_state(state) == "no_results"

    def test_results_visible_with_result_cards(self) -> None:
        state = {"has_result_cards": True, "has_currency_text": False}
        assert _classify_page_state(state) == "results_visible"

    def test_results_visible_with_currency_text(self) -> None:
        state = {"has_result_cards": False, "has_currency_text": True}
        assert _classify_page_state(state) == "results_visible"

    def test_results_visible_with_sort_controls(self) -> None:
        state = {"has_result_cards": False, "has_sort_controls": True}
        assert _classify_page_state(state) == "results_visible"

    def test_incomplete_load(self) -> None:
        state = {"ready_state": "loading", "has_currency_text": False, "has_result_cards": False}
        assert _classify_page_state(state) == "incomplete_load"

    def test_nearly_empty(self) -> None:
        state = {"ready_state": "complete", "body_text_length": 100, "has_currency_text": False, "has_result_cards": False}
        assert _classify_page_state(state) == "nearly_empty"

    def test_unknown(self) -> None:
        state = {"ready_state": "complete", "body_text_length": 5000, "has_currency_text": False, "has_result_cards": False}
        assert _classify_page_state(state) == "unknown"


class TestClassifyFailureReason:
    def test_navigation_failed(self) -> None:
        reason = _classify_failure_reason("target_select", None, {}, None)
        assert reason == "navigation_failed"

    def test_failed_challenge(self) -> None:
        page_state = {"has_challenge": True}
        reason = _classify_failure_reason(None, page_state, {}, None)
        assert reason == "failed_challenge"

    def test_failed_invalid_search_page(self) -> None:
        page_state = {"has_search_form": True, "has_result_cards": False}
        reason = _classify_failure_reason(None, page_state, {}, None)
        assert reason == "failed_invalid_search_page"

    def test_failed_wait_timeout_before_results(self) -> None:
        page_state = {"has_loading_skeleton": True, "has_result_cards": False}
        reason = _classify_failure_reason(None, page_state, {}, None)
        assert reason == "failed_wait_timeout_before_results"

    def test_failed_no_results(self) -> None:
        page_state = {"has_no_results": True, "has_currency_text": False}
        reason = _classify_failure_reason(None, page_state, {}, None)
        assert reason == "failed_no_results"

    def test_failed_no_price_candidates_after_results(self) -> None:
        page_state = {"has_result_cards": True, "has_currency_text": True}
        capture = {"domCards": []}
        reason = _classify_failure_reason(None, page_state, capture, None)
        assert reason == "failed_no_price_candidates_after_results"

    def test_failed_cookie_interstitial(self) -> None:
        page_state = {"has_cookie_banner": True, "body_text_length": 5000}
        reason = _classify_failure_reason(None, page_state, {}, None)
        assert reason == "failed_cookie_interstitial"
