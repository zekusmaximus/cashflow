# Decision Log

Closed design decisions for the Liquidity Gate workspace. Each entry is a
short record of *what* we decided and *why*. Update PROJECT_STATUS.md for
operational state; this file is for "we considered X and went with Y, and
here's why we won't revisit that for a while" calls.

---

## 2026-06-01 — Classification-first UI (Classify view, register filters/search/bulk, taxonomy fixes)

**Context.** The desktop app booted into Source Intake, and the only
classification surface — the Transaction Register — was the last section
of the Cash Flow tab. The user's primary day-to-day task (re-bucketing
transactions) required switching tabs and scrolling past seven analytics
sections every session. The register filtered on direction only; there
was no way to isolate a category, account, or merchant.

**Decision.** Made classification a first-class surface.

- Added a third `AppView` value `'classify'` (`app-shell.tsx`), set as the
  **default** view in `App.tsx`, with a `⌘3` shortcut + palette entry. The
  register was extracted to `src/features/register/register-view.tsx` and
  renders full-height as a standalone Classify view.
- `getTransactionPage` gained `primaryCategory`, `accountId`, and `search`
  filters (all bound parameters; the controlled `direction` union stays
  inline; transfer-exclusion preserved). Added a header unclassified-count
  pill that deep-links into the filtered Classify view.
- Bulk select + "apply to all matching" issue one `upsertTransactionOverride`
  per row through a lifted mutation.
- Taxonomy fixes in the editor: added `business_expense` (33 existing rows
  carried it but the dropdown couldn't produce it), removed `unclassified`
  as a save target (state, not destination), and added a `lifecycle`
  control wired through the existing override column.

**Alternatives considered.**

- *Float the register to the top of the Cash Flow tab instead of a separate
  view.* Rejected. The analytics and the classification queue are different
  jobs at different cadences; sharing one scroll column is what created the
  problem. A dedicated default view is the fix.
- *Leave `unclassified` in the dropdown.* Rejected. It is a state, not a
  classification target; offering it invited saving rows back into limbo.

---

## 2026-06-01 — UI rule capture via Python classifier sidecar; manual-rule priority band; override-stamp fix

**Context.** The UI could write per-row overrides but not durable
`classification_rules`, and could not run the classifier (Python, in the
MCP server) — so a captured rule could never re-bucket existing rows or be
applied without leaving the app. We wanted rule capture from a register row
without forking the matcher into a second engine.

**Decision.** Route rule application through the *same* Python classifier
via a sidecar, never a reimplementation.

- New Python CLI entry `cli_classify.py` (registered as `apply-classifier`
  in `pyproject.toml`, mirroring the `refresh-hysa-gate` precedent): upserts
  the rule, runs `apply_classifier(reclassify_all=True)`, then re-applies
  stored overrides; emits a JSON result. A Rust Tauri command
  (`apply_classification_rule`) spawns it replicating `.mcp.json` exactly
  (venv interpreter, `cwd=server`, `LIQUIDITY_GATE_ROOT=..`), overridable via
  `LIQUIDITY_GATE_PYTHON`.
- The frontend's live "matches N" preview uses `new RegExp(pattern,'i')`
  against `description_raw` — byte-for-byte the engine's matching semantics
  (case-insensitive `re.search` on `description_raw`), so the preview and the
  authoritative run agree. The TS regex is preview-only; Python is the sole
  source of truth for writes.
- **Manual-rule priority band: 6–9 (default 8).** Below the structural
  transfer-protection rules (priority 5, inviolable) and above general rules
  (10+). Lower number wins. The UI clamps captured rules to ≥6 so a manual
  merchant rule can never preempt transfer protection, and defaults
  `direction_filter` to the source row's direction (never `transfer`).
- **`manual_override_applied` stamp fix.** `upsertTransactionOverride` now
  stamps `metadata_json.manual_override_applied = true` (via `json_set`), the
  exact flag the classifier's `reclassify_all` checks before skipping a row.
  A bootstrap backfill stamps pre-existing overrides. Without this, the first
  reclassify run would overwrite every manual override. (See PROJECT_STATUS
  "Known issues" — the backfill's first cut missed 20 rows; tracked there.)

**Why `reclassify_all=True` on capture.** Manual rules must win over the
classifier's prior *general* guesses on already-classified rows; the default
classifier scope only touches `unclassified` rows. `reclassify_all` is safe
*only because* manual overrides are protected by the flag above.

**Alternatives considered.**

- *Port the matcher to Rust, or re-implement it in TS for the UI.* Rejected.
  Two engines drift; the regex/first-match/priority semantics would diverge
  the moment either side changed. One Python engine, invoked as a sidecar, is
  the only way to guarantee the UI and the MCP classify identically.
- *Write the rule row from TS and let the next MCP ingest apply it.* Rejected
  as the sole mechanism. It leaves existing matching rows stale until an
  unrelated ingest and pulls the user out of the app to finish a task.
- *Server-side preview (dry-run) for the match count.* Deferred. A debounced
  `dry_run=True` sidecar call would make the preview authoritative across the
  full dataset; the current local regex preview undercounts when the view is
  filtered/paginated. Acceptable for v1; noted as a refinement.

---

## 2026-05-27 — Retirement of `subcategory = 'rental_mortgage'`

**Context.** The rental property mortgage (M & T Mortgage, $1,024.14 on
the 6th of each month from Beacon checking) had drifted: four months
classified as `rental/mortgage`, one month (2026-05-06, transaction
`15fc3478-27d5-440c-8123-5a6dd2a5a46a`) as `rental/rental_mortgage`. The
two values were treated as siblings in the UI dropdown even though they
referred to the same payment.

**Decision.** Retire `rental_mortgage`. The canonical subcategory for a
mortgage payment is `mortgage`, regardless of whether the property is
the primary residence or a rental. The rental context is already carried
by `primary_category = 'rental'` and `household_role = 'rental'`, so
prefixing the subcategory was redundant and invited drift.

Concretely:

- `rental_mortgage` removed from `SUBCATEGORY_BY_PRIMARY.rental` in
  `src/features/dashboard/dashboard-view.tsx`. `mortgage` added in its
  place, so the dropdown under `rental` lists the canonical value.
- The seed `classification_rules` in `server/sql/schema.sql` do not emit
  `rental_mortgage`; the offending rule was added at runtime in a prior
  Cowork session and lives only in the local SQLite DB. The user
  resolves it operationally by listing rules and updating any rule whose
  `subcategory` is `rental_mortgage` to `mortgage`, then re-running
  `apply_classifier(reclassify_all=true)` (or scoped to M & T Mortgage)
  to settle the five rental-mortgage rows on the canonical value. A
  one-row `upsert_transaction_override` on the May transaction is the
  minimum fix; the rule update prevents regression on the next import.

**Alternatives considered.**

- *Keep both values as legitimate siblings.* Rejected. The data shows
  drift, not two different concepts. One year of statements with this
  taxonomy would have produced inconsistent rental-expense rollups.
- *Canonicalise on `rental_mortgage` instead.* Rejected. Four of five
  rows already use `mortgage`; the smaller migration wins, and
  `mortgage` matches the primary-residence vocabulary.
- *Add a CHECK constraint on `subcategory` enforcing the dropdown enum
  at the DB layer.* Deferred. `subcategory` is intentionally free-text
  (see the 2026-05-27 entry on `home_maintenance` /
  `home_improvement` / `landscaping`). Closing that off is a larger
  decision than this fix.

**Drift sweep.** A query of the form

```sql
SELECT primary_category, merchant_normalized,
       COUNT(DISTINCT subcategory) AS n_subs,
       GROUP_CONCAT(DISTINCT subcategory) AS subs,
       COUNT(*) AS n_txns
FROM transactions
WHERE merchant_normalized IS NOT NULL
GROUP BY primary_category, merchant_normalized
HAVING n_subs > 1
ORDER BY n_txns DESC;
```

surfaces every (primary_category, merchant) pair classified into more
than one subcategory. The user runs this against the live DB; any
results beyond M & T Mortgage are surfaced here for adjudication, not
migrated unilaterally.

---

## 2026-05-27 — `upsert_transaction_override` API input documented separately from storage key

**Context.** `docs://project-status` previously described the override
mechanism as "keyed by account + date + amount + normalized
description." That sentence accurately described the *storage* key but
read as if it described the API input. Multiple readers — including
Cowork agents — built calls passing `account_id`, `occurred_on`,
`amount`, and `description` directly and were rejected by the Pydantic
schema with `extra_forbidden`.

**Decision.** Rewrite the bullets in `docs/PROJECT_STATUS.md` under
"Durable overrides" to call out the API input shape explicitly
(`transaction_id` plus optional override fields) and clarify that the
match key is *derived server-side* from the resolved transaction.
Storage key vs. API input is now two sentences instead of one.

The Pydantic model
(`server/src/liquidity_gate_mcp/models.py:UpsertTransactionOverrideRequest`)
is the source of truth and was not changed.

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
