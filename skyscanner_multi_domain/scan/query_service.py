"""Query service — location resolution and query-payload construction.

Extracted from ``cli.SimpleCLI`` so that non-CLI callers (desktop UI service,
future integrations) can resolve locations and build query payloads without
depending on the CLI module. ``cli.SimpleCLI`` delegates here for backward
compatibility.

This is part of paying down the ``desktop_ui_service -> cli.SimpleCLI`` P1
debt: the query/planning slice is now package-owned. Result-processing
methods still live on ``SimpleCLI`` and remain documented debt.
"""

from __future__ import annotations

from skyscanner_multi_domain.geo.location_resolver import (
    COUNTRY_ROUTE_DEFAULT_AIRPORT_LIMIT,
    CountryRecord,
    LocationRecord,
    LocationResolver,
    ResolvedLocation,
)
from skyscanner_multi_domain.geo.regions import build_effective_region_codes


class QueryService:
    """Resolve locations and build scan query payloads.

    Thin, side-effect-free layer over :class:`LocationResolver` plus the
    region-code builder. Holds no scan state.
    """

    def __init__(self, location_resolver: LocationResolver | None = None) -> None:
        self.location_resolver = location_resolver or LocationResolver()

    # ── location resolution ────────────────────────────────────────────────

    def normalize_location(self, value: str, prefer_metro: bool) -> str:
        return self.location_resolver.normalize_location(
            value, prefer_metro=prefer_metro
        )

    def resolve_location(self, value: str, prefer_metro: bool) -> ResolvedLocation:
        return self.location_resolver.resolve_location(value, prefer_metro=prefer_metro)

    def resolve_country(self, value: str) -> CountryRecord:
        return self.location_resolver.resolve_country(value)

    # ── route planning ─────────────────────────────────────────────────────

    def build_country_route_plan(
        self,
        origin_country_value: str,
        destination_country_value: str,
        *,
        manual_region_codes: list[str] | None = None,
        airport_limit: int = COUNTRY_ROUTE_DEFAULT_AIRPORT_LIMIT,
    ) -> tuple[CountryRecord, CountryRecord, list[LocationRecord], list[LocationRecord], list[str]]:
        origin_country, origin_airports = self.location_resolver.get_country_route_airports(
            origin_country_value,
            limit=airport_limit,
        )
        destination_country, destination_airports = (
            self.location_resolver.get_country_route_airports(
                destination_country_value,
                limit=airport_limit,
            )
        )
        regions = build_effective_region_codes(
            origin_country=origin_country.code,
            destination_country=destination_country.code,
            manual_region_codes=manual_region_codes or [],
        )
        return (
            origin_country,
            destination_country,
            origin_airports,
            destination_airports,
            regions,
        )

    def build_expanded_route_plan(
        self,
        *,
        origin_value: str | None,
        destination_value: str | None,
        origin_is_country: bool,
        destination_is_country: bool,
        prefer_origin_metro: bool,
        manual_region_codes: list[str] | None = None,
        airport_limit: int = COUNTRY_ROUTE_DEFAULT_AIRPORT_LIMIT,
    ) -> tuple[str, str, str, str, list[LocationRecord], list[LocationRecord], list[str]]:
        if origin_is_country:
            if not origin_value:
                raise ValueError("缺少出发国家。")
            origin_country, origin_points = self.location_resolver.get_country_route_airports(
                origin_value,
                limit=airport_limit,
            )
            origin_label = origin_country.name
            origin_file_token = f"{origin_country.code}_ANY"
            origin_region_country = origin_country.code
        else:
            if not origin_value:
                raise ValueError("缺少出发地。")
            origin = self.resolve_location(origin_value, prefer_metro=prefer_origin_metro)
            origin_points = [
                LocationRecord(
                    name=origin.name,
                    code=origin.code,
                    kind=origin.kind,
                    municipality=origin.municipality,
                    country=origin.country,
                )
            ]
            origin_label = origin.query or origin.name or origin.code
            origin_file_token = origin.code
            origin_region_country = origin.country

        if destination_is_country:
            if not destination_value:
                raise ValueError("缺少目的国家。")
            destination_country, destination_points = (
                self.location_resolver.get_country_route_airports(
                    destination_value,
                    limit=airport_limit,
                )
            )
            destination_label = destination_country.name
            destination_file_token = f"{destination_country.code}_ANY"
            destination_region_country = destination_country.code
        else:
            if not destination_value:
                raise ValueError("缺少目的地。")
            destination = self.resolve_location(destination_value, prefer_metro=False)
            destination_points = [
                LocationRecord(
                    name=destination.name,
                    code=destination.code,
                    kind=destination.kind,
                    municipality=destination.municipality,
                    country=destination.country,
                )
            ]
            destination_label = destination.query or destination.name or destination.code
            destination_file_token = destination.code
            destination_region_country = destination.country

        regions = build_effective_region_codes(
            origin_country=origin_region_country,
            destination_country=destination_region_country,
            manual_region_codes=manual_region_codes or [],
        )
        return (
            origin_label,
            destination_label,
            origin_file_token,
            destination_file_token,
            origin_points,
            destination_points,
            regions,
        )

    def build_effective_regions(
        self,
        origin_value: str,
        destination_value: str,
        *,
        prefer_origin_metro: bool,
        manual_region_codes: list[str] | None = None,
    ) -> tuple[ResolvedLocation, ResolvedLocation, list[str]]:
        origin = self.resolve_location(origin_value, prefer_metro=prefer_origin_metro)
        destination = self.resolve_location(destination_value, prefer_metro=False)
        regions = build_effective_region_codes(
            origin_country=origin.country,
            destination_country=destination.country,
            manual_region_codes=manual_region_codes or [],
        )
        return origin, destination, regions

    # ── query payloads ─────────────────────────────────────────────────────

    @staticmethod
    def _query_title(
        origin_label: str,
        destination_label: str,
        date: str,
        return_date: str | None = None,
    ) -> str:
        if return_date:
            return f"{origin_label} -> {destination_label} ({date} / {return_date})"
        return f"{origin_label} -> {destination_label} ({date})"

    def build_point_query_payload(
        self,
        *,
        origin_input: str,
        destination_input: str,
        origin_label: str,
        destination_label: str,
        origin_code: str,
        destination_code: str,
        date: str,
        return_date: str | None,
        date_window_days: int,
        manual_regions: list[str],
        effective_regions: list[str],
        exact_airport: bool,
    ) -> dict[str, object]:
        return {
            "identity": {
                "mode": "point_to_point",
                "origin_input": origin_input,
                "destination_input": destination_input,
                "origin_label": origin_label,
                "destination_label": destination_label,
                "origin_code": origin_code,
                "destination_code": destination_code,
                "date": date,
                "return_date": return_date,
                "date_window_days": int(date_window_days),
                "trip_type": "round_trip" if return_date else "one_way",
                "manual_regions": sorted(code.upper() for code in manual_regions),
                "effective_regions": list(effective_regions),
                "exact_airport": bool(exact_airport),
            },
            "display": {
                "title": self._query_title(origin_label, destination_label, date, return_date),
            },
        }

    def build_expanded_query_payload(
        self,
        *,
        origin_value: str,
        destination_value: str,
        origin_label: str,
        destination_label: str,
        origin_file_token: str,
        destination_file_token: str,
        date: str,
        return_date: str | None,
        date_window_days: int,
        manual_regions: list[str],
        effective_regions: list[str],
        exact_airport: bool,
        origin_is_country: bool,
        destination_is_country: bool,
        airport_limit: int,
    ) -> dict[str, object]:
        return {
            "identity": {
                "mode": "expanded_route",
                "origin_input": origin_value,
                "destination_input": destination_value,
                "origin_label": origin_label,
                "destination_label": destination_label,
                "origin_code": origin_file_token,
                "destination_code": destination_file_token,
                "date": date,
                "return_date": return_date,
                "date_window_days": int(date_window_days),
                "trip_type": "round_trip" if return_date else "one_way",
                "manual_regions": sorted(code.upper() for code in manual_regions),
                "effective_regions": list(effective_regions),
                "exact_airport": bool(exact_airport),
                "origin_is_country": bool(origin_is_country),
                "destination_is_country": bool(destination_is_country),
                "airport_limit": int(airport_limit),
            },
            "display": {
                "title": self._query_title(origin_label, destination_label, date, return_date),
            },
        }
