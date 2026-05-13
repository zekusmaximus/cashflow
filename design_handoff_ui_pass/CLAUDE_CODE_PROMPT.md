# Prompt for Claude Code

Paste this into Claude Code (or a fresh Claude conversation) running inside the `liquidity-gate` repo. Make sure `design_handoff_ui_pass/` exists at the repo root before you start, or adjust the paths.

---

I'm applying a visual redesign to the Document Intake and Cash-Flow Dashboard views of this app. A designer produced a handoff package at `design_handoff_ui_pass/`. Please:

1. **Read `design_handoff_ui_pass/README.md` end to end** before touching any code. It documents the exact problems being fixed, the design tokens, and a per-file spec for `src/components/layout/app-shell.tsx`, `src/features/document-intake/document-intake-view.tsx`, and `src/features/dashboard/dashboard-view.tsx`.

2. **Open `design_handoff_ui_pass/redesign.html`** in a browser to see the target visual. Click the "Show notes" button at the top-right to surface annotations explaining each change.

3. Implement the redesign in the three React files listed in the README, preserving:
   - All TanStack Query hooks (`useDashboard`, `useDocumentChecklist`, `useTransactionCounts`)
   - The `ChecklistDataset` and `DashboardSnapshot` type contracts
   - The matcher logic in `src/features/document-intake/`
   - Tauri command names and capabilities
   - The Python MCP server (don't touch `server/`)

4. The README requires two new props on `AppShell` (`obtainedDocuments`, `liquidityGateCurrent`). Wire these in from `App.tsx` — both values are already available on the query results.

5. The `:root` CSS in `src/index.css` has a radial-gradient + linear-gradient background that conflicts with the new flat page color. Replace it with `background: #faf8f3`.

6. Optionally add `paper: '#faf8f3'` to `tailwind.config.ts` under `colors`, and a `card` boxShadow (spec is in the README). If you skip the token additions, use the arbitrary-value form (`bg-[#faf8f3]`, inline shadow style) — both are fine.

7. After implementing, verify:
   - `npm run dev` renders both views cleanly at 1280px, 1024px, and 768px with no horizontal scroll
   - A long watch-root path (e.g. `/Users/jeff/Library/Application Support/CashFlow/intake/2026-archive/`) truncates instead of pushing the file count off-screen
   - `npm test` still passes
   - The Tauri build (`npm run tauri dev`) still talks to `list_watch_root_files`

8. Don't change the matcher tests, the SQLite bootstrap, or the CSV ingest path. This is a pure visual + layout pass.

Use the existing `Card`, `Badge`, `Button` primitives where they fit; the README spells out where to use bespoke containers instead (the new cards are a quieter `rounded-xl border border-ink/8 shadow-card` rather than the existing `rounded-[28px]`).

When you're done, show me the diff before committing so I can sanity-check the call sites in `App.tsx`.
