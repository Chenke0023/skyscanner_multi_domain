# Current Todo List

This backlog is ordered for a solo developer. Keep `main` import-stable first, then continue SearchPlan work. Do not add dynamic pruning until explainability, batch execution, and telemetry are stable.

## P0: Main Stability

### 1. Keep import boundaries fixed

Status: active guardrail.

Todo:

- Keep `test_import_boundaries.py`.
- Keep documented package module importability checks.
- Package modules must not import root-level compatibility shims.
- Root shims are only for legacy imports, old tests, and mock targets.

Acceptance:

```bash
python -m pytest -q test_import_boundaries.py
python -m pytest -q
```

### 2. Record `desktop_ui_service -> cli.SimpleCLI` as explicit debt

Status: known P1 debt, not a current blocker.

Current issue:

```text
desktop_ui_service.py -> cli.SimpleCLI
```

Target direction:

```text
cli.py -> shared QueryService
desktop_ui_service.py -> shared QueryService
```

Todo:

- Do not expand desktop reuse of `SimpleCLI`.
- Move shared request building and scan orchestration into package code before adding new cross-entry behavior.
- Eventually introduce `skyscanner_multi_domain/app/query_service.py` or an equivalent package module.

Acceptance:

- Documentation names the dependency and target direction.
- New desktop features do not add more calls through `SimpleCLI`.

## P1: SearchPlan Explain And Batches

### 3. SearchPlan explain output

Status: implemented baseline.

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

### 4. SearchPlan outputs full execution plan

Status: implemented baseline.

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

### 5. Batch execution progress for opencli path

Status: next implementation target.

Todo:

- Have scan orchestration emit batch phase progress as each `ScanBatch` starts/finishes.
- Surface batch phase in CLI progress.
- Surface batch phase in desktop WebView service payloads.

Do not:

- Dynamically skip tasks.
- Automatically stop early.
- Add pruning logic.

Acceptance:

- Result set is unchanged.
- Higher-priority results can be displayed earlier.
- Progress payload includes the active batch phase and reason.

## P1: Result Trust And Evidence

### 6. Parser diagnostics in result objects

Status: planned.

Todo:

- Add confidence to `FlightQuote` / result rows.
- Add evidence fields.
- Track price source: `cheapest_block`, `best_block`, `first_price_fallback`, `recovered_best`, or `manual_confirmed`.
- Preserve parser warnings.

Acceptance:

- First-price fallback is medium/low confidence by default.
- Best/Cheapest disagreement produces a warning.
- Reports can display confidence and price source.

### 7. Decision report

Status: planned.

Todo:

- Add a Markdown conclusion section.
- Show lowest price, runner-up, spread, confidence, recommendation reason, and risks.
- Keep raw table as appendix.

Acceptance:

- A non-engineering user can see which option to verify first and why.

## P1: Engineering Boundary Cleanup

### 8. Remove `desktop_ui_service -> cli.SimpleCLI`

Status: planned.

Target:

```text
skyscanner_multi_domain/app/query_service.py
```

Todo:

- Extract non-CLI behavior from `SimpleCLI`.
- Keep `cli.py` focused on argparse, printing, and export.
- Have `desktop_ui_service.py` call package service code.

Acceptance:

- `desktop_logic.py` does not import `cli`.
- `desktop_ui_service.py` does not import `cli`.
- Add `test_desktop_ui_service_does_not_import_cli` when the dependency is removed.

### 9. Define `skyscanner_neo.py` lifecycle

Status: documented as compatibility / legacy.

Todo:

- Short term: do not add new product logic.
- Mid term: move replay and URL mutation into package modules.
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

Status: planned.

Todo:

- Show active phase: core route, edge dates, nearby dates, remaining verification.
- Show tentative lowest price.
- Show what is being verified next.

### 12. Failed-market repair panel

Status: planned.

Todo:

- Classify failed markets: loading, parse, network, challenge, browser missing.
- Offer actions: retry, extend wait, open browser, skip.

## P2: Telemetry And Quality

### 13. SearchPlan telemetry

Status: baseline telemetry exists; expand after batch progress.

Track:

- `total_tasks`
- `first_valid_price_task_index`
- `best_price_task_index`
- `best_market_rank`
- `best_date_rank`
- `best_route_rank`
- `failed_tasks_by_reason`

### 14. Market reliability score

Status: planned.

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

### 15. User-confirmed price loop

Status: planned.

Todo:

- Add "open and confirm price".
- Store confirmed / mismatched local sample.
- Promote confirmed samples into parser fixtures.

## P3: Release And CI

### 16. Release hygiene

Todo:

- GitHub Release
- version number
- changelog
- macOS app build smoke
- simplified README install/run path

### 17. CI

Minimum:

```bash
python -m pytest -q
python -m py_compile cli.py desktop_webview.py desktop_ui_service.py
npm --prefix webui run build
```

## Suggested Next Five Tasks

1. Keep the explicit `desktop_ui_service -> cli.SimpleCLI` debt visible.
2. Emit SearchPlan batch progress from scan orchestration.
3. Display SearchPlan phase/status in desktop WebView.
4. Expand plan telemetry in history/details views.
5. Add parser diagnostics to result objects.

## Do Not Do Yet

- Do not dynamically prune scan tasks.
- Do not delete root shims.
- Do not rewrite the GUI.
- Do not add new features to legacy Tk.
- Do not turn this into a standalone web SaaS.
