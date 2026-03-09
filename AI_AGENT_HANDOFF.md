# AI Agent Handoff

Last updated: 2026-03-08
Project root: `skyscanner_multi_domain`

## 1. Project Purpose

This project is now a focused Skyscanner multi-market comparison tool.

Current supported workflow:

1. Launch a dedicated Edge instance with CDP on port `9222`
2. Open multiple Skyscanner market result pages for the same route/date
3. Read page text from the rendered page via CDP
4. Extract the lowest visible price
5. Normalize prices to CNY
6. Save a simplified Markdown comparison table

The project no longer keeps older experimental implementations such as Playwright/Patchright/API-only variants.

## 2. Active Entry Points

- `gui.py`
  - Main user-facing GUI for non-technical users
  - Tkinter app
  - Launchable directly with `python3 gui.py`
- `cli.py`
  - Simplified CLI wrapper around the same page-scan flow
  - Handles location normalization, CNY conversion, Markdown output
- `skyscanner_neo.py`
  - Core runtime logic
  - Browser/CDP detection
  - Edge auto-launch
  - browser profile cache pruning
  - page polling and extraction
- `Skyscanner 多市场比价.app`
  - macOS app bundle wrapper
  - Launches `gui.py` from the project root
- `scripts/build_macos_app.sh`
  - Rebuilds the macOS `.app`

## 3. Key Runtime Behavior

### Browser / CDP

- Uses a dedicated Edge profile at:
  - `data/browser-profiles/edge-cdp-profile`
- If an older profile still exists under `outputs/edge-cdp-profile`, the runtime now attempts a one-time move into `data/browser-profiles/`
- CDP endpoint:
  - `http://localhost:9222`
- If CDP is unavailable, the app attempts to auto-launch Edge
- Before auto-launch, cache-like directories in the dedicated profile are pruned to reduce disk usage while preserving session state

### Why the dedicated profile exists

It isolates this tool from the user's daily browser and preserves:

- cookies
- local/session storage
- Skyscanner anti-bot / human-verification state

It intentionally does **not** preserve all cache files. Cache cleanup is part of the runtime now.

## 4. Output Contract

Current output is intentionally minimal.

Saved format:

- Markdown file in `outputs/`
- Markdown file in `outputs/reports/`
- Example:
  - `outputs/reports/edge_page_BJSA_ALA_20260429.md`

Table columns:

- Region
- Price in CNY
- Link

The output intentionally omits:

- original source currency
- verbose status/debug fields
- JSON result dumps

GUI display is aligned with this simplified output.

## 5. Current File Layout

Expected important files/directories:

- `README.md`
- `AI_AGENT_HANDOFF.md`
- `gui.py`
- `cli.py`
- `skyscanner_neo.py`
- `requirements.txt`
- `scripts/build_macos_app.sh`
- `Skyscanner 多市场比价.app`
- `vendor/neo/`
- `outputs/`
- `outputs/reports/`
- `logs/`
- `data/browser-profiles/`

Likely present but non-essential:

- `__pycache__/`
- old `outputs/gui_app.log` from before the log path cleanup

## 6. Major Decisions Already Made

### Decision A: Keep only the current working path

The repository was aggressively cleaned.

Removed:

- old experimental scripts
- screenshots
- old JSON outputs
- old reports/guides
- legacy `src/` and `utils/` tree

Reason:

- the project had too many stale entry points
- only the current GUI/CLI + CDP page-read path is maintained

### Decision B: Prefer real browser page text over old request replay attempts

Current extraction uses real page rendering and CDP page text.

Reason:

- site anti-bot behavior made older approaches brittle
- the GUI workflow benefits from a real browser that a human can interact with when required

### Decision C: Preserve verification state, prune caches

Profile pruning now targets cache-heavy directories only.

Reason:

- full profile deletion wastes solved verification state
- full retention made the profile grow too large

## 7. Known Constraints

### Anti-bot / human verification

Skyscanner may still present human verification.

Current behavior:

- the scanner polls instead of finishing immediately after a fixed delay
- challenge/loading pages are explicitly distinguished from ordinary parse failures
- if the user completes the challenge in Edge before timeout, extraction can continue

### Location normalization

User-facing inputs are normalized before scan.

Examples already mapped:

- `北京` -> `BJSA` by default unless strict airport mode is enabled
- `阿拉木图` -> `ALA`
- `雅加达` / `Jakarta` -> `JKT`

If an input cannot be normalized, GUI/CLI fail early with a clear error instead of opening a broken URL.

### Markets

The current default market list remains:

- `CN`
- `US`
- `UK`
- `SG`
- `HK`
- `KZ`

Behavior can still differ by redirect and anti-bot state.

## 8. Important Implementation Notes

### `cli.py`

Owns:

- location normalization
- FX conversion to CNY
- simplified output rows
- Markdown table generation

If changing the output format, start here first.

### `gui.py`

Owns:

- visible app layout
- input validation
- real-time input-to-code hints
- simplified result grid

The app currently favors clarity over rich UX.

### `skyscanner_neo.py`

Owns:

- Edge/Chrome detection
- CDP readiness checks
- browser auto-launch
- cache pruning
- page polling
- quote extraction

If changing browser behavior, start here first.

## 9. Verified Status As Of Last Update

Verified recently:

- `gui.py`, `cli.py`, `skyscanner_neo.py` compile successfully
- GUI initializes successfully
- location hints resolve correctly for known cities
- Markdown output is generated correctly
- cache pruning previously reduced `outputs/edge-cdp-profile` from roughly `454M` to roughly `208M`; active runtime location is now `data/browser-profiles/edge-cdp-profile`

## 10. Remaining Improvement Ideas

These are not yet implemented:

- GUI market checkboxes instead of free-text region entry
- explicit GUI notice when the browser is waiting on human verification
- clickable link opening directly from the GUI table
- a GUI button for manual browser-cache cleanup
- richer city-code suggestions / autocomplete

## 11. Safe Next Steps For Another Agent

If continuing work, recommended order:

1. Read `README.md`
2. Read this file
3. Inspect `gui.py`, `cli.py`, `skyscanner_neo.py`
4. Preserve the dedicated Edge profile approach unless deliberately redesigning the runtime
5. Avoid reintroducing deleted legacy variants unless there is a strong reason

## 12. Things To Avoid

- Do not delete `data/browser-profiles/edge-cdp-profile` casually unless the user explicitly accepts losing verification/browser state
- Do not re-expand the project with multiple stale prototype entry points
- Do not switch the saved output back to verbose JSON unless the user asks for debugging-oriented output
