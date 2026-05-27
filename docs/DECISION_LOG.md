# Decision Log

Closed design decisions for the Liquidity Gate workspace. Each entry is a
short record of *what* we decided and *why*. Update PROJECT_STATUS.md for
operational state; this file is for "we considered X and went with Y, and
here's why we won't revisit that for a while" calls.

---

## 2026-05-27 — Check register ingestion (`ingest_check_register`)

**Context.** Eight outbound paper checks (series 179–182 and 209–212)
totalling $8,743.69 had been classified by the rule engine using amount
alone, with no payee data. `description_raw` for these rows is literally
`CHECK# 209` etc., so the classifier had nothing to work with.

**Decision.** Added an MCP tool that reads
`<watch_root>/check_register.csv` and writes a `transaction_overrides`
row for every register entry that resolves to a known transaction.

- Required CSV columns: `account, check_number, date_written, amount,
  payee`. Optional: `primary_category, subcategory, lifecycle,
  household_role, notes`.
- Matching is strict first (regex `^CHECK#\s*0*<n>\s*$`, same
  `account_id`, `ABS(amount)` equal). Falls back to date window
  (`±10 days` on `posted_on` else `occurred_on`) when the strict pass
  finds nothing; ambiguous fallbacks are surfaced for manual triage
  instead of silently picking one.
- The tool calls `upsert_transaction_override` for each match — same
  code path the UI uses — so the row-fanout (multiple transactions
  sharing one match_key) and `manual_override_applied` metadata stamp
  behave identically across surfaces.
- `dry_run=true` previews without writing.

**Alternatives considered.**

- *Auto-run inside `ingest_documents` whenever the file is present.*
  Rejected. Registers should not be silently re-applied by every
  ingest cycle — explicit invocation gives a clean audit trail and
  lets the analyst see register-vs-classifier interactions in the tool
  output.
- *Reuse `upsert_transaction_override` schema vs invent a new
  `check_register_overrides` table.* Reused. The override match_key
  shape is exactly what we want (account + date + amount + normalized
  description), and a second table would have to re-implement the
  apply-to-existing fanout for no benefit.
- *Call `apply_classifier(transaction_ids=[…])` after writing
  overrides.* Rejected. `apply_classifier` does not accept a
  transaction-ID list (only `account_filter` / `reclassify_all`), and
  the upsert already materializes the new fields onto existing rows —
  re-running the classifier would either be a no-op or risk
  re-applying a generic rule on top of our specific override.

---

## 2026-05-27 — Mobile check deposit ledger ingestion (`ingest_check_deposit_ledger`)

**Context.** Nine `MOBILE CHECK DEP` rows on Beacon checking totalling
~$37K were all tagged `income`/`check_deposit` with no source
attribution. The three Feb–Mar 2026 deposits of exactly $5,235.87
almost certainly represent recurring rental income, but the system had
no way to express "this was rent from tenant X".

**Decision.** Added a peer MCP tool with the same shape as the check
register tool, reading `<watch_root>/check_deposits.csv`. Match key is
`(account_id, amount, occurred_on ± 3 days)` against rows whose
`description_raw` matches `MOBILE CHECK DEP` (case-insensitive,
tolerant of `DEPOSIT` suffix variants).

- The default categorization when the ledger leaves the columns blank
  is `primary_category = 'income'` with subcategory unset — the
  rental/business/refund nuance comes from the ledger row, not the
  tool.
- Setting `primary_category = 'transfer'` in the ledger explicitly
  marks a deposit as a transfer in disguise (e.g. a check from the
  user's own credit-union account), which skips income classification
  entirely.

**Alternatives considered.**

- *Combine register + deposit ledger into one CSV.* Rejected. The
  match semantics differ enough (per-check identifier on outbounds vs.
  date-window on inbounds) that one file with mixed schemas would
  surprise the user. Two parallel files also let the analyst maintain
  them at different cadences.
- *Auto-promote any deposit ≥ $5,000 to "rental income".* Rejected.
  Heuristics that look right on the current dataset will misclassify
  the next deposit. The ledger row is the user's deliberate statement
  of intent.

---

## 2026-05-27 — `home_maintenance`, `home_improvement`, `landscaping` subcategories

**Context.** Recurring outdoor service (mowing, plowing) was being
parked under `utilities`; one-off painting / HVAC service was being
parked under `property_fees`; capital improvements (roof, windows) had
no home for them at all. These misclassifications inflate the
`fixed_obligation/utilities` line and bury what's actually capital
spending.

**Decision.** Added three subcategories to the UI dropdown taxonomy:

- `fixed_obligation/landscaping` — recurring outdoor service contracts
- `fixed_obligation/home_maintenance` — non-recurring upkeep
  (painting, HVAC service, plumber, electrician, appliance repair)
- `abnormal/home_improvement` — capital improvements (roof, windows,
  kitchen, additions)

Also added `income/business_income` for incoming business or
consulting payments, alongside the existing `income/rental_income`.

The dropdown source-of-truth lives at
`src/features/dashboard/dashboard-view.tsx`
(`SUBCATEGORY_BY_PRIMARY`). The classifier rule engine does not enforce
the enum at the DB layer — `subcategory` is a free-text column with no
CHECK constraint — so the additions only need to land in the UI
dropdown and any new classifier rules that target them. No schema
migration is required.

---

## 2026-05-27 — Retirement of `subcategory = 'dining'`

**Context.** One transaction (occurred_on 2026-01-04) still carried the
pre-dropdown `dining` label. The UI dropdown standardised on
`dining_out` months ago; `dining` was an orphan.

**Decision.** Retire `dining`. The canonical value is `dining_out`. A
one-time migration script at
`scripts/migrations/2026-05-27_consolidate_dining_orphan.py` uses the
existing `upsert_transaction_override` path to rewrite the orphan row
(SHA256 match_key prevents this from being expressed as pure SQL).

The script is idempotent: re-running after the first pass finds zero
matching rows and exits cleanly. The override also stamps
`metadata_json.manual_override_applied = true`, so a future
`apply_classifier(reclassify_all=true)` will not undo it.

---

## 2026-05-27 — Annual household reference table (TOML, read-only)

**Context.** Pay stub ingestion was explicitly rejected for this
project — net pay is what hits the bank, and household cash flow is
what we measure. But year-end W-2 / 401(k) / HSA / withholding totals
still matter for derived metrics like effective tax rate and true
pre-tax savings rate.

**Decision.** Added a lightweight TOML file at
`<watch_root>/annual_household_reference.toml` with one `[[year]]`
block per calendar year, and a read-only MCP tool
`get_annual_reference(year)`. The user edits the file directly — no
write tool exists by design.

`compute_monthly_summary` and `generate_monthly_summary` accept an
optional `annual_reference` arg. When the file has at least one
nonzero value for the requested year, the summary output gains an
`annual_reference` section with derived ratios (effective tax rate,
gross-to-net ratio, pre-tax savings rate). When the file is absent or
all-zero, the section is *omitted* — not reported as zeros, which
would be misleading.

**Alternatives considered.**

- *Store the totals in a new SQL table with a write tool.* Rejected.
  A TOML file the user edits in their editor is lower-friction and
  matches the existing `balances.toml` pattern.
- *Compute the same ratios from per-paystub PDF extraction.* Deferred
  to roadmap item 2 (Paystub PDF extraction). The TOML is a useful
  intermediate that does not block on that work.

---

## 2026-05-27 — Defer UI "Import Check Register" button

**Context.** Phase 2d of the data-quality build called for a Tauri-app
button that calls `ingest_check_register` and renders the result.

**Decision.** Defer for v1. The Tauri frontend has no transport to MCP
tools today (see PROJECT_STATUS Roadmap §6 entry on "Add source": MCP
calls from the desktop runtime were deferred there for the same
reason). Shipping a half-baked TS-side CSV parser that bypasses the
override match_key would diverge from the Python implementation and
break the override survives-re-import guarantee.

The Cowork analyst session is the primary surface for these ledger
imports until we either (a) build an MCP transport from the Tauri
runtime or (b) re-implement the upsert in TS against the shared
SQLite. Either is significant work that does not justify itself
without other MCP-driven UI features waiting in queue.
