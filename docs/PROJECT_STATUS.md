# Project Status

Living tracker of what's built, what's next, and what's outstanding for the
Liquidity Gate household cash-flow workspace. Update this file as work
lands. The master index ([00_CASH_FLOW_MASTER_INDEX.md](00_CASH_FLOW_MASTER_INDEX.md))
remains the canonical project plan; this file is the operational heartbeat.

**Last updated:** 2026-05-13

## Snapshot

End-to-end pipeline works for three account types: drop a Chase, Beacon,
or Ally HYSA CSV in the watch root, the Tauri app auto-matches and shows
ingestion counts, the dashboard reflects real numbers, and Claude Desktop
can call the MCP server's tools to refresh and analyze. ~1,080 real
transactions in the local SQLite (924 Chase + 137 Beacon + 19 Ally).
Cross-account transfer pairing operational; all 11 Beacon ↔ Ally HYSA
flows pair cleanly after the Beacon parser was extended to recognize the
`ALLY BANK P2P` description variant. The next major unblock is Ashley's
separate checking so the remaining inflow side of the dashboard fills in.

**Next single task** (lowest-friction unblock): Ashley separate checking
parser (doc-004). Three Ally HYSA inbound transfers ($4,500 on 1/5,
$5,080 on 4/8, $1,000 on 5/8) currently have no Beacon counterpart and
likely originate from Ashley's checking — doc-004 is what closes the loop
on those.

## Architecture (one-screen reminder)

Three processes share one SQLite database at
`%APPDATA%\com.jeff.liquiditygate\liquidity-gate.db` (Windows) or platform
equivalent. Override via `LIQUIDITY_GATE_DB_PATH`.

```
Tauri desktop app  (npm run tauri dev)
  └─ React UI: Document Intake + Cash Flow Dashboard
  └─ @tauri-apps/plugin-sql reads/writes the shared DB

Python MCP server  (launched by Claude Desktop subprocess)
  └─ 5 tools, 3 resources, 1 prompt
  └─ Same DB, write connection separate from read-only query connection

Claude Desktop chat
  └─ .claudecowork/agent.md persona (paste into Project custom instructions)
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
- [x] Python and TypeScript implementations, kept in sync
  - [server/src/liquidity_gate_mcp/tools.py](../server/src/liquidity_gate_mcp/tools.py)
  - [src/features/document-intake/matcher.ts](../src/features/document-intake/matcher.ts)

### Ingestion pipeline
- [x] Chase credit-card CSV parser ([parsers/chase_csv.py](../server/src/liquidity_gate_mcp/parsers/chase_csv.py))
  - [x] Type=Payment → direction='transfer'
  - [x] Idempotent SHA-256 source_record_key
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
- [x] Registry-based parser dispatch ([ingest.py](../server/src/liquidity_gate_mcp/ingest.py))
  - [x] One-line additions for new institutions

### Cross-account analysis
- [x] Transfer-pair detector ([transfers.py](../server/src/liquidity_gate_mcp/transfers.py))
  - [x] Mutual-best-match algorithm (both sides must see each other as unique closest partner)
  - [x] Idempotent
  - [x] Surfaces unpaired and ambiguous rows for diagnosis
  - [x] Optional suspected_untagged report for parser regex gaps
- [x] All Beacon `ALLY BANK $TRANSFER` and `ALLY BANK P2P` rows pair with their Ally counterparts

### MCP integration
- [x] 5 tools registered: `read_document_metadata`, `ingest_documents`, `pair_transfers`, `reconcile_transactions`, `query_cashflow_data`
- [x] 3 resources: `docs://master-index`, `docs://tracker`, `watch://recent-events`
- [x] 1 prompt: `financial_detective`
- [x] Wired to Claude Desktop via [`%APPDATA%\Claude\claude_desktop_config.json`](https://docs.anthropic.com)
- [x] Verified end-to-end ingestion call from a Claude Desktop chat

### Frontend
- [x] Document Intake view: 86-row checklist, auto-match badges, ingested counts
- [x] Watch-root status banner showing root path and indexed file count
- [x] Cash Flow Dashboard: real Inflow/Outflow bars from `monthly_cashflow_summary` view
- [x] React Query: 5s staleTime + refetchOnWindowFocus for snappy local refresh

### Documentation
- [x] README with first-time setup, naming conventions, Cowork wiring
- [x] Database location documented in Local-First Rules
- [x] [`.claudecowork/`](.claudecowork/) descriptors for Cowork integration

## Roadmap

Roughly ordered by value-per-effort. Items higher up unblock items below.

### 1. Ashley separate checking parser (doc-004) — UNBLOCKED, NEXT
- [ ] `parsers/ashley_csv.py` (need a real CSV first — verify column shape)
- [ ] Add `WEBSTR CK WEBXFR P2P` style transfer regex if descriptions confirm Ashley→joint flows
- [ ] Tests
- [ ] Re-run `pair_transfers` — WEBSTR/CALABR pairs should resolve, plus the three known orphan Ally inbounds ($4,500 1/5, $5,080 4/8, $1,000 5/8) suspected to originate from Ashley

### 2. Reconciliation tab
- [ ] New schema element for opening/closing per account per period
- [ ] Beacon's running_balance in metadata_json gives free per-row balance check
- [ ] UI surface in dashboard: per account, computed end vs statement end
- [ ] Variance must be zero (or explicitly explained) before downstream tabs are trusted
- [ ] Master index §6 #2

### 3. Rule-based classifier
- [ ] `classification_rules` table: regex over `description_raw` → (primary_category, subcategory, treatment, household_role, recurrence, confidence)
- [ ] Classifier module that scans transactions and writes classifications
- [ ] Manual override UI for unclassified rows
- [ ] Master index §7
- [ ] Aim: ~60% auto-classification, rest in triage queue

### 4. Paystub PDF extraction
- [ ] Decide: LLM-based (privacy tradeoff) vs local OCR + per-employer templates
- [ ] One extractor handles all employers with a JSON schema (gross, 401k, HSA, federal/state/FICA, RSU withholding, net)
- [ ] Per-paystub data lands in transactions or a new payroll_events table
- [ ] Required for capital-plan feasibility tests in master index §10

### 5. Normalization layer
- [ ] `sinking_funds` table (annual premiums → monthly reserve)
- [ ] Event-income tagging on transactions (bonus, RSU, refund)
- [ ] Normalized monthly view alongside `monthly_cashflow_summary`
- [ ] Master index §8

### 6. Forward projection engine
- [ ] Project through 12/31/2027
- [ ] Inputs: net pay, fixed obligations, premiums, rental, expected RSU events
- [ ] Output: HYSA balance trajectory, projected $80K gate date, capital-plan feasibility status
- [ ] Master index §10

### 7. Appendix export
- [ ] Generate `2026_Household_Cashflow_Reality_Appendix.md` from analyzed data
- [ ] Sections per master index §11
- [ ] Expose as MCP tool so Claude can produce on demand
- [ ] **This is the actual project deliverable**

### 8. Dashboard build-out
- [ ] 14 sections per master index §6 (currently only 3 cards)
- [ ] Reconciliation status banner
- [ ] Transaction Register with classification edit
- [ ] HYSA trajectory chart
- [ ] Capital-Plan Feasibility card with Green/Yellow/Red badge
- [ ] Subscription audit / leakage card driven from real data

## Known issues and loose ends

- [ ] **Chase same-day dedup collapse.** 12 transactions collapsed because `source_record_key` hashes (date, amount, description) and Chase had identical same-day micro-charges. Beacon avoids this by including running balance. Fix on Chase side (if ever needed): add per-row sequence number to the hash.
- [ ] **Untagged transfer patterns from Beacon parser** (require Ally/Ashley CSVs to confirm via pair detector):
  - `WEBSTR CK WEBXFR P2P` involving "ASHLEY M CALABR"
  - `FID BKG SVC LLC MONEYLINE` (Fidelity sweeps, often $15)
  - `VENMO PAYMENT`
  - `MOBILE CHECK DEP`
- [ ] **Project custom instructions not yet pasted in claude.ai.** Paste the current contents of [.claudecowork/agent.md](../.claudecowork/agent.md) into the cashflow Project's custom instructions so every chat auto-loads the persona. (The persona itself was updated 2026-05-13 to reference the `docs://master-index` and `docs://tracker` MCP resources instead of file paths.)
- [ ] **Three Ally HYSA inbound transfers have no Beacon counterpart and are presumed Ashley-sourced.** Verified via DB query 2026-05-13: $4,500 on 2026-01-05, $5,080 on 2026-04-08, $1,000 on 2026-05-08 (all `Requested transfer from JEFFREY A ZYJESKI Ally Bank Transfer`). No Beacon row exists at any date with those amounts. Most likely originate from Ashley's separate checking (her usual pattern is paying Chase and depositing to Beacon, but Ally pulls would also fit — Ally records the destination account holder in the description, not the source). These will pair automatically once doc-004 (Ashley parser) ingests the matching outflows. Not a code issue; a data-coverage gap.
- [ ] **Tauri bundle resource entry now unused.** [src-tauri/tauri.conf.json:29](../src-tauri/tauri.conf.json#L29) still bundles the tracker CSV as a Tauri resource, but the loader now fetches via Vite. Remove for tidiness when convenient.
- [ ] **Ambient module declarations now partially unused.** [src/types/ambient-modules.d.ts](../src/types/ambient-modules.d.ts) still declares `@tauri-apps/api/path` and `@tauri-apps/plugin-fs` from the original (broken) dynamic-import pattern. Clean up if no longer needed.

## How to update this file

Move items from **Roadmap** to **Done** as they land. Keep checklists
flat — sub-items per task only when they're individually verifiable.
Update **Last updated** at the top. Add new known issues as they're
discovered, not as work happens (those go in commits).

When starting a new chat to work on a roadmap item, point Claude at this
file first ("Read docs/PROJECT_STATUS.md and let's tackle item N"); use
the orientation prompt in your notes only if you need fuller context.
