# Project Status

Living tracker of what's built, what's next, and what's outstanding for the
Liquidity Gate household spending reconstruction workspace. Update this file as work
lands. The master index ([00_CASH_FLOW_MASTER_INDEX.md](00_CASH_FLOW_MASTER_INDEX.md))
remains the canonical project plan; this file is the operational heartbeat.

**Last updated:** 2026-05-18

## Snapshot

End-to-end spending-first pipeline works for four account types: drop a
Chase, Beacon, Ally HYSA, or Webster (Ashley) CSV in the watch root, the
Tauri app auto-matches core sources and later context, the dashboard
reflects real numbers, and Claude Cowork / Claude Desktop can call the MCP
server's tools to ingest, pair transfers, analyze monthly and annual
spending, and persist exact-match manual cleanup. Current analyst work can
be anchored either to the repo or directly to the watch root, as long as the
Liquidity Gate MCP is attached. Real-DB
run on 2026-05-17 produced 31 cross-account pairs
(including the 5×5 Webster → Beacon cluster on 2026-01-21, which the
connected-components fallback retires deterministically) and 12 fresh
Chase rows from the same-day dedup recovery. ~1,144 transactions now
in the local SQLite (936 Chase + 137 Beacon + 19 Ally + 52 Webster).

Scope revision phases 0-3 are complete: docs, Cowork prompts, tracker
priorities, and the intake UI now treat core transaction feeds as the
baseline for useful analysis. Payroll, tax, insurance, debt, rental, and
other planning inputs remain available as optional later layers rather than
baseline blockers.

Durable transaction overrides are now live and validated: Cowork can store
payee/category/role/lifecycle fixes that survive a future YTD-to-monthly
re-import for the same logical transaction.

**Residual diagnostic surface** (25 unpaired + 4 ambiguous) splits into
three categories that each need a different fix, captured in detail in
auto-memory `unpaired-transfer-diagnostics`:

1. **IonBank ONLINE XFR (~14 Beacon rows)** — partner account not yet
   ingested. Resolved when the other IonBank account's CSV arrives.
2. **Ally bonus-check / direct-deposit inbounds (3 rows)** — Ally parser
   regex is greedy and tags real inflows as transfer. Fix path: parser
   refinement OR classifier override OR manual mark.
3. **Multi-leg same-day Webster/Beacon ambiguity (4 + 4 rows on 2/25,
   2/27, 4/3, 4/6)** — same amount on multiple legs with mixed clearing
   delays. Algorithm can't deterministically choose without
   description-aware pairing or manual triage.

**Next single task**: rule-based classifier (#2) — regex-driven
classification over `description_raw` writing `primary_category`,
`subcategory`, `household_role`, `recurrence`, `treatment` to
transactions. A durable transaction-override backend now exists for
exact-match manual fixes (checks, Venmo labels, payee mapping) and will
survive YTD-to-monthly re-imports; the classifier remains the next step
for broader recurring automation. Target ~60% auto-classification with a
triage queue for the rest. Unblocks the Transaction Register and
Variable Lifestyle Spend tabs.

## Architecture (one-screen reminder)

Two local folders matter:

- Repo workspace: `C:\Users\Jeff\Projects\cashflow` — code, docs,
  `.claudecowork`, and the MCP source of truth.
- Watch root / analyst folder: `C:\Users\Jeff\Documents\Cashflow` —
  private CSVs, `balances.toml`, and the day-to-day Cowork project if you
  prefer to work directly where files land.

Three processes share one SQLite database at
`%APPDATA%\com.jeff.liquiditygate\liquidity-gate.db` (Windows) or platform
equivalent. Override via `LIQUIDITY_GATE_DB_PATH`.

```
Tauri desktop app  (npm run tauri dev)
  └─ React UI: Source Intake + Cash Flow Dashboard
  └─ @tauri-apps/plugin-sql reads/writes the shared DB

Python MCP server  (launched by Claude Desktop subprocess)
  └─ 7 tools, 3 resources, 1 prompt
  └─ Same DB, write connection separate from read-only query connection

Claude Cowork / Claude Desktop analyst project
  └─ If the active project is the watch root, custom instructions should mirror the repo's .claudecowork/agent.md
  └─ Calls MCP tools, reads MCP resources
```

Watch root: `C:\Users\Jeff\Documents\Cashflow` (set via
`LIQUIDITY_GATE_WATCH_ROOT` at Windows User scope).

## Done

### Foundation
- [x] Tauri v2 + React + TypeScript shell with SQLite + filesystem plugins
- [x] Restrictive Content Security Policy
- [x] Vite watcher excludes `.venv*`, `server/`, `src-tauri/target/`, etc.
- [x] Python MCP server scaffold with read-only SQL guard
- [x] Shared schema in [server/sql/schema.sql](../server/sql/schema.sql)
- [x] Unified database path: Tauri appConfigDir convention, both ends agree
- [x] CI runs pytest + vitest + tsc + npm build on every push

### Auto-matching
- [x] Filename scoring: token overlap + bigram-ratio + extension bonus, threshold 0.35
- [x] CamelCase + letter/digit splits in tokenizer (`401k` matches `401(k)`)
- [x] File-first winner-takes-all assignment (no duplicate row claims)
- [x] Manual tracker `Obtained ✓` flags and filesystem matches now agree across desktop UI and MCP metadata
- [x] Python and TypeScript implementations, kept in sync
  - [server/src/liquidity_gate_mcp/tools.py](../server/src/liquidity_gate_mcp/tools.py)
  - [src/features/document-intake/matcher.ts](../src/features/document-intake/matcher.ts)

### Ingestion pipeline
- [x] Chase credit-card CSV parser ([parsers/chase_csv.py](../server/src/liquidity_gate_mcp/parsers/chase_csv.py))
  - [x] Type=Payment → direction='transfer'
  - [x] Idempotent SHA-256 source_record_key
  - [x] `|seqN` suffix disambiguates duplicate same-day same-amount same-description rows while the first occurrence keeps the legacy hash (backwards-compat)
  - [x] Pending rows skipped
- [x] Beacon checking CSV parser ([parsers/beacon_csv.py](../server/src/liquidity_gate_mcp/parsers/beacon_csv.py))
  - [x] Four transfer regexes (CHASE CREDIT CRD, ALLY BANK $TRANSFER, ALLY BANK P2P, IonBank ONLINE XFR)
  - [x] Running balance preserved in metadata_json
  - [x] Source key includes balance (avoids same-day duplicate collapse)
- [x] Ally HYSA CSV parser ([parsers/ally_csv.py](../server/src/liquidity_gate_mcp/parsers/ally_csv.py))
  - [x] US-style M/D/YYYY date parsing
  - [x] Time-in-source-key tiebreaker (Ally has no running balance column)
  - [x] Transfer regexes for "Requested transfer from/to" patterns
  - [x] Interest Paid stays inflow; external Zelle stays outflow
- [x] Webster checking CSV parser ([parsers/webster_csv.py](../server/src/liquidity_gate_mcp/parsers/webster_csv.py))
  - [x] Ashley-owned account (`owner="ashley"`, `household_role="ashley"`)
  - [x] Split Debit/Credit columns combined; running balance preserved in metadata
  - [x] Transfer regexes for `CHASE CREDIT CRD EPAY` and `CK TRANSFER … ASHLEY M CALABR`
  - [x] FID MoneyLine inbounds (RSU proceeds) and Venmo/PayPal outflows left for the classifier, not auto-tagged
- [x] Registry-based parser dispatch ([ingest.py](../server/src/liquidity_gate_mcp/ingest.py))
  - [x] One-line additions for new institutions

### Cross-account analysis
- [x] Transfer-pair detector ([transfers.py](../server/src/liquidity_gate_mcp/transfers.py))
  - [x] Mutual-best-match algorithm (both sides must see each other as unique closest partner)
  - [x] Connected-components cluster fallback: each cents bucket is split into best_partners-linked components, then any component with two accounts, equal row counts, opposite signs, and mutual best_partners pairs 1-to-1 by stable id-sort. Retires the 2026-01-21 cluster (5 Webster outbounds paired with 5 Beacon inbounds two days later) without false-pairing unrelated $5K rows elsewhere in the bucket.
  - [x] Idempotent
  - [x] Surfaces unpaired and ambiguous rows for diagnosis
  - [x] Optional suspected_untagged report for parser regex gaps
- [x] All Beacon `ALLY BANK $TRANSFER` and `ALLY BANK P2P` rows pair with their Ally counterparts

### MCP integration
- [x] 7 tools registered: `read_document_metadata`, `ingest_documents`, `pair_transfers`, `reconcile_transactions`, `reconcile_periods`, `query_cashflow_data`, `upsert_transaction_override`
- [x] 3 resources: `docs://master-index`, `docs://tracker`, `watch://recent-events`
- [x] 1 spending-first prompt: `financial_detective`
- [x] Wired to Claude Desktop via [`%APPDATA%\Claude\claude_desktop_config.json`](https://docs.anthropic.com)
- [x] Verified end-to-end ingestion call from a Claude Desktop chat

### Durable overrides

- [x] `transaction_overrides` table keyed by a source-independent match key (account + date + amount + normalized description)
- [x] `upsert_transaction_override` MCP tool stores payee/category/role/lifecycle/note overrides and reapplies them to future re-imports
- [x] Existing matching transactions are updated immediately; future monthly imports inherit the same override even when `source_document_name` changes

### Frontend
- [x] Source Intake view: tracker-driven core-source coverage, later-context badges, ingested counts
- [x] Watch-root status banner showing root path and indexed file count
- [x] Cash Flow Dashboard: real Inflow/Outflow bars from `monthly_cashflow_summary` view
- [x] React Query: 5s staleTime + refetchOnWindowFocus for snappy local refresh

### Reconciliation
- [x] `reconciliation_periods` schema (one row per account per month, idempotent on UNIQUE (account_id, period_start, period_end))
- [x] `balances.toml` sidecar in watch root for Chase/Ally openings + per-period overrides; aliases (chase/beacon/ally/webster) resolve when there's one account per institution
- [x] Compute module ([reconciliation.py](../server/src/liquidity_gate_mcp/reconciliation.py)) — opening + signed net = computed closing; credit-card sign inversion for amount-owed view; statement closing falls back to `metadata_json.running_balance` for Beacon/Webster
- [x] Variance chains via statement closing (not computed), so a $5 gap in January doesn't cascade into February
- [x] `variance_explanation` preserved across reruns (human note survives recomputation)
- [x] `reconcile_periods` MCP tool wired into the server
- [x] Dashboard variance card per account with Green (<$1) / Yellow ($1–$10) / Red (>$10) / "No statement" badge

### Documentation
- [x] README with first-time setup, naming conventions, Cowork wiring
- [x] Database location documented in Local-First Rules
- [x] [`.claudecowork/`](.claudecowork/) descriptors for Cowork integration
- [x] Watch-root defaults and docs consistently refer to `C:\Users\Jeff\Documents\Cashflow`
- [x] Repo-vs-watch-root split documented for Cowork analyst vs builder work

## Roadmap

Roughly ordered by value-per-effort for the spending-first scope. Item 1
improves the baseline household spending tool directly. Items below it are
later layers or optional planning-oriented extensions and should not block
normal use of the repo for transaction-based spending analysis.

### 1. Rule-based classifier
- [ ] `classification_rules` table: regex over `description_raw` → (primary_category, subcategory, treatment, household_role, recurrence, confidence)
- [ ] Classifier module that scans transactions and writes classifications
- [ ] Manual override UI for unclassified rows
- [ ] Master index §7
- [ ] Aim: ~60% auto-classification, rest in triage queue

### 2. Optional later: Paystub PDF extraction
- [ ] Decide: LLM-based (privacy tradeoff) vs local OCR + per-employer templates
- [ ] One extractor handles all employers with a JSON schema (gross, 401k, HSA, federal/state/FICA, RSU withholding, net)
- [ ] Per-paystub data lands in transactions or a new payroll_events table
- [ ] Required for capital-plan feasibility tests in master index §10

### 3. Later enrichment: Normalization layer
- [ ] `sinking_funds` table (annual premiums → monthly reserve)
- [ ] Event-income tagging on transactions (bonus, RSU, refund)
- [ ] Normalized monthly view alongside `monthly_cashflow_summary`
- [ ] Master index §8

### 4. Optional later: Forward projection engine
- [ ] Project through 12/31/2027
- [ ] Inputs: net pay, fixed obligations, premiums, rental, expected RSU events
- [ ] Output: HYSA balance trajectory, projected $80K gate date, capital-plan feasibility status
- [ ] Master index §10

### 5. Optional later: Appendix export
- [ ] Generate `2026_Household_Cashflow_Reality_Appendix.md` from analyzed data
- [ ] Sections per master index §11
- [ ] Expose as MCP tool so Claude can produce on demand
- [ ] Optional handoff deliverable when broader planning support is explicitly requested

### 6. Dashboard build-out
- [ ] 14 sections per master index §6 (currently 4 cards: cash flow, HYSA gate, leakage, reconciliation)
- [ ] Transaction Register with classification edit
- [ ] HYSA trajectory chart
- [ ] Capital-Plan Feasibility card with Green/Yellow/Red badge
- [ ] Subscription audit / leakage card driven from real data
- [ ] Reconciliation history drill-down (currently only the latest period per account)
- [ ] Manual `variance_explanation` entry UI (currently DB-only)

## Known issues and loose ends

- [ ] **Chase same-day dedup backfill.** Fixed in code: a backwards-compatible `|seqN` suffix now disambiguates duplicate (date, amount, description) rows within a file, while the first occurrence keeps the legacy hash so already-ingested rows still match on re-import. The 12 collapsed rows from the original Chase ingest remain absent in the DB; they'll appear automatically next time the Chase CSV is re-exported and re-ingested. No data backfill needed otherwise.
- [ ] **Untagged transfer patterns from Beacon parser** (need classifier work, not regex):
  - `FID BKG SVC LLC MONEYLINE` (Fidelity sweeps, often $15)
  - `VENMO PAYMENT`
  - `MOBILE CHECK DEP`
- [ ] **Watch-root Cowork projects do not inherit repo-local instructions automatically.** If the active Cowork project is anchored to `C:\Users\Jeff\Documents\Cashflow` instead of the repo, mirror the current contents of [.claudecowork/agent.md](../.claudecowork/agent.md) into that project's custom instructions so the spending-first persona stays aligned with the MCP resources and tool flow.
- [ ] **Three Ally HYSA inbound transfers have no Beacon counterpart.** Verified via DB query 2026-05-13: $4,500 on 2026-01-05, $5,080 on 2026-04-08, $1,000 on 2026-05-08 (all `Requested transfer from JEFFREY A ZYJESKI Ally Bank Transfer`). The 1/5 row is **Jeff's bonus check deposit** (confirmed 2026-05-17) — classify as event income, not a transfer. The 4/8 and 5/8 rows are still unconfirmed origin; the 4/8 $5,080 is close to but not equal to Webster's 4/6 $5,000 `CK TRANSFER` outbound, so amount-based pairing won't catch it. Likely need explicit document confirmation rather than pattern-matching.

## How to update this file

Move items from **Roadmap** to **Done** as they land. Keep checklists
flat — sub-items per task only when they're individually verifiable.
Update **Last updated** at the top. Add new known issues as they're
discovered, not as work happens (those go in commits).

When starting a new chat to work on a roadmap item, point Claude at this
file first ("Read docs/PROJECT_STATUS.md and let's tackle item N"); use
the orientation prompt in your notes only if you need fuller context.
