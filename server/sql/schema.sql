PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS documents (
  id TEXT PRIMARY KEY,
  category TEXT NOT NULL,
  document_name TEXT NOT NULL,
  subject_matter TEXT NOT NULL,
  preferred_format TEXT NOT NULL,
  priority TEXT NOT NULL,
  source_hint TEXT NOT NULL,
  why_needed TEXT NOT NULL,
  obtained INTEGER NOT NULL DEFAULT 0 CHECK (obtained IN (0, 1)),
  date_added TEXT,
  notes TEXT NOT NULL DEFAULT '',
  local_path TEXT,
  file_hash TEXT,
  discovered_at TEXT
);

CREATE TABLE IF NOT EXISTS accounts (
  id TEXT PRIMARY KEY,
  institution TEXT NOT NULL,
  account_name TEXT NOT NULL,
  account_type TEXT NOT NULL,
  owner TEXT NOT NULL,
  currency TEXT NOT NULL DEFAULT 'USD',
  is_active INTEGER NOT NULL DEFAULT 1 CHECK (is_active IN (0, 1))
);

CREATE TABLE IF NOT EXISTS import_batches (
  id TEXT PRIMARY KEY,
  source_name TEXT NOT NULL,
  parser_version TEXT NOT NULL,
  imported_at TEXT NOT NULL,
  raw_payload TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS transactions (
  id TEXT PRIMARY KEY,
  account_id TEXT NOT NULL,
  import_batch_id TEXT NOT NULL,
  source_record_key TEXT NOT NULL,
  source_document_name TEXT NOT NULL,
  occurred_on TEXT NOT NULL,
  posted_on TEXT,
  description_raw TEXT NOT NULL,
  merchant_normalized TEXT,
  amount REAL NOT NULL,
  direction TEXT NOT NULL CHECK (direction IN ('inflow', 'outflow', 'transfer')),
  currency TEXT NOT NULL DEFAULT 'USD',
  primary_category TEXT NOT NULL,
  subcategory TEXT,
  household_role TEXT NOT NULL DEFAULT 'joint',
  lifecycle TEXT NOT NULL DEFAULT 'recurring',
  transfer_group_key TEXT,
  is_reconciled INTEGER NOT NULL DEFAULT 0 CHECK (is_reconciled IN (0, 1)),
  statement_period TEXT,
  metadata_json TEXT NOT NULL DEFAULT '{}',
  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  -- Dedup identity is account-scoped, not file-scoped: the same transaction
  -- must collapse to one row whether it arrives in a YTD export or a
  -- per-month file. source_record_key is derived from stable transaction
  -- content (date + amount + normalized description + occurrence ordinal),
  -- so (account_id, source_record_key) uniquely identifies a transaction
  -- across overlapping source documents. (Existing databases are migrated to
  -- this constraint by scripts/migrations/2026-05-31_transactions_account_scoped_unique.py.)
  UNIQUE (account_id, source_record_key),
  FOREIGN KEY (account_id) REFERENCES accounts(id),
  FOREIGN KEY (import_batch_id) REFERENCES import_batches(id)
);

CREATE TABLE IF NOT EXISTS transaction_overrides (
  id TEXT PRIMARY KEY,
  match_key TEXT NOT NULL UNIQUE,
  account_id TEXT NOT NULL,
  occurred_on TEXT NOT NULL,
  amount REAL NOT NULL,
  description_raw TEXT NOT NULL,
  merchant_normalized TEXT,
  primary_category TEXT,
  subcategory TEXT,
  household_role TEXT,
  lifecycle TEXT,
  note TEXT NOT NULL DEFAULT '',
  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  FOREIGN KEY (account_id) REFERENCES accounts(id)
);

CREATE TABLE IF NOT EXISTS rsu_grants (
  id TEXT PRIMARY KEY,
  owner TEXT NOT NULL,
  employer TEXT NOT NULL,
  award_label TEXT NOT NULL,
  grant_date TEXT NOT NULL,
  shares_granted REAL NOT NULL,
  award_type TEXT NOT NULL,
  metadata_json TEXT NOT NULL DEFAULT '{}'
);

CREATE TABLE IF NOT EXISTS rsu_vesting_events (
  id TEXT PRIMARY KEY,
  grant_id TEXT NOT NULL,
  vest_date TEXT NOT NULL,
  shares_vested REAL NOT NULL,
  shares_sold REAL NOT NULL DEFAULT 0,
  shares_retained REAL NOT NULL DEFAULT 0,
  fair_market_value REAL NOT NULL DEFAULT 0,
  gross_income REAL NOT NULL DEFAULT 0,
  withholding_amount REAL NOT NULL DEFAULT 0,
  net_cash_received REAL NOT NULL DEFAULT 0,
  brokerage_account_id TEXT,
  metadata_json TEXT NOT NULL DEFAULT '{}',
  FOREIGN KEY (grant_id) REFERENCES rsu_grants(id),
  FOREIGN KEY (brokerage_account_id) REFERENCES accounts(id)
);

CREATE TABLE IF NOT EXISTS tax_safe_harbor_targets (
  id TEXT PRIMARY KEY,
  tax_year INTEGER NOT NULL,
  jurisdiction TEXT NOT NULL,
  method TEXT NOT NULL,
  prior_year_liability REAL NOT NULL DEFAULT 0,
  current_year_projection REAL NOT NULL DEFAULT 0,
  withholding_ytd REAL NOT NULL DEFAULT 0,
  estimated_payments_ytd REAL NOT NULL DEFAULT 0,
  target_amount REAL NOT NULL DEFAULT 0,
  remaining_gap REAL NOT NULL DEFAULT 0,
  due_dates_json TEXT NOT NULL DEFAULT '[]',
  notes TEXT NOT NULL DEFAULT ''
);

CREATE TABLE IF NOT EXISTS leakage_categories (
  id TEXT PRIMARY KEY,
  category_key TEXT NOT NULL UNIQUE,
  name TEXT NOT NULL,
  description TEXT NOT NULL,
  owner TEXT NOT NULL DEFAULT 'joint',
  monthly_cap REAL,
  annual_cap REAL,
  severity TEXT NOT NULL DEFAULT 'watch',
  notes TEXT NOT NULL DEFAULT ''
);

CREATE TABLE IF NOT EXISTS liquidity_gates (
  id TEXT PRIMARY KEY,
  gate_key TEXT NOT NULL UNIQUE,
  label TEXT NOT NULL,
  current_amount REAL NOT NULL DEFAULT 0,
  target_amount REAL NOT NULL DEFAULT 0,
  target_date TEXT NOT NULL,
  notes TEXT NOT NULL DEFAULT ''
);

-- Seed the Ally HYSA liquidity gate. current_amount is a placeholder: run
-- `refresh-hysa-gate` to populate it from v_computed_balance. target_amount
-- is the $80,000 HYSA savings goal.
INSERT OR IGNORE INTO liquidity_gates
  (id, gate_key, label, current_amount, target_amount, target_date, notes)
VALUES (
  'gate-ally-hysa',
  'ally_hysa',
  'Ally HYSA Balance',
  0,
  80000,
  '2026-12-31',
  'Anchor-computed. Run refresh-hysa-gate to update current_amount from transactions.'
);

CREATE TABLE IF NOT EXISTS reconciliation_periods (
  id TEXT PRIMARY KEY,
  account_id TEXT NOT NULL,
  period_start TEXT NOT NULL,
  period_end TEXT NOT NULL,
  statement_opening_balance REAL,
  statement_closing_balance REAL,
  closing_balance_source TEXT,
  computed_inflows REAL NOT NULL DEFAULT 0,
  computed_outflows REAL NOT NULL DEFAULT 0,
  computed_transfers_in REAL NOT NULL DEFAULT 0,
  computed_transfers_out REAL NOT NULL DEFAULT 0,
  computed_closing_balance REAL,
  variance_amount REAL,
  variance_explanation TEXT NOT NULL DEFAULT '',
  computed_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  UNIQUE (account_id, period_start, period_end),
  FOREIGN KEY (account_id) REFERENCES accounts(id)
);

CREATE TABLE IF NOT EXISTS classification_rules (
  id TEXT PRIMARY KEY,
  pattern TEXT NOT NULL,
  account_filter TEXT,
  direction_filter TEXT,
  primary_category TEXT,
  subcategory TEXT,
  merchant_normalized TEXT,
  household_role TEXT,
  lifecycle TEXT,
  confidence TEXT NOT NULL DEFAULT 'medium',
  priority INTEGER NOT NULL DEFAULT 100,
  notes TEXT NOT NULL DEFAULT '',
  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

-- Seed rules are inserted once; INSERT OR IGNORE skips rows whose id already exists.
-- priority: lower number = checked first; 900 = fallback catchall.

INSERT OR IGNORE INTO classification_rules
  (id, pattern, account_filter, direction_filter, primary_category, subcategory,
   merchant_normalized, household_role, lifecycle, confidence, priority, notes)
VALUES
  ('rule-transfer-catchall', '.+', NULL, 'transfer', 'transfer', NULL,
   NULL, NULL, NULL, 'high', 900,
   'Any transfer-direction row gets primary_category=transfer'),

  ('rule-interest-paid', '(?i)interest paid', NULL, NULL, 'income', 'interest',
   NULL, NULL, 'recurring', 'high', 10,
   'Ally and savings interest postings'),

  ('rule-payroll-novartis', '(?i)novartis.*payroll|payroll.*novartis', NULL, NULL,
   'income', 'payroll', 'Novartis Payroll', 'ashley', 'recurring', 'high', 10,
   'Ashley Novartis payroll deposits'),

  ('rule-mobile-check-dep', '(?i)mobile check dep', NULL, 'inflow', 'income',
   'check_deposit', 'Mobile Check Deposit', NULL, 'one_time', 'high', 10,
   'Mobile check deposits are real inflows, not transfers'),

  ('rule-fidelity-moneyline', '(?i)fid bkg svc llc moneyline', NULL, NULL,
   'income', 'rsu_proceeds', 'Fidelity MoneyLine', NULL, 'one_time', 'high', 15,
   'Fidelity MoneyLine sweeps — RSU/stock-plan proceeds on Webster and Beacon'),

  ('rule-amazon', '(?i)\bamazon\b|\bamzn\b', NULL, NULL,
   'variable_lifestyle', 'amazon', 'Amazon', NULL, 'recurring', 'high', 20,
   'Amazon purchases and subscriptions'),

  ('rule-whole-foods', '(?i)whole foods|wholefds', NULL, NULL,
   'variable_lifestyle', 'groceries', 'Whole Foods', NULL, 'recurring', 'high', 20,
   'Whole Foods grocery purchases');

-- Self-Help FCU rental savings (acct-selfhelp-savings). Every rule here is
-- account_filter-scoped so none of it can reach another account's rows.
--
-- These match against the joined description the selfhelp parser builds,
-- "<Description> | <Ext>" (see parsers/selfhelp_csv.build_description). That
-- matters because Description is EMPTY on 8 of the 21 rows in the
-- life-of-account export -- including every "Share Deposit" rent row -- so the
-- transaction type only reaches the classifier through the Ext half. On those
-- rows there is no "|" at all (the join strips the separator), which is why
-- the Ext-anchored patterns below accept either a leading "|" or the start of
-- the string. The raw Ext value is also stored at metadata_json.ext for
-- future rules that want an exact match rather than a substring.
--
-- Priorities: sweeps/verify (15) < dividends (18) < rent (20). Dividends sit
-- ahead of rent deliberately, per the handoff.

INSERT OR IGNORE INTO classification_rules
  (id, pattern, account_filter, direction_filter, primary_category, subcategory,
   merchant_normalized, household_role, lifecycle, confidence, priority, notes)
VALUES
  -- Sweeps out to the Ally HYSA. The parser already tags these
  -- direction='transfer'; this rule pins the category so they never count as
  -- rental spending, and names them for the register. The trailing ACH id is
  -- masked in newer exports, so the pattern stops at the "$TRANSFER" token.
  ('rule-selfhelp-ally-sweep', '(?i)ally bank \$?\s*transfer',
   'acct-selfhelp-savings', 'transfer', 'transfer', NULL,
   'Ally Sweep', 'jeff', 'recurring', 'high', 5,
   'Self-Help -> Ally rent sweeps; pairs with the Ally "Requested transfer from" legs'),

  -- Ally's account-verification micro-deposits (+0.60, +0.46, -1.06 on
  -- 2026-07-15). They net to zero and are plumbing, not income. Matched by
  -- description, never by amount, so a future re-verification of any size
  -- lands the same way.
  ('rule-selfhelp-acctverify', '(?i)ally bank acctverify',
   'acct-selfhelp-savings', 'transfer', 'transfer', NULL,
   'Ally Account Verification', 'jeff', 'one_time', 'high', 15,
   'Ally micro-deposit account-verification probes; net zero, not income'),

  -- Share dividends. Matched on the Ext token, NOT the description text: the
  -- five dividend rows use two different description formats ("TERM: ... APYE:"
  -- for 3/31 and 5/31, "Annual Percentage Yield Earned:" for 6/30, 7/31 and
  -- 8/31), so a description-text rule would catch at most three of five.
  ('rule-selfhelp-dividend', '(?i)(?:^|\|\s*)dividend\s*$',
   'acct-selfhelp-savings', 'inflow', 'income', 'interest',
   'Self-Help Dividend', 'jeff', 'recurring', 'high', 18,
   'Self-Help share dividends; matched on the Ext column, not description text'),

  -- Rent from the 140 Kane D5 tenant. Matched on the Ext token and NEVER on
  -- amount: the executed lease steps $1,295/mo through 2026-12 up to
  -- $1,375/mo from 2027-01-01, and a life-of-account export may carry
  -- pre-2026 rent at other amounts. Accepts both Ext spellings the tenant has
  -- used ("Share Deposit Transfer" through 2026-04, "Share Deposit" from
  -- 2026-05) and, because it anchors on the end of the string, never matches
  -- the "ACH Share Withdrawal" sweeps.
  ('rule-selfhelp-rent', '(?i)(?:^|\|\s*)share deposit(?: transfer)?\s*$',
   'acct-selfhelp-savings', 'inflow', 'rental', 'rental_income',
   '140 Kane D5 Rent', 'rental', 'recurring', 'high', 20,
   'Rental income from the 140 Kane D5 tenant; matched on Ext, never on amount');

-- Monthly cashflow rollup — CATEGORY-DRIVEN, never direction-driven. This view
-- reconciles exactly to compute_monthly_summary in
-- server/src/liquidity_gate_mcp/monthly_summary.py (inflow == fcf_transactions
-- .inflows; outflow == .fixed_obligations + .discretionary; net_cash_flow ==
-- inflow - outflow). Per DL-2026-06-10-A the IonBank mortgage carries
-- direction='transfer' but primary_category='fixed_obligation', so spend can no
-- longer be computed from `direction` — a transfer-direction fixed_obligation row
-- IS spending and must land in `outflow`. Only direction='inflow' rows are held
-- out of the outflow buckets, so refunds (inflow rows in a spend category) net
-- out by exclusion exactly as the Python path does.
--
--   inflow  = income inflows only (primary_category='income' AND direction='inflow')
--   outflow = fixed_obligation + discretionary, direction != 'inflow'
--   net_cash_flow = inflow - outflow
--
-- The category strings below are duplicated from monthly_summary.py
-- (DISCRETIONARY_CATEGORIES = variable_lifestyle/medical/abnormal, plus the
-- fixed_obligation scalar). Keep the two in sync — the reconciliation test in
-- server/tests/test_annual_summary.py pins them together. transfer, income, tax,
-- investment, rental and business_expense are intentionally outside the spend
-- bridge; do not add them to `outflow`.
-- Column names (month, inflow, outflow, net_cash_flow) are load-bearing for
-- src/services/sqlite.ts and the CashFlowMonth type — only the semantics change.
-- CREATE VIEW IF NOT EXISTS will not replace an existing view, so drop first.
DROP VIEW IF EXISTS monthly_cashflow_summary;
CREATE VIEW monthly_cashflow_summary AS
SELECT
  substr(occurred_on, 1, 7) AS month,
  ROUND(SUM(CASE WHEN primary_category = 'income' AND direction = 'inflow'
                 THEN amount ELSE 0 END), 2) AS inflow,
  ROUND(SUM(CASE WHEN primary_category IN
                      ('fixed_obligation', 'variable_lifestyle', 'medical', 'abnormal')
                  AND direction != 'inflow'
                 THEN ABS(amount) ELSE 0 END), 2) AS outflow,
  ROUND(SUM(CASE WHEN primary_category = 'income' AND direction = 'inflow'
                 THEN amount ELSE 0 END)
        - SUM(CASE WHEN primary_category IN
                      ('fixed_obligation', 'variable_lifestyle', 'medical', 'abnormal')
                  AND direction != 'inflow'
                 THEN ABS(amount) ELSE 0 END), 2) AS net_cash_flow
FROM transactions
GROUP BY substr(occurred_on, 1, 7)
ORDER BY month;

-- Estimated current balance per account, anchored on the most recent known
-- statement closing. The anchor is the latest reconciliation_periods row with
-- a non-NULL statement_closing_balance; the 12/31/2025 opening balances are
-- seeded as Dec-2025 rows (see computed_balance.seed_balance_anchors), so they
-- serve as the fallback anchor until a real monthly statement is recorded.
-- net_since_anchor sums every transaction strictly after the anchor date
-- through today, and computed_balance = anchor_balance + net_since_anchor for
-- every account type. All three directions count — each row is real money
-- moving on that account, so a transfer in or out shifts the balance exactly
-- like any other posting. (Excluding transfers belongs in spending analysis,
-- where inter-account moves would otherwise double-count as spending — not in
-- a balance view.) Accounts with no anchor at all (e.g. Citi, untracked at
-- 2025-12-31) return NULL for the computed columns.
--
-- Sign convention (DL-2026-09-22-E):
--   * Cash accounts (checking, savings): the balance is money held. Inflows
--     (+ABS), outflows (-ABS), transfers by their stored signed amount.
--   * Credit cards (accounts.account_type = 'credit_card'): the balance is the
--     amount OWED, positive = owed, matching the balances.toml opening seed and
--     reconcile_periods (closing = opening - net_signed). The cash-signed net
--     is negated: charges (outflow) raise the balance (+ABS), refunds and
--     statement credits (inflow) lower it (-ABS), and payments (transfer, stored
--     positive) lower it (-amount). A negative card balance is a credit, which
--     for this household means a wrong seed or missing charges; verify_state
--     reports it.
-- CREATE VIEW IF NOT EXISTS will not replace an existing view, and
-- DatabaseManager.initialize() re-runs this file at every startup, so drop
-- first to pick up definition changes on an existing database.
DROP VIEW IF EXISTS v_computed_balance;
CREATE VIEW v_computed_balance AS
SELECT
  a.id AS account_id,
  anchor.anchor_date AS anchor_date,
  anchor.anchor_balance AS anchor_balance,
  CASE WHEN anchor.anchor_date IS NULL THEN NULL ELSE ROUND(COALESCE((
    SELECT SUM(CASE
                 WHEN t.direction = 'inflow'   THEN ABS(t.amount)
                 WHEN t.direction = 'outflow'  THEN -ABS(t.amount)
                 WHEN t.direction = 'transfer' THEN t.amount
                 ELSE 0
               END)
      FROM transactions t
     WHERE t.account_id = a.id
       AND t.occurred_on > anchor.anchor_date
       AND t.occurred_on <= DATE('now', 'localtime')
  ), 0) * CASE WHEN a.account_type = 'credit_card' THEN -1 ELSE 1 END, 2)
  END AS net_since_anchor,
  CASE WHEN anchor.anchor_date IS NULL THEN NULL ELSE ROUND(anchor.anchor_balance + COALESCE((
    SELECT SUM(CASE
                 WHEN t.direction = 'inflow'   THEN ABS(t.amount)
                 WHEN t.direction = 'outflow'  THEN -ABS(t.amount)
                 WHEN t.direction = 'transfer' THEN t.amount
                 ELSE 0
               END)
      FROM transactions t
     WHERE t.account_id = a.id
       AND t.occurred_on > anchor.anchor_date
       AND t.occurred_on <= DATE('now', 'localtime')
  ), 0) * CASE WHEN a.account_type = 'credit_card' THEN -1 ELSE 1 END, 2)
  END AS computed_balance,
  DATE('now', 'localtime') AS as_of_date
FROM accounts a
LEFT JOIN (
  SELECT rp.account_id,
         rp.period_end AS anchor_date,
         rp.statement_closing_balance AS anchor_balance
    FROM reconciliation_periods rp
   WHERE rp.statement_closing_balance IS NOT NULL
     AND NOT EXISTS (
           SELECT 1
             FROM reconciliation_periods rp2
            WHERE rp2.account_id = rp.account_id
              AND rp2.statement_closing_balance IS NOT NULL
              AND rp2.period_end > rp.period_end
         )
) anchor ON anchor.account_id = a.id;
