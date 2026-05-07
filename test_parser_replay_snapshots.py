from __future__ import annotations

import json
from pathlib import Path

from skyscanner_multi_domain.diagnostics.snapshots import build_opencli_snapshot_payload
from skyscanner_multi_domain.models import FlightQuote, RegionConfig
from tools.replay_parser_snapshots import build_report, load_snapshots


def test_opencli_snapshot_payload_is_bounded_and_replayable(tmp_path: Path) -> None:
    region = RegionConfig("US", "United States", "skyscanner.com", "en-US", "USD")
    quote = FlightQuote(
        region="US",
        domain="skyscanner.com",
        price=None,
        currency="USD",
        source_url="https://example.test",
        status="page_parse_failed",
    )
    quote.readiness = "unknown_parse_surface"
    quote.max_chunk_size_used = 100000
    quote.extract_attempt_count = 3
    quote.candidate_sources = ["embedded_json_price"]
    page_text = (
        "Cheapest marker context. "
        '{"itinerary":{"provider":"Example Air","totalPrice":{"amount":480,'
        '"currency":"USD"},"deeplink":"https://example.test/book"}}'
    )
    payload = build_opencli_snapshot_payload(
        route={"origin": "PEK", "destination": "ALA", "date": "2026-05-20"},
        region=region,
        quote=quote,
        page_text=page_text,
    )
    path = tmp_path / "snapshot.json"
    path.write_text(json.dumps(payload), encoding="utf-8")

    snapshots = load_snapshots(tmp_path)
    report = build_report(snapshots)

    assert payload["schema_version"] == 1
    assert payload["page_text_head"]
    assert report["total_snapshots"] == 1
    assert report["recovered_price_count"] == 1
