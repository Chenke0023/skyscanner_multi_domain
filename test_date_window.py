import unittest

from date_window import (
    build_date_window,
    build_round_trip_date_window,
    format_trip_date_label,
)


class BuildDateWindowTests(unittest.TestCase):
    def test_zero_window_returns_center_date_only(self) -> None:
        self.assertEqual(build_date_window("2026-04-29", 0), ["2026-04-29"])

    def test_positive_window_expands_symmetrically(self) -> None:
        self.assertEqual(
            build_date_window("2026-04-29", 2),
            [
                "2026-04-27",
                "2026-04-28",
                "2026-04-29",
                "2026-04-30",
                "2026-05-01",
            ],
        )

    def test_negative_window_is_rejected(self) -> None:
        with self.assertRaises(ValueError):
            build_date_window("2026-04-29", -1)

    def test_round_trip_window_preserves_trip_length(self) -> None:
        self.assertEqual(
            build_round_trip_date_window("2026-04-29", "2026-05-03", 1),
            [
                ("2026-04-28", "2026-05-02"),
                ("2026-04-29", "2026-05-03"),
                ("2026-04-30", "2026-05-04"),
            ],
        )

    def test_round_trip_window_rejects_return_before_departure(self) -> None:
        with self.assertRaises(ValueError):
            build_round_trip_date_window("2026-04-29", "2026-04-28", 0)

    def test_format_trip_date_label_for_round_trip(self) -> None:
        self.assertEqual(
            format_trip_date_label("2026-04-29", "2026-05-03"),
            "2026-04-29 -> 2026-05-03",
        )


if __name__ == "__main__":
    unittest.main()
