# Prompt for Claude Code — Pass 02: Unify the progress story

Paste this into Claude Code running inside the `liquidity-gate` repo.

**Prereq:** Pass 01 (`design_handoff_audit_pass/01-color-semantics/`) must be merged first. The new components in this pass reference the recolored tokens (ember for blocked, tide for progress).

---

I'm applying the second visual pass from the audit handoff. The spec lives at `design_handoff_audit_pass/02-unify-progress/SPEC.md`. Please:

1. **Read the SPEC end to end** before touching any code. The change collapses three duplicate progress surfaces (header status pills, intake KPI tiles, watch-root counter) into one progress object, adds a primary CTA for essential-missing documents, and tightens the sticky header.

2. **Optional context:** open `Design Audit.html` at the project root, filter by "Collapse" and "Add", and read findings `f-stat-dupes` and `f-essentials-cta`. The proposed condensed layout sketch in section 5 of the audit is the target.

3. **Changes by file:**

   - `src/components/layout/app-shell.tsx`:
     - Trim the brand block to a single line (drop "Local · 2026")
     - Shorten tab labels: "Document intake" → "Intake", "Cash-flow dashboard" → "Cash flow"
     - Replace `StatusPills` with a new `HysaRail` component (the HYSA gate progress is now permanent header chrome on both views)
     - Remove "Show notes" toggle entirely
     - Simplify `AppShellProps`: drop `totalDocuments`, `obtainedDocuments`, `missingDocuments`

   - `src/features/document-intake/document-intake-view.tsx`:
     - Collapse the eyebrow + H1 + paragraph into a single-line title
     - Delete the 4-tile KPI strip
     - Delete the "Add item" and "Rescan folder" buttons
     - Add new `<IntakeProgress>` component (segmented rail: obtained / other missing / essential missing)
     - Add new `<EssentialsBanner>` component (only renders when `essentialMissingCount > 0`; "Start with essentials" filters the table)
     - Lift the table filter state into the view so the banner can drive it

   - `src/features/dashboard/dashboard-view.tsx`:
     - Collapse the page title the same way (one line)
     - Remove the HYSA gate KPI tile (it's in the header rail now); KPI strip becomes 3-up

   - `src/App.tsx`:
     - Update `AppShell` props (drop the three doc-count props)

4. **New derived field on `ChecklistSummary`:**
   ```ts
   essentialMissingCount: items.filter(
     i => i.status === 'missing' && i.priority === 'essential'
   ).length
   ```
   Add to the type and the summary builder. Update fixtures.

5. **Do NOT touch:**
   - TanStack Query hooks (`useDashboard`, `useDocumentChecklist`, `useTransactionCounts`)
   - Matcher logic in `src/features/document-intake/matcher.ts`
   - SQLite bootstrap / Tauri command names / capabilities
   - Python MCP server
   - Matcher tests
   - The category sidebar (becomes a filter in pass 03)
   - The "Why needed" column (collapses in pass 03)
   - The chart and leakage cards (pass 04)

6. **Edge cases the SPEC requires you to handle:**
   - `total === 0`: no NaN, rail renders as empty track
   - `essentialMissingCount === 0`: banner doesn't render, headline drops the essential callout
   - `obtained === total`: rail is fully moss

7. After implementing, verify:
   - `npm run dev` at 1280px: vertical chrome above the intake table is ~140px (roughly half what it was)
   - Header HYSA rail appears on both Intake and Cash flow views
   - Clicking "Start with essentials" filters the table to Essential + Missing and scrolls to it
   - `npm test` passes
   - No new TypeScript errors

When you're done, show me the diff plus a screenshot of the intake view at 1280px so I can confirm the vertical-space savings landed.
