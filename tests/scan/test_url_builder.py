"""Search URL building tests (migrated from test_skyscanner_neo.py)."""

from __future__ import annotations

import unittest

from skyscanner_neo import REGIONS, build_search_url


class SearchUrlTests(unittest.TestCase):
    def test_build_search_url_for_one_way(self) -> None:
        url = build_search_url(REGIONS["HK"], "BJSA", "ALA", "2026-04-29")

        self.assertIn("/transport/flights/bjsa/ala/260429/", url)
        self.assertIn("rtn=0", url)

    def test_build_search_url_for_round_trip(self) -> None:
        url = build_search_url(
            REGIONS["HK"],
            "BJSA",
            "ALA",
            "2026-04-29",
            return_date="2026-05-03",
        )

        self.assertIn("/transport/flights/bjsa/ala/260429/260503/", url)
        self.assertIn("rtn=1", url)


if __name__ == "__main__":
    unittest.main()