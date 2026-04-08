from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

from skyscanner_models import RegionConfig
from transport_cdp import _quote_from_cdp_payload, detect_cdp_version


def _build_connection(response_bodies: list[tuple[int, str]]) -> MagicMock:
    connection = MagicMock()
    responses = []
    for status, body in response_bodies:
        response = MagicMock()
        response.status = status
        response.read.return_value = body.encode("utf-8")
        responses.append(response)
    connection.getresponse.side_effect = responses
    return connection


def test_detect_cdp_version_accepts_first_valid_browser_payload() -> None:
    localhost = _build_connection(
        [(200, json.dumps({"Browser": "Edg/146.0.3856.84", "Protocol-Version": "1.3"}))]
    )

    with patch("transport_cdp.http.client.HTTPConnection", return_value=localhost):
        info = detect_cdp_version()

    assert info is not None
    assert info["Browser"] == "Edg/146.0.3856.84"


def test_detect_cdp_version_skips_404_and_tries_next_host() -> None:
    localhost = _build_connection([(404, "")])
    loopback_v6 = _build_connection(
        [(200, json.dumps({"Browser": "Edg/146.0.3856.84", "Protocol-Version": "1.3"}))]
    )
    loopback_v4 = _build_connection([(404, "")])

    with patch(
        "transport_cdp.http.client.HTTPConnection",
        side_effect=[localhost, loopback_v6, loopback_v4],
    ):
        info = detect_cdp_version()

    assert info is not None
    assert info["Browser"] == "Edg/146.0.3856.84"


def test_quote_from_cdp_payload_marks_px_challenge_from_url() -> None:
    region = RegionConfig(
        code="SG",
        name="Singapore",
        domain="https://www.skyscanner.sg",
        currency="SGD",
        locale="en-SG",
    )

    quote = _quote_from_cdp_payload(
        region,
        {
            "url": "https://www.skyscanner.com.sg/sttc/px/captcha-v2/index.html",
            "text": "",
        },
        "https://www.skyscanner.sg/transport/flights/bjsa/dps/260502/",
    )

    assert quote.status == "px_challenge"
    assert quote.price is None
    assert "PX" in (quote.error or "")
