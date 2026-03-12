# AI Agent Handoff

Last updated: 2026-03-09
Project root: `skyscanner_multi_domain`

## 1. Project Purpose

This project is a Skyscanner multi-market comparison tool based on real Edge result pages.

Current workflow:

1. Connect to local Edge CDP on port `9222`
2. Open multiple Skyscanner market result pages for the same route/date
3. Read `document.body.innerText` from the rendered page
4. Extract both:
   - Best / 最佳
   - Cheapest / 最低价
5. Convert prices to CNY when FX is available
6. Save a Markdown comparison report

## 2. Active Entry Points

- `gui.py`
  - Main UI for non-technical users
  - Tkinter app
  - Launch with `python3 gui.py`
- `cli.py`
  - CLI wrapper around the same page-scan flow
  - Handles location resolution, effective market calculation, CNY conversion, Markdown output
- `skyscanner_neo.py`
  - Core runtime and parser
  - Region config, Edge/CDP logic, page extraction, label matching, market defaults

## 3. Current Output Contract

Saved report path:

- `outputs/reports/edge_page_<origin>_<destination>_<yyyymmdd>.md`

Current Markdown table columns:

- 地区
- 最佳（原币）
- 最佳（人民币）
- 最低价（原币）
- 最低价（人民币）
- 状态
- 错误
- 链接

GUI table is aligned with the same fields.
CLI console output also prints both Best and Cheapest summaries.

## 4. Important Current Behavior

### Smart effective regions

Region selection is no longer just a fixed manual default.

`cli.py` and `gui.py` now use `build_effective_region_codes(...)` from `skyscanner_neo.py`.
That means effective regions are built from:

- baseline defaults
- origin country
- destination country
- user-provided extra region codes

### Current baseline default regions

Current baseline defaults are:

- `CN`
- `HK`
- `SG`
- `US`
- `UK`

Important:

- `JP` and `KR` were explicitly removed from default baseline regions by user request
- they can still be added manually

### GUI region input semantics

GUI field is now “额外地区代码”, not “全部地区代码”.
The hint line shows:

- default included regions
- actual effective regions for the current route

### CLI region input semantics

CLI `-r/--regions` now means extra regions to append to smart defaults, not replace them.

## 5. Parsing Fixes That Matter

The recent work focused on fixing incorrect Best vs Cheapest extraction.

### Main issue

Many markets were returning suspicious results where:

- Best == Cheapest too often, or
- Best was lower than Cheapest, or
- parser matched non-flight content such as hotel modules

### Root cause

Generic label matching was too loose in some locales.
Some pages contain unrelated modules that include words like “最佳”, which caused false Best matches.

### Important fixes now in place

#### A. Region-specific best/cheapest labels

Key region-specific labels include:

- `CN`: Best uses `综合最佳`, `最优`, `最佳`; Cheapest uses `最便宜`
- `HK`: Best uses `最優`, `最佳`; Cheapest uses `最便宜`
- `SG`: Best uses `综合最佳`, `最优`, `最佳`; Cheapest uses `最便宜`
- `SE`: Best `Bäst`, Cheapest `Billigast`
- `KR`: Best `추천순`, Cheapest `최저가`
- `JP`: Best `おすすめ`, `おすすめ順`; Cheapest `最安値`

#### B. Candidate ranking uses price hints

The parser now prefers labeled blocks that look like real flight price sections.
Hints include phrases such as:

- `总费用为`
- `費用總計`
- `价格低至`
- `價格低至`
- `最低只要`
- `起`

This reduces contamination from unrelated modules.

#### C. Best-candidate recovery logic

If the first Best candidate is lower than Cheapest, parser does not blindly accept it.
It attempts to recover by picking a later Best candidate that is >= Cheapest.
If no valid candidate exists, Best is dropped and status becomes inconsistent.

#### D. Null-body protection in CDP page reads

Page reads now tolerate temporary `document.body == null` instead of crashing.

## 6. Files Most Relevant To Future Work

### `skyscanner_neo.py`

Most important file for extraction correctness.
Key areas:

- `BASELINE_REGIONS`
- `build_effective_region_codes(...)`
- `REGION_BEST_LABELS`
- `REGION_CHEAPEST_LABELS`
- `get_flight_results_scope(...)`
- `extract_labeled_page_price_candidates(...)`
- `best_candidates_for_region(...)`
- `extract_page_quote(...)`
- `run_page_scan(...)`

If Best/Cheapest parsing breaks again, start here first.

### `cli.py`

Important responsibilities:

- location resolution
- effective region calculation
- FX conversion via `FxRateService`
- report rendering
- CLI summary output

### `gui.py`

Important responsibilities:

- UI inputs
- effective region hinting
- route/city resolution
- displaying Best/Cheapest columns
- running the same scan flow in a worker thread

## 7. Verified Recent Result Pattern

A recent generated report for `BJSA -> CDG` on `2026-04-28` showed:

- FR, HK, CN, UK, US, SG returning both Best and Cheapest
- JP returning Cheapest-only in that run
- after CN/HK/SG fixes, those markets no longer defaulted to obviously wrong identical values

This report path was used during validation:

- `outputs/reports/edge_page_BJSA_CDG_20260428.md`

## 8. Known Remaining Constraints

### Anti-bot / challenge pages

Skyscanner may still show challenge or loading pages.
Current code distinguishes:

- challenge
- loading
- parse failure
- cheapest-only / best-only / inconsistent cases

### JP can still be partial

JP has been observed returning Cheapest-only in some runs.
That is a known limitation, not fully solved.

### Live page text can vary by session

Because extraction depends on rendered text in a real browser, results may change with:

- locale redirects
- challenge state
- logged-in / cookie state
- timing of page readiness

## 9. Recent Git / Merge Context

A clean Skyscanner parsing fix commit was created in a worktree and then merged into local `main`.
Local `main` had unrelated dirty changes at the time, so the safe flow used was:

1. stash existing `main` changes
2. cherry-pick the clean Skyscanner fix commit
3. restore stashed changes
4. resolve the remaining conflict in `skyscanner_neo.py`

Result:

- Skyscanner fix exists on local `main`
- prior unrelated local changes were preserved

## 10. Safe Next Steps For Another Agent

Recommended order:

1. Read this file
2. Read `cli.py`, `gui.py`, `skyscanner_neo.py`
3. If debugging extraction, inspect a saved report first
4. If needed, validate against live Edge page text before changing parsing rules
5. Keep JP/KR out of baseline defaults unless the user asks otherwise

## 11. Things To Avoid

- Do not change region defaults casually; user explicitly requested JP/KR be excluded by default
- Do not reduce the output back to a single price column
- Do not remove GUI/CLI effective-region hinting without request
- Do not trust generic Best labels in CN/HK/SG without scoped/labeled validation
- Do not overwrite existing local uncommitted work on `main`
