"""Region fixtures for all tests."""

from __future__ import annotations

import pytest


@pytest.fixture
def region_cn():
    from skyscanner_models import RegionConfig
    return RegionConfig(
        code="CN", name="中国",
        domain="https://www.skyscanner.cn",
        currency="CNY", locale="zh-CN",
    )


@pytest.fixture
def region_hk():
    from skyscanner_models import RegionConfig
    return RegionConfig(
        code="HK", name="香港",
        domain="https://www.skyscanner.com.hk",
        currency="HKD", locale="zh-HK",
    )


@pytest.fixture
def region_sg():
    from skyscanner_models import RegionConfig
    return RegionConfig(
        code="SG", name="Singapore",
        domain="https://www.skyscanner.sg",
        currency="SGD", locale="en-SG",
    )


@pytest.fixture
def region_uk():
    from skyscanner_models import RegionConfig
    return RegionConfig(
        code="UK", name="United Kingdom",
        domain="https://www.skyscanner.co.uk",
        currency="GBP", locale="en-GB",
    )


@pytest.fixture
def region_id():
    from skyscanner_models import RegionConfig
    return RegionConfig(
        code="ID", name="Indonesia",
        domain="https://www.skyscanner.co.id",
        currency="IDR", locale="id-ID",
    )