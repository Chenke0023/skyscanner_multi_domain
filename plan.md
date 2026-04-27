# Anthropic-Style UI Refresh Plan

## Goal
Transform the desktop web UI from a dense, full-screen dashboard into a lightweight, command-driven companion app that feels native to macOS and aligns with Anthropic’s design philosophy (minimal chrome, layered disclosure, conversational flow).

## Guiding Principles
1. **Small footprint first**: Default window ~1000×680, min_size down to ~760×520.
2. **Single-column flow**: Replace the fixed three-column grid with a vertical card stream.
3. **Blank-slate home**: Show only the query input on launch; reveal results progressively after a scan.
4. **Hide by default**: Logs, alerts, environment checks, and history live in drawers/menus, not permanent panels.
5. **Neutral palette**: Reduce warm gradients, borders, and shadows; rely on whitespace and one subtle accent color.
6. **Command-driven interaction**: Promote natural-language or streamlined query entry; advanced parameters are secondary.

---

## Phase 1 — De-bloat the Window (foundation)
- [ ] Shrink default window in `desktop_webview.py` to `width=1000, height=680` and `min_size=(760, 520)`.
- [ ] Remove the persistent right column (alerts / environment / logs) from `App.tsx`.
- [ ] Collapse the left column into a side drawer triggered by a top-left icon button.
- [ ] Convert the hero block (title, description, metrics) into a compact header bar; remove the long subtitle paragraph.
- [ ] Reduce global padding and card border/shadow usage in `styles.css`.

## Phase 2 — Blank-Slate Home + Progressive Disclosure
- [ ] On initial load, show only a centered query card (origin, destination, dates, trip type) and a primary action button.
- [ ] Hide all result cards (cheapest, recommendation, top-recs, tables, calendar) until at least one scan has completed.
- [ ] Replace empty-state placeholders with nothing; if a section has no data, it simply does not render.
- [ ] Stream results vertically after scanning: cheapest → recommendation → top recs → “Details” expando.

## Phase 3 — Streamline Results & Tables
- [ ] Replace the permanent success/failure tables with a single toggle button: “Show raw results”.
- [ ] Move the insight panel tabs (calendar, compare, history, table) under the “Details” expando.
- [ ] Remove the “table focus” tab; it provides no unique content.
- [ ] Add a filter summary line above raw results with a one-click “Clear filters” reset.

## Phase 4 — Relocate Secondary Features
- [ ] Move alert configuration into a top-right gear-icon drawer.
- [ ] Move environment status to a bottom status-bar dot (green/yellow/red) with a hover tooltip; full check output opens in a small modal.
- [ ] Move logs into the same gear drawer or a dedicated “Logs” menu item; never show them by default.
- [ ] Move history & favorites into the left drawer, keeping the main canvas clean.

## Phase 5 — Visual Palette Simplification
- [ ] Replace the warm radial-gradient background with a near-white or very light gray solid (`#fafafa` / `#f7f5f2`).
- [ ] Reduce card border radius from 22px to 14–16px and drop heavy gradients/shadows.
- [ ] Consolidate accent colors to a single neutral tone; remove gold-vs-stone card variants.
- [ ] Increase whitespace between cards; let spacing create hierarchy instead of borders.

## Phase 6 — Component Refactor
- [ ] Split `App.tsx` into smaller modules:
  - `components/QueryCard.tsx`
  - `components/ResultStream.tsx`
  - `components/RawResults.tsx`
  - `components/Drawer.tsx`
  - `components/StatusBar.tsx`
- [ ] Keep the `DesktopBridge` and `DesktopUIService` contracts unchanged; this is a frontend-only refactor.

## Out of Scope (for this branch)
- Replacing the Python bridge or scan engine.
- Adding true natural-language parsing (can be faked with smart defaults first).
- Changing the build toolchain or packaging logic.

## Success Criteria
1. Window opens at ≤1000×680 and feels comfortable on a 13-inch MacBook without maximizing.
2. First-time launch shows ≤3 interactive elements before scanning.
3. No permanent empty-state text blocks are visible on launch.
4. All previous functionality (alerts, logs, history, tables, calendar) remains reachable within two clicks.
