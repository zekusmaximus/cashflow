# Project Status

Living tracker of what's built, what's next, and what's outstanding for the
Liquidity Gate household spending reconstruction workspace. Update this file as work
lands. The master index ([00_CASH_FLOW_MASTER_INDEX.md](00_CASH_FLOW_MASTER_INDEX.md))
remains the canonical project plan; this file is the operational heartbeat.

**Last updated:** 2026-05-18 (night, post-ally-fix-and-transaction-register)

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

Rule-based classifier is at 86.7% coverage: 130 rules, 992 of 1,144
transactions classified. Two Cowork sessions on 2026-05-18 built the full
rule set — first pass (10 rules, 53%) plus a second pass (113 new rules)
covering all known targets: Zara, Sephora, French Cleaners, Servomation,
Google One, Venmo, Mobile Check Dep, streaming, subscriptions, utilities,
retail, dining, fuel, pet, services, advertising, travel, entertainment,
donations, tax, insurance, loans, and property fees. Two catchall rules
(`rule-sp-catchall`, `rule-square-prefix-catchall`) absorb future
SP*/SQ* long-tail merchants automatically. 152 rows remain unclassified
(15 inflows, 137 single-occurrence small-merchant outflows) — pushing
above 90% would require per-merchant rules or manual overrides that don't
materially improve spending analytics.

Cowork integration optimized 2026-05-18: `docs://project-status` MCP resource
added (4 resources total), classifier and override tools wired into the
`agent.md` default workflow, `config.json` startup sequence updated to MCP
resource URIs, `mcp-server.json` now points to the venv Python directly, and
the watch-root `.claudecowork` directory is a junction to the repo — all 7
identified Cowork deficiencies resolved.

**Residual diagnostic surface** (reduced after Ally fix) splits into two
remaining categories; `unpaired-transfer-diagnostics` in auto-memory has
full row-level detail:

1. **IonBank ONLINE XFR (~14 Beacon rows)** — partner account not yet
   ingested. Resolved when the other IonBank account's CSV arrives.
2. **Multi-leg same-day Webster/Beacon ambiguity (4 + 4 rows on 2/25,
   2/27, 4/3, 4/6)** — same amount on multiple legs with mixed clearing
   delays. Algorithm can't deterministically choose without
   description-aware pairing or manual triage.

Previously reported item 2 (Ally inbound-from rows tagged as transfer with
no counterpart) is now **resolved**: `pair_transfers` auto-reclassifies
unpaired Ally HYSA `Requested transfer from …` rows as `direction='inflow'`
after the pairing pass. The three affected rows ($4,500 on 2026-01-05,
$5,080 on 2026-04-08, $1,000 on 2026-05-08) will be corrected on the next
`pair_transfers` call.

**Next single task**: Transaction Register classification edit UI — inline
category/merchant overrides calling `upsert_transaction_override` (roadmap §6,
read-only register is now live).

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
  └─ 7 tools, 4 resources, 1 prompt
  └─ Same DB, write connection separate from read-only query connection

Claude Cowork / Claude Desktop analyst project
  └─ Watch-root .claudecowork/ is a junction to the repo; instructions stay in sync automatically
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
- [x] Unpaired Ally HYSA `Requested transfer from …` rows auto-reclassified as `direction='inflow'` after the pairing pass (`ally_inbound_reclassified` reported in `PairTransfersResult`)

### MCP integration
- [x] 7 tools registered: `read_document_metadata`, `ingest_documents`, `pair_transfers`, `reconcile_transactions`, `reconcile_periods`, `query_cashflow_data`, `upsert_transaction_override`
- [x] 4 resources: `docs://master-index`, `docs://tracker`, `docs://project-status`, `watch://recent-events`
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
- [x] Transaction Register section on Dashboard: paginated table (50/page) with direction filter (Outflows default / Inflows / All), merchant/description display, category badge (unclassified highlighted), pagination controls

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
- [x] Cowork integration optimized 2026-05-18: `docs://project-status` MCP resource, classifier/override tools in `agent.md` workflow, MCP URIs in `config.json` startup, venv Python in `mcp-server.json`, watch-root `.claudecowork` junction pointing to repo

### Classifier
- [x] `classification_rules` table with priority, account_filter, direction_filter, confidence
- [x] `classifier.py` — apply_classifier, upsert_classification_rule, list_classification_rules
- [x] 7 seed rules in schema (idempotent INSERT OR IGNORE)
- [x] Auto-classification on every ingest_documents call
- [x] 3 MCP tools registered in server.py
- [x] 18 tests in test_classifier.py (124/124 suite passing)
- [x] First Cowork rule-building session: 10 merchant rules, 609/1,144 classified (53%)
- [x] Second Cowork rule-building session 2026-05-18: 113 new rules, 992/1,144 classified (86.7%); 130 rules total; two SP*/SQ* catchalls absorb future long-tail merchants

## Roadmap

Roughly ordered by value-per-effort for the spending-first scope. Item 1
improves the baseline household spending tool directly. Items below it are
later layers or optional planning-oriented extensions and should not block
normal use of the repo for transaction-based spending analysis.

### 1. Rule-based classifier ✓
- [x] `classification_rules` table: regex → (primary_category, subcategory, merchant_normalized, household_role, lifecycle, confidence)
- [x] Classifier module (`classifier.py`) — first-match wins, protects manual overrides, writes provenance to metadata_json
- [x] Auto-runs on every `ingest_documents` call
- [x] Three MCP tools: `apply_classifier`, `upsert_classification_rule`, `list_classification_rules`
- [x] 7 seed rules shipped with schema; 130 rules total after two Cowork sessions
- [x] 992/1,144 transactions classified (86.7%); 152 remain (single-occurrence long tail)
- [x] Two catchall rules (`rule-sp-catchall`, `rule-square-prefix-catchall`) absorb future SP*/SQ* merchants automatically
- [ ] Manual override UI for unclassified rows (Dashboard build-out item)

### 1b. Second classifier pass ✓
- [x] 113 new rules across four batches covering all known targets and full retail/dining/streaming/subscription/utility/pet/service long tail
- [x] 86.7% coverage achieved (992/1,144); target was 75%

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
- [ ] 14 sections per master index §6 (currently 5 cards: cash flow, HYSA gate, leakage, reconciliation, transaction register)
- [x] Transaction Register — read-only paginated ledger with direction filter and category display
- [ ] Transaction Register — inline classification edit UI (calls `upsert_transaction_override`)
- [ ] HYSA trajectory chart
- [ ] Capital-Plan Feasibility card with Green/Yellow/Red badge
- [ ] Subscription audit / leakage card driven from real data
- [ ] Reconciliation history drill-down (currently only the latest period per account)
- [ ] Manual `variance_explanation` entry UI (currently DB-only)

## Known issues and loose ends

- [ ] **Chase same-day dedup backfill.** Fixed in code: a backwards-compatible `|seqN` suffix now disambiguates duplicate (date, amount, description) rows within a file, while the first occurrence keeps the legacy hash so already-ingested rows still match on re-import. The 12 collapsed rows from the original Chase ingest remain absent in the DB; they'll appear automatically next time the Chase CSV is re-exported and re-ingested. No data backfill needed otherwise.
- [x] **Untagged transfer patterns from Beacon parser** — FID BKG SVC LLC MONEYLINE, VENMO PAYMENT, and MOBILE CHECK DEP are now covered by classifier rules added in the second pass.
- [x] **Watch-root Cowork project isolation resolved.** `C:\Users\Jeff\Documents\Cashflow\.claudecowork` is now a junction to the repo `.claudecowork\` directory — `agent.md`, `config.json`, and `mcp-server.json` are shared automatically. No manual mirroring needed.
- [x] **Three Ally HYSA inbound transfers have no Beacon counterpart — resolved.** $4,500 on 2026-01-05, $5,080 on 2026-04-08, $1,000 on 2026-05-08 (all `Requested transfer from JEFFREY A ZYJESKI Ally Bank Transfer`). `pair_transfers` now auto-reclassifies unpaired Ally HYSA inbound-from rows as `direction='inflow'`; these three rows will be corrected on the next tool run. The 1/5 row is confirmed as Jeff's bonus check deposit; origin of the 4/8 and 5/8 rows is still unconfirmed but both are correctly treated as inflows.
- [ ] **Medium-confidence classifications to confirm** (flagged by second classifier pass, 2026-05-18):
  - Render, Supabase, Namecheap, GitHub, Roll20, OpenAI — currently `fixed_obligation/subscriptions_apps` (Jeff). Flip to `business_expense` if used for professional/author work.
  - PayLease / GW Management (10 rows, ~$10K+/yr) — currently `fixed_obligation/property_fees`. Confirm: primary-residence HOA, rental HOA, or rental management fees. Rental category may be more accurate.
  - US Bank Loan Payment — confirm whether auto, mortgage, or other; subcategory currently `loan_payment`.
  - Venmo → Ashley Calabrese (7 rows) — currently `variable_lifestyle/intra_household`. If these are intra-household reimbursements, consider excluding from spending totals via `upsert_transaction_override`.
  - PMUSA — guessed Philip Morris USA Rewards; confirm.
  - Tiffani* / Tiffani for W — guessed boutique; confirm merchant.
  - Tainara Gisele Rosa dos Santos — recurring Zelle; likely cleaning or personal service. Confirm and reclassify if needed.

## How to update this file

Move items from **Roadmap** to **Done** as they land. Keep checklists
flat — sub-items per task only when they're individually verifiable.
Update **Last updated** at the top. Add new known issues as they're
discovered, not as work happens (those go in commits).

When starting a new chat to work on a roadmap item, point Claude at this
file first ("Read docs/PROJECT_STATUS.md and let's tackle item N"); use
the orientation prompt in your notes only if you need fuller context.
