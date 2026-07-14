# Changelog

## Unreleased

- Dismiss origin and destination suggestion menus immediately after a location is selected, and ignore stale suggestion responses.
- Notify the desktop user immediately when a market triggers a bot/CAPTCHA challenge, and retain unresolved challenge tabs instead of closing them when the scan wait expires.

## 1.3.0 - 2026-07-13

### Breaking changes

- Removed OpenCLI, Scrapling, Neo, Google Jump, captcha solving, failure replay, and their compatibility interfaces.
- `page` is now the default and only stable transport; `cdp_structured` remains an explicit experimental CDP parser.
- A connectable CDP endpoint or an installed Chrome, Edge, or Comet browser is now required; browsers are neither bundled nor downloaded.

### Changed

- Replaced dynamic locale metadata and the full airport dataset with committed static country/currency metadata and a five-column IATA airport subset.
- Reduced runtime dependencies to `aiohttp`, `requests`, and `pywebview`.
- Tightened macOS packaging with forbidden-module checks and 150MB app / 60MB ZIP limits.

## 1.2.1 - 2026-07-07

- Finalized the todo backlog cleanup and release evidence after the 1.2.0 prep tag.
- Added focused boundary tests for benchmark, desktop scan workers, and Scrapling fetch failures.
- Removed the legacy Tk desktop entrypoints, package legacy namespace, and root `skyscanner_neo.py` shim from the active release surface, and guarded removed shims in release smoke.
- Removed leftover Neo helper re-exports and old captcha client constructor compatibility from active code paths.
- Verified local release gates and macOS app build smoke for the desktop WebView app.

## 1.2.0 - 2026-07-03

- Removed root-level compatibility shims from package import paths and guarded the boundary in tests.
- Split shared query/result/launchd/Neo URL helpers into package modules.
- Added SearchPlan batch progress, market reliability scoring, and exact-mode telemetry.
- Added parser trust metadata, warning evidence, decision reports, repair actions, and user-confirmed price samples.
- Added CI gates for Ruff, mypy, pytest, frontend test/build, and release smoke metadata checks.

## 1.1.0 - 2026-05-13

- Added desktop WebView workflow, history views, alert configuration, and background refresh support.
- Improved OpenCLI fetch telemetry and parser recovery snapshots.

## 1.0.0 - 2026-04-29

- Initial macOS desktop and CLI baseline for Skyscanner multi-market comparison.
