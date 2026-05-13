from __future__ import annotations

from skyscanner_multi_domain.models import FlightQuote
from skyscanner_multi_domain.scan.fallback_router import build_fallback_telemetry


def _quote(**overrides) -> FlightQuote:
    defaults = dict(
        region="SG",
        domain="https://www.skyscanner.com.sg",
        price=None,
        currency="SGD",
        source_url="https://example.test/SG",
        status="opencli_error",
    )
    defaults.update(overrides)
    return FlightQuote(**defaults)


def test_build_fallback_telemetry_preserves_benchmark_keys() -> None:
    telemetry = build_fallback_telemetry(
        [
            _quote(status="page_challenge"),
            _quote(status="opencli_no_flights"),
            _quote(price=312.0, confidence=0.45, status="price_found"),
            _quote(status="opencli_error"),
            _quote(price=299.0, confidence=0.9, status="page_semantic_mismatch"),
        ]
    )

    assert telemetry == {
        "fallback_skipped_challenge_count": 1,
        "fallback_skipped_no_flights_count": 1,
        "fallback_skipped_other_count": 0,
        "fallback_routed_to_cdp_count": 3,
        "fallback_routed_to_scrapling_count": 3,
        "fallback_manual_review_required_count": 1,
        "fallback_low_confidence_count": 1,
    }
