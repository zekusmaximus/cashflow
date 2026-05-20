# Prompt for Claude Code — Pass 03: Sidebar as filter, collapse "Why needed", reveal-in-Finder

Paste this into Claude Code running inside the `liquidity-gate` repo.

**Prereq:** Passes 01 and 02 (`design_handoff_audit_pass/01-color-semantics/`, `02-unify-progress/`) merged. Pass 02 already lifted the table filter state into the intake view; this pass extends the filter shape with a `category` dimension.

---

I'm applying the third visual + interaction pass from the audit handoff. The spec lives at `design_handoff_audit_pass/03-sidebar-filter/SPEC.md`. Please:

1. **Read the SPEC end to end** before touching any code. It covers four changes: (a) converting the read-only category sidebar into a clickable filter rail, (b) collapsing the "Why needed" column into a row-expand, (c) adding a new "Last modified" column, (d) wiring reveal-in-Finder and copy-path actions onto the watch-root strip via a new Tauri command.

2. **Changes by file:**

   - `src/features/document-intake/document-intake-view.tsx`:
     - Extend `TableFilter` to include `category: string | 'all'`
     - Convert `<CategorySidebar>` to a clickable rail; each row is a `<button>`; active row is tide-tinted with a ring
     - Add an "All categories" virtual row at the top
     - Strip the letter prefix (`A.`, `B.`) from display; preserve as `categoryId`
     - Drop the "Why needed" column from the table
     - Add a "Last modified" column (value from matched file `mtime`, `—` when no match)
     - Add a 32px expand-chevron column; clicking a row toggles inline expansion showing rationale + matched files
     - Show an active-filter chip row above the table toolbar when any filter is non-default
     - Empty state when filters narrow to zero rows

   - `src/features/document-intake/watch-root-strip.tsx` (or wherever it lives):
     - Add two icon buttons on the right: reveal-in-Finder + copy-path
     - Use the existing toast primitive if one exists; otherwise a one-second floating confirmation is fine

   - `src-tauri/src/main.rs` (or equivalent):
     - Add `reveal_in_file_manager` command (macOS `open -R`, Windows `explorer /select,`, Linux `xdg-open`)
     - Register on the `invoke_handler` line
     - For Tauri v2: declare the capability in `src-tauri/capabilities/main.json` — register only this specific command, not blanket `shell:allow-open`

3. **Optional new derived field on `ChecklistItem`:** `categoryId` (e.g. `"A"` from `"A. Core"`). Compute once at the ingest layer if not already present.

4. **Do NOT touch:**
   - TanStack Query hooks
   - Matcher logic
   - SQLite bootstrap / type contracts beyond the small `categoryId` addition
   - Python MCP server
   - Matcher tests
   - The chart, leakage cards, or any dashboard surface (pass 04)
   - The header / shell (settled in pass 02)

5. **Edge cases:**
   - Active filter narrows to zero rows → empty state with "Clear filter" link
   - Row expansion is session-only (no localStorage)
   - Multiple rows can be expanded simultaneously
   - On Linux there's no universal "select file in folder" command; `xdg-open` opens the parent folder. Document this in the PR description.

6. After implementing, verify:
   - `npm run dev`: clicking a category narrows the table; clicking again clears
   - The expand-chevron rotates 180° on expansion
   - Watch-root reveal-in-Finder works on macOS at minimum
   - `npm test` passes
   - `npm run tauri dev`: new command registered, no permission errors in console

When you're done, show me the diff plus a quick verification of the reveal-in-Finder click on macOS. The Tauri permission setup is the riskiest piece — if `tauri dev` throws a capability error, sort it before declaring done.
