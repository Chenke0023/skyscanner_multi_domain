# Legacy Tk Policy

`legacy/gui.py` and root-level `gui.py` are frozen compatibility entry points.

Allowed changes:

- startup fixes
- import compatibility fixes
- security fixes
- narrowly scoped crash fixes

Disallowed changes:

- new SearchPlan UI
- new trust telemetry UI
- new repair panels
- new history drawers
- new parser evidence displays

All new end-user product UX must go through:

- `desktop_webview.py`
- `desktop_ui_service.py`
- `webui/`

Removal can be considered only after WebView covers required user workflows, root-level shim migration is complete, two small releases pass without Tk fixes, and full pytest plus desktop import smoke pass.
