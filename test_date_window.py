import unittest

from date_window import build_date_window


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


if __name__ == "__main__":
    unittest.main()
