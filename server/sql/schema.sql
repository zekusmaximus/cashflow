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
  UNIQUE (source_document_name, source_record_key),
  FOREIGN KEY (account_id) REFERENCES accounts(id),
  FOREIGN KEY (import_batch_id) REFERENCES import_batches(id)
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

CREATE VIEW IF NOT EXISTS monthly_cashflow_summary AS
SELECT
  substr(occurred_on, 1, 7) AS month,
  ROUND(SUM(CASE WHEN direction = 'inflow' THEN amount ELSE 0 END), 2) AS inflow,
  ROUND(SUM(CASE WHEN direction = 'outflow' THEN ABS(amount) ELSE 0 END), 2) AS outflow,
  ROUND(SUM(CASE WHEN direction = 'inflow' THEN amount WHEN direction = 'outflow' THEN amount ELSE 0 END), 2) AS net_cash_flow
FROM transactions
GROUP BY substr(occurred_on, 1, 7)
ORDER BY month;
