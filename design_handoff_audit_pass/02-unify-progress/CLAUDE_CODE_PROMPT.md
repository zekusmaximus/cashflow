# Prompt for Claude Code — Pass 02: Unify the progress story

Paste this into Claude Code running inside the `liquidity-gate` repo.

**Prereq:** Pass 01 (`design_handoff_audit_pass/01-color-semantics/`) merged. The new components in this pass reference the recolored tokens (`ember` for essentials-still-open, `tide` for HYSA progress).

---

I'm applying the second visual pass from the audit handoff. The spec lives at `design_handoff_audit_pass/02-unify-progress/SPEC.md` and is written against the **live** codebase vocabulary — `Source intake`, `coreReady`, `coreSources`, `openLater`, `isEssential` — not the audit HTML mockup's `Document intake` / `obtained` / `missing` terms.

1. **Read the SPEC end to end** before touching any code. It collapses three duplicate progress surfaces (header `StatusPills`, intake `MetricTile` grid, watch-root counter) into one `IntakeProgress` object plus a conditional `EssentialsBanner`, and tightens the sticky header.

2. **Optional context:** open `Design Audit.html` at the project root and read findings `f-stat-dupes` and `f-essentials-cta`.

3. **Changes by file:**

   - `src/components/layout/app-shell.tsx`:
     - Trim the brand block to one line (drop "Local · 2026")
     - Shorten one tab: `Cash-flow dashboard` → `Cash flow`. **Keep `Source intake` as-is.**
     - Delete `StatusPills`; add `HysaRail` in its place (HYSA gate progress is permanent header chrome on both views)
     - Remove `coreReady`, `coreSources`, `openLater` from `AppShellProps`

   - `src/App.tsx` (or wherever AppShell is called):
     - Drop the three source-count props from the AppShell call
     - The intake view still needs `coreReady` / `coreSources` / `openLater` — make sure they reach it through whatever path is already in place (probably via the `useDocumentChecklist` query result inside the view itself)

   - `src/features/document-intake/document-intake-view.tsx`:
     - Collapse the eyebrow + H1 + paragraph into a single-line title (`Phase 1` + `Source intake`)
     - Delete the `MetricTile` 4-up grid. Delete `MetricTile` itself if no other consumer exists (check first)
     - Delete the "Add item" and "Rescan folder" buttons
     - Add new `<IntakeProgress>` component (segmented rail: core ready / essentials gap / open later)
     - Add new `<EssentialsBanner>` component (only renders when `essentialsGap > 0`; "Start with essentials" filters the table to `priority: essential, status: open`)
     - Lift the table filter state into the view so the banner can drive it

   - `src/features/dashboard/dashboard-view.tsx`:
     - Collapse the page title the same way: `Phase 2` + `Cash flow`
     - Remove the HYSA gate KPI tile (it's in the header rail now); KPI strip becomes 3-up

4. **The derived value:**
   ```ts
   const essentialsGap = Math.max(0, coreSources - coreReady);
   ```
   No new field on `ChecklistSummary` is needed — this is computed at the consumer. If your data model exposes "essential sources still open" under a different name, use that instead.

5. **The filter contract** the EssentialsBanner pushes onto the table:
   ```ts
   { status: 'open', priority: 'essential' }
   ```
   Adapt the literal enum values to whatever the existing table filter accepts (e.g. `isEssential: true, status: 'open'`). The semantic intent is "narrow to essential AND still-needed" — the literal shape is local detail.

6. **Do NOT touch:**
   - TanStack Query hooks
   - Matcher logic
   - SQLite bootstrap / Tauri command names / capabilities
   - Python MCP server
   - Matcher tests
   - The category sidebar (becomes a filter in pass 03)
   - The "Why needed" column (collapses in pass 03)
   - The chart, leakage cards, or any dashboard surface beyond the page title + HYSA tile (pass 04)

7. **Edge cases:**
   - `coreSources === 0`: no NaN; rail renders as the empty track
   - `essentialsGap === 0`: banner doesn't render; headline drops the essential callout
   - `coreReady === coreSources` AND `openLater === 0`: rail is fully moss

8. After implementing, verify:
   - `npm run dev` at 1280px: vertical chrome above the intake table is ~140px (roughly half what it was)
   - Header `HysaRail` appears on both Source intake and Cash flow views
   - Clicking "Start with essentials" filters the table to essential + open
   - `npm test` passes
   - No new TypeScript errors

When you're done, show me the diff plus a screenshot of the Source intake view at 1280px so I can confirm the vertical-space savings landed.
