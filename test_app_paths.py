from __future__ import annotations

from pathlib import Path
from tempfile import TemporaryDirectory
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


def test_get_browser_profile_dir_migrates_from_legacy_data_root() -> None:
    with TemporaryDirectory() as temp_dir:
        temp_path = Path(temp_dir)
        target_root = temp_path / "runtime" / "browser-profiles"
        legacy_data_root = temp_path / "data" / "browser-profiles"
        legacy_profile = legacy_data_root / "edge-cdp-profile"
        (legacy_profile / "Default").mkdir(parents=True)

        with (
            patch.object(app_paths, "BROWSER_PROFILES_DIR", target_root),
            patch.object(
                app_paths,
                "LEGACY_BROWSER_PROFILE_ROOTS",
                (temp_path / "outputs", legacy_data_root),
            ),
            patch.object(
                app_paths,
                "ensure_runtime_dirs",
                side_effect=lambda: target_root.mkdir(parents=True, exist_ok=True),
            ),
        ):
            resolved = app_paths.get_browser_profile_dir("edge")

        assert resolved == target_root / "edge-cdp-profile"
        assert resolved.exists()
        assert not legacy_profile.exists()
