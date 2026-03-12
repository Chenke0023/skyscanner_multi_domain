# AI Agent Handoff

Last updated: 2026-03-12
Project root: `skyscanner_multi_domain`

## 1. Project Purpose

This project compares Skyscanner prices across multiple markets by reading real result pages from a local Edge instance over CDP.

Current workflow:

1. Connect to local Edge CDP on port `9222`
2. Expand the requested date into a date window when enabled
3. Open the same route/date on multiple Skyscanner market domains
4. Read page text from the rendered result page
5. Extract both Best and Cheapest prices
6. Convert prices to CNY when FX is available
7. Save per-day Markdown reports plus an optional window summary

## 2. Active Entry Points

- `gui.py`
  - Main UI for non-technical users
  - Tkinter app
- `cli.py`
  - CLI wrapper around the same scan flow
  - Handles location resolution, smart effective regions, FX conversion, and Markdown output
- `skyscanner_neo.py`
  - Scan orchestrator
  - Browser/CDP detection and page polling
  - Neo compatibility path
- `skyscanner_regions.py`
  - Region config and smart region selection
- `skyscanner_page_parser.py`
  - Best/Cheapest extraction logic
  - Long-page text slicing helpers used to avoid the old 12,000-char truncation issue
- `skyscanner_models.py`
  - Shared dataclasses (`RegionConfig`, `FlightQuote`)

## 3. Current Output Contract

Saved report path:

- `outputs/reports/edge_page_<origin>_<destination>_<yyyymmdd>.md`
- `outputs/reports/edge_page_<origin>_<destination>_<start>_<end>_summary.md` (when date window summary is enabled)

Current Markdown columns:

- 地区
- 最佳（原币）
- 最佳（人民币）
- 最低价（原币）
- 最低价（人民币）
- 状态
- 错误
- 链接

GUI table is aligned with the same fields.
CLI also prints Best and Cheapest winners when available.

Date-window behavior:

- CLI default: `--date-window 3`
- GUI default: `±3` days
- setting `0` means single-day scan only
- GUI can optionally save a combined window summary

## 4. Runtime Paths

Project-local outputs:

- reports: `outputs/reports/`
- logs: `logs/`

State directory:

- browser profiles: `$XDG_STATE_HOME/skyscanner_multi_domain/browser-profiles/`
- FX cache: `$XDG_STATE_HOME/skyscanner_multi_domain/fx_rates_cache.json`

Fallback when `XDG_STATE_HOME` is not set:

- `~/.local/state/skyscanner_multi_domain/`

Legacy behavior still supported:

- if an old profile exists under `outputs/*-cdp-profile`, runtime attempts a one-time move into the state directory

## 5. Important Current Behavior

### Smart effective regions

`cli.py` and `gui.py` use `build_effective_region_codes(...)`.
Effective regions are built from:

- baseline defaults
- origin country
- destination country
- user-provided extra region codes

### Current baseline default regions

- `CN`
- `HK`
- `SG`
- `US`
- `UK`

Important:

- `JP` and `KR` are intentionally excluded from baseline defaults
- they can still be added manually

### GUI / CLI region semantics

- GUI field means extra regions, not full replacement
- CLI `-r/--regions` also appends to smart defaults, not replaces them

### Date window semantics

- the center date is always included
- the window expands symmetrically around the center date
- each day still saves its own report
- the summary file aggregates all dates into one table

## 6. Parsing Notes That Matter

### Best / Cheapest parsing

The parser is locale-aware and uses region-specific labels where needed.
Examples:

- `CN`: Best uses `综合最佳`, `最优`, `最佳`; Cheapest uses `最便宜`
- `HK`: Best uses `最優`, `最佳`; Cheapest uses `最便宜`
- `SG`: Best uses `综合最佳`, `最优`, `最佳`; Cheapest uses `最便宜`
- `SE`: Best `Bäst`, Cheapest `Billigast`
- `KR`: Best `추천순`, Cheapest `최저가`
- `JP`: Best `おすすめ`, `おすすめ順`; Cheapest `最安値`
- `ID`: Best `Terbaik`; Cheapest `Termurah`

### Long-page text capture fix

Older runs could fail when the sort section appeared after the first 12,000 characters of `document.body.innerText`.
Current behavior now:

- CDP capture keeps up to 80,000 chars
- capture is anchored around sort-section hints / labels when they appear later in the page
- parser also applies the same anchored slicing as a fallback guard

Regression tests now cover:

- old 12,000-char budget with late sort section
- label-only anchoring without explicit sort-section hint
- very long pages where the sort section appears near the end

### Best-candidate recovery logic

If the first Best candidate is lower than Cheapest, parser does not blindly accept it.
It attempts to recover by selecting a later Best candidate that is `>= Cheapest`.
If no valid candidate exists, Best is dropped and status becomes `page_text_inconsistent`.

### Challenge / loading pages

The parser explicitly distinguishes:

- `page_challenge`
- `page_loading`
- `page_parse_failed`
- `page_text_best_only`
- `page_text_cheapest_only`
- `page_text_inconsistent`
- `page_text_recovered_best`

## 7. Files Most Relevant To Future Work

- `skyscanner_page_parser.py`
  - start here for Best/Cheapest extraction bugs
- `skyscanner_regions.py`
  - start here for market defaults and host aliases
- `skyscanner_neo.py`
  - start here for CDP/browser/page polling behavior
- `cli.py`
  - start here for output rendering, date-window scanning, and CLI summary behavior
- `gui.py`
  - start here for UI behavior, date-window controls, and scan-thread orchestration
- `date_window.py`
  - start here for date-range generation behavior

## 8. Known Constraints

### Anti-bot / challenge pages

Skyscanner may still show challenge or loading pages.
Results depend on the live browser session and can vary with:

- locale redirects
- challenge state
- cookies / login state
- timing of page readiness

### JP can still be partial

JP has been observed returning Cheapest-only in some runs.
That remains a known limitation.

## 9. Safe Next Steps For Another Agent

Recommended order:

1. Read this file
2. Read `README.md`
3. Inspect `skyscanner_page_parser.py`, `skyscanner_regions.py`, and `skyscanner_neo.py`
4. Validate parser changes with `python3 -m pytest -q test_skyscanner_neo.py`
5. Validate date-window behavior with `python3 -m pytest -q test_date_window.py`
6. Validate live behavior against a real Edge session before changing label rules again

## 10. Things To Avoid

- Do not change baseline default regions casually; `JP` / `KR` were intentionally excluded
- Do not revert output back to a single-price table without an explicit request
- Do not assume project-local `data/browser-profiles/` is the active runtime path
- Do not trust generic Best labels in CN/HK/SG without scoped validation
- Do not delete browser profile state unless the user explicitly accepts losing verification state
