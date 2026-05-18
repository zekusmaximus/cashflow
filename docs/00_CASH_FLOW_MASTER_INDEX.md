# Master Index — 2026 Household Spending Reconstruction

**Last updated:** 2026-05-18  
**Maintained by:** Jeff, with ChatGPT/AI-assisted review  
**Repo workspace:** `C:\Users\Jeff\Projects\cashflow`  
**Watch root / analyst folder:** `C:\Users\Jeff\Documents\Cashflow`  
**Purpose:** Single landing page for any new chat session working on the household spending reconstruction workspace. Read this file first, then the tracker CSV.

---

## Quick Status

This project exists to build a **local-first, transaction-first household spending reconstruction** for 2026. Its default job is to answer practical monthly and annual spending questions from core account exports, not to block analysis on every document in the broader financial picture.

> Where is money actually going each month, what is recurring versus one-off, and which source accounts still need to be ingested before the picture is trustworthy?

Useful spending analysis should begin once the core transaction sources are present. Payroll, tax, insurance, debt, rental, and capital-planning documents are optional later inputs unless a specific task explicitly requires them.

The current parser-backed baseline is Chase credit-card CSVs, Beacon checking CSVs, Ally HYSA CSVs, and Webster (Ashley) checking CSVs. These are enough to begin useful transaction-level and month-level household spending analysis.

The first project file is:

| Document | Purpose | Status |
|---|---|---|
| `Spreadsheet_checklist_for_document_tracking.csv` | Intake tracker for core sources, optional enrichments, and deferred planning inputs | **Active intake tracker** |

The current tracker identifies **86 tracked document/data items** across nine categories:

1. Core Transactions
2. Income & Payroll
3. Debt & Housing
4. Utilities & Household
5. Insurance & Protection
6. Rental Property
7. Medical / HSA
8. Subscriptions & Apps
9. Tax Documents

Only a subset of those items is required for baseline spending reconstruction. The rest should be treated as optional enrichments or deferred planning inputs that can be layered in later.

Project architecture remains local-file based. Repo docs, prompts, and code live in the repo workspace; raw financial files live in the external watch root and are exposed to Claude through the Liquidity Gate MCP and, when desired, a Cowork project anchored directly to the watch root. Core spending analysis starts with transaction CSVs and can later incorporate supporting PDFs or screenshots when they materially improve classification or verification. See `2026_Cashflow_Decision_Log.md` for architecture decisions and closed classification items.

---

## How to Start a New Chat

Use this prompt:

> Continuing the 2026 Household Spending Reconstruction workspace. Read `docs://master-index` first, then `docs://project-status` for current roadmap state, then `docs://tracker`. Start with the available transaction exports and reconstruct monthly and annual spending. Ask for additional source accounts only when transaction coverage is incomplete. Treat payroll, tax, insurance, and broader planning documents as optional unless the task explicitly depends on them.

If the active Cowork project is anchored to the watch root, the `.claudecowork/` directory is available there via a junction to the repo — no manual mirroring needed.

If the chat is about a specific task, add one of these:

- “Focus only on core transaction source intake and coverage gaps.”
- “Focus only on Chase and checking categorization.”
- “Focus only on monthly spending, category totals, and merchant patterns.”
- “Focus only on transfer cleanup and double-counting control.”
- “Focus only on recurring charges and one-time items.”
- “Use optional planning documents only for a specific follow-up question.”
---

## 1. Project Objective

The workspace should produce a practical household spending control system, not a full financial-planning dossier.

It should answer:

1. What are we actually spending each month?
2. Which categories and merchants drive the largest outflows?
3. Which charges are recurring, seasonal, one-time, reimbursable, or abnormal?
4. Which transfers should be excluded from spending totals?
5. What changed month over month, and which changes look material?
6. Which source accounts are still missing and materially limit confidence?
7. Which spending buckets look unusually high or still need cleanup?
8. What practical follow-up questions or category controls would improve visibility?

---

## 2. Relationship to the Capital-Efficiency Plan

This project can support the capital-efficiency plan later, but that is not its default scope.

The capital-efficiency plan answers:

> What should we do with marginal dollars?

This cash-flow project answers:

> What are we actually spending, what is recurring versus one-time, and how does that change month to month?

The baseline output for this repo should be a clean transaction ledger plus monthly and annual spending summaries that Claude Cowork can discuss directly.

If broader planning later needs a concise appendix, that export can be produced as a downstream artifact rather than treated as the baseline product definition.

The capital-efficiency plan should not become a transaction-level budget file. This project should own the transaction-level work and export only the conclusions needed for planning when that later handoff is explicitly requested.

---

## 3. Source-of-Truth Rules

### Primary factual record

Start with the transaction sources that directly reconstruct household cash flow:

- CSV transaction exports
- bank and credit-card statements when CSV is unavailable or verification is needed
- P2P exports such as Venmo, PayPal, or Zelle when they are materially used

### Optional enrichment record

Use these when they materially improve classification or context:

- Amazon or Instacart exports
- subscription screenshots or exports
- utility or home-service bills
- other merchant-specific supporting files

### Deferred planning inputs

Use broader planning documents only when the task explicitly requires them:

- payroll stubs and compensation records
- loan, mortgage, debt, or insurance files
- rental records
- HSA or tax documents

### Secondary factual record

Use manually provided explanations only to classify or clarify transactions. When Jeff explains an item, log it as a new entry in `2026_Cashflow_Decision_Log.md` and do not re-open it unless new documents contradict the explanation. The decision log is the single immutable record of project architecture decisions, closed classification items, and methodology rulings.

Closed classification items currently logged (see decision log for full detail):

- **DL-003** Citi autopay charges — EZPass + political contributions; intentional fixed recurring.
- **DL-004** $16,000 teller deposit on 1/9/2026 — Q4 2025 GBA compensation bonus.
- **DL-005** CT DRS Business Direct Pay $139 — 2025 CT income tax; portal label artifact; no LLC.
- **DL-006** Healthy Paws 2026 reimbursement — keep policy; annual audit only; track gross and reimbursement separately.
- **DL-007** Author-income / solo-401(k) thread — retired unless meaningful new author income appears.

Do not re-open any of these items absent a new contradictory document.

### Do not double-count

- Count Chase spending when the card charge occurs.
- Exclude later checking payments to Chase as spending; classify as card-payment transfers.
- Exclude Beacon → Ally or other savings transfers from spending.
- Classify Ally → Beacon as liquidity support, not income.
- Classify Webster / Ashley → Beacon or other joint-checking transfers as household funding transfer unless source records show external income.
- Split RSUs into compensation income, withholding, shares sold, shares retained, and net cash received.
- Split HSA activity into payroll contributions, employer contributions, investment activity, distributions, and medical spending.

---

## 4. Current Core Tracker

The active intake tracker is:

`Spreadsheet_checklist_for_document_tracking.csv`

It contains these columns:

| Column | Use |
|---|---|
| Category | Broad document class |
| Document | Specific document or export needed |
| Subject Matter | What the document affects |
| Format | Preferred file type |
| Priority | Essential / useful / conditional |
| Source / Where to Get | Portal, vendor, employer, bank, or preparer |
| Why Needed | Modeling reason |
| Obtained ✓ | Intake status |
| Date Added | Upload or acquisition date |
| Notes | Clarifications, caveats, replacement status |

Treat the tracker as a scope guide rather than a hard gate:

- core transaction sources are required for baseline spending reconstruction
- optional enrichments improve categorization and context
- deferred planning inputs should not block useful spending analysis

### Tracker maintenance rules

- Mark a document obtained only when the file is uploaded to the active watch/Cowork folder or clearly available in the connected local folder.
- If a document already exists in the capital-efficiency project, note: “Available in capital-efficiency project; copy/upload if needed.”
- If a document is superseded by a newer file, keep the old one noted but mark the newer file as controlling.
- If a document is not applicable, mark it “N/A” in notes rather than deleting the line.
- For recurring monthly documents, note coverage period, e.g., “Jan–Apr 2026 complete; May pending.”

---

## 5. Document Intake Priority

Useful spending analysis can start before the full tracker is complete. The baseline threshold is core transaction coverage, not document completeness.

### Tier 1 — Required for spending reconstruction

These sources are enough to begin useful monthly and annual household spending discussions:

| Priority | Document / data source | Why it matters |
|---:|---|---|
| 1 | Chase credit-card CSVs | Main discretionary and semi-discretionary spending source |
| 2 | Beacon / joint checking CSVs | Bills, payroll deposits, card payments, transfers, and checks |
| 3 | Webster / Ashley checking CSV/statements, if active | Closes any household funding or spending that bypasses joint checking |
| 4 | Ally HYSA CSV/statements, when relevant | Distinguishes savings transfers and liquidity moves from spending |
| 5 | Venmo / PayPal / Zelle / Cash App history, if material | Captures household spending or reimbursements that bypass the core bank feeds |

Once the relevant Tier 1 sources are present, Claude Cowork should be able to have useful monthly and annual spending discussions even if later tiers are still missing.

At the moment, the parser-backed Tier 1 baseline is already present in the active watch root: Chase, Beacon, Ally HYSA, and Webster checking.

### Tier 2 — Optional enrichment for better categorization

Upload after Tier 1 or as available:

- Amazon order history export
- Instacart / grocery delivery history
- Apple / Google / Amazon subscription screenshots
- streaming and software subscription lists
- pet grooming/daycare/boarding invoices
- vet bills and insurance reimbursement history
- utilities and home-service bills

### Tier 3 — Deferred broader planning inputs

Use these only when the task explicitly shifts from spending reconstruction into broader planning:

- Jeff paystubs, bonuses, and payroll detail
- Ashley paystubs, bonus records, and RSU stubs
- 401(k) and HSA election confirmations
- mortgage, HELOC, rental mortgage, and car-loan statements
- insurance premium schedules and policy details
- rental lease, rent history, HOA/condo dues, repairs, and insurance
- 2025 joint tax return
- 2026 tax projection from Cherubino, if prepared
- 2026 estimated tax payment receipts, if any
- RSU tax withholding records
- brokerage tax estimates / dividend estimates
- prior-year Schedule E and depreciation schedule for rental property

---

## 6. Dashboard Structure

The baseline dashboard should start with transaction-focused sections such as source coverage, transaction register, monthly spending, variable lifestyle spend, transfer exclusions, and recurring-charge review. The shipped UI currently covers Source Intake plus a spending-first dashboard; broader planning views can remain available later as optional layers.

The finished dashboard can include these sections or spreadsheet tabs:

1. **Data Inventory** — documents received, missing, date range, status
2. **Reconciliation** — for each account: opening balance + period inflows − period outflows = computed ending balance, compared against statement-reported ending balance. Variance must be zero (or explicitly explained) before any downstream tab is trusted. Covers Chase, Beacon, Ashley checking, Ally HYSA, Fidelity HSA, taxable brokerage, Venmo/PayPal/Zelle, and any other transactional account. Required inputs: 12/31/2025 closing balance per account and the period-end statement balance per account.
3. **Transaction Register** — combined categorized transaction-level data
4. **Monthly Income** — regular pay, bonus, RSU, rent, refunds, reimbursements
5. **Fixed Obligations** — mortgages, loans, insurance, utilities, subscriptions, recurring services
6. **Variable Lifestyle Spend** — groceries, Amazon, pets, travel, clothing, entertainment, gifts, restaurants, software
7. **One-Time / Abnormal Items** — emergency vet, home repairs, annual premiums, tax refunds/payments, unusual travel, RSUs
8. **Transfers Excluded from Spending** — card payments, HYSA transfers, inter-account transfers
9. **Rental Property Cash Flow** — rent, mortgage, HOA, insurance, taxes, repairs, vacancy reserve
10. **Medical / HSA Tracking** — HSA contributions, distributions, out-of-pocket receipts, reimbursable expenses
11. **Subscriptions / App Audit** — recurring charges, owner, keep/cancel/review
12. **HYSA Trajectory** — starting balance, transfers, interest, projected $80K gate date
13. **Capital-Plan Feasibility Test** — whether planned 401(k), HSA, insurance, Roth, and liquidity decisions are supportable
14. **Recommendations / Action Log** — category caps, document gaps, follow-up decisions

---

## 7. Core Classification Framework

Every transaction should receive these fields where possible:

| Field | Examples |
|---|---|
| Date | transaction date |
| Account | Chase, Beacon, Ally, Ashley checking, HSA, etc. |
| Merchant / Description | raw transaction text |
| Amount | signed cash-flow amount |
| Direction | inflow / outflow / transfer |
| Primary Category | income, fixed obligation, variable lifestyle, transfer, tax, rental, medical, investment, abnormal |
| Subcategory | groceries, Amazon, pet medical, mortgage, insurance, RSU, bonus, etc. |
| Household Role | Jeff, Ashley, joint, rental, pet, professional, tax |
| Recurrence | recurring / seasonal / one-time / unknown |
| Treatment | count in spend / exclude transfer / normalize / sinking fund / reimburse / tax-related |
| Confidence | high / medium / low |
| Notes | explanation or needed follow-up |

This matters more than perfect category labels. The dashboard’s purpose is not moral judgment. It is to prevent cash-flow drift from hiding inside vague buckets.

---

## 8. Optional Later Layer — Monthly Normalization Rules

The dashboard should show both actual and normalized spending.

| View | Purpose |
|---|---|
| Actual monthly cash flow | What really happened that month |
| Normalized recurring burn | Sustainable baseline excluding event distortions |
| Annualized run rate | Rough full-year projection based on YTD pattern |
| Sinking-fund need | Annual/semiannual/seasonal costs converted to monthly reserve |
| Event-income reliance | Degree to which bonuses/RSUs/refunds are supporting normal lifestyle |

Examples:

- Annual insurance premiums should be converted into monthly sinking-fund cost.
- Large vet bills should be separated into pet medical abnormal and netted against insurance reimbursement.
- Tax refunds are not ordinary income.
- Bonuses and RSU proceeds are event income, not normal monthly income.
- Vacation costs should be treated as actual cash outflow and also converted into an annual travel budget assumption.

---

## 9. Optional Later Layer — Key Output Metrics

Each dashboard refresh should produce these numbers:

| Metric | Definition |
|---|---|
| Normal monthly net income | Regular take-home pay after payroll deductions, excluding bonus/RSU/refund events |
| Fixed monthly burn | Mortgage/debt/utilities/insurance/services/subscriptions baseline |
| Variable lifestyle burn | Chase/checking variable spend, normalized |
| Monthly surplus / deficit | Normal income minus fixed and variable burn |
| Event-income dependence | Portion of annual plan funded by bonus/RSU/refunds/reimbursements |
| HYSA current balance | Current Ally HYSA balance |
| HYSA projected gate date | Date Ally reaches $80K under current trajectory |
| Discretionary pressure categories | Categories most likely to squeeze the capital plan |
| One-time abnormal total | YTD abnormal items excluded from recurring burn |
| Capital-plan feasibility status | Green / Yellow / Red |

### Feasibility status definitions

| Status | Meaning |
|---|---|
| **Green** | Current lifestyle and normal cash flow support the base capital-efficiency plan and $80K HYSA trajectory without material category caps |
| **Yellow** | Plan is supportable only if discretionary categories are capped, bonuses/RSUs are earmarked, or after-tax Roth timing remains gated |
| **Red** | Plan is not supportable without reducing spending, slowing HYSA build, changing Roth timing, altering debt-payoff pace, or revisiting rental/property decisions |

---

## 10. Optional Later Layer — Capital-Plan Feasibility Tests

The dashboard should explicitly test these planning moves:

| Action | Dashboard question |
|---|---|
| Jeff base 401(k) max | Has the reduced net pay been absorbed without drawing down cash? |
| Ashley base 401(k) max | Has the reduced net pay been absorbed without relying on RSUs/refunds? |
| Ashley HSA family max | Is HSA payroll contribution on track and reflected in net pay? |
| Jeff catch-up if pre-tax permitted | What is the net-pay reduction and tax benefit? |
| Ashley life insurance | Can the premium fit as a fixed monthly/annual obligation? |
| Jeff own-occ DI | Can the premium fit, and does it change discretionary capacity? |
| HYSA $80K gate | Is the date realistic under actual spending? |
| 2027 backdoor Roths | Can the lump-sum cash requirement be met without lowering emergency liquidity? |
| 2027 mega-backdoor Roth | Can after-tax cash flow support the election? |
| Ameriprise / Novartis restructuring | Does tax drag or withholding create a cash-flow reserve need? |
| Rental rent increase / keep-sell decision | Does the rental produce acceptable net cash after mortgage, HOA, insurance, repairs, and vacancy reserve? |

---

## 11. Optional Later Layer — Importable Appendix Specification

The main deliverable for the capital-efficiency plan is:

`2026_Household_Cashflow_Reality_Appendix.md`

It should be concise enough to import or link from the capital-efficiency plan. It should not include every transaction.

Required sections:

1. Executive cash-flow conclusion
2. Data completeness table
3. Monthly income baseline
4. Fixed obligations
5. Variable lifestyle spend
6. One-time / abnormal items
7. Transfers and double-counting controls
8. HYSA / liquidity trajectory
9. Capital-plan feasibility test
10. Current operating recommendations

Recommended conclusion language:

```markdown
Current appendix status: [Green / Yellow / Red].

As of [date], normalized recurring monthly surplus is approximately $X before bonus/RSU/event income. The Ally HYSA $80K gate is projected for [date]. The current capital-efficiency recommendations remain feasible / require category caps / require delaying after-tax Roth re-engagement.

The first adjustment lever, if the projection deteriorates, is discretionary category control. Base 401(k), HSA, insurance, and liquidity-protection moves should not be reduced unless the appendix status turns red.
```

---

## 12. Folder / File Naming Conventions

Use sortable, descriptive names:

### Master and tracker files

- `00_CASH_FLOW_MASTER_INDEX.md` — this file; project landing page
- `Spreadsheet_checklist_for_document_tracking.csv` — active intake tracker
- `2026_Cashflow_Decision_Log.md` — architecture decisions and closed classification items (immutable, append-only)
- `2026_Household_Cashflow_Reality_Appendix.md` — final exportable conclusion (created later)

### Source files

Recommended pattern:

`YYYY-MM-DD_Source_AccountOrTopic_Description.ext`

Examples:

- `2026-04-30_Chase_YTD_CardActivity.csv`
- `2026-04-30_Beacon_Checking_YTD.csv`
- `2026-05-17_Webster_Checking.csv`
- `2026-04-30_Ally_HYSA_Statement.pdf`
- `2026-04-17_Ashley_Paystub.pdf`
- `2026-05-01_Ion_Primary_Mortgage_Statement.pdf`
- `2026-05-01_HealthyPaws_Claim_EOB.pdf`

### Archive files

If a working memo is superseded, do not delete it. Rename or move it as:

- `archive/2026_YTD_Cashflow_Analysis_INITIAL_PARTIAL.md`
- `archive/old_tracker_versions/`

---

## 13. Current Open Inputs and Gaps

These are the current high-value inputs or cleanup areas after the spending-first reset and current parser-backed ingest pass:

| Item | Why it matters | Status |
|---|---|---|
| IonBank or other counterpart transfer export, if available | Explains the remaining Beacon `ONLINE XFR` rows that currently look like transfers without a paired source | Optional / open |
| Material Venmo / PayPal / Zelle / Cash App export | Captures spending or reimbursements that bypass the core bank + card feeds | Conditional |
| Durable manual cleanup for checks, Venmo labels, ambiguous payees, and generic transfers | Improves merchant/category accuracy now and survives later re-imports through transaction overrides | Active workflow |
| Monthly-file replacement plan for current YTD sources | Needed before swapping YTD exports for monthly files; do not mix both in one DB without an explicit reset or migration decision | Later, but important |
| Broader planning files (payroll, tax, insurance, debt, rental) | Only needed when the question shifts beyond spending reconstruction | Deferred |

---

## 14. Current Working Assumptions

These assumptions are provisional and should be replaced with document-backed numbers as files are uploaded:

1. Chase is the primary household credit card for discretionary spending.
2. Beacon / joint checking, Webster / Ashley checking, and Ally HYSA close most of the currently relevant transfer and household-funding loops.
3. Useful spending analysis should start from the available Chase, Beacon, Webster, and Ally feeds before broader documents are requested.
4. Ally HYSA is the primary liquidity and $80K gate account.
5. Bonus, RSU, refunds, and reimbursements should be treated as event income, not ordinary monthly income.
6. Card payments and inter-account transfers must be paired or excluded before category totals are trusted.
7. Exact-match manual cleanup should be stored as durable transaction overrides when the same issue is likely to recur on future imports.
8. Material P2P usage still needs its own export if it bypasses the core bank/card feeds.
9. Broader planning documents remain out of scope unless the user explicitly asks for those topics.

---

## 15. Default Working Sequence

### Step 1 — Read the MCP resources

Read `docs://master-index` and `docs://tracker` first.

### Step 2 — Measure source coverage and ingest new files

Use `read_document_metadata` to see which core sources are already present. If new parser-backed files are available, run `ingest_documents`.

### Step 3 — Neutralize transfer inflation

Use `pair_transfers` so card payments and inter-account moves do not inflate spending. Treat residual unpaired rows as diagnostics, not automatic spending.

### Step 4 — Analyze spending and store durable cleanup

Use `query_cashflow_data` for monthly and annual summaries, merchant/category review, recurring-charge review, and anomaly checks. When a manual label should survive future imports, store it through `upsert_transaction_override`.

### Step 5 — Layer on optional later work only if needed

Add classifier rules, normalization, planning documents, forward projection, or appendix output only when the current question explicitly calls for them.

---

## 16. What Not To Do

Do not:

- give generic budgeting advice;
- moralize spending categories;
- count credit-card payments as spending when card charges are already counted;
- treat bonuses, RSUs, refunds, reimbursements, or Ally transfers as ordinary income;
- collapse pet medical, pet routine, and pet lifestyle into one undifferentiated category;
- assume all annualized YTD spending is sustainable or recurring;
- recommend cutting base 401(k), HSA, insurance, or emergency liquidity before identifying discretionary category controls;
- re-open closed transaction-classification issues without new contradictory documents;
- use stale capital-efficiency documents when newer cash-flow records are available.

---

## 17. Default Output Format for Future Analysis

Use this format unless a narrower task is requested:

1. **Bottom line**
2. **Data used / material gaps**
3. **What changed since last update**
4. **Spending findings**
5. **Transfer / classification caveats**
6. **Risks / caveats**
7. **Specific next steps**

---

## 18. Immediate Next Steps

1. Keep this file and `Spreadsheet_checklist_for_document_tracking.csv` served through the MCP resources.
2. Continue using the four current parser-backed core feeds as the default analysis base.
3. When future monthly CSVs replace YTD files, make that a deliberate reset or migration step rather than mixing YTD and monthly files in the same database.
4. Use `pair_transfers` before drawing spending conclusions, and use durable overrides for checks, Venmo/payee cleanup, and sticky category fixes that should survive re-imports.
5. Request broader planning documents only when a later question explicitly depends on them.
