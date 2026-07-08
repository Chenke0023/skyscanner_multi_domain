from __future__ import annotations

import json

from skyscanner_multi_domain.geo.regions import REGIONS
from skyscanner_multi_domain.scan.url_builder import (
    extract_quote,
    find_candidate_captures,
    mutate_payload,
    prepare_headers,
    rewrite_url,
)


def test_rewrite_url_updates_domain_market_currency_locale_and_date() -> None:
    rewritten = rewrite_url(
        "https://www.skyscanner.com/transport/flights/PEK/ALA/260429/?market=US&locale=en-US&currency=USD",
        REGIONS["HK"],
        "2026-05-20",
    )

    assert rewritten.startswith("https://www.skyscanner.com.hk/")
    assert "260520" in rewritten
    assert "market=HK" in rewritten
    assert "locale=zh-HK" in rewritten
    assert "currency=HKD" in rewritten


def test_mutate_payload_updates_route_date_and_market_context() -> None:
    payload = {
        "query": {
            "market": "US",
            "locale": "en-US",
            "currency": "USD",
            "queryLegs": [
                {
                    "originPlaceId": {"iata": "PEK"},
                    "destinationPlaceId": {"iata": "ALA"},
                    "date": {"year": 2026, "month": 4, "day": 29},
                }
            ],
        }
    }

    mutated = mutate_payload(payload, "PVG", "NQZ", "2026-05-20", REGIONS["SG"])

    assert mutated["query"]["market"] == "SG"
    assert mutated["query"]["locale"] == "en-SG"
    assert mutated["query"]["currency"] == "SGD"
    leg = mutated["query"]["queryLegs"][0]
    assert leg["originPlaceId"]["iata"] == "PVG"
    assert leg["destinationPlaceId"]["iata"] == "NQZ"
    assert leg["date"] == {"year": 2026, "month": 5, "day": 20}


def test_find_candidate_captures_ranks_matching_skyscanner_post() -> None:
    captures = [
        {"url": "https://example.test/api", "method": "GET", "requestBody": {}, "timestamp": 2},
        {
            "url": "https://www.skyscanner.com/g/conductor/v1/fps3/search",
            "method": "POST",
            "requestBody": {"origin": "PEK", "destination": "ALA", "date": "2026-05-20"},
            "responseStatus": 200,
            "timestamp": 1,
        },
    ]

    candidates = find_candidate_captures(captures, "PEK", "ALA", "2026-05-20")

    assert candidates == [captures[1]]


def test_prepare_headers_filters_unsafe_and_auth_headers() -> None:
    headers = prepare_headers(
        {
            "Accept": "application/json",
            "Cookie": "secret",
            "Authorization": "Bearer secret",
            "Content-Length": "99",
            "X-Market": "US",
        },
        REGIONS["HK"],
        "https://www.skyscanner.com/g/search",
        include_auth=False,
    )

    assert headers["Accept"] == "application/json"
    assert headers["X-Market"] == "US"
    assert "Cookie" not in headers
    assert "Authorization" not in headers
    assert "Content-Length" not in headers
    assert headers["Referer"] == REGIONS["HK"].domain


def test_extract_quote_picks_lowest_price_candidate() -> None:
    quote = extract_quote(
        REGIONS["UK"],
        "https://www.skyscanner.net/search",
        json.dumps(
            {
                "itineraries": [
                    {"price": {"amount": 320, "currency": "GBP"}},
                    {"price": {"amount": 250, "currency": "GBP"}},
                ]
            }
        ),
        200,
    )

    assert quote.price == 250
    assert quote.currency == "GBP"
    assert quote.price_path == "itineraries[1].price.amount"
