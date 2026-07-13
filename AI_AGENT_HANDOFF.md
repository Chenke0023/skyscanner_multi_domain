# AI Agent Handoff

## Current architecture

The supported runtime is direct system-browser CDP only:

- `page` is the default stable scanner.
- `cdp_structured` is an explicit experimental parser on the same CDP session.
- Chrome, Edge, or Comet must be installed, unless a CDP endpoint is already running.
- Desktop WebView, location search, history, reports, and exports remain supported.

Primary ownership:

- `desktop_webview.py`: desktop shell
- `desktop_ui_service.py`: UI bridge and worker boundary
- `skyscanner_multi_domain.scan.orchestrator`: scan lifecycle
- `skyscanner_multi_domain.transports.cdp`: browser/CDP runtime
- `skyscanner_multi_domain.scan.result_service.ResultService`: result presentation

The old dependency direction `desktop_ui_service -> cli.SimpleCLI` is forbidden; desktop code calls package services directly.

## Maintenance rules

- Do not add another browser stack, bundled browser, download path, compatibility shim, or cross-transport fallback.
- Keep runtime dependencies limited to what the desktop and direct CDP path use.
- Preserve the macOS build gates: app ≤ 150MB and ZIP ≤ 60MB.
- Run `python3 -m pytest -q`, WebUI tests/build, `python3 scripts/release_smoke.py`, and the standalone build before release.
