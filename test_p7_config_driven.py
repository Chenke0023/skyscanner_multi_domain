"""P7.3/P7.4 tests: config-driven trust policy, planner branches, transport mode."""

from __future__ import annotations

import unittest
from unittest.mock import patch

from skyscanner_multi_domain.models import FlightQuote
from skyscanner_multi_domain.scan.config import (
    ChallengePolicy,
    LowConfidencePolicy,
    ScanConfig,
    TransportMode,
)
from skyscanner_multi_domain.scan.fetch_types import (
    AttemptAction,
    AttemptPlanner,
    apply_quote_trust_policy,
)
from skyscanner_multi_domain.scan.orchestrator import run_page_scan


def _quote(**overrides) -> FlightQuote:
    defaults = dict(
        region="SG",
        domain="https://www.skyscanner.com.sg",
        price=None,
        currency="SGD",
        source_url="https://example.test/SG",
        status="ok",
    )
    defaults.update(overrides)
    return FlightQuote(**defaults)


# ── Trust policy: low_confidence_policy 4 branches ────────────────────────────


class TrustPolicyLowConfidenceTests(unittest.TestCase):
    def test_high_confidence_is_rankable_visible(self) -> None:
        q = _quote(price=312.0, confidence=0.91)
        cfg = ScanConfig(low_confidence_policy=LowConfidencePolicy.FALLBACK)
        apply_quote_trust_policy(q, config=cfg)
        assert q.rankable is True
        assert q.result_visibility == "visible"
        assert q.requires_manual_review is False

    def test_low_confidence_fallback_marks_fallback_candidate(self) -> None:
        q = _quote(price=312.0, confidence=0.45)
        cfg = ScanConfig(low_confidence_policy=LowConfidencePolicy.FALLBACK)
        apply_quote_trust_policy(q, config=cfg)
        assert q.rankable is False
        assert q.result_visibility == "fallback_candidate"
        assert q.requires_manual_review is True  # 0.45 < 0.50 review threshold

    def test_low_confidence_show_visible_unranked(self) -> None:
        q = _quote(price=312.0, confidence=0.45)
        cfg = ScanConfig(low_confidence_policy=LowConfidencePolicy.SHOW)
        apply_quote_trust_policy(q, config=cfg)
        assert q.rankable is False
        assert q.result_visibility == "visible"
        assert q.requires_manual_review is False

    def test_low_confidence_hide_debug_only(self) -> None:
        q = _quote(price=312.0, confidence=0.45)
        cfg = ScanConfig(low_confidence_policy=LowConfidencePolicy.HIDE)
        apply_quote_trust_policy(q, config=cfg)
        assert q.rankable is False
        assert q.result_visibility == "debug_only"
        assert q.requires_manual_review is False

    def test_low_confidence_accept_review_marks_review(self) -> None:
        q = _quote(price=312.0, confidence=0.45)
        cfg = ScanConfig(low_confidence_policy=LowConfidencePolicy.ACCEPT_REVIEW)
        apply_quote_trust_policy(q, config=cfg)
        assert q.rankable is False
        assert q.result_visibility == "visible"
        assert q.requires_manual_review is True

    def test_no_config_uses_default_fallback_policy(self) -> None:
        q = _quote(price=312.0, confidence=0.45)
        apply_quote_trust_policy(q, config=None)
        assert q.rankable is False
        assert q.result_visibility == "fallback_candidate"

    def test_failed_quote_left_alone(self) -> None:
        q = _quote(price=None, status="opencli_error")
        cfg = ScanConfig(low_confidence_policy=LowConfidencePolicy.HIDE)
        apply_quote_trust_policy(q, config=cfg)
        # No price → no annotations applied
        assert q.rankable is None
        assert q.result_visibility is None
        assert q.requires_manual_review is False

    def test_idempotent(self) -> None:
        q = _quote(price=312.0, confidence=0.91)
        cfg = ScanConfig()
        apply_quote_trust_policy(q, config=cfg)
        apply_quote_trust_policy(q, config=cfg)
        assert q.rankable is True
        assert q.result_visibility == "visible"

    def test_custom_rankable_threshold(self) -> None:
        # confidence=0.70 with default 0.80 threshold → low_confidence
        q = _quote(price=312.0, confidence=0.70)
        cfg_strict = ScanConfig(rankable_confidence=0.80)
        apply_quote_trust_policy(q, config=cfg_strict)
        assert q.rankable is False

        # Same confidence with relaxed 0.60 threshold → rankable
        q2 = _quote(price=312.0, confidence=0.70)
        cfg_relaxed = ScanConfig(rankable_confidence=0.60)
        apply_quote_trust_policy(q2, config=cfg_relaxed)
        assert q2.rankable is True


# ── Trust policy: challenge_policy ────────────────────────────────────────────


class TrustPolicyChallengeTests(unittest.TestCase):
    def test_challenge_stop_hidden(self) -> None:
        q = _quote(price=None, status="page_challenge")
        cfg = ScanConfig(challenge_policy=ChallengePolicy.STOP)
        apply_quote_trust_policy(q, config=cfg)
        assert q.rankable is False
        assert q.result_visibility == "hidden"
        assert q.requires_manual_review is False

    def test_challenge_manual_visible_with_review(self) -> None:
        q = _quote(price=None, status="page_challenge")
        cfg = ScanConfig(challenge_policy=ChallengePolicy.MANUAL)
        apply_quote_trust_policy(q, config=cfg)
        assert q.rankable is False
        assert q.result_visibility == "visible"
        assert q.requires_manual_review is True

    def test_px_challenge_treated_same(self) -> None:
        q = _quote(price=None, status="px_challenge")
        cfg = ScanConfig(challenge_policy=ChallengePolicy.MANUAL)
        apply_quote_trust_policy(q, config=cfg)
        assert q.requires_manual_review is True


# ── Planner: low_confidence_policy branches ───────────────────────────────────


class PlannerLowConfidenceBranchTests(unittest.TestCase):
    def test_low_confidence_fallback_triggers_fallback_action(self) -> None:
        q = _quote(price=312.0, confidence=0.45, status="ok")
        cfg = ScanConfig(low_confidence_policy=LowConfidencePolicy.FALLBACK)
        plan = AttemptPlanner(config=cfg).plan(q)
        assert plan.action in (AttemptAction.FALLBACK_CDP, AttemptAction.FALLBACK_SCRAPLING)
        assert plan.failure_class == "low_confidence"

    def test_low_confidence_show_returns_accept_with_review(self) -> None:
        q = _quote(price=312.0, confidence=0.45, status="ok")
        cfg = ScanConfig(low_confidence_policy=LowConfidencePolicy.SHOW)
        plan = AttemptPlanner(config=cfg).plan(q)
        assert plan.action == AttemptAction.ACCEPT_WITH_REVIEW
        assert plan.manual_review_required is False
        # Trust policy applied as side effect
        assert q.rankable is False
        assert q.result_visibility == "visible"

    def test_low_confidence_hide_returns_accept_with_review(self) -> None:
        q = _quote(price=312.0, confidence=0.45, status="ok")
        cfg = ScanConfig(low_confidence_policy=LowConfidencePolicy.HIDE)
        plan = AttemptPlanner(config=cfg).plan(q)
        assert plan.action == AttemptAction.ACCEPT_WITH_REVIEW
        assert plan.manual_review_required is False
        assert q.result_visibility == "debug_only"

    def test_low_confidence_accept_review_marks_manual_review(self) -> None:
        q = _quote(price=312.0, confidence=0.45, status="ok")
        cfg = ScanConfig(low_confidence_policy=LowConfidencePolicy.ACCEPT_REVIEW)
        plan = AttemptPlanner(config=cfg).plan(q)
        assert plan.action == AttemptAction.ACCEPT_WITH_REVIEW
        assert plan.manual_review_required is True
        assert q.requires_manual_review is True


# ── Planner: challenge_policy ─────────────────────────────────────────────────


class PlannerChallengeBranchTests(unittest.TestCase):
    def test_challenge_stop_returns_terminal(self) -> None:
        q = _quote(price=None, status="page_challenge")
        cfg = ScanConfig(challenge_policy=ChallengePolicy.STOP)
        plan = AttemptPlanner(config=cfg).plan(q)
        assert plan.action == AttemptAction.TERMINAL
        assert plan.failure_class == "challenge"

    def test_challenge_manual_returns_manual_review(self) -> None:
        q = _quote(price=None, status="page_challenge")
        cfg = ScanConfig(challenge_policy=ChallengePolicy.MANUAL)
        plan = AttemptPlanner(config=cfg).plan(q)
        assert plan.action == AttemptAction.MANUAL_REVIEW
        assert plan.manual_review_required is True
        # Quote also annotated via trust policy
        assert q.requires_manual_review is True
        assert q.result_visibility == "visible"

    def test_no_config_default_stop(self) -> None:
        q = _quote(price=None, status="page_challenge")
        plan = AttemptPlanner().plan(q)
        assert plan.action == AttemptAction.TERMINAL


# ── Transport mode strict enforcement ─────────────────────────────────────────


class TransportModeStrictTests(unittest.IsolatedAsyncioTestCase):
    """When config.transport != AUTO, fallback chain must be disabled."""

    async def test_opencli_strict_skips_cdp_fallback(self) -> None:
        opencli_calls: list = []
        cdp_calls: list = []
        scrapling_calls: list = []

        async def fake_opencli(args, regions, **kwargs):
            opencli_calls.append(regions)
            return [
                FlightQuote(
                    region=r.code, domain=r.domain, price=None, currency=r.currency,
                    source_url=f"https://example.test/{r.code}",
                    status="opencli_error",
                    fetch_metadata={"phase": "wait_interactive"},
                )
                for r in regions
            ]

        async def fake_cdp(args, regions, **kwargs):
            cdp_calls.append(regions)
            return []

        async def fake_scrapling(args, regions, **kwargs):
            scrapling_calls.append(regions)
            return []

        cfg = ScanConfig(transport=TransportMode.OPENCLI, no_trace=True)

        with patch(
            "skyscanner_multi_domain.transports.opencli.compare_via_opencli",
            side_effect=fake_opencli,
        ), patch(
            "skyscanner_multi_domain.transports.cdp.compare_via_pages",
            side_effect=fake_cdp,
        ), patch(
            "skyscanner_multi_domain.transports.scrapling.compare_via_scrapling",
            side_effect=fake_scrapling,
        ):
            await run_page_scan(
                origin="BJS", destination="ALA", date="2026-06-10",
                region_codes=["SG"], transport="opencli",
                allow_browser_fallback=True,  # should be overridden by config
                config=cfg,
            )

        assert len(opencli_calls) >= 1
        assert cdp_calls == [], "CDP fallback should be disabled when transport=opencli strict"
        assert scrapling_calls == [], "Scrapling fallback should be disabled when transport=opencli strict"

    async def test_cdp_strict_skips_opencli(self) -> None:
        opencli_calls: list = []
        cdp_calls: list = []

        async def fake_cdp(args, regions, **kwargs):
            cdp_calls.append(regions)
            return [
                FlightQuote(
                    region=r.code, domain=r.domain, price=305.0, currency=r.currency,
                    source_url=f"https://example.test/{r.code}",
                    status="ok", confidence=0.9,
                )
                for r in regions
            ]

        async def fake_opencli(args, regions, **kwargs):
            opencli_calls.append(regions)
            return []

        cfg = ScanConfig(transport=TransportMode.CDP, no_trace=True)

        with patch(
            "skyscanner_multi_domain.transports.cdp.compare_via_pages",
            side_effect=fake_cdp,
        ), patch(
            "skyscanner_multi_domain.transports.opencli.compare_via_opencli",
            side_effect=fake_opencli,
        ):
            quotes = await run_page_scan(
                origin="BJS", destination="ALA", date="2026-06-10",
                region_codes=["SG"], transport="opencli",
                allow_browser_fallback=True,
                config=cfg,
            )

        assert len(cdp_calls) >= 1
        assert opencli_calls == [], "OpenCLI should not be called when transport=cdp strict"
        assert any(q.price == 305.0 for q in quotes)

    async def test_scrapling_strict_skips_cdp_fallback(self) -> None:
        scrapling_calls: list = []
        cdp_calls: list = []

        async def fake_scrapling(args, regions, **kwargs):
            scrapling_calls.append(regions)
            return [
                FlightQuote(
                    region=r.code, domain=r.domain, price=None, currency=r.currency,
                    source_url=f"https://example.test/{r.code}",
                    status="scrapling_failed",
                )
                for r in regions
            ]

        async def fake_cdp(args, regions, **kwargs):
            cdp_calls.append(regions)
            return []

        cfg = ScanConfig(transport=TransportMode.SCRAPLING, no_trace=True)

        with patch(
            "skyscanner_multi_domain.transports.scrapling.compare_via_scrapling",
            side_effect=fake_scrapling,
        ), patch(
            "skyscanner_multi_domain.transports.cdp.compare_via_pages",
            side_effect=fake_cdp,
        ):
            await run_page_scan(
                origin="BJS", destination="ALA", date="2026-06-10",
                region_codes=["SG"], transport="opencli",
                allow_browser_fallback=True,
                config=cfg,
            )

        assert len(scrapling_calls) >= 1
        assert cdp_calls == [], "CDP fallback should be disabled when transport=scrapling strict"

    async def test_auto_preserves_legacy_fallback_chain(self) -> None:
        """transport=AUTO → caller's transport/allow_browser_fallback unchanged."""
        opencli_calls: list = []
        cdp_calls: list = []

        async def fake_opencli(args, regions, **kwargs):
            opencli_calls.append(regions)
            return [
                FlightQuote(
                    region=r.code, domain=r.domain, price=None, currency=r.currency,
                    source_url=f"https://example.test/{r.code}",
                    status="opencli_error",
                    fetch_metadata={"phase": "wait_interactive"},
                )
                for r in regions
            ]

        async def fake_cdp(args, regions, **kwargs):
            cdp_calls.append(regions)
            return [
                FlightQuote(
                    region=r.code, domain=r.domain, price=200.0, currency=r.currency,
                    source_url=f"https://example.test/{r.code}",
                    status="ok", confidence=0.9,
                )
                for r in regions
            ]

        cfg = ScanConfig(transport=TransportMode.AUTO, no_trace=True)

        with patch(
            "skyscanner_multi_domain.transports.opencli.compare_via_opencli",
            side_effect=fake_opencli,
        ), patch(
            "skyscanner_multi_domain.transports.cdp.compare_via_pages",
            side_effect=fake_cdp,
        ):
            await run_page_scan(
                origin="BJS", destination="ALA", date="2026-06-10",
                region_codes=["SG"], transport="opencli",
                allow_browser_fallback=True,
                config=cfg,
            )

        assert len(opencli_calls) >= 1
        assert len(cdp_calls) >= 1, "AUTO should still allow CDP fallback"

    async def test_cdp_structured_falls_back_to_opencli(self) -> None:
        structured_calls: list = []
        opencli_calls: list = []

        async def fake_structured(args, regions, **kwargs):
            structured_calls.append(regions)
            return [
                FlightQuote(
                    region=r.code, domain=r.domain, price=None, currency=r.currency,
                    source_url=f"https://example.test/{r.code}",
                    status="cdp_structured_parse_failed",
                    source_kind="cdp_structured",
                    error="No structured price evidence found",
                )
                for r in regions
            ]

        async def fake_opencli(args, regions, **kwargs):
            opencli_calls.append(regions)
            return [
                FlightQuote(
                    region=r.code, domain=r.domain, price=188.0, currency=r.currency,
                    source_url=f"https://example.test/{r.code}",
                    status="price_found", confidence=0.9, source_kind="opencli",
                )
                for r in regions
            ]

        with patch(
            "skyscanner_multi_domain.transports.cdp.ensure_cdp_ready",
            return_value={"Browser": "test"},
        ), patch(
            "skyscanner_multi_domain.transports.cdp_structured.compare_via_cdp_structured",
            side_effect=fake_structured,
        ), patch(
            "skyscanner_multi_domain.transports.opencli.compare_via_opencli",
            side_effect=fake_opencli,
        ):
            quotes = await run_page_scan(
                origin="BJS", destination="ALA", date="2026-06-10",
                region_codes=["SG"], transport="cdp_structured",
                allow_browser_fallback=True,
                config=ScanConfig(no_trace=True),
            )

        assert len(structured_calls) == 1
        assert len(opencli_calls) == 1
        assert quotes[0].price == 188.0
        assert quotes[0].source_kind == "opencli"
        assert quotes[0].fallback_attempts[0]["transport"] == "cdp_structured_primary"


# ── CLI config building ───────────────────────────────────────────────────────


class CliConfigBuildingTests(unittest.TestCase):
    def test_build_scan_config_default_is_auto(self) -> None:
        from cli import SimpleCLI
        import argparse

        args = argparse.Namespace()
        cfg = SimpleCLI._build_scan_config(args)
        assert cfg.transport == TransportMode.AUTO
        assert cfg.cdp_mode.value == "attach"
        assert cfg.low_confidence_policy.value == "fallback"
        assert cfg.challenge_policy.value == "stop"

    def test_build_scan_config_transport_mode_strict(self) -> None:
        from cli import SimpleCLI
        import argparse

        args = argparse.Namespace(transport_mode="opencli")
        cfg = SimpleCLI._build_scan_config(args)
        assert cfg.transport == TransportMode.OPENCLI

    def test_build_scan_config_invalid_transport_mode_falls_back_to_auto(self) -> None:
        from cli import SimpleCLI
        import argparse

        args = argparse.Namespace(transport_mode="bogus")
        cfg = SimpleCLI._build_scan_config(args)
        assert cfg.transport == TransportMode.AUTO

    def test_build_scan_config_low_confidence_show(self) -> None:
        from cli import SimpleCLI
        import argparse

        args = argparse.Namespace(low_confidence_policy="show")
        cfg = SimpleCLI._build_scan_config(args)
        assert cfg.low_confidence_policy == LowConfidencePolicy.SHOW

    def test_build_scan_config_challenge_manual(self) -> None:
        from cli import SimpleCLI
        import argparse

        args = argparse.Namespace(challenge_policy="manual")
        cfg = SimpleCLI._build_scan_config(args)
        assert cfg.challenge_policy == ChallengePolicy.MANUAL


if __name__ == "__main__":
    unittest.main()
