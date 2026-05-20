# Handoff: Liquidity Gate — Audit pass

A second visual + interaction pass on the Document Intake and Cash-Flow Dashboard views, building on the calmer pass already shipped via `design_handoff_ui_pass/`.

The full rationale lives in `Design Audit.html` at the project root — open it in a browser. The audit calls out 22 findings; this handoff package implements the highest-impact 5 as a series of small, independently-mergeable PRs.

---

## Why staged, not monolithic

The previous handoff worked because it was one cohesive pass (calmer visuals + overflow bug fixes) touching exactly 3 files with a single mental model. This pass covers 5 different concerns with different blast radii — bundling them would produce a 1000-line diff with no clean bisect path.

Each numbered subfolder is a self-contained PR. Land them in order; verify each in the Tauri build before starting the next.

| # | Pass | Scope | Risk | Est. effort |
|---|---|---|---|---|
| **01** | Color semantics | Class-name swaps across 3 files. No state changes. | Very low | 1–2 hrs |
| **02** | Unify progress story | Drop duplicate KPI tiles, introduce a single progress object + essentials CTA. | Low | ½ day |
| **03** | Sidebar as filter, collapse "Why needed" column, reveal-in-Finder | Adds filter state composition + Tauri shell invocation. | Medium | 1 day |
| **04** | Quiet the chart, add leakage drill-down | Hover tooltips on bars, net-by-month strip, transaction side panel, fix the misleading delta math. | Medium | 1–2 days |
| **05** | Keyboard surface | ⌘K command palette, ⌘1/⌘2 tab switching, ⌘R force rescan, focus-ring polish. | Medium-high | 1 day |

All five passes are scaffolded in this folder. Land them in order — each pass's SPEC notes which earlier passes it depends on.

---

## How to use each subfolder

Every subfolder has the same shape:

```
NN-name/
├── SPEC.md                 ← What to change, why, and exact code
└── CLAUDE_CODE_PROMPT.md   ← Paste this into Claude Code to drive the work
```

`SPEC.md` is the source of truth. `CLAUDE_CODE_PROMPT.md` is a short paste-in prompt that points Claude Code at the SPEC and sets the guardrails. Both files reference the audit by anchor (e.g. `Design Audit.html#f-color-conflict`).

---

## Files NOT to touch (any pass)

Same guardrails as the previous handoff. None of these passes should reach into:

- The matcher logic in `src/features/document-intake/matcher.ts`
- `bootstrapLocalDatabase` and the SQLite layer
- The `ChecklistDataset` / `DashboardSnapshot` type contracts
- Any Tauri command names or capabilities (`list_watch_root_files`, etc.)
- The Python MCP server under `server/`
- Matcher tests in `__tests__/`
- The CSV ingest path

These are pure visual + interaction passes.

---

## Verification, after each pass

1. `npm run dev` — both views render cleanly at 1280px, 1024px, and 768px with no horizontal scroll
2. `npm test` — all matcher / checklist tests still pass
3. `npm run tauri dev` — the desktop build talks to `list_watch_root_files` and the watch-root path resolves
4. Long-path stress: set the watch root to `/Users/jeff/Library/Application Support/CashFlow/intake/2026-archive/long-subfolder/` and confirm the path truncates without pushing the file counter off-screen

Each SPEC.md adds pass-specific acceptance criteria on top of these.

---

## Reference files in this folder

- `Design Audit.html` — the full audit document with filterable findings and before/after mocks. Open this first.
- `01-color-semantics/` — re-assign clay / tide / ember to one job each.
- `02-unify-progress/` — collapse duplicate stats into one progress object + essentials CTA.
