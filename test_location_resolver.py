import unittest

from location_resolver import LocationResolver, load_location_mappings


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

    def test_country_route_airports_use_curated_priority(self) -> None:
        resolved, airports = self.resolver.get_country_route_airports("中国", limit=3)

        self.assertEqual(resolved.code, "CN")
        self.assertEqual([airport.code for airport in airports], ["PEK", "PKX", "PVG"])

    def test_location_mappings_json_contains_required_sections(self) -> None:
        mappings = load_location_mappings()

        self.assertEqual(mappings.airport_aliases["第比利斯"], "TBS")
        self.assertEqual(mappings.airport_code_countries["TBS"], "GE")
        self.assertEqual(mappings.metro_codes["北京"], "BJSA")


if __name__ == "__main__":
    unittest.main()
