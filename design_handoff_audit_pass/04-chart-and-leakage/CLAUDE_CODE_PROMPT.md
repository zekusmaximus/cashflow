# Prompt for Claude Code — Pass 04: Quiet the chart, add leakage drill-down, fix delta math

Paste this into Claude Code running inside the `liquidity-gate` repo.

**Prereq:** Pass 01 (color semantics) merged. Passes 02 and 03 are independent of this pass — pass 04 only touches the dashboard view + adds one new Tauri command.

---

I'm applying the fourth pass from the audit handoff. The spec lives at `design_handoff_audit_pass/04-chart-and-leakage/SPEC.md`. Please:

1. **Read the SPEC end to end** before touching any code. It covers five changes to the dashboard view plus one new component: (a) chart hover tooltip + net-by-month strip, (b) trailing-3-mo delta math on KPI tiles, (c) tighten Liquidity Gates to a 3-up strip with a forward projection slot, (d) "See transactions" drawer wired to each leakage card, (e) Custom option on the time-range control.

2. **Changes by file:**

   - `src/features/dashboard/dashboard-view.tsx`:
     - Replace per-bar value labels with a shared hover tooltip (anchored to column index, not mouse coords)
     - Add a net-by-month strip below the X-axis labels (moss for positive, ember for negative)
     - Replace ad-hoc "+x% vs. Jan" delta with trailing-3-mo vs prior-3-mo (fall back to MoM if < 6 months of data); label explicitly
     - For outflow delta, flip polarity colors (rising outflow → ember)
     - Tighten Liquidity Gates from two big cards to a 3-row strip (two real gates + Roth projection slot)
     - Add "See transactions" button to each leakage card
     - Add Custom option to the time-range control (opens a popover with start/end month+year selects)

   - **New file:** `src/features/dashboard/transaction-drawer.tsx`:
     - Right-side drawer (420px wide), backdrop click + Escape to close
     - Lists matched transactions (vendor, amount, date, raw description, match score)
     - Loading skeleton + empty state
     - Respects `prefers-reduced-motion` (drop slide animation if set)

   - **New TanStack Query hook:** `useLeakageTransactions(categoryId, range)`
     - Calls a new Tauri command `list_leakage_transactions`
     - `staleTime: 30_000`
     - `enabled` controlled by drawer open state

   - **New Tauri command** in `src-tauri/src/main.rs`:
     - `list_leakage_transactions(category_id: String, range: TimeRange) -> Vec<Transaction>`
     - Reads from existing `transactions` table; filters by category + date range
     - **No schema migration**
     - Register on the `invoke_handler` line. **No ACL / capability declaration needed** — app-defined `#[tauri::command]` functions are not gated by the shell plugin's permission system. (The earlier `reveal_in_file_manager` command in pass 03 confirmed this empirically.)

3. **Do NOT touch:**
   - Matcher logic
   - SQLite schema (no migrations)
   - Existing TanStack Query hooks (`useDashboard`, etc.) beyond adding the new one
   - Python MCP server
   - The intake view (settled in passes 02/03)
   - The header / shell
   - The category sidebar

4. **Edge cases:**
   - `< 6 months of data` → fall back to MoM delta with the label "vs. prior month"
   - `< 2 months of data` → no delta at all; render "no comparison yet"
   - Hovering between columns → tooltip snaps instantly; no transition delay
   - Drawer with no matches → empty state, not error
   - Roth projection math is optional for this pass — if it's a rabbit hole, ship the slot as a "Coming soon" placeholder and open a follow-up issue

5. **Implementation order recommended:**
   1. Transaction drawer scaffold + new Tauri command (the biggest piece — get the data path working first; stub rows with hardcoded data if SQL takes time)
   2. Wire drawer open/close from leakage cards
   3. Chart tooltip + net strip (small, mostly visual)
   4. KPI delta math (small, isolated)
   5. Liquidity Gates strip restructure (cosmetic)
   6. Custom time-range option (small)

6. **Do not introduce a charting library or date library.** Native HTML + the existing inline bar+grid approach is sufficient.

7. After implementing, verify:
   - `npm run dev`: hover any bar column → tooltip appears; net strip renders below; KPI deltas show "vs. trailing 3-mo"
   - "See transactions" opens the drawer; Escape and backdrop click both close it
   - `npm test` passes
   - `npm run tauri dev`: new command registered, no permission errors

When you're done, show me the diff plus a screenshot of the drawer open over a leakage card so I can confirm the layering looks right.
