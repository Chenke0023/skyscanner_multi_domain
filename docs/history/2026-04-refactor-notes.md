# 2026-04 Refactor Notes

This file preserves historical context that should not drive current implementation decisions. Current active paths and next tasks live in `AI_AGENT_HANDOFF.md`.

## Product Path Decision

The project moved from multiple user-facing entry points to one primary product path:

- Active end-user product: desktop WebView (`desktop_webview.py`, `desktop_ui_service.py`, `webui/`)
- Developer entry: CLI (`cli.py`)
- Legacy: Tk GUI (`legacy/gui.py`, `gui.py`) and root-level compatibility shims

The old Tk GUI remains only for compatibility and startup-level fixes. New UI work belongs in the desktop WebView path.

## Module Split

The previous large `skyscanner_neo.py` surface was split into focused package modules:

- `skyscanner_multi_domain/planning/search_plan.py`
- `skyscanner_multi_domain/scan/orchestrator.py`
- `skyscanner_multi_domain/scan/history.py`
- `skyscanner_multi_domain/transports/opencli.py`
- `skyscanner_multi_domain/transports/cdp.py`
- `skyscanner_multi_domain/transports/scrapling.py`
- `skyscanner_multi_domain/parsing/page_parser.py`
- `skyscanner_multi_domain/geo/location_resolver.py`
- `skyscanner_multi_domain/geo/regions.py`

Root-level files such as `transport_cdp.py`, `transport_scrapling.py`, `scan_orchestrator.py`, `search_plan.py`, `location_resolver.py`, and `skyscanner_regions.py` were retained as compatibility shims for old imports and test mock paths.

## Transport Evolution

The transport stack evolved through these stages:

1. Scrapling was introduced as a scraping transport with staged retries.
2. CDP `page` transport was retained for browser fallback and diagnostics.
3. `opencli` became the default browser automation path.
4. Runtime fallback order became: `opencli` -> `page` -> Scrapling legacy.

Historical browser session persistence checks found Comet retained probe cookies across restart, while Edge did not in the tested setup. Treat that as diagnostic history, not a product default.

## SearchPlan Work

SearchPlan was added to explain and rank route/date/market candidates. It currently attaches plan metadata and telemetry but does not prune the final scan set.

Related deferred work:

- Show plan phase/status in the desktop WebView UI.
- Surface plan telemetry in history/details views.
- Only after telemetry is stable, consider conservative user-confirmed early stop behavior.

## Repository Cleanup

Historical refactor branches were merged and cleaned up around the April 2026 migration. Do not infer active branch strategy from old branch names in this file; use current git state and current task context instead.
