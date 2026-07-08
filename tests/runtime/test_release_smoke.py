from __future__ import annotations

from pathlib import Path

from scripts.release_smoke import (
    CI_GATE_NEEDLES,
    LEGACY_TK_FORBIDDEN_TEXT,
    PYPROJECT_FILE,
    REMOVED_PACKAGE_LEGACY_NAMESPACES,
    REMOVED_FLAT_ROOT_SHIMS,
    SOURCE_APP_SCRIPT,
    collect_webui_style_guardrail_violations,
    read_release_version,
    run_release_smoke,
)


def test_release_version_is_semver() -> None:
    assert read_release_version() == "1.2.1"


def test_release_smoke_metadata_passes_without_requiring_built_webui() -> None:
    checks = run_release_smoke(require_webui_dist=False)

    assert "pyproject version 1.2.1" in checks
    assert "webui package version 1.2.1" in checks
    assert "changelog entry" in checks
    assert "README install path" in checks
    assert "legacy Tk entrypoints removed" in checks
    assert "flat root shims removed" in checks
    assert "removed root shim references" in checks
    assert "package legacy namespace removed" in checks
    assert "macOS bundle version wiring" in checks
    assert "PyInstaller build data" in checks
    assert "CI test gates" in checks
    assert "webui style guardrails" in checks
    assert "webui dist asset" not in checks
    assert set(CI_GATE_NEEDLES) == {
        "python -m ruff check .",
        "python -m mypy",
        "npm ci",
        "npm test -- --run",
        "npm run build",
        "python scripts/release_smoke.py",
        "python -m pytest -q",
    }


def test_package_legacy_is_not_excluded_from_lint_or_type_gates() -> None:
    pyproject = PYPROJECT_FILE.read_text(encoding="utf-8")

    assert '"legacy"' not in pyproject
    assert '"legacy/"' not in pyproject


def test_package_legacy_namespace_stays_out_of_release_surface() -> None:
    assert [path for path in REMOVED_PACKAGE_LEGACY_NAMESPACES if path.exists()] == []


def test_removed_legacy_tk_fallback_text_stays_out_of_release_surface() -> None:
    for path, forbidden_text in LEGACY_TK_FORBIDDEN_TEXT:
        assert forbidden_text not in path.read_text(encoding="utf-8")


def test_removed_flat_root_shims_stay_out_of_release_surface() -> None:
    assert [path for path in REMOVED_FLAT_ROOT_SHIMS if path.exists()] == []


def test_source_linked_app_script_uses_central_version_file() -> None:
    script = SOURCE_APP_SCRIPT.read_text(encoding="utf-8")

    assert 'VERSION_FILE="${PROJECT_ROOT}/data/version.txt"' in script
    assert "<string>${VERSION}</string>" in script
    assert "<string>1.0</string>" not in script


def test_webui_style_guardrails_detect_forbidden_patterns(tmp_path: Path) -> None:
    source_dir = tmp_path / "src"
    source_dir.mkdir()
    (source_dir / "Component.tsx").write_text("<svg /><div className=\"tracking-tight rounded-2xl\" />", encoding="utf-8")
    (source_dir / "styles.css").write_text(
        """
.bad {
  background: linear-gradient(#fff, #000);
  border-radius: 20px;
  font-size: clamp(12px, 2vw, 16px);
  letter-spacing: 0.1em;
}
""",
        encoding="utf-8",
    )

    violations = collect_webui_style_guardrail_violations(source_dir)

    assert "handwritten SVG" in " ".join(violations)
    assert "Tailwind tracking utility" in " ".join(violations)
    assert "gradient background" in " ".join(violations)
    assert "viewport-scaled font size" in " ".join(violations)
    assert "oversized border radius" in " ".join(violations)
    assert "non-zero letter spacing" in " ".join(violations)


def test_webui_style_guardrails_allow_zero_letter_spacing(tmp_path: Path) -> None:
    source_dir = tmp_path / "src"
    source_dir.mkdir()
    (source_dir / "styles.css").write_text(".ok { letter-spacing: 0; }", encoding="utf-8")

    assert collect_webui_style_guardrail_violations(source_dir) == []
