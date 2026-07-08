# AI Agent Handoff

Last updated: 2026-05-07
Project root: `skyscanner_multi_domain`

## 1. Current Product Path

The desktop WebView app is the only active end-user product path:

1. `desktop_webview.py` starts the desktop shell.
2. `webui/` provides the bundled React UI.
3. `desktop_ui_service.py` bridges UI actions to the scan engine.
4. Core scan modules live under `skyscanner_multi_domain/`.

The CLI remains supported as a developer entry for automation, smoke tests, debugging, SearchPlan inspection, and report export.

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
- `skyscanner_multi_domain/transports/scrapling.py` — Scrapling fallback transport
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

Flat compatibility shims (removed):

- The root-level flat shims (`app_paths.py`, `attempt_trace.py`, `date_window.py`, `fx_rates.py`, `skyscanner_neo.py`, `skyscanner_models.py`, `scan_orchestrator.py`, `scan_history.py`, `search_plan.py`, `transport_*.py`, `skyscanner_page_parser.py`, `location_resolver.py`, `skyscanner_regions.py`) have been removed.
- Active/internal callers now import package paths (`skyscanner_multi_domain.*`) directly.
- `test_import_boundaries.py` keeps a `ROOT_SHIMS` deny-list so package code never re-introduces these flat root names. Do not recreate removed flat root-level shims; new code must import package paths.

Neo tooling:

- `skyscanner_multi_domain/neo.py` owns the Neo CLI, replay, doctor, and compare behavior.
- `test_import_boundaries.py` guards that the root Neo shim stays removed.

Historical notes:

- Flat root-level compatibility shims have been removed, including `skyscanner_neo.py`.

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

- Do not recreate removed flat root-level compatibility shims; import package paths (`skyscanner_multi_domain.*`) directly.
- Do not recreate `skyscanner_neo.py`; keep Neo-related code in package modules such as `skyscanner_multi_domain.neo`.
- Do not turn `webui/` into a standalone cloud/web product.
- Do not introduce SearchPlan pruning until explainability, plan metadata, and telemetry are stable.
- Do not add challenge/captcha bypass logic.
- Do not turn OpenCLI `region_concurrency` into implicit high-concurrency scraping without explicit design work.

## 5. Common Test Commands

```bash
python -m py_compile cli.py desktop_ui_service.py skyscanner_multi_domain/neo.py
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

- Keep `desktop_ui_service -> cli.SimpleCLI` as retired P1 debt; prevent regressions that reintroduce the import.
- Keep the WebView component split (`QueryCard`, `ResultStream`, `RawResults`, `Drawers`, `StatusBar`) behavior-preserving; bridge/service contracts still live outside React components.
- `tests/test_webui_component_boundaries.py` guards the WebView Phase 6 component split so `App.tsx` does not reabsorb result-table or drawer helper code.
- `webui/src/App.test.tsx` now includes a first-launch blank-slate regression, and `webui/src/testSetup.ts` performs RTL cleanup after each test.
- Failed-market repair actions now support queue retry, run retry, extend wait, open manual-review links, and skip current repair tasks.
- Keep `cli.py` focused on argparse/printing/export; query/planning lives in `skyscanner_multi_domain.scan.query_service.QueryService`, and desktop result processing lives in `skyscanner_multi_domain.scan.result_service.ResultService`.
- CI now runs ruff, full non-vendor source mypy, frontend test/build, release smoke, and pytest via `.github/workflows/ci.yml`; mypy currently covers 61 non-vendor, non-WebUI source files.
- Broad swallowed exceptions in primary runtime/transport paths now log at debug/warning instead of disappearing silently; the current audit has no bare `except` handlers and 1 non-vendor broad boundary handler remaining.
- `tests/test_exception_boundaries.py` now guards the remaining broad-handler allow-list.
- Market reliability now feeds SearchPlan ordering from recent success, parser confidence, historical win rate, fallback dependence, and challenge/loading risk without reducing the task set.
- Neo capture URL/payload/header helpers now live in `skyscanner_multi_domain.scan.url_builder`; Neo CLI behavior now lives in `skyscanner_multi_domain.neo`.
- User-confirmed price loop baseline records confirmed/mismatched WebView rows to `runtime/price_confirmations.jsonl` and shows confirmation counts in Trust UX.
- Release hygiene baseline is in place: version 1.2.1, `CHANGELOG.md`, quick README install/run path, and `scripts/release_smoke.py`.
- Only after explainability, batch progress, and telemetry are stable, consider conservative user-confirmed early stop in fast mode.

Recently completed:

- Extracted location resolution, route planning, and query-payload building from `cli.SimpleCLI` into `skyscanner_multi_domain.scan.query_service.QueryService`; `desktop_ui_service` now routes all query/location work through `self.query`.
- Removed the remaining thin `SimpleCLI` query route/effective-region wrapper methods; CLI call sites now use `self._query_service` directly.
- `test_simple_cli_does_not_reintroduce_thin_service_wrappers` guards against thin `SimpleCLI` service wrappers returning.
- Extracted desktop result processing and markdown persistence into `skyscanner_multi_domain.scan.result_service.ResultService`; `desktop_ui_service.py` no longer imports `cli`.
- Enabled Ruff Bugbear (`B`) and expanded mypy to the full non-vendor source gate of 61 files, including package Neo compatibility modules.
- Audited bare `except ...: pass` handlers and replaced broad silent catches in captcha, runtime paths, orchestrator, CDP, structured CDP, Google jump, OpenCLI, and Scrapling paths with logging.

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
- SearchPlan market reliability score now reflects parser confidence, fallback dependence, and challenge/loading risk in addition to success and win history.
- Extracted Neo capture selection, URL rewriting, payload mutation, header preparation, response quote extraction, and the Neo CLI into package modules; the root `skyscanner_neo.py` shim is removed.
- WebView repair panel actions now call `apply_repair_action` for class-specific queue retry, selected-region rerun, extended-wait rerun, challenge link opening, and per-task skip.
- WebView success rows now expose `确认` / `不符` actions backed by `skyscanner_multi_domain.scan.confirmation.PriceConfirmationStore`.
- Added release smoke checks for version consistency, changelog coverage, README install path, removed flat root shims, PyInstaller bundle version wiring, CI gates, and built WebView assets.
- Completed the WebView Phase 6 component refactor: `App.tsx` now orchestrates state/bridge callbacks while query entry, results, raw tables, drawers, and status bar live in smaller components.

The fuller execution backlog is in `docs/todo.md`.

## 7. Known Pitfalls

- Browser scraping is slow and unstable; avoid high concurrency as a default.
- History data may contain old rows without plan metadata; code must tolerate missing `plan_*` fields.
- Flat root-level compatibility shims have been removed; `test_import_boundaries.py` enforces a `ROOT_SHIMS` deny-list so package code never re-introduces flat root imports. Do not recreate shims.
- Tests live under `tests/` (module-organized) plus four root-level entry/structural tests (`test_cli`, `test_desktop_ui_service`, `test_failure_replay`, `test_import_boundaries`).
