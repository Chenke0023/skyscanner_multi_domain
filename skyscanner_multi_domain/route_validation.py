"""Route/date validation shared by browser transports and result policy."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from typing import TYPE_CHECKING
from urllib.parse import urlparse

if TYPE_CHECKING:
    from skyscanner_multi_domain.models import FlightQuote

SEMANTIC_MISMATCH_STATUS = "page_semantic_mismatch"
CHALLENGE_STATUSES = frozenset({"page_challenge", "px_challenge"})
MAX_ROUTE_RECOVERY_ATTEMPTS = 2


@dataclass(frozen=True)
class SearchRouteSignature:
    origin: str
    destination: str
    outbound_date: str
    return_date: str | None = None

    @property
    def route_label(self) -> str:
        return f"{self.origin.upper()}→{self.destination.upper()}"

    @property
    def date_label(self) -> str:
        outbound = _display_date(self.outbound_date)
        if self.return_date:
            return f"{outbound} / {_display_date(self.return_date)}"
        return outbound


def _display_date(value: str) -> str:
    try:
        return datetime.strptime(value, "%y%m%d").strftime("%Y-%m-%d")
    except ValueError:
        return value


def parse_search_route_url(url: str) -> SearchRouteSignature | None:
    """Extract canonical route/date path components from a flight-search URL."""
    parsed = urlparse(str(url or ""))
    if parsed.scheme.casefold() not in {"http", "https"}:
        return None

    segments = [segment.casefold() for segment in parsed.path.split("/") if segment]
    for index in range(len(segments) - 1):
        if segments[index : index + 2] != ["transport", "flights"]:
            continue
        route_parts = segments[index + 2 :]
        if len(route_parts) not in {3, 4}:
            return None
        return SearchRouteSignature(
            origin=route_parts[0],
            destination=route_parts[1],
            outbound_date=route_parts[2],
            return_date=route_parts[3] if len(route_parts) == 4 else None,
        )
    return None


def search_route_matches_requested_url(actual_url: str, requested_url: str) -> bool:
    """Compare route/date components while allowing legitimate host/query redirects."""
    requested_route = parse_search_route_url(requested_url)
    actual_route = parse_search_route_url(actual_url)
    return requested_route is not None and actual_route == requested_route


def is_challenge_status(status: str | None) -> bool:
    return str(status or "") in CHALLENGE_STATUSES


def is_semantic_mismatch(value: object) -> bool:
    """Return whether a quote-like object or mapping is unsafe for price decisions."""
    if isinstance(value, Mapping):
        status = value.get("status")
        route_mismatch = value.get("route_mismatch")
        date_mismatch = value.get("date_mismatch")
    else:
        status = getattr(value, "status", None)
        route_mismatch = getattr(value, "route_mismatch", False)
        date_mismatch = getattr(value, "date_mismatch", False)
    return (
        str(status or "") == SEMANTIC_MISMATCH_STATUS
        or route_mismatch is True
        or date_mismatch is True
    )


def clear_quote_prices(quote: FlightQuote) -> FlightQuote:
    """Remove every price-bearing field so an unsafe observation cannot leak into ranking."""
    discarded = {
        "price": quote.price,
        "best_price": quote.best_price,
        "cheapest_price": quote.cheapest_price,
        "price_source": quote.price_source,
    }
    if any(value is not None for value in discarded.values()):
        quote.fetch_metadata.setdefault("discarded_price", discarded)
    quote.price = None
    quote.price_path = None
    quote.best_price = None
    quote.best_price_path = None
    quote.cheapest_price = None
    quote.cheapest_price_path = None
    quote.selected_candidate_rank = None
    quote.itinerary_legs = []
    quote.rankable = False
    quote.result_visibility = "hidden"
    quote.requires_manual_review = False
    return quote


def reject_quote_route_mismatch(
    quote: FlightQuote,
    *,
    requested_url: str,
    actual_url: str | None = None,
) -> FlightQuote:
    """Turn a quote from the wrong route/date into a terminal, unrankable failure."""
    actual_url = str(actual_url if actual_url is not None else quote.source_url)
    expected = parse_search_route_url(requested_url)
    actual = parse_search_route_url(actual_url)

    clear_quote_prices(quote)
    quote.source_url = actual_url
    quote.status = SEMANTIC_MISMATCH_STATUS
    quote.confidence = 0.0
    quote.route_detected = actual.route_label if actual is not None else None
    quote.date_detected = actual.date_label if actual is not None else None
    quote.route_mismatch = actual is None or expected is None or (
        actual.origin,
        actual.destination,
    ) != (
        expected.origin,
        expected.destination,
    )
    quote.date_mismatch = actual is not None and expected is not None and (
        actual.outbound_date,
        actual.return_date,
    ) != (
        expected.outbound_date,
        expected.return_date,
    )

    expected_label = (
        f"{expected.route_label} {expected.date_label}" if expected is not None else requested_url
    )
    if quote.route_mismatch and quote.date_mismatch:
        mismatch_label = "航线/日期"
    elif quote.date_mismatch:
        mismatch_label = "日期"
    else:
        mismatch_label = "航线"
    quote.error = f"页面{mismatch_label}不匹配：请求 {expected_label}，实际页面 {actual_url}"
    warning = f"discarded_mismatched_page:{actual_url}"
    if warning not in quote.parser_warnings:
        quote.parser_warnings.append(warning)
    quote.fetch_metadata.update(
        {
            "expected_url": requested_url,
            "actual_url": actual_url,
            "route_validation": "mismatch",
        }
    )
    return quote
