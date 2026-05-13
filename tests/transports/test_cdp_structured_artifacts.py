from __future__ import annotations

import argparse
import json

from skyscanner_multi_domain.models import RegionConfig
from skyscanner_multi_domain.parsing.quote_merge import resolve_quote
from skyscanner_multi_domain.transports.cdp_structured import _write_diagnostics


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
    result = resolve_quote(region, "https://example.test/search", [])

    diagnostic_dir = _write_diagnostics(
        args=argparse.Namespace(origin="PEK", destination="HKG", date="2026-06-01", return_date=None),
        region=region,
        capture={"domCards": [], "hydrationScripts": [], "pageText": "sample"},
        result=result,
        network_candidates=[],
        screenshot_png=b"png-bytes",
    )

    artifact_dir = tmp_path / "logs" / "failures" / "cdp_structured" / "PEK_HKG_20260601" / "HK"
    assert str(artifact_dir) == diagnostic_dir
    for name in (
        "meta.json",
        "network_candidates.json",
        "hydration_candidates.json",
        "dom_cards.json",
        "page_text.txt",
        "final_decision.json",
        "screenshot.png",
    ):
        assert (artifact_dir / name).exists()
    final_decision = json.loads((artifact_dir / "final_decision.json").read_text(encoding="utf-8"))
    assert final_decision["decision_trace"]
    assert (artifact_dir / "screenshot.png").read_bytes() == b"png-bytes"
