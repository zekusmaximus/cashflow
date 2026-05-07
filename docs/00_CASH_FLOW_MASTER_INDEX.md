# Master Index — 2026 Household Cash-Flow Reality Dashboard

**Last updated:** 2026-05-06  
**Maintained by:** Jeff, with ChatGPT/AI-assisted review  
**Project folder:** Cash Flow  
**Purpose:** Single landing page for any new chat session working on the household cash-flow dashboard. Read this file first, then the document tracker spreadsheet.

---

## Quick Status

This project exists to build a **document-based household cash-flow reconstruction and forward projection** for 2026–2027. Its job is not to replace the capital-efficiency plan. Its job is to answer the practical question the capital-efficiency plan depends on:

> Can the household sustain the planned 401(k), HSA, insurance, liquidity, Roth, and taxable-investment decisions without creating lifestyle strain or relying on vague assumptions?

The first project file is:

| Document | Purpose | Status |
|---|---|---|
| `Spreadsheet_checklist_for_document_tracking.xlsx` | Collection tracker for all documents needed to build the dashboard | **Active first-file tracker** |

The current tracker identifies **86 total document/data items** across nine categories:

1. Core Transactions
2. Income & Payroll
3. Debt & Housing
4. Utilities & Household
5. Insurance & Protection
6. Rental Property
7. Medical / HSA
8. Subscriptions & Apps
9. Tax Documents

Current tracker status at project launch: **0 / 86 items obtained inside this folder**, pending document uploads and status updates. Some documents already exist in the broader capital-efficiency project and can be copied or re-uploaded here rather than re-created.

Project architecture: data intake is **manual CSV/PDF upload only** (DL-001) and refresh cadence is **on-demand only** (DL-002). See `2026_Cashflow_Decision_Log.md` for both decisions and for closed classification items.

---

## How to Start a New Chat

Use this prompt:

> Continuing the 2026 Household Cash-Flow Reality Dashboard. Read `00_CASH_FLOW_MASTER_INDEX.md` first, then `Spreadsheet_checklist_for_document_tracking.xlsx`. Treat uploaded transaction files, paystubs, account statements, bills, and insurance documents as the primary factual record. Build a cash-flow reconstruction and forward projection that can later export a clean `2026_Household_Cashflow_Reality_Appendix.md` for the capital-efficiency plan.

If the chat is about a specific task, add one of these:

- “Focus only on document intake and tracker updates.”
- “Focus only on Chase and checking categorization.”
- “Focus only on payroll and net-pay modeling.”
- “Focus only on fixed obligations and annual bills.”
- “Focus only on HYSA trajectory and capital-plan feasibility.”
- “Create or update the importable cash-flow appendix.”

---

## 1. Project Objective

The dashboard should produce a practical household cash-flow control system, not generic budgeting advice.

It should answer:

1. What is the household’s true monthly fixed burn?
2. What is the household’s true variable lifestyle burn?
3. Which expenses are recurring, seasonal, one-time, reimbursable, tax-related, or abnormal?
4. What is normal monthly net income after payroll deductions?
5. How dependent is the household on bonuses, RSUs, tax refunds, reimbursements, or transfers?
6. What is the realistic Ally HYSA trajectory?
7. Is the $80,000 HYSA gate feasible before 2027 Roth re-engagement?
8. Which spending categories threaten the capital-efficiency plan?
9. What category caps or sinking funds would preserve lifestyle while preventing cash-flow drift?
10. What clean conclusion should be imported into `Household_Capital_Efficiency_Review_2026.md`?

---

## 2. Relationship to the Capital-Efficiency Plan

This project is a **supporting operational model** for the capital-efficiency plan.

The capital-efficiency plan answers:

> What should we do with marginal dollars?

This cash-flow project answers:

> Do we actually have the marginal dollars, and where are they going?

The final output should be a concise markdown appendix:

`2026_Household_Cashflow_Reality_Appendix.md`

That appendix should be importable into, or cross-referenced by, `Household_Capital_Efficiency_Review_2026.md`.

The capital-efficiency plan should not become a transaction-level budget file. This project should own the transaction-level work and export only the conclusions needed for planning.

---

## 3. Source-of-Truth Rules

### Primary factual record

Use actual documents and exports first:

- CSV transaction exports
- bank and credit-card statements
- payroll stubs
- loan and mortgage statements
- insurance declarations and premium schedules
- rental records
- HSA records
- tax records
- subscription screenshots/exports

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
- Classify Ashley → joint checking as household funding transfer unless payroll/RSU source records show external income.
- Split RSUs into compensation income, withholding, shares sold, shares retained, and net cash received.
- Split HSA activity into payroll contributions, employer contributions, investment activity, distributions, and medical spending.

---

## 4. Current Core Tracker

The active intake tracker is:

`Spreadsheet_checklist_for_document_tracking.xlsx`

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

### Tracker maintenance rules

- Mark a document obtained only when the file is uploaded to this Cash Flow project or clearly available in the connected folder.
- If a document already exists in the capital-efficiency project, note: “Available in capital-efficiency project; copy/upload if needed.”
- If a document is superseded by a newer file, keep the old one noted but mark the newer file as controlling.
- If a document is not applicable, mark it “N/A” in notes rather than deleting the line.
- For recurring monthly documents, note coverage period, e.g., “Jan–Apr 2026 complete; May pending.”

---

## 5. Document Intake Priority

### Phase 1 — Minimum viable dashboard

These documents are required before any reliable household-wide dashboard can be produced:

| Priority | Document / data source | Why it matters |
|---:|---|---|
| 1 | Chase credit-card CSV — all 2026 YTD | Main discretionary and semi-discretionary spending source |
| 2 | Beacon / joint checking CSV — all 2026 YTD | Mortgage, card payments, Jeff pay, transfers, checks |
| 3 | Ashley separate checking CSV/statements — all 2026 YTD | Closes the largest missing source of card payments and household funding |
| 4 | Ally HYSA CSV/statements — all 2026 YTD | HYSA build, transfers, interest, liquidity gate |
| 5 | Jeff 2026 paystubs, including bonuses | Normal net pay and 401(k)/tax impact |
| 6 | Ashley 2026 paystubs, including bonus/RSU stubs | Normal net pay, 401(k), HSA, RSU withholding, benefit deductions |
| 7 | Current 401(k) and HSA election confirmations | Forward cash-flow modeling |
| 8 | Current mortgage, HELOC, rental mortgage, and car-loan statements | Fixed-debt baseline |
| 9 | Insurance premium schedules: home, auto, umbrella, life, DI/LTD if available | Annual and monthly fixed-cost normalization |
| 10 | Rental lease, rent history, HOA/condo dues, repairs, and insurance | True rental cash flow |

### Phase 2 — Better categorization and leakage detection

Upload after Phase 1 or as available:

- Amazon order history export
- Instacart / grocery delivery history
- Venmo / PayPal / Zelle history
- Apple / Google / Amazon subscription screenshots
- streaming and software subscription lists
- pet grooming/daycare/boarding invoices
- vet bills and insurance reimbursement history
- utilities and home-service bills

### Phase 3 — Tax and planning precision

Upload for 2027 projection and capital-plan handoff:

- 2025 joint tax return
- 2026 tax projection from Cherubino, if prepared
- 2026 estimated tax payment receipts, if any
- RSU tax withholding records
- brokerage tax estimates / dividend estimates
- prior-year Schedule E and depreciation schedule for rental property

---

## 6. Dashboard Structure

The finished dashboard should include these sections or spreadsheet tabs:

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

## 8. Monthly Normalization Rules

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

## 9. Key Output Metrics

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

## 10. Capital-Plan Feasibility Tests

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

## 11. Importable Appendix Specification

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
- `Spreadsheet_checklist_for_document_tracking.xlsx` — active intake tracker
- `2026_Cashflow_Decision_Log.md` — architecture decisions and closed classification items (immutable, append-only)
- `2026_Household_Cashflow_Reality_Appendix.md` — final exportable conclusion (created later)

### Source files

Recommended pattern:

`YYYY-MM-DD_Source_AccountOrTopic_Description.ext`

Examples:

- `2026-04-30_Chase_YTD_CardActivity.csv`
- `2026-04-30_Beacon_Checking_YTD.csv`
- `2026-04-30_Ashley_Checking_YTD.csv`
- `2026-04-30_Ally_HYSA_Statement.pdf`
- `2026-04-17_Ashley_Paystub.pdf`
- `2026-05-01_Ion_Primary_Mortgage_Statement.pdf`
- `2026-05-01_HealthyPaws_Claim_EOB.pdf`

### Archive files

If a working memo is superseded, do not delete it. Rename or move it as:

- `archive/2026_YTD_Cashflow_Analysis_INITIAL_PARTIAL.md`
- `archive/old_tracker_versions/`

---

## 13. Known Open Items at Launch

These are the highest-impact missing items based on prior cash-flow work and the new tracker:

| Item | Why it matters | Status |
|---|---|---|
| Ashley checking export | Closes the missing source of many Chase payments and household transfers | Open |
| Current full Chase CSV | Main lifestyle-spend register | Open in new folder unless already uploaded |
| Current full Beacon checking CSV | Joint checking and fixed-payment register | Open in new folder unless already uploaded |
| Ally HYSA YTD export/statements | Required for $80K gate modeling | Open in new folder unless already uploaded |
| Current 2026 paystubs for both spouses | Required for forward net-pay model | Partial in capital-efficiency project; verify coverage |
| Insurance premium schedules and quotes | Required for fixed-cost scenario modeling | Open / pending |
| Rental income and expense records | Required for true rental cash-flow branch | Open |
| Subscription screenshots | Required for app/subscription audit | Open |

---

## 14. Current Working Assumptions

These assumptions are provisional and should be replaced with document-backed numbers as files are uploaded:

1. Chase is the primary household credit card for discretionary spending.
2. Beacon/joint checking captures many fixed obligations and Jeff-side income, but not the full household cash-flow picture.
3. Ashley maintains or uses another account that must be added to close the loop.
4. Ally HYSA is the primary liquidity and $80K gate account.
5. Bonus, RSU, refunds, and reimbursements should be treated as event income, not ordinary monthly income.
6. Pet spending must be separated into routine, medical abnormal, grooming/daycare/boarding, and insurance premiums/reimbursements.
7. Subscription/app leakage is likely real but should be confirmed before cuts are recommended.
8. The base capital-efficiency plan remains in force unless the dashboard shows red feasibility.

---

## 15. First Build Sequence

### Step 1 — Intake and tracker update

Upload or copy the Phase 1 documents. Update the tracker with obtained status, date added, and notes.

### Step 2 — Reconstruct 2026 YTD actual cash flow

Build combined transaction tables for:

- Chase
- Beacon / joint checking
- Ashley checking
- Ally HYSA
- Venmo/PayPal/Zelle, if provided

Classify transfers so spending is not double-counted.

### Step 3 — Normalize monthly burn

Separate:

- fixed monthly obligations
- variable lifestyle spend
- seasonal/annual sinking funds
- one-time abnormal items
- event income

### Step 4 — Build forward projection

Project through 12/31/2027:

- normal net pay after current/planned payroll elections
- fixed obligations
- insurance premiums
- rental cash flow
- known annual bills
- expected bonus/RSU events
- HYSA balance trajectory

### Step 5 — Produce appendix

Create `2026_Household_Cashflow_Reality_Appendix.md` with a clear Green/Yellow/Red conclusion and a concise recommendation table.

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
2. **Data used / data missing**
3. **What changed since last update**
4. **Cash-flow findings**
5. **Capital-plan feasibility impact**
6. **Risks / caveats**
7. **Specific next steps**

---

## 18. Immediate Next Steps

1. Put this file in the Cash Flow folder as `00_CASH_FLOW_MASTER_INDEX.md`.
2. Keep `Spreadsheet_checklist_for_document_tracking.xlsx` as the active intake tracker.
3. Upload Phase 1 documents, starting with Ashley checking, Chase, Beacon, Ally, and current paystubs.
4. After the first complete upload pass, create the first dashboard reconstruction.
5. Once the dashboard is reliable, export `2026_Household_Cashflow_Reality_Appendix.md` for the capital-efficiency plan.
