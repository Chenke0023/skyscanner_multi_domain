# AI Agent Handoff

Last updated: 2026-04-09
Project root: `skyscanner_multi_domain`

## 0. Current Development Status (branch: `main`)

### Completed changes (latest)

- Added trip-mode support across CLI and GUI:
  - one-way and round-trip flows share the same scan pipeline
  - round-trip date windows preserve stay length
  - GUI includes departure / return date picker controls
- Added expanded endpoint support in CLI and GUI:
  - location -> location
  - location -> country
  - country -> location
  - country -> country
- Added country expansion logic in `location_resolver.py`:
  - country alias / ISO resolution
  - curated candidate-airport selection per country
  - mixed endpoint expansion into concrete airport pairs
- Added mixed-route aggregation behavior:
  - when one or both endpoints are countries, the app scans candidate airport pairs
  - per-market results are aggregated back to the best available route per market
  - output now preserves the winning `航段` for each market row
- Added CLI support for:
  - `--origin-country`
  - `--destination-country`
  - `--country-airport-limit`
- Updated GUI support for:
  - `出发地按国家`
  - `目的地按国家`
  - mixed location/country combinations without forcing both endpoints into country mode
- Added regression coverage in `test_location_resolver.py` and new `test_cli.py`
- Repository housekeeping completed:
  - current active local branch set is only `main`
  - current active remote branch set is only `origin/main`

- Refactored `skyscanner_neo.py` (1664 lines) into four focused modules:
  - `transport_scrapling.py` (330 lines): Scrapling fetch, captcha detection, `compare_via_scrapling`
  - `transport_cdp.py` (376 lines): CDP browser management, page transport, `compare_via_pages`
  - `scan_orchestrator.py` (226 lines): `run_page_scan`, fallback routing, failure logging, output formatting
  - `skyscanner_neo.py` (890 lines): Neo CLI, capture replay, URL rewriting, backward-compat re-exports
- All existing imports from `skyscanner_neo` continue to work via re-exports
- Test mock targets updated from `skyscanner_neo.*` to actual source modules (`transport_cdp.*`, `transport_scrapling.*`)
- All 15 tests pass; `cli.py` and `gui.py` imports verified
- The refactor branch has already been merged into `main`
- Historical local/remote feature branches have been cleaned up; `main` is now the only active branch to continue from

### Previously completed (in `main`)

- Added a new transport path: `--transport scrapling` in CLI `page` command.
- `run_page_scan(...)` now supports transport routing:
  - `scrapling` (primary + default)
  - `page` (Edge CDP fallback)
- Implemented `compare_via_scrapling(...)` in `skyscanner_neo.py` with:
  - lazy import and graceful `scrapling_unavailable` handling
  - staged `StealthyFetcher.fetch(...)` retries
  - HTTP fallback via `Fetcher.get(...)`
- Added Scrapling text extraction cleanup:
  - prefer visible HTML text extracted from parsed DOM
  - strip `script/style/noscript/template` payloads before parsing
  - fallback to CSS text extraction and raw body/text/html/content accessors
- GUI call path updated to pass `transport="scrapling"` explicitly.
- Dependencies updated:
  - `requirements.txt` includes `scrapling[fetchers]>=0.3.0`.
- Parser updated so loading/challenge hints no longer mask valid Best / Cheapest prices when real price text is already present.
- Added failure artifact persistence:
  - final market-level failures are written to `logs/failures/`
  - logs include route, transport, region, status, error, URL, and page-text excerpt
- Added per-market automatic fallback:
  - when Scrapling ends in retryable failure states, only the failed market is retried via `page`
- Added regression tests for:
  - script payload pollution in Scrapling text extraction
  - loading text coexisting with valid price blocks
  - Scrapling -> page per-market fallback routing
- Added GUI table column-header sorting:
  - click any column header to sort all rows across all dates
  - price columns sort numerically; text columns sort lexicographically
  - repeated click toggles ascending ↑ / descending ↓
  - sort state resets on new scan
- Added GUI per-region progress bar and status:
  - `ttk.Progressbar` shows overall scan progress
  - status text updates per-region: `正在扫描 2026-04-29 [中国] (attempts/expected: 3/49)`
  - `run_page_scan` and `compare_via_scrapling` accept `on_region_start` callback
- Added GUI cancel button:
  - "取消" button appears in status bar during scanning
  - uses `threading.Event` to signal worker thread to exit between dates/regions
  - GUI resets to ready state on cancel
- Added clickable links in GUI result table:
  - double-click the "链接" column to open the Skyscanner result page in default browser
  - uses `webbrowser.open()`

### Verified results (latest)

- Syntax check: passed
- Unit tests: passed (`15/15` in `test_skyscanner_neo.py`, plus `test_date_window.py`)
- E2E comparison (same route/date):
  - `--transport page`: returns valid Best/Cheapest prices for tested regions
  - `--transport scrapling`: returns valid Best/Cheapest prices for default regions (`CN, HK, SG, UK, KZ`) on `BJSA -> ALA`, `2026-04-29`
- Main branch status:
  - Refactor + transport split work merged into `main`
  - `scrapling` is now the default production path in both CLI and GUI
  - failed markets under Scrapling now auto-fallback to `page` on a per-market basis
  - latest GUI live run can be treated as successful under the current assumption

### Repository housekeeping completed on 2026-03-13

- Merged `refactor/split-skyscanner-neo` into `main`
- Commit `876ac14` ("docs: update README and AI_AGENT_HANDOFF for module split") is already included in `main`
- Recorded historical merges for:
  - `codex/restore-date-window-and-split-parser`
  - `worktree/skyscanner-multi-domain`
- Removed merged local branches
- Removed no-longer-needed remote branches
- The four-module NEO split remains intact in `main`; it was not reverted or deleted

### Current status

`scrapling` is now the primary transport and has been verified as the default CLI path for the tested route/date/regions above. The same transport is also the default GUI path.

When a market still ends in a final Scrapling failure state (`page_loading`, `page_parse_failed`, challenge-like states, etc.), runtime now automatically retries just that market through the `page` transport instead of failing the whole scan path immediately.

The `page` transport remains in the codebase as a compatibility fallback and debugging path, but it is no longer the recommended default.

### Next implementation target (optional future work)

- Add richer per-attempt diagnostics into failed Scrapling `error` messages
- Expand live validation to more routes / markets before considering removal of the `page` path

## 1. Project Purpose

This project compares Skyscanner prices across multiple markets by fetching and parsing real Skyscanner result pages.

Current workflow:

1. Expand the requested date into a date window when enabled
2. Resolve each endpoint as either a single location or a country-expanded airport set
3. Open the same route/date on multiple Skyscanner market domains
4. Use Scrapling to fetch page content and extract visible text
5. Parse both Best and Cheapest prices from the sort/results section
6. Convert prices to CNY when FX is available
7. Save per-day Markdown reports plus an optional window summary
8. Automatically retry only failed markets via local Edge CDP when Scrapling hits retryable final states

## 2. Active Entry Points

- `gui.py`
  - Main UI for non-technical users
  - Tkinter app
  - Supports one-way / round-trip
  - Supports mixed location/country endpoint expansion
  - Supports column-header click-to-sort across all dates
  - Per-region progress bar with cancel support
  - Double-click link column to open in browser
- `cli.py`
  - CLI wrapper around the same scan flow
  - Handles location/country resolution, smart effective regions, FX conversion, and Markdown output
- `skyscanner_neo.py`
  - Neo compatibility layer: NeoCli wrapper, capture replay, URL rewriting, payload mutation
  - Re-exports all moved symbols for backward compatibility
  - Legacy `doctor` / `compare` CLI subcommands
- `scan_orchestrator.py`
  - Scan routing and fallback logic (`run_page_scan`)
  - Failure logging, output formatting (`print_quotes`, `quotes_to_dicts`)
- `transport_scrapling.py`
  - Scrapling fetch with staged retries, captcha detection
  - `compare_via_scrapling`
- `transport_cdp.py`
  - Browser detection, CDP management, page text capture
  - `compare_via_pages`
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

- 航段
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

Mixed endpoint behavior:

- when one side is a country, file tokens use `<ISO>_ANY`
- example: `edge_page_BJSA_UZ_ANY_20260520.md`
- result rows preserve the concrete winning route, such as `BJSA -> TAS`

Date-window behavior:

- CLI default: `--date-window 3`
- GUI default: `±3` days
- setting `0` means single-day scan only
- GUI can optionally save a combined window summary

## 4. Runtime Paths

Project-local outputs:

- reports: `outputs/reports/`
- logs: `logs/`
- failure samples: `logs/failures/`

State directory:

- browser profiles: `$XDG_STATE_HOME/skyscanner_multi_domain/browser-profiles/`
- FX cache: `$XDG_STATE_HOME/skyscanner_multi_domain/fx_rates_cache.json`

Fallback when `XDG_STATE_HOME` is not set:

- `~/.local/state/skyscanner_multi_domain/`

Legacy behavior still supported:

- if an old profile exists under `outputs/*-cdp-profile`, runtime attempts a one-time move into the state directory

Failure logging behavior:

- any final market-level failure now persists a debug artifact under `logs/failures/`
- the log includes route, transport, region, status, error, source URL, and a page-text excerpt when available

## 5. Important Current Behavior

### Smart effective regions

`cli.py` and `gui.py` use `build_effective_region_codes(...)`.
Effective regions are built from:

- baseline defaults
- origin country
- destination country
- user-provided extra region codes

When endpoint expansion is enabled:

- single-location endpoints still contribute their resolved country to smart regions
- country endpoints contribute their ISO country code directly
- mixed location/country routes therefore keep the same smart-region behavior

### Current baseline default regions

- `CN`
- `HK`
- `SG`
- `UK`

Important:

- `JP` and `KR` are intentionally excluded from baseline defaults
- they can still be added manually

### GUI / CLI region semantics

- GUI field means extra regions, not full replacement
- CLI `-r/--regions` also appends to smart defaults, not replaces them

### Endpoint expansion semantics

- point-to-point mode remains the default when no country argument/toggle is enabled
- GUI can enable country expansion independently for origin and destination
- CLI can enable country expansion independently via `--origin-country` and `--destination-country`
- country expansion is intentionally capped by `--country-airport-limit` / the GUI default limit to avoid route-count explosion
- mixed-route scans aggregate per-market winners back into one row per market

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

Older runs could fail when the sort section appeared after the first 12,000 characters of captured page text.
Current behavior now:

- long page text is sliced around sort-section hints / labels when they appear later in the page
- parser applies the same anchored slicing as a fallback guard
- Scrapling text extraction strips embedded script payloads before parsing, reducing false loading/challenge matches

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

It now prefers valid parsed prices over transient loading text when both appear in the same page snapshot.

## 7. Files Most Relevant To Future Work

- `skyscanner_page_parser.py`
  - start here for Best/Cheapest extraction bugs
- `skyscanner_regions.py`
  - start here for market defaults and host aliases
- `scan_orchestrator.py`
  - start here for scan routing, fallback logic, and `run_page_scan`
- `transport_scrapling.py`
  - start here for Scrapling transport behavior, retry logic, and captcha detection
- `transport_cdp.py`
  - start here for CDP browser management, page text capture, and `compare_via_pages`
- `skyscanner_neo.py`
  - start here for Neo CLI, capture replay, URL rewriting; also holds backward-compat re-exports
- `cli.py`
  - start here for output rendering, endpoint expansion, date-window scanning, and CLI summary behavior
- `gui.py`
  - start here for UI behavior, date-window controls, endpoint-mode toggles, scan-thread orchestration, progress/cancel, and link opening
- `location_resolver.py`
  - start here for airport/metro/country resolution and candidate-airport expansion
- `date_window.py`
  - start here for date-range generation behavior

## 8. Known Constraints

### Anti-bot / challenge pages

Skyscanner may still show challenge or loading pages.
Results depend on live site behavior and can vary with:

- locale redirects
- challenge state
- cookies / login state
- timing of page readiness

The primary mitigation is now Scrapling retry / text-cleanup logic. The `page` transport remains available if manual fallback is needed.

### JP can still be partial

JP has been observed returning Cheapest-only in some runs.
That remains a known limitation.

## 9. Safe Next Steps For Another Agent

Recommended order:

1. Read this file
2. Read `README.md`
3. Inspect `skyscanner_page_parser.py`, `skyscanner_regions.py`, `scan_orchestrator.py`, `transport_scrapling.py`, and `transport_cdp.py`
4. Validate parser changes with `python3 -m pytest -q test_skyscanner_neo.py`
5. Validate date-window behavior with `python3 -m pytest -q test_date_window.py`
6. Validate live Scrapling behavior first; use `--transport page` only if you need to compare against the browser-based fallback

## 10. Things To Avoid

- Do not change baseline default regions casually; `JP` / `KR` were intentionally excluded
- Do not revert output back to a single-price table without an explicit request
- Do not assume project-local `data/browser-profiles/` is the active runtime path
- Do not trust generic Best labels in CN/HK/SG without scoped validation
- Do not delete browser profile state unless the user explicitly accepts losing verification state
- Do not reframe the project docs back to “Edge/CDP-first” unless the primary transport changes again
