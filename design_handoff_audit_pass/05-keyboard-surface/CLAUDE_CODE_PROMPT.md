# Prompt for Claude Code — Pass 05: Keyboard surface

Paste this into Claude Code running inside the `liquidity-gate` repo.

**Prereq:** All earlier passes (01–04) merged. The palette's commands target state and selectors that earlier passes establish — the intake filter shape, the dashboard time range, the watch-root strip actions, the transaction drawer.

---

I'm applying the fifth and final pass from the audit handoff. The spec lives at `design_handoff_audit_pass/05-keyboard-surface/SPEC.md`. The SPEC uses the live codebase vocabulary (`'open'` / `'ready'` status, `isEssential` priority, `Source intake` tab label) — adapt any filter literal that differs from what pass 02/03 actually wrote. Please:

1. **Read the SPEC end to end.** It covers four pieces: (a) a `⌘K` command palette with fuzzy search, (b) global keyboard shortcuts for tab switching + force rescan, (c) a dismissible bottom-right hint pill, (d) consistent focus-ring styles app-wide.

2. **New files:**
   - `src/components/keyboard/command-palette.tsx` — the modal itself (centered, ~520px, input + result list, portal-rendered, Escape / arrow keys / Enter)
   - `src/components/keyboard/use-keyboard-shortcuts.tsx` — global hook mapping key chords to actions; ignores most events sourced from text inputs (except `⌘K` itself)
   - `src/components/keyboard/commands.ts` — command registry builder that composes nav + intake filters + categories + dashboard ranges + system actions
   - `src/components/keyboard/keyboard-hint.tsx` — persistent `⌘K commands` pill in bottom-right; dismissible; auto-hides after first palette open

3. **Modified files:**
   - `src/App.tsx`:
     - Install `useKeyboardShortcuts` at the app root with chords for `mod+k`, `mod+1`, `mod+2`, `mod+r`
     - Render `<CommandPalette>` and `<KeyboardHint>` at the app root
     - Build the command registry from current state via `buildCommandRegistry()`
   - Global CSS (likely `src/index.css`):
     - Add `*:focus-visible` rule with a 2px tide outline + offset
     - Add a button-specific tighter offset

4. **Tauri gotcha — handle it explicitly:**
   - Tauri intercepts `⌘R` for webview reload by default
   - Decide: either disable Tauri's reload binding (`src-tauri/tauri.conf.json` → `windows[].reload` or platform-specific config), or assign force-rescan to `⌘⇧R`
   - Document the choice in the PR description
   - Test every shortcut in `npm run tauri dev`, not just `npm run dev`

5. **Do NOT:**
   - Introduce third-party command-palette libraries (no fuse.js, kbar, cmdk, etc.). The fuzzy matcher in the SPEC is a 10-line scorer.
   - Replicate filter / range / rescan logic in command actions. Every command calls into setters that already exist from earlier passes.
   - Build Vim-style leader chords or multi-key sequences. Single mod-key combos + `⌘K` only.
   - Touch the matcher, SQLite, MCP server, type contracts (beyond adding new component props), or any feature surface from passes 01–04.

6. **Edge cases:**
   - Text-input focus: `⌘1` / `⌘2` / `⌘K` still work; plain keys do not steal input
   - Reduced motion: the palette has no animation in this spec; if added later, gate on `prefers-reduced-motion`
   - The hint pill must not overlap the transaction drawer (pass 04) or the watch-root strip (pass 03)
   - Auto-hide of the hint pill after first palette open via shortcut (not via click) — they've learned the keyboard surface, the hint is paid for

7. After implementing, verify in `npm run tauri dev` (not just `npm run dev`):
   - `⌘K` opens palette from any focus state including inside the search input
   - Typing "ess" surfaces "Filter to essential missing"; Enter executes, palette closes, intake view shows filtered table
   - `⌘1` / `⌘2` switch tabs
   - Force-rescan shortcut works without breaking webview reload (or vice versa, per your choice)
   - Tab-cycling through both views hits every interactive element with a visible focus ring
   - `npm test` passes
   - No new third-party deps in `package.json`

When you're done, show me the diff plus a short screen recording (or a sequence of screenshots) of: opening the palette → typing "ess" → Enter → table is filtered. This is the marquee interaction of the whole audit pass; it should feel instant.
