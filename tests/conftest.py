"""Shared pytest configuration for tests/ directory."""

from __future__ import annotations

import sys
from unittest.mock import patch

import pytest


@pytest.fixture(autouse=True)
def clean_sys_modules():
    """Remove any test-added modules from sys.modules after each test."""
    before = set(sys.modules.keys())
    yield
    after = set(sys.modules.keys())
    for k in after - before:
        sys.modules.pop(k, None)