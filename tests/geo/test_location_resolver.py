import unittest

try:
    import babel  # noqa: F401

    BABEL_AVAILABLE = True
except ImportError:
    BABEL_AVAILABLE = False

from skyscanner_multi_domain.geo.countries import iter_cldr_country_codes
from skyscanner_multi_domain.geo.location_resolver import (
    LocationResolver,
    load_airport_country_codes,
    load_country_records,
    load_location_mappings,
)
from skyscanner_multi_domain.geo.regions import (
    DEFAULT_REGIONS,
    REGIONS,
    build_effective_region_codes,
    dedupe_region_codes,
)


class LocationResolverTests(unittest.TestCase):
    def setUp(self) -> None:
        self.resolver = LocationResolver()

    def test_city_name_prefers_metro_when_enabled(self) -> None:
        resolved = self.resolver.resolve_location("北京", prefer_metro=True)

        self.assertEqual(resolved.code, "BJSA")
        self.assertEqual(resolved.kind, "metro")
        self.assertEqual(resolved.country, "CN")

    def test_city_name_can_resolve_to_airport_when_exact_airport_requested(self) -> None:
        resolved = self.resolver.resolve_location("北京", prefer_metro=False)

        self.assertEqual(resolved.code, "PEK")
        self.assertEqual(resolved.kind, "airport")
        self.assertEqual(resolved.country, "CN")

    def test_external_alias_mapping_can_resolve_tbilisi(self) -> None:
        resolved = self.resolver.resolve_location("第比利斯", prefer_metro=False)

        self.assertEqual(resolved.code, "TBS")
        self.assertEqual(resolved.country, "GE")

    def test_country_name_can_resolve_to_iso_code(self) -> None:
        resolved = self.resolver.resolve_country("乌兹别克斯坦")

        self.assertEqual(resolved.code, "UZ")
        self.assertEqual(resolved.name, "乌兹别克斯坦")

    def test_pakistan_country_aliases_resolve_to_iso_code(self) -> None:
        for query in ("巴基斯坦", "Pakistan", "PK"):
            with self.subTest(query=query):
                resolved = self.resolver.resolve_country(query)

                self.assertEqual(resolved.code, "PK")
                self.assertEqual(resolved.name, "巴基斯坦")

    def test_country_search_finds_pakistan_by_partial_chinese_name(self) -> None:
        suggestions = self.resolver.search_countries("巴基", limit=3)

        self.assertGreaterEqual(len(suggestions), 1)
        self.assertEqual(suggestions[0].code, "PK")
        self.assertEqual(suggestions[0].name, "巴基斯坦")

    def test_country_route_airports_use_curated_priority(self) -> None:
        resolved, airports = self.resolver.get_country_route_airports("中国", limit=3)

        self.assertEqual(resolved.code, "CN")
        self.assertEqual([airport.code for airport in airports], ["PEK", "PKX", "PVG"])

    def test_pakistan_route_airports_use_curated_priority(self) -> None:
        resolved, airports = self.resolver.get_country_route_airports("巴基斯坦", limit=5)

        self.assertEqual(resolved.code, "PK")
        self.assertEqual([airport.code for airport in airports], ["ISB", "KHI", "LHE", "PEW", "SKT"])

    def test_effective_regions_include_route_country_market_without_changing_default(self) -> None:
        self.assertEqual(DEFAULT_REGIONS, ["CN", "HK"])
        self.assertIn("PK", REGIONS)
        self.assertEqual(dedupe_region_codes(["GB"]), ["UK"])

        regions = build_effective_region_codes(destination_country="PK")

        self.assertEqual(regions, ["CN", "HK", "PK"])
        self.assertIn("PK", regions)

    def test_all_valid_airport_country_codes_resolve_by_iso_code(self) -> None:
        for country_code in load_airport_country_codes():
            with self.subTest(country_code=country_code):
                resolved = self.resolver.resolve_country(country_code)

                self.assertEqual(resolved.code, country_code)

    @unittest.skipUnless(BABEL_AVAILABLE, "Babel/CLDR is not installed")
    def test_cldr_named_airport_countries_do_not_fall_back_to_raw_code(self) -> None:
        cldr_country_codes = set(iter_cldr_country_codes())
        raw_code_names = [
            record.code
            for record in load_country_records()
            if record.code in cldr_country_codes and record.name == record.code
        ]

        self.assertEqual(raw_code_names, [])

    def test_location_mappings_json_contains_required_sections(self) -> None:
        mappings = load_location_mappings()

        self.assertEqual(mappings.airport_aliases["第比利斯"], "TBS")
        self.assertEqual(mappings.airport_code_countries["TBS"], "GE")
        self.assertEqual(mappings.metro_codes["北京"], "BJSA")


if __name__ == "__main__":
    unittest.main()
