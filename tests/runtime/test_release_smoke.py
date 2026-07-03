from __future__ import annotations

from scripts.release_smoke import read_release_version, run_release_smoke


def test_release_version_is_semver() -> None:
    assert read_release_version() == "1.2.0"


def test_release_smoke_metadata_passes_without_requiring_built_webui() -> None:
    checks = run_release_smoke(require_webui_dist=False)

    assert "changelog entry" in checks
    assert "README install path" in checks
    assert "macOS bundle version wiring" in checks
