from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import app_paths


def test_runtime_root_defaults_to_source_root_when_not_frozen() -> None:
    with patch.object(app_paths.sys, "frozen", False, create=True):
        resolved = app_paths._resolve_runtime_root()

    assert resolved == app_paths.SOURCE_ROOT


def test_runtime_root_uses_override_when_present() -> None:
    with patch.dict(app_paths.os.environ, {"SKYSCANNER_APP_HOME": "/tmp/skyscanner-app-home"}):
        resolved = app_paths._resolve_runtime_root()

    assert resolved == Path("/tmp/skyscanner-app-home")
