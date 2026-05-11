"""Shared test fixtures — regions, pages, quotes, fake transports."""

from __future__ import annotations

import types
from pathlib import Path

# ── Region fixtures ────────────────────────────────────────────────────────────

def region_cn():
    from skyscanner_models import RegionConfig
    return RegionConfig(
        code="CN", name="中国",
        domain="https://www.skyscanner.cn",
        currency="CNY", locale="zh-CN",
    )

def region_hk():
    from skyscanner_models import RegionConfig
    return RegionConfig(
        code="HK", name="香港",
        domain="https://www.skyscanner.com.hk",
        currency="HKD", locale="zh-HK",
    )

def region_sg():
    from skyscanner_models import RegionConfig
    return RegionConfig(
        code="SG", name="Singapore",
        domain="https://www.skyscanner.sg",
        currency="SGD", locale="en-SG",
    )

def region_uk():
    from skyscanner_models import RegionConfig
    return RegionConfig(
        code="UK", name="United Kingdom",
        domain="https://www.skyscanner.co.uk",
        currency="GBP", locale="en-GB",
    )

def region_id():
    from skyscanner_models import RegionConfig
    return RegionConfig(
        code="ID", name="Indonesia",
        domain="https://www.skyscanner.co.id",
        currency="IDR", locale="id-ID",
    )


# ── Page fixtures ──────────────────────────────────────────────────────────────

class FullPricePage:
    """Page with clear Best + Cheapest prices in Chinese."""
    url = "https://www.skyscanner.com.hk/transport/flights/bjsa/ala/260429/"
    html = """
    <html><body>
      <div>搜尋結果顯示方式</div>
      <div>最佳</div><div>HK$3,305</div>
      <div>最便宜</div><div>HK$3,072</div>
    </body></html>
    """


class ShellPage:
    """Skyscanner shell page with no real price data."""
    url = "https://www.skyscanner.com.hk/transport/flights/bjsa/ala/260429/"
    html = "<html><body><h1>Skyscanner 上從北京到阿拉木圖的便宜機票</h1></body></html>"


class PxCaptchaPage:
    """PX captcha challenge page."""
    url = "https://www.skyscanner.com.sg/sttc/px/captcha-v2/index.html"
    html = """
    <html><body>
      <h1>Verify you are human</h1>
      <div>captcha-v2</div>
      <div>Press and hold</div>
    </body></html>
    """


class EmptyPage:
    """Completely empty page."""
    url = "https://www.skyscanner.com.sg/transport/flights/bjsa/tbs/260428/"
    html = "<html><body></body></html>"


# ── Quote fixtures ──────────────────────────────────────────────────────────────

def quote_success(region="CN", price=2187.0, currency="CNY", domain="https://www.skyscanner.cn"):
    from skyscanner_models import FlightQuote
    return FlightQuote(
        region=region, domain=domain,
        price=price, currency=currency,
        source_url=f"{domain}/transport/flights/bjsa/ala/260429/",
        status="ok",
    )

def quote_failed(region="HK", status="page_parse_failed", error="页面正文未识别到 Best/Cheapest 价格"):
    from skyscanner_models import FlightQuote
    return FlightQuote(
        region=region,
        domain="https://www.skyscanner.com.hk",
        price=None, currency="HKD",
        source_url="https://www.skyscanner.com.hk/transport/flights/bjsa/ala/260429/",
        status=status, error=error,
    )


# ── Fake transport modules ──────────────────────────────────────────────────────

def make_fake_scrapling(fetch_page=None, get_page=None):
    """Build a fake scrapling module with configurable page returns."""
    if fetch_page is None:
        fetch_page = FullPricePage()
    if get_page is None:
        get_page = FullPricePage()

    return types.SimpleNamespace(
        Fetcher=types.SimpleNamespace(get=lambda *a, **k: get_page),
        StealthyFetcher=types.SimpleNamespace(fetch=lambda *a, **k: fetch_page),
    )


def make_fake_captcha_solver():
    """Fake captcha solver with no-op client."""
    return types.SimpleNamespace(
        CaptchaSolverClient=None,
        CaptchaSolverError=Exception,
    )


def make_fake_opencli_fetch(price=2187.0, currency="CNY"):
    """Fake opencli fetch result."""
    return types.SimpleNamespace(
        price=price, currency=currency,
        status="ok", error=None,
    )


# ── Argument fixtures ──────────────────────────────────────────────────────────

def std_args():
    import argparse
    return argparse.Namespace(
        origin="BJSA", destination="ALA", date="2026-04-29",
        return_date=None, timeout=30, page_wait=5,
    )

def std_target_url(region):
    return f"{region.domain}/transport/flights/bjsa/ala/260429/"