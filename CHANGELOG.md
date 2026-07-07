# Changelog

## 1.2.1 - 2026-07-07

- Finalized the todo backlog cleanup and release evidence after the 1.2.0 prep tag.
- Added focused boundary tests for benchmark, desktop scan workers, and Scrapling fetch failures.
- Verified local release gates and macOS app build smoke for the desktop WebView app.

## 1.2.0 - 2026-07-03

- Removed root-level compatibility shims from package import paths and guarded the boundary in tests.
- Split shared query/result/launchd/Neo URL helpers into package modules.
- Added SearchPlan batch progress, market reliability scoring, and exact-mode telemetry.
- Added parser trust metadata, warning evidence, decision reports, repair actions, and user-confirmed price samples.
- Added CI gates for Ruff, mypy, pytest, frontend build, and release smoke metadata checks.

## 1.1.0 - 2026-05-13

- Added desktop WebView workflow, history views, alert configuration, and background refresh support.
- Improved OpenCLI fetch telemetry and parser recovery snapshots.

## 1.0.0 - 2026-04-29

- Initial macOS desktop and CLI baseline for Skyscanner multi-market comparison.
