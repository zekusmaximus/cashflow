# Prompt for Claude Code — Pass 01: Color semantics

Paste this into Claude Code running inside the `liquidity-gate` repo.

---

I'm applying the first of two visual passes from a follow-on audit. The handoff package lives at `design_handoff_audit_pass/`. Please:

1. **Read `design_handoff_audit_pass/01-color-semantics/SPEC.md` end to end** before touching any code. It documents a token reassignment: `clay` narrows to outflow-only, `tide` becomes the in-progress accent, `ember` broadens to cover blocked / missing / over-cap states.

2. **Optional but useful:** open `Design Audit.html` at the project root and click the "Filter: Re-color" chip in the header to see the finding in context with before/after swatches.

3. **Apply the class-name swaps in three files only:**
   - `src/components/layout/app-shell.tsx` — one swap in `StatusPills`
   - `src/features/document-intake/document-intake-view.tsx` — Missing KPI tile + Missing status chip
   - `src/features/dashboard/dashboard-view.tsx` — HYSA gate KPI rail + both Liquidity Gate cards (rail and percentage badge)

4. **Do NOT change:**
   - Outflow bars on the cash-flow chart (they stay `bg-clay` — this is the surface that earns the token)
   - The leakage cap-fill segment (`bg-clay` cap + `bg-ember` overage — already correct under the new contract)
   - The Obtained chip / inflow bars / category progress rails (`bg-moss`, all correct)
   - The Essential priority badge (`border-ember/30 bg-ember/8`, already correct)
   - Any TypeScript types, hooks, matcher logic, SQLite calls, Tauri commands, or the MCP server

5. **No Tailwind config changes.** All four tokens (`moss`, `tide`, `clay`, `ember`) already exist.

6. After implementing, verify:
   - `npm run dev` — header dot, Missing chips, and Missing KPI eyebrow render ember; HYSA + safe-harbor gate rails render tide
   - `npm test` still passes
   - No new TypeScript errors

7. The SPEC notes the Missing KPI tile will be deleted in pass 02. Recolor it anyway so this PR stands alone visually.

When you're done, show me the diff. It should be ~8 call sites across 3 files — if you've touched more than ~12 lines you're over-replacing. Don't blanket-substitute `clay → ember`; outflow and leakage cap-fill must stay clay.
