# Scope Revision Plan

## Purpose

This document proposes the smallest practical scope revision needed to turn
Liquidity Gate into a simpler household cash-flow tool for monthly and annual
spending discussions with Claude Cowork.

The aim is not to redesign the product from scratch. The aim is to keep the
existing local-first architecture, reuse the current CSV ingestion and transfer
handling, reduce the amount of information treated as required, and leave a
clear path for later expansion if needed.

## Revised Product Definition

Liquidity Gate should be defined as:

> A local-first household spending reconstruction workspace that ingests core
> transaction exports, stores them in SQLite, excludes transfers, and gives
> Claude Cowork enough clean monthly and annual spending data to support useful
> discussions about household habits, trends, and tradeoffs.

It should not, by default, be defined as:

- a full household financial-planning system
- a capital-efficiency planning appendix generator
- a tax, payroll, RSU, insurance, and debt-document collection workspace
- a complete net-worth or forward-projection engine

Those can remain future or optional extensions, but they should no longer drive
the baseline intake workflow.

## Scope Principles

1. Transaction-first

The baseline workflow should start from transaction feeds that explain where
money came from and where it went.

2. Minimal required evidence

Only require documents that materially improve spending reconstruction.

3. Preserve working infrastructure

Do not remove the SQLite schema, parser registry, transfer pairing, or existing
dashboard pipeline unless a concrete problem requires it.

4. Separate required from optional

Important future planning documents can remain known to the project, but they
should be labeled optional or deferred rather than blocking useful analysis.

5. Monthly-first import policy

Prefer monthly CSV imports per account. YTD can remain tolerated if needed, but
it should not be the default recommended operating mode.

6. Expand by layers

Advanced domains like payroll, taxes, rental analysis, and capital planning
should layer on top of the spending core instead of defining the core.

## What Stays In Scope Now

These capabilities already match the simpler goal and should stay in scope:

- local SQLite database as the shared source for the desktop app and MCP server
- CSV ingestion for the four currently supported account types
- transfer-pair detection to avoid double-counting card payments and account
  transfers
- monthly cash-flow summaries derived from transactions
- a Claude-accessible MCP surface for querying and discussing spending data
- lightweight reconciliation where it improves trust in totals

These are already the strongest parts of the current implementation and do not
need structural revision.

## What Becomes Required, Conditional, and Deferred

### Required for the baseline product

The baseline product should require only the smallest set of data sources needed
to reconstruct household spending accurately:

- primary credit-card CSV exports used for most discretionary spending
- primary checking CSV exports used for payroll deposits, bill pay, and card
  payments
- any separate checking account CSVs that materially fund household spending
- savings-account CSVs only when needed to distinguish transfers from spending
- Venmo, PayPal, or Zelle exports only if those channels are materially used

### Conditional but not blocking

These can improve context, but they should not block baseline spending review:

- additional minor credit cards
- Amazon or Instacart exports
- utility bills
- subscription screenshots
- medical or pet invoices
- manual notes that explain unusual transactions

### Deferred from this repo's default scope

These belong in a later phase or a separate financial-planning workflow:

- paystubs and payroll detail beyond what bank deposits already show
- 401(k), HSA, and benefit elections
- tax returns, safe-harbor calculations, and RSU withholding analysis
- mortgage, HELOC, car-loan, insurance, and protection-planning documents
- rental-property planning and capital-efficiency materials
- forward projection, liquidity-gate forecasting, and appendix export

## Monthly Import Policy

## Recommended standard

Use one CSV per account per month, with filenames that include the year and
month.

Examples:

- `2026-01_Chase_Credit_Card.csv`
- `2026-01_Beacon_Checking.csv`
- `2026-01_Ally_HYSA.csv`
- `2026-01_Webster_Checking.csv`

This fits the current parser behavior and makes period-based analysis easier to
reason about.

## Supported now

Monthly CSV imports are already compatible with the current ingestion model.
The parsers derive an optional `statement_period` from the filename and do not
require a YTD file.

## Operational rule

Do not mix monthly and YTD imports for the same account in the same database.
The current deduplication logic keys uniqueness by `source_document_name` plus
`source_record_key`, so overlapping monthly and YTD files can duplicate the same
real-world transactions.

## PDF stance

PDF statements can remain visible in the intake workflow as references, but they
should not be described as a normal ingestion path for the spending database.
Today, the implemented parsers are CSV-based.

## Minimal Revision Strategy

The scope revision should happen in layers, with the smallest possible first
move.

### Phase 0: Reframe the project without changing its architecture

Goal: change what the project claims to be, without changing the underlying
code paths.

Recommended edits:

- revise the opening sections of `README.md` so the project is described as a
  household spending reconstruction tool first
- revise `docs/00_CASH_FLOW_MASTER_INDEX.md` so the baseline outcome is useful
  monthly and annual spending analysis, not capital-plan support
- add an explicit distinction between:
  - required for spending reconstruction
  - optional for better categorization
  - deferred for broader planning
- revise the intake language so missing advanced documents are not presented as
  blockers to useful conversations

Why this should happen first:

- it changes user expectations immediately
- it reduces document pressure without any schema or parser changes
- it aligns Claude Cowork behavior with the actual data value already present

### Phase 1: Narrow the default Claude Cowork workflow

Goal: stop steering Cowork toward document-gap hunting before it has analyzed
the transaction data.

Recommended edits:

- update `.claudecowork/config.json` so the startup sequence prioritizes the
  spending scope and transaction analysis workflow
- update `.claudecowork/agent.md` so default behavior becomes:
  - inspect available transaction sources
  - query spending patterns, categories, and trends
  - ask for additional source accounts only when analysis is incomplete
  - avoid requesting payroll, tax, insurance, or planning artifacts unless the
    user explicitly asks for those topics

Suggested default Cowork orientation:

- identify monthly spend by merchant and category
- compare month-over-month changes
- separate transfers from real spending
- highlight recurring charges and obvious one-off events
- surface the biggest spending buckets and unusual deltas

This is a prompt and workflow revision, not a systems rewrite.

### Phase 2: Simplify the tracker without breaking the current parser map

Goal: keep the tracker useful, but stop using it as a giant implied gate.

Recommended edits:

- keep the existing first four parser-backed transaction rows stable
- relabel many non-transaction rows from Essential to Optional or Deferred
- group the tracker into a smaller default view centered on spending sources
- move advanced planning rows into a later section or appendix-style area

Important implementation note:

The current tracker row IDs are position-based, and the parser registry maps
specific parser behavior to `doc-001` through `doc-004`. That means a minimal
scope revision should preserve the order of the first four CSV transaction rows
unless the parser registry is updated at the same time.

Practical rule:

- do not delete or reorder the first four tracker rows in the first revision
- change labels, priority text, notes, or downstream sections first
- only renumber or reorder later if the parser dispatch is made more explicit

This is the most important constraint for keeping the revision low-risk.

### Phase 3: Make the UI language match the simpler scope

Goal: reduce the feeling that the product is incomplete unless dozens of other
documents are present.

Recommended edits:

- change intake copy from `Document checklist` to something closer to
  `Sources` or `Financial sources`
- reduce emphasis on the total count of tracked documents as a success metric
- make it clearer that the dashboard can still be useful with only core
  transaction feeds
- treat advanced categories as optional enrichments rather than missing
  prerequisites

This can be small copy-only work at first.

### Phase 4: Add only one new behavioral guardrail if needed

Goal: support the monthly-first workflow cleanly.

Recommended improvement:

- document and optionally validate the rule that users should choose either
  monthly imports or YTD imports per account, not both

This can begin as documentation only. If needed later, it can become a warning
in the UI or MCP layer.

## Recommended File-Level Change Order

To keep the revision minimal and safe, make future edits in this order:

1. `SCOPE_REVISION.md`

This document, as the agreed plan.

2. `README.md`

Reframe the product definition and import policy.

3. `docs/00_CASH_FLOW_MASTER_INDEX.md`

Reduce the baseline intake scope and move planning-heavy content to a later
section.

4. `docs/Spreadsheet_checklist_for_document_tracking.csv`

Reclassify document priority while preserving the first four transaction rows.

5. `.claudecowork/config.json` and `.claudecowork/agent.md`

Shift Cowork from document-completeness policing to transaction-first analysis.

6. Optional UI copy changes

Only after the docs and Cowork prompt match the revised scope.

## Changes Explicitly Not Required

The first scope revision should not require:

- deleting any schema tables
- removing advanced roadmap items from the repository
- rewriting the parser layer
- changing the transfer-pairing algorithm
- changing the shared database design
- merging this project with the broader financial-picture project

This is primarily a scope and workflow correction, not a technical rewrite.

## Future Expansion Path

The revised scope should still leave clean room for expansion later.

### Expansion track A: Better spending insight

Safe next steps after the scope revision:

- regex or rules-based classification over transaction descriptions
- merchant cleanup and category overrides
- recurring-charge detection
- month-over-month anomaly summaries for Cowork

These directly improve the stated purpose of the repo.

### Expansion track B: Optional financial context packs

If broader planning becomes useful later, add optional packs rather than making
them part of the baseline definition:

- payroll and compensation pack
- debt and housing pack
- tax and RSU pack
- rental-property pack
- liquidity and forward-planning pack

Each pack should have its own explicit trigger, inputs, and outputs.

### Expansion track C: Cross-project handoff

If the separate full-financial-picture project remains the home for planning,
this repo can serve as the clean spending ledger feeding that broader system.

That is a better separation of concerns than trying to make one project do both
jobs by default.

## Acceptance Criteria For The Revised Scope

The scope revision is successful when all of the following are true:

- a new user can understand the project as a household spending tool within the
  first page of the README
- Cowork can have a useful discussion about spending with only core transaction
  CSVs present
- missing paystubs, tax files, insurance documents, and planning records are no
  longer treated as blockers to baseline analysis
- monthly CSV imports are documented as a first-class operating mode
- the tracker no longer implies that 80-plus documents are required before the
  product is useful
- the existing four-parser ingestion path continues to work unchanged

## Recommended First Pass Summary

If only one pass is made, it should do the following and nothing more:

- reframe the project around spending reconstruction
- narrow the required inputs to core transaction sources
- document monthly CSV imports as the preferred workflow
- warn against mixing monthly and YTD imports for the same account
- retune Cowork so it starts with transaction analysis rather than document
  collection

That is the smallest revision that meaningfully fixes the current scope drift
while preserving room to expand later.