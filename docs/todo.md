# Current Todo List

This backlog is ordered for a solo developer. Keep `main` import-stable first, then continue SearchPlan work. Do not add dynamic pruning until explainability, batch execution, and telemetry are stable.

## P0: Main Stability

### 1. Keep import boundaries fixed

Status: active guardrail.

Todo:

- Keep `test_import_boundaries.py`.
- Keep documented package module importability checks.
- Package modules must not import root-level compatibility shims (enforced via the `ROOT_SHIMS` deny-list).
- Root-level shims have been removed; all callers import `skyscanner_multi_domain.*` directly. Do not recreate shims.

Acceptance:

```bash
python -m pytest -q test_import_boundaries.py
python -m pytest -q
```

### 2. Keep `desktop_ui_service -> cli.SimpleCLI` removal guarded

Status: implemented. Desktop no longer imports `cli`.

Current issue:

```text
desktop_ui_service.py -> cli.SimpleCLI  (removed)
```

Target direction:

```text
cli.py -> skyscanner_multi_domain.scan.query_service.QueryService
desktop_ui_service.py -> skyscanner_multi_domain.scan.query_service.QueryService
desktop_ui_service.py -> skyscanner_multi_domain.scan.result_service.ResultService
```

Done:

- Extracted location resolution + query-payload building into
  `skyscanner_multi_domain/scan/query_service.py`.
- `SimpleCLI` delegates the query slice to `QueryService`.
- `desktop_ui_service.py` calls `self.query.*` for all location/query work;
  no query-slice calls remain on `self.cli`.
- Extracted desktop result-processing and markdown persistence into
  `skyscanner_multi_domain/scan/result_service.py`.
- `desktop_ui_service.py` now uses `self.results.*`; no `cli` import remains.

Todo:

- Do not reintroduce desktop reuse of `SimpleCLI`.
- Continue shrinking duplicated CLI result helpers by delegating more CLI paths to
  `ResultService`.
  - Removed the thin `SimpleCLI` result wrapper methods for quote
    simplification, markdown/report persistence, route metadata, and snapshot
    conversion; CLI call sites now call `ResultService` directly.
  - Removed remaining thin query/result wrappers for query payload building,
    row sorting, and best-row selection.

Acceptance:

- Documentation names the dependency and target direction.
- `test_import_boundaries.py` asserts `desktop_ui_service.py` does not import `cli`.

## P1: SearchPlan Explain And Batches

### 3. SearchPlan explain output

Status: implemented.

Current implementation:

- `RouteCandidate.reason` and `score_breakdown`
- `DateCandidate.reason` and `phase`
- `MarketCandidate.reason` and `score_breakdown`
- `ScanTask.priority`, `phase`, and `reason`
- CLI `--show-plan`

Acceptance:

```bash
python cli.py page -o 北京 -d 阿拉木图 -t 2026-05-20 --date-window 1 --show-plan
```

The command prints the plan and does not start a live scan.

Verified 2026-07-07: the command prints market/date/route ordering, batches,
and task priorities, then exits without starting a live scan.

### 4. SearchPlan outputs full execution plan

Status: implemented.

Current structures:

- `SearchPlan`
- `RouteCandidate`
- `DateCandidate`
- `MarketCandidate`
- `ScanTask`
- `ScanBatch`

Constraint:

- This stage must not reduce the scan set. It only changes order and batch grouping.

Acceptance:

- Every original route/date/market combination still appears in `SearchPlan.tasks`.
- `sum(len(batch.tasks) for batch in plan.batches) == len(plan.tasks)`.
- `test_plan_task_count_unchanged_in_phase_two` passes.

Verified by `tests/planning/test_search_plan.py`.

### 5. Batch execution progress for opencli path

Status: implemented.

Do not:

- Dynamically skip tasks.
- Automatically stop early.
- Add pruning logic.

Acceptance:

- Result set is unchanged.
- Higher-priority results can be displayed earlier.
- Progress payload includes the active batch phase and reason.

Notes:

- SearchPlan batches now emit `plan_batch_start` and `plan_batch_complete`.
- Progress payload includes `active_plan_phase` and `plan_batch_*` fields.
- CLI progress prints SearchPlan batch start/complete lines.
- Desktop WebView service state and status updates include the active phase,
  batch index/count, reason, and completion flag.
- `test_opencli_emits_search_plan_batch_progress_without_dropping_regions`
  guards that batch progress is emitted without reducing the result set.
- No pruning, early stopping, or task skipping is enabled.

## P1: Result Trust And Evidence

### 6. Parser diagnostics in result objects

Status: implemented.

Current implementation:

- `FlightQuote` carries parser trust metadata: `confidence`, `price_source`, `evidence_text`, and `parser_warnings`.
- Scan/result row snapshots preserve the same trust fields for reports and history consumers.
- Price source values include `cheapest_block`, `best_block`, `first_price_fallback`, `recovered_best`, `manual_confirmed`, and `unpriced`.
- Parser warnings are preserved for Best/Cheapest disagreement, one-sided parses, recovered Best prices, and fallback-only extraction.

Acceptance:

- First-price fallback is medium/low confidence by default.
- Best/Cheapest disagreement produces a warning.
- Reports display confidence, price source, parser warning summaries, and warning evidence.

### 7. Decision report

Status: implemented for CLI Markdown reports.

Current implementation:

- Markdown exports now add a top-level `扫描结论` section before the raw price table.
- The conclusion names the first result to verify, runner-up, spread, route, market, date when relevant, confidence, source, and result link.
- Risk hints call out low-confidence primary prices, first-price fallback, parser warnings, risky failed markets, and fallback-only priced sets.
- Raw price rows remain under `价格明细` and now include confidence, price source, and parser warning summary columns.
- Rows with parser warnings also produce a `解析警告与证据` section with evidence snippets when available.

Acceptance:

- A non-engineering user can see which option to verify first and why.

## P1: Engineering Boundary Cleanup

### 8. Remove `desktop_ui_service -> cli.SimpleCLI`

Status: implemented for the desktop boundary.

Target:

```text
skyscanner_multi_domain/scan/query_service.py   (done)
skyscanner_multi_domain/scan/result_service.py  (done)
```

Todo:

- Extract non-CLI behavior from `SimpleCLI`.
  - Done: location resolution, route planning, query-payload building
    (now in `skyscanner_multi_domain/scan/query_service.py`).
  - Done: desktop result-processing (`simplify_quotes`, `save_simplified_results`,
    `save_window_results`, `rows_to_quote_snapshots`, row ranking helpers)
    (now in `skyscanner_multi_domain/scan/result_service.py`).
- Keep `cli.py` focused on argparse, printing, and export.
  - `cli.py` now keeps result rendering wrappers out of `SimpleCLI`; direct
    result operations are routed through `self._result_service` or
    `ResultService` helpers.
  - Query payload construction now calls `self._query_service` directly instead
    of preserving pass-through `SimpleCLI` methods.
- Have `desktop_ui_service.py` call package service code.

Acceptance:

- `desktop_logic.py` does not import `cli`.
- `desktop_ui_service.py` does not import `cli`.
- `test_desktop_ui_service_no_longer_imports_cli` guards the boundary.

### 9. Define `skyscanner_neo.py` lifecycle

Status: compatibility / legacy with package extraction in progress.

Todo:

- Short term: do not add new product logic.
- Mid term: move replay and URL mutation into package modules.
  - Done: capture selection, URL rewriting, payload mutation, header
    preparation, and capture response quote extraction live in
    `skyscanner_multi_domain/scan/url_builder.py`.
  - `skyscanner_neo.py` re-exports the moved helpers for backward
    compatibility.
- Long term: turn root `skyscanner_neo.py` into a shim or move active legacy code under `legacy/`.

Candidate split:

- `skyscanner_multi_domain/diagnostics/failure_replay.py`
- `skyscanner_multi_domain/scan/url_builder.py`
- `skyscanner_multi_domain/legacy/neo.py`

## P2: Product Experience

### 10. Freeze legacy Tk GUI

Status: active policy.

Todo:

- Do not add SearchPlan UI to Tk.
- Fix only startup-level legacy breakage.
- Put new UX into `desktop_webview.py`, `desktop_ui_service.py`, and `webui/`.

Acceptance:

- Docs continue to say desktop WebView is the only end-user product path.

### 11. Desktop WebView scan phase display

Status: implemented.

Current implementation:

- Desktop service progress state carries `active_plan_phase`, `plan_batch_*`, and plan task counts.
- WebView status bar shows the active SearchPlan phase, batch index/count, and batch reason while scanning.
- Existing result cards continue to show the tentative lowest price as partial scan rows arrive.

### 12. Failed-market repair panel

Status: implemented.

Current implementation:

- `scan/repair.py` classifies failed markets into parse, timeout/loading,
  challenge, no-flight, network, and other repair classes.
- WebView trust state exposes grouped repair tasks and failure-class counts.
- The WebView repair panel can queue class-specific retry tasks, run the retry
  queue, extend wait for loading/empty-shell failures, open challenge links for
  manual review, and skip current repair tasks without mutating scan history.
- Retry/extend actions rerun selected regions only; challenge remains manual
  review and is not bypassed automatically.
- `tests/scan/test_scan_repair.py` covers repair classification, no-flight
  exclusion, manual challenge review, status filtering, and serialization.

## P2: Telemetry And Quality

### 13. Search Efficiency, Recovery UX & Report v3

Status: implemented.

Current implementation:

- `scan/repair.py` builds repair plans from failed quotes.
- Successful markets are excluded from repair plans.
- `no_flights` is not repaired.
- `challenge` maps to manual review and is not automatically retried.
- `planning/execution_policy.py` defines Exact/Fast/Repair policy models.
- Exact mode remains the default and runs all planned tasks.
- Fast mode can defer non-probe batches in policy evaluation, but automatic skipping and early stop are not enabled by default.
- History persists exact-mode execution policy telemetry.
- WebView state exposes fetch quality, parser recovery, snapshot summary, repair plan, candidate metadata, fallback attempts, readiness, confidence, source, evidence, and warnings.
- WebView shows fetch trust summary, failure repair grouping, repair action buttons, and parser evidence snippets.
- WebView result drill-down now keeps fallback transport, status/action, and
  error/reason in the fallback chain instead of showing transport names only.
- WebView test coverage now asserts fallback detail text appears in the evidence
  panel and row drill-down.
- Markdown decision reports mark Exact Mode/full scan and include candidate sources/fallback chain when available.
- `docs/legacy_tk_policy.md` freezes legacy Tk.
- Import-boundary tests prevent legacy Tk from importing new trust/recovery modules.

Policy:

- No default pruning.
- No default early stop.
- No default task skipping.
- No challenge/captcha bypass.
- No legacy Tk feature work.

### 14. OpenCLI Fetch Reliability & Parser Recovery v2

Status: implemented.

Current implementation:

- Fetch telemetry now separates final result metrics from OpenCLI direct metrics:
  - `fetch_total_regions`
  - `fetch_price_found_count`
  - `fetch_price_found_rate`
  - `opencli_direct_attempted_count`
  - `opencli_direct_price_found_count`
  - `opencli_direct_price_found_rate`
  - `fallback_attempted_count`
  - `fallback_rescued_count`
  - `fallback_rescue_rate`
  - failure buckets for challenge, timeout, loading, parse failed, no flights, not attempted, and other failures
  - tab open/reuse/close totals, extract attempts, and max chunk observed
- CLI prints a fetch summary in this form:
  - `[fetch] final 7/10 markets found price, opencli direct 5, fallback rescued 2, challenge 1, tabs opened 3, reused 6`
- OpenCLI page readiness is classified before deciding retry/fallback:
  - `price_ready`
  - `still_loading`
  - `challenge`
  - `empty_shell`
  - `no_flights`
  - `unknown_parse_surface`
- Parser recovery now collects ranked price candidates from visible text and embedded JSON/script state.
- FlightQuote carries candidate count, selected rank, and candidate sources.
- OpenCLI saves bounded snapshots for parse failures, no-flight failures, low-confidence recovery, and candidate-bearing failures.
- `tools/replay_parser_snapshots.py` replays snapshot JSON files through the parser.
- `tools/benchmark_fetch.py --compare-transports page,opencli` can run the
  same route/date/market inputs across multiple transports and output one JSON
  comparison report.
- Benchmark runs have a hard `--max-run-seconds` guard so a slow transport
  returns a timeout row instead of hanging the comparison script.
- 2026-07-07 local smoke: `page,opencli` benchmark on 北京 -> 阿拉木图
  (`2026-05-20`, 1 run, 5 regions, max 10s/run) saved
  `runtime/benchmarks/bench_北京_阿拉木图_20260520_20260707_121522.json`;
  page found 5/5 prices in 5.7s, OpenCLI timed out at 10s with 0/5.

Policy:

- No SearchPlan pruning.
- No automatic early stop.
- No scan task skipping.
- No challenge/captcha bypass.
- No legacy Tk GUI feature work.

### 15. SearchPlan telemetry

Status: implemented in history/details.

Track:

- `total_tasks`
- `first_valid_price_task_index`
- `best_price_task_index`
- `best_market_rank`
- `best_date_rank`
- `best_route_rank`
- `failed_tasks_by_reason`

Current implementation:

- Scan history stores `plan_telemetry` on each recorded query payload.
- Desktop history details show task coverage, first valid task, best-price task, best market rank, and failure reasons.
- Desktop history details also summarize parser trust source distribution, low-confidence result count, fallback/recovered parse count, and parser warning count.

### 16. Market reliability score

Status: implemented.

Inputs:

- recent success rate
- parser confidence
- fallback penalty
- historical win rate
- challenge/loading penalty

Uses:

- market ordering
- result confidence
- UI risk hints

Current implementation:

- `SearchPlan` market ranking now combines historical win rate, recent success,
  parser confidence, fallback dependence, and challenge/loading risk.
- `MarketCandidate.reliability` stores the combined reliability score.
- `MarketCandidate.score_breakdown` includes `market_reliability`, and market
  reasons call out low reliability, fallback dependence, and challenge/loading
  risk when they are material.
- The planner still preserves the full route/date/market task set; reliability
  only changes task order and explanations.

### 17. User-confirmed price loop

Status: implemented.

Current implementation:

- Successful WebView result rows expose `确认` and `不符` actions.
- `desktop_ui_service.record_price_confirmation` stores confirmed or mismatched
  local samples in `runtime/price_confirmations.jsonl` via
  `skyscanner_multi_domain.scan.confirmation.PriceConfirmationStore`.
- Trust summary shows confirmed/total sample counts.
- The stored sample keeps row/date/route/market/link, CNY prices, confidence,
  price source, parser warnings, evidence text, status, and note fields.
- `PriceConfirmationStore.export_confirmed_parser_fixtures(...)` exports
  confirmed samples with evidence text into JSON parser-fixture payloads;
  mismatched or evidence-less samples are skipped.
- CLI `export-confirmations` exports the confirmed parser fixtures from the
  runtime confirmation store into `tests/fixtures/price_confirmations/` or a
  caller-provided `--output-dir`.
- `tests/scan/test_confirmation.py` covers confirmation storage and fixture
  export.

## P3: Release And CI

### 18. Release hygiene

Status: implemented for v1.2.1 release readiness.

Done:

- Version number is centralized in `data/version.txt` and mirrored in
  `pyproject.toml` and `webui/package.json`.
- `CHANGELOG.md` records release notes.
- `README.md` has a short install/run path for source and macOS app builds.
- `scripts/release_smoke.py` validates release metadata, PyInstaller data
  wiring, bundle version handling, and built WebView assets.
- CI runs frontend build and release smoke after Python lint/type checks.
- 2026-07-07 local release gates passed for v1.2.1:
  `python -m ruff check .`,
  `python -m mypy`, `python -m pytest -q`, `npm test -- --run &&
  npm run build`, and `python scripts/release_smoke.py`.
- 2026-07-07 local macOS app build smoke passed:
  `./scripts/build_macos_standalone_app.sh` produced
  `dist/Skyscanner 多市场比价.app` v1.2.1, executable present, size 229M.
- v1.2.1 is ready to tag and publish with the built macOS app artifact.

### 19. CI

Status: implemented in `.github/workflows/ci.yml`.

Minimum:

```bash
python -m ruff check .
python -m mypy
python -m pytest -q
```

Current scope:

- Ruff now includes Bugbear (`B`) in addition to `E4/E7/E9/F`.
- mypy is enabled for 43 source files across runtime, pricing, geo, diagnostics,
  parsing, selected planning modules, scan support/orchestration modules, and
  the primary transport implementations plus CLI, desktop, and Neo entry points.
- CI also runs `npm ci && npm run build` for `webui/` and
  `python scripts/release_smoke.py`.
- Full-project mypy remains future work; the first gate prevents the new shared
  and support modules from drifting without claiming the legacy app is fully typed.

### 20. Swallowed exception audit

Status: implemented for current runtime scope; remaining broad handlers are
documented boundary handlers.

Done:

- Replaced broad `except Exception: pass` in the main runtime/transport paths
  with debug or warning logs so cleanup/probe failures are no longer completely
  invisible.
- Reduced the broad-handler count from the initial 56 to 53 while preserving
  transport boundary behavior.
- Removed remaining bare `except` handlers; none are present in the current audit.
- Replaced broad config/manifest handlers in CLI manual-tabs loading and app
  build-manifest loading with `OSError` / `JSONDecodeError`.
- Replaced broad Google Jump HTTP fallback handlers with `aiohttp.ClientError`
  / `TimeoutError`.
- Replaced broad Neo raw-request HTTP handler with `aiohttp.ClientError` /
  `TimeoutError`; current non-vendor broad-handler count is 52.
- Replaced broad OpenCLI tab close/wait handlers with `OpenCLIError`;
  current non-vendor broad-handler count is 50.
- Replaced CDP version connection close broad handler with `OSError`;
  current non-vendor broad-handler count is 49.
- Replaced safe Scrapling CDP connection/cookie/probe handlers and structured
  CDP eval/poll/target-select handlers with transport-specific exception sets;
  current non-vendor broad-handler count is 40.
- Replaced Google Jump CDP navigation and Neo doctor session-persistence
  handlers with specific exception sets; current non-vendor broad-handler count
  is 38.
- Replaced CDP eval/page-loop handlers and OpenCLI orchestration/snapshot
  handlers with transport/file exception sets; current non-vendor broad-handler
  count is 31.
- Replaced scan-orchestrator parser/history/Google-Jump fallback handlers,
  Playwright probe cleanup handlers, page-snippet coercion, text extraction,
  captcha health/backend handlers, and deterministic captcha-quote build
  handlers with specific exception sets.
- Current non-vendor broad-handler count is 9; remaining broad handlers are
  top-level UI/benchmark/legacy boundaries or third-party Scrapling fetch
  boundaries where narrowing would risk changing external error behavior.
- `tests/tools/test_benchmark_fetch.py` now covers the benchmark boundary
  handler for scan exceptions and hard timeouts before any future narrowing.
- `tests/test_desktop_ui_service_boundaries.py` covers the desktop point-scan
  worker boundary for setup failures and cancel-preferred handling, plus the
  expanded-route scan worker setup-failure boundary.
- Desktop scan workers now share one `_handle_worker_exception` boundary path,
  so point and expanded scans keep the same cancel-vs-error behavior.
- `test_compare_via_scrapling_returns_failure_quote_when_fetchers_raise`
  covers Scrapling stealth/HTTP fetcher exceptions returning a failure quote
  instead of escaping the transport boundary.

Todo:

- Before changing any of the remaining broad boundary handlers, add focused
  coverage for the specific external exception being narrowed.

## Suggested Next Five Tasks

1. Keep the removed `desktop_ui_service -> cli.SimpleCLI` boundary guarded.
2. Add focused coverage before narrowing any remaining broad boundary handler.
3. Publish v1.2.1 release artifacts after the branch is pushed.

## Do Not Do Yet

- Do not dynamically prune scan tasks.
- Do not recreate root-level compatibility shims (they are removed; `ROOT_SHIMS` deny-list enforced).
- Do not rewrite the GUI.
- Do not add new features to legacy Tk.
- Do not turn this into a standalone web SaaS.
