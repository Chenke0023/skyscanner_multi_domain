"""Captcha detection tests for Scrapling transport."""

from __future__ import annotations

import unittest

from skyscanner_multi_domain.parsing.challenge import check_captcha_in_page


class CaptchaDetectionTests(unittest.TestCase):
    def test_check_captcha_in_page_detects_px_challenge(self) -> None:
        class FakePage:
            url = "https://www.skyscanner.com.sg/sttc/px/captcha-v2/index.html"

        has_captcha, captcha_type = check_captcha_in_page(
            "Verify you are human\ncaptcha-v2\nPress and hold", FakePage()
        )

        self.assertTrue(has_captcha)
        self.assertEqual(captcha_type, "px")


if __name__ == "__main__":
    unittest.main()