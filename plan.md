# Anthropic-Style UI Refresh Plan

## Goal
Transform the desktop web UI from a dense, full-screen dashboard into a lightweight, command-driven companion app that feels native to macOS and aligns with Anthropic’s design philosophy (minimal chrome, layered disclosure, conversational flow).

## Guiding Principles
1. **Small footprint first**: Default window ~1000×680, min_size down to ~760×520.
2. **Single-column flow**: Replace the fixed three-column grid with a vertical card stream.
3. **Blank-slate home**: Show only the query card on launch; reveal results progressively after a scan.
4. **Hide by default**: Logs, alerts, environment checks, and history live in drawers/menus, not permanent panels.
5. **Neutral palette**: Reduce warm gradients, borders, and shadows; rely on whitespace and one subtle accent color.
6. **Command-driven interaction**: Promote natural-language or streamlined query entry; advanced parameters are secondary.

---

## Phase 1 — De-bloat the Window (foundation)
- [x] Shrink default window in `desktop_webview.py` to `width=1000, height=680` and `min_size=(760, 520)`.
- [x] Remove the persistent right column (alerts / environment / logs) from `App.tsx`.
- [x] Collapse the left column into a side drawer triggered by a top-left icon button.
- [x] Convert the hero block (title, description, metrics) into a compact header bar; remove the long subtitle paragraph.
- [x] Reduce global padding and card border/shadow usage in `styles.css`.

## Phase 2 — Blank-Slate Home + Progressive Disclosure
- [x] On initial load, show only a centered query card (origin, destination, dates, trip type) and a primary action button.
- [x] Hide all result cards (cheapest, recommendation, top-recs, tables, calendar) until at least one scan has completed.
- [x] Replace empty-state placeholders with nothing; if a section has no data, it simply does not render.
- [x] Stream results vertically after scanning: cheapest → recommendation → top recs → “Details” expando.
- [x] Add a first-launch regression test that keeps result stream, raw tables, and logs hidden before scanning.

## Phase 3 — Streamline Results & Tables
- [x] Replace the permanent success/failure tables with a single toggle button: “Show raw results”.
- [x] Move the insight panel tabs (calendar, compare, history, table) under the “Details” expando.
- [x] Remove the “table focus” tab; it provides no unique content.
- [x] Add a filter summary line above raw results with a one-click “Clear filters” reset.

## Phase 4 — Relocate Secondary Features
- [x] Move alert configuration into a top-right gear-icon drawer.
- [x] Move environment status to a bottom status-bar dot (green/yellow/red) with a hover tooltip; full check output opens from the settings drawer.
- [x] Move logs into the same gear drawer or a dedicated “Logs” menu item; never show them by default.
- [x] Move history & favorites into the left drawer, keeping the main canvas clean.

## Phase 5 — Visual Palette Simplification
- [x] Replace the warm radial-gradient background with a near-white or very light gray solid (`#fafafa` / `#f7f5f2`).
- [x] Reduce card border radius from 22px to 14–16px and drop heavy gradients/shadows.
- [x] Consolidate accent colors to a single neutral tone; remove gold-vs-stone card variants.
- [x] Increase whitespace between cards; let spacing create hierarchy instead of borders.

## Phase 6 — Component Refactor
- [x] Split `App.tsx` into smaller modules:
  - [x] `components/QueryCard.tsx`
  - [x] `components/ResultStream.tsx`
  - [x] `components/RawResults.tsx`
  - [x] `components/Drawer.tsx`
  - [x] `components/StatusBar.tsx`
- [x] Keep the `DesktopBridge` and `DesktopUIService` contracts unchanged; this is a frontend-only refactor.
- [x] Add a structural regression test so `App.tsx` keeps delegating result, raw table, drawer, query, and status UI to components.

## Out of Scope (for this branch)
- Replacing the Python bridge or scan engine.
- Adding true natural-language parsing (can be faked with smart defaults first).
- Changing the build toolchain or packaging logic.

## Success Criteria
1. Window opens at ≤1000×680 and feels comfortable on a 13-inch MacBook without maximizing.
2. First-time launch shows the query card only: no result stream, raw tables, logs, or expanded advanced controls before scanning.
3. No permanent empty-state text blocks are visible on launch.
4. All previous functionality (alerts, logs, history, tables, calendar) remains reachable within two clicks.
