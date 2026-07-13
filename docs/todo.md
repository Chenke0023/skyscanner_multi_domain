# Engineering TODO

## Supported product path

- Desktop WebView and CLI call the package scan services directly.
- `page` is the stable default; `cdp_structured` is an explicit experiment on the same CDP runtime.
- A running CDP endpoint or installed Chrome, Edge, or Comet is required.
- Location search, history, reports, confirmations, and exports remain supported.

The removed dependency direction `desktop_ui_service -> cli.SimpleCLI` must not return. Result formatting belongs to `ResultService` and package services.

## Release checklist

- `python3 -m pytest -q`
- `cd webui && npm test -- --run && npm run build`
- `python3 scripts/release_smoke.py`
- `scripts/build_macos_standalone_app.sh`
- Confirm `.app` ≤ 150MB and ZIP ≤ 60MB.
- Confirm the bundle contains no retired browser stack or development-only dependency.
- Smoke-test an existing CDP endpoint and automatic launch of an installed system browser.

## Broad exception audit

The only intentional broad exception boundary is `desktop_ui_service.py` in `_worker_boundary`, where background worker failures are normalized for the UI. All other application boundaries should catch specific exceptions.
