# AI Agent Handoff

Last updated: 2026-05-07
Project root: `skyscanner_multi_domain`

## 1. Current Product Path

The desktop WebView app is the only active end-user product path:

1. `desktop_webview.py` starts the desktop shell.
2. `webui/` provides the bundled React UI.
3. `desktop_ui_service.py` bridges UI actions to the scan engine.
4. Core scan modules live under `skyscanner_multi_domain/`.

The CLI remains supported as a developer entry for automation, smoke tests, debugging, SearchPlan inspection, and report export. The Tk GUI and root-level shims are legacy-only.

## 2. Directory Map

Primary product:

- `desktop_webview.py` — desktop app shell
- `desktop_ui_service.py` — UI bridge and scan worker coordination
- `webui/` — bundled React UI assets

Developer entry:

- `cli.py` — headless runner and diagnostic/export interface

Core engine:

- `skyscanner_multi_domain/planning/search_plan.py` — candidate scoring, explain plan, batches, plan metadata
- `skyscanner_multi_domain/planning/execution_policy.py` — exact/fast/repair execution policy scaffold; exact is default
- `skyscanner_multi_domain/scan/orchestrator.py` — scan orchestration, fallback routing, quote formatting
- `skyscanner_multi_domain/scan/repair.py` — failed-market repair plan builder
- `skyscanner_multi_domain/scan/history.py` — scan history, preview cache, plan telemetry
- `skyscanner_multi_domain/transports/opencli.py` — default browser automation transport
- `skyscanner_multi_domain/transports/cdp.py` — CDP/browser fallback transport
- `skyscanner_multi_domain/transports/scrapling.py` — Scrapling legacy fallback transport
- `skyscanner_multi_domain/parsing/page_parser.py` — Best/Cheapest page parser
- `skyscanner_multi_domain/parsing/readiness.py` — OpenCLI page readiness classifier
- `skyscanner_multi_domain/parsing/price_candidates.py` — candidate price collection, embedded JSON recovery, ranking
- `skyscanner_multi_domain/diagnostics/snapshots.py` — bounded OpenCLI failure snapshots
- `tools/replay_parser_snapshots.py` — offline parser replay for OpenCLI snapshots
- `skyscanner_multi_domain/geo/location_resolver.py` — location/country/airport resolution
- `skyscanner_multi_domain/geo/regions.py` — market/region configuration
- `skyscanner_multi_domain/models.py` — shared data models
- `skyscanner_multi_domain/planning/date_window.py` — date windows and trip labels
- `skyscanner_multi_domain/runtime/paths.py` — project/runtime paths
- `skyscanner_multi_domain/diagnostics/attempt_trace.py` — attempt trace logging
- `skyscanner_multi_domain/pricing/fx_rates.py` — FX conversion

Compatibility shims:

- Root-level `app_paths.py`, `attempt_trace.py`, `date_window.py`, `fx_rates.py`, `skyscanner_models.py`, `scan_orchestrator.py`, `scan_history.py`, `search_plan.py`, `transport_*.py`, `skyscanner_page_parser.py`, `location_resolver.py`, and `skyscanner_regions.py` re-export from the package modules.
- Keep these shims for compatibility with old imports, tests, and mock targets; do not add new logic there.
- New code must import package paths, not root-level shims. New tests should prefer package paths unless they explicitly verify compatibility.
- Keep shims for at least two small versions or until all tests/mock targets are migrated. Before removing them, run full pytest, CLI smoke, and desktop import smoke.

Legacy:

- `legacy/gui.py` and `gui.py` are deprecated Tk entry points. Only fix startup-level breakage.
- `skyscanner_neo.py` is a compatibility / legacy Neo entry. It still owns existing Neo CLI, replay, and URL mutation behavior, but it is not a new feature entry point.

Historical notes:

- Old refactor notes and branch history are in `docs/history/2026-04-refactor-notes.md`.

## 3. Data Flow

User input flows through:

1. UI or CLI parses route/date/market options.
2. `geo/location_resolver.py` resolves endpoints and country-expanded airport candidates.
3. `geo/regions.py` builds effective market candidates.
4. `planning/search_plan.py` ranks route/date/market candidates and can render `--show-plan`.
5. `scan/orchestrator.py` scans ordered candidates with opencli, CDP fallback, then Scrapling fallback.
6. OpenCLI uses a serial bounded tab pool; `region_concurrency` controls retained tab lanes, not parallel region execution.
7. `parsing/readiness.py` classifies extracted page text as `price_ready`, `still_loading`, `challenge`, `empty_shell`, `no_flights`, or `unknown_parse_surface`.
8. `parsing/page_parser.py` and `parsing/price_candidates.py` extract Best/Cheapest prices, collect ranked `PriceCandidate` evidence, and recover safe embedded JSON/script candidates.
9. Parser trust metadata is attached to quotes and result rows: confidence, price source, evidence text, warnings, candidate count, selected rank, and candidate sources.
10. `diagnostics/snapshots.py` stores bounded OpenCLI snapshots for parse failures, low-confidence recoveries, and price disagreement cases.
11. CLI Markdown reports add a decision summary, trust columns, and warning/evidence details.
12. `scan/history.py` stores rows, quote snapshots, preview cache, plan telemetry, fetch quality telemetry, parser recovery telemetry, and snapshot summary.

Current SearchPlan behavior is intentionally conservative:

- It ranks and explains candidates.
- It attaches `plan_rank`, `plan_reason`, route/date/market ranks, and telemetry.
- It emits opencli batch progress with `active_plan_phase` and `plan_batch_*` fields.
- It does not prune or reduce the final scan set.
- It does not early stop or skip scan tasks.
- It does not bypass challenge/captcha pages; challenge handling is identify, record, and require manual review or later retry.
- ExecutionPolicy is a separate layer from SearchPlan. Exact mode is default. Fast mode scaffold must stay explicit, auditable, and disabled by default.

## 4. Do Not Modify By Default

- Do not add new user-facing features to `legacy/gui.py` or `gui.py`.
- Follow `docs/legacy_tk_policy.md`; legacy Tk is frozen for compatibility only.
- Do not put new core logic into root-level compatibility shims.
- Do not add new product logic to `skyscanner_neo.py`; move new Neo-related code into package modules first.
- Do not turn `webui/` into a standalone cloud/web product.
- Do not introduce SearchPlan pruning until explainability, plan metadata, and telemetry are stable.
- Do not add challenge/captcha bypass logic.
- Do not turn OpenCLI `region_concurrency` into implicit high-concurrency scraping without explicit design work.

## 5. Common Test Commands

```bash
python -m py_compile cli.py desktop_ui_service.py skyscanner_neo.py
python -m py_compile skyscanner_multi_domain/scan/orchestrator.py
python -m py_compile skyscanner_multi_domain/planning/search_plan.py
python -m py_compile skyscanner_multi_domain/planning/execution_policy.py skyscanner_multi_domain/scan/repair.py
python -m py_compile skyscanner_multi_domain/parsing/readiness.py skyscanner_multi_domain/parsing/price_candidates.py
python -m py_compile tools/replay_parser_snapshots.py
pytest -q
python cli.py page -o 北京 -d 阿拉木图 -t 2026-05-20 --date-window 1 --show-plan
python tools/replay_parser_snapshots.py logs/snapshots/opencli --json
```

## 6. Current Next Tasks

- Keep `desktop_ui_service -> cli.SimpleCLI` as explicit P1 debt; do not expand desktop reuse of `SimpleCLI`.
- Add richer WebView warning drill-down with evidence snippets per row.
- Build failed-market repair actions beyond queue retry.
- Start `desktop_ui_service -> cli.SimpleCLI` extraction toward a package query service.
- Only after explainability, batch progress, and telemetry are stable, consider conservative user-confirmed early stop in fast mode.

Recently completed:

- Parser diagnostics/confidence metadata now flows through `FlightQuote` and scan/report rows.
- CLI Markdown reports show a `扫描结论` section, confidence/source/warning columns, and warning/evidence details.
- Parser trust metadata tests and CLI report tests cover missing legacy fields, fallback warnings, decision risk hints, and date-window reports.
- Desktop WebView status now surfaces SearchPlan phase/batch progress.
- Desktop WebView result rows show confidence/source/warning trust fields.
- Desktop history details show SearchPlan telemetry, failure reasons, and parser trust summaries.
- OpenCLI fetch quality telemetry distinguishes final price found, OpenCLI direct hits, fallback rescued results, failure classes, tab reuse, extract attempts, and max chunk observed.
- PriceCandidate metadata flows through quotes/history: candidate count, selected rank, and candidate sources.
- OpenCLI failure snapshots and `tools/replay_parser_snapshots.py` provide an offline parser recovery loop.
- Repair Mode can build failed-market repair plans without rescanning successful markets; challenge tasks are manual review by default.
- WebView state exposes fetch quality, parser recovery, snapshot, candidate, fallback, and repair-plan fields for Trust UX.

The fuller execution backlog is in `docs/todo.md`.

## 7. Known Pitfalls

- Browser scraping is slow and unstable; avoid high concurrency as a default.
- History data may contain old rows without plan metadata; code must tolerate missing `plan_*` fields.
- Root-level shims are compatibility only. Updating logic in both shim and package will cause drift.
- Some tests intentionally import old root-level names to verify compatibility.
