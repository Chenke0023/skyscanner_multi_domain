from __future__ import annotations

from skyscanner_multi_domain.scan.confirmation import PriceConfirmationStore, sample_from_row


def test_price_confirmation_store_appends_and_summarizes(tmp_path) -> None:
    store = PriceConfirmationStore(tmp_path / "confirmations.jsonl")
    row = {
        "date": "2026-06-01",
        "route": "PEK -> HKG",
        "region_code": "HK",
        "region_name": "香港",
        "link": "https://example.test",
        "cheapest_cny_price": 900.0,
        "confidence": 0.91,
        "price_source": "cheapest_block",
        "parser_warnings": ["warn"],
    }

    store.append(sample_from_row(row, status="confirmed", created_at="2026-06-01T00:00:00+00:00"))
    store.append(sample_from_row(row, status="mismatched", note="price changed", created_at="2026-06-01T00:01:00+00:00"))

    samples = store.load()
    summary = store.summary()

    assert [sample.status for sample in samples] == ["confirmed", "mismatched"]
    assert samples[0].region_code == "HK"
    assert samples[1].note == "price changed"
    assert summary["total"] == 2
    assert summary["confirmed"] == 1
    assert summary["mismatched"] == 1
    assert summary["latest_at"] == "2026-06-01T00:01:00+00:00"
