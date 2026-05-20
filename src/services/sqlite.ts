import type {
  CashFlowMonth,
  DashboardRange,
  DashboardSnapshot,
  HysaTrajectory,
  HysaTrajectoryPoint,
  LeakageCategory,
  LiquidityGate,
  ReconciliationPeriod,
  SubscriptionAuditEntry,
  Transaction,
  TransactionDirectionFilter,
  TransactionPage,
} from '../features/dashboard/types';
import { dashboardMock } from '../features/dashboard/mock';
import { projectHysaGate } from '../features/dashboard/hysa-projection';
import { isTauriRuntime } from '../lib/tauri';

/** Master-index §9: $80K HYSA gate before 2027 Roth re-engagement. */
const HYSA_GATE_TARGET = 80_000;

interface SqlDatabase {
  execute(query: string, bindValues?: unknown[]): Promise<unknown>;
  select<T>(query: string, bindValues?: unknown[]): Promise<T[]>;
}

let databasePromise: Promise<SqlDatabase | null> | null = null;
let schemaPromise: Promise<string> | null = null;

const schemaAssetUrl = new URL('../../server/sql/schema.sql', import.meta.url).href;

export function splitSqlStatements(sql: string): string[] {
  // Robust splitter: respects single/double-quoted strings and line/block
  // comments so that semicolons inside SQL strings or trigger bodies do not
  // prematurely end a statement.
  const statements: string[] = [];
  let buffer = '';
  let i = 0;
  const length = sql.length;

  let inSingle = false;
  let inDouble = false;
  let inLineComment = false;
  let inBlockComment = false;
  let beginDepth = 0;

  while (i < length) {
    const ch = sql[i];
    const next = sql[i + 1];

    if (inLineComment) {
      buffer += ch;
      if (ch === '\n') inLineComment = false;
      i += 1;
      continue;
    }
    if (inBlockComment) {
      buffer += ch;
      if (ch === '*' && next === '/') {
        buffer += next;
        i += 2;
        inBlockComment = false;
        continue;
      }
      i += 1;
      continue;
    }
    if (inSingle) {
      buffer += ch;
      if (ch === "'") {
        if (next === "'") {
          buffer += next;
          i += 2;
          continue;
        }
        inSingle = false;
      }
      i += 1;
      continue;
    }
    if (inDouble) {
      buffer += ch;
      if (ch === '"') {
        if (next === '"') {
          buffer += next;
          i += 2;
          continue;
        }
        inDouble = false;
      }
      i += 1;
      continue;
    }

    if (ch === '-' && next === '-') {
      buffer += ch + next;
      i += 2;
      inLineComment = true;
      continue;
    }
    if (ch === '/' && next === '*') {
      buffer += ch + next;
      i += 2;
      inBlockComment = true;
      continue;
    }
    if (ch === "'") {
      buffer += ch;
      inSingle = true;
      i += 1;
      continue;
    }
    if (ch === '"') {
      buffer += ch;
      inDouble = true;
      i += 1;
      continue;
    }

    // Detect BEGIN ... END for triggers (case-insensitive, word boundary).
    if ((ch === 'B' || ch === 'b') && /^begin\b/i.test(sql.slice(i))) {
      buffer += sql.slice(i, i + 5);
      beginDepth += 1;
      i += 5;
      continue;
    }
    if ((ch === 'E' || ch === 'e') && /^end\b/i.test(sql.slice(i)) && beginDepth > 0) {
      buffer += sql.slice(i, i + 3);
      beginDepth -= 1;
      i += 3;
      continue;
    }

    if (ch === ';' && beginDepth === 0) {
      const trimmed = buffer.trim();
      if (trimmed.length > 0 && !isCommentOnly(trimmed)) {
        statements.push(trimmed);
      }
      buffer = '';
      i += 1;
      continue;
    }

    buffer += ch;
    i += 1;
  }

  const tail = buffer.trim();
  if (tail.length > 0 && !isCommentOnly(tail)) {
    statements.push(tail);
  }

  return statements;
}

function isCommentOnly(statement: string): boolean {
  // Strip leading line/block comments and check if anything substantive remains.
  let s = statement.trim();
  while (s.length > 0) {
    if (s.startsWith('--')) {
      const newline = s.indexOf('\n');
      if (newline === -1) return true;
      s = s.slice(newline + 1).trim();
      continue;
    }
    if (s.startsWith('/*')) {
      const close = s.indexOf('*/');
      if (close === -1) return true;
      s = s.slice(close + 2).trim();
      continue;
    }
    return false;
  }
  return true;
}

async function loadSchemaSql(): Promise<string> {
  if (!schemaPromise) {
    schemaPromise = fetch(schemaAssetUrl).then(async (response) => {
      if (!response.ok) {
        throw new Error('Unable to load the shared SQLite schema.');
      }

      return response.text();
    });
  }

  return schemaPromise;
}

async function loadDatabase(): Promise<SqlDatabase | null> {
  if (!isTauriRuntime()) {
    return null;
  }

  const sqlModule = (await import('@tauri-apps/plugin-sql')) as unknown as {
    default: { load(connection: string): Promise<SqlDatabase> };
  };

  return sqlModule.default.load('sqlite:liquidity-gate.db');
}

export async function getDatabase(): Promise<SqlDatabase | null> {
  if (!databasePromise) {
    databasePromise = loadDatabase();
  }

  return databasePromise;
}

export async function bootstrapLocalDatabase(): Promise<void> {
  const database = await getDatabase();
  if (!database) {
    return;
  }

  const schemaSql = await loadSchemaSql();

  for (const statement of splitSqlStatements(schemaSql)) {
    await database.execute(statement);
  }
}

export async function seedDemoLiquidityGates(): Promise<void> {
  // Explicit, opt-in demo data action. Not invoked from bootstrap.
  const database = await getDatabase();
  if (!database) return;
  for (const gate of dashboardMock.gates) {
    await database.execute(
      `INSERT OR IGNORE INTO liquidity_gates (gate_key, label, current_amount, target_amount, target_date)
       VALUES ($1, $2, $3, $4, $5)`,
      [gate.gateKey, gate.label, gate.currentAmount, gate.targetAmount, gate.targetDate],
    );
  }
}

interface MonthlyRow {
  month: string;
  inflow: number | null;
  outflow: number | null;
}

interface LeakageRow {
  name: string;
  categoryKey: string;
  monthlyBurn: number | null;
  cap: number | null;
}

interface HysaPointRow {
  date: string;
  balance: number | null;
  accountLabel: string;
}

interface SubscriptionRow {
  merchant: string | null;
  subcategory: string | null;
  householdRole: string | null;
  chargeCount: number;
  monthCount: number;
  totalAmount: number;
  lastChargedOn: string;
}

interface ReconciliationRow {
  accountId: string;
  accountLabel: string;
  accountType: string;
  periodStart: string;
  periodEnd: string;
  statementOpeningBalance: number | null;
  statementClosingBalance: number | null;
  closingBalanceSource: string | null;
  computedClosingBalance: number | null;
  varianceAmount: number | null;
  varianceExplanation: string | null;
}

function monthLabel(month: string): string {
  const [, monthPart] = month.split('-');
  const idx = Math.max(0, Math.min(11, Number(monthPart) - 1));
  return ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'][idx];
}

export async function getDashboardSnapshot(range: DashboardRange = 'ytd'): Promise<DashboardSnapshot> {
  await bootstrapLocalDatabase();

  const database = await getDatabase();
  if (!database) {
    // Web/dev fallback: surface demo data so the dashboard renders during
    // early iteration without a Tauri SQLite backend.
    return { ...dashboardMock };
  }

  // Build the WHERE clause for the monthly query based on the requested range.
  // All values are controlled constants — no user input reaches the SQL string.
  const monthlyWhere =
    range === 'ytd'
      ? "WHERE month >= strftime('%Y', 'now') || '-01'"
      : range === '12mo'
        ? "WHERE month >= strftime('%Y-%m', date('now', '-11 months'))"
        : '';

  const monthlyRows = await database.select<MonthlyRow>(
    `SELECT month, inflow, outflow
       FROM monthly_cashflow_summary
       ${monthlyWhere}
       ORDER BY month ASC`,
  );

  const months: CashFlowMonth[] = monthlyRows.map((row) => ({
    label: monthLabel(row.month),
    month: row.month,
    inflow: row.inflow ?? 0,
    outflow: row.outflow ?? 0,
  }));

  // Keep the HYSA gate current_amount in sync with the most recently
  // reconciled savings-account balance. The UPDATE is a no-op when no
  // reconciliation periods exist yet for savings accounts.
  await database.execute(
    `UPDATE liquidity_gates
        SET current_amount = (
          SELECT COALESCE(rp.statement_closing_balance, rp.computed_closing_balance)
            FROM reconciliation_periods rp
            JOIN accounts a ON a.id = rp.account_id
           WHERE a.account_type = 'savings'
             AND COALESCE(rp.statement_closing_balance, rp.computed_closing_balance) IS NOT NULL
           ORDER BY rp.period_end DESC
           LIMIT 1
        )
      WHERE gate_key = 'hysa-80000'
        AND EXISTS (
          SELECT 1 FROM reconciliation_periods rp
          JOIN accounts a ON a.id = rp.account_id
          WHERE a.account_type = 'savings'
            AND COALESCE(rp.statement_closing_balance, rp.computed_closing_balance) IS NOT NULL
        )`,
  );

  const gates = await database.select<LiquidityGate>(
    `SELECT gate_key as gateKey, label, current_amount as currentAmount, target_amount as targetAmount, target_date as targetDate
       FROM liquidity_gates
       ORDER BY target_date ASC`,
  );

  // Derive leakage burn from transactions joined to leakage_categories.
  // If no leakage categories exist yet, fall back to an empty list (the
  // dashboard renders an explicit empty-state).
  const leakageRows = await database.select<LeakageRow>(
    `SELECT lc.name AS name,
            lc.category_key AS categoryKey,
            COALESCE(lc.monthly_cap, 0) AS cap,
            COALESCE((
              SELECT ROUND(AVG(monthly_total), 2) FROM (
                SELECT substr(t.occurred_on, 1, 7) AS m, SUM(ABS(t.amount)) AS monthly_total
                  FROM transactions t
                 WHERE t.direction = 'outflow'
                   AND t.primary_category = lc.category_key
                 GROUP BY substr(t.occurred_on, 1, 7)
              )
            ), 0) AS monthlyBurn
       FROM leakage_categories lc
       ORDER BY lc.name ASC`,
  );

  const leakageCategories: LeakageCategory[] = leakageRows.map((row) => ({
    name: row.name,
    categoryKey: row.categoryKey,
    monthlyBurn: row.monthlyBurn ?? 0,
    cap: row.cap ?? 0,
  }));

  // Recurring software / streaming subscriptions, aggregated per merchant.
  // The filter intentionally tracks subcategories produced by the classifier
  // rule set (subscriptions_apps + streaming) rather than the broader
  // fixed_obligation bucket — utilities, insurance, and loan payments are
  // reviewed elsewhere. monthly_burn averages the total spend per merchant
  // across the distinct months the charge appears, which mirrors how the
  // analyst sizes "keep / cancel / review" decisions.
  const subscriptionRows = await database.select<SubscriptionRow>(
    `SELECT
         COALESCE(NULLIF(merchant_normalized, ''), description_raw) AS merchant,
         subcategory,
         NULLIF(household_role, '')                                 AS householdRole,
         COUNT(*)                                                   AS chargeCount,
         COUNT(DISTINCT substr(occurred_on, 1, 7))                  AS monthCount,
         ROUND(SUM(ABS(amount)), 2)                                 AS totalAmount,
         MAX(occurred_on)                                           AS lastChargedOn
       FROM transactions
      WHERE direction = 'outflow'
        AND primary_category = 'fixed_obligation'
        AND subcategory IN ('subscriptions_apps', 'streaming')
      GROUP BY merchant, subcategory, householdRole
      ORDER BY (totalAmount / NULLIF(monthCount, 0)) DESC
      LIMIT 24`,
  );

  const subscriptionAudit: SubscriptionAuditEntry[] = subscriptionRows.map((row) => {
    const chargeCount = row.chargeCount ?? 0;
    const monthCount = row.monthCount ?? 0;
    const totalAmount = row.totalAmount ?? 0;
    return {
      merchant: row.merchant ?? '(unknown)',
      subcategory: row.subcategory,
      householdRole: row.householdRole,
      chargeCount,
      monthCount,
      avgAmount: chargeCount > 0 ? totalAmount / chargeCount : 0,
      monthlyBurn: monthCount > 0 ? totalAmount / monthCount : 0,
      lastChargedOn: row.lastChargedOn,
    };
  });

  // All reconciliation periods for every account, sorted so the most recent
  // period per account is encountered first. The UI groups by account_id and
  // exposes prior periods via the per-card history drill-down.
  const reconciliationRows = await database.select<ReconciliationRow>(
    `SELECT rp.account_id        AS accountId,
            (a.institution || ' · ' || a.account_name) AS accountLabel,
            a.account_type        AS accountType,
            rp.period_start       AS periodStart,
            rp.period_end         AS periodEnd,
            rp.statement_opening_balance AS statementOpeningBalance,
            rp.statement_closing_balance AS statementClosingBalance,
            rp.closing_balance_source    AS closingBalanceSource,
            rp.computed_closing_balance  AS computedClosingBalance,
            rp.variance_amount    AS varianceAmount,
            rp.variance_explanation AS varianceExplanation
       FROM reconciliation_periods rp
       JOIN accounts a ON a.id = rp.account_id
      ORDER BY a.institution ASC, a.account_name ASC, rp.period_end DESC`,
  );

  // HYSA trajectory: one (date, balance) point per reconciled period for any
  // savings account. The Ally HYSA CSV has no running-balance column, so we
  // rely on reconcile_periods.computed_closing_balance (falling back to the
  // statement balance when present). Points are sorted oldest→newest so the
  // projection helper can fit a straight line.
  const hysaPointRows = await database.select<HysaPointRow>(
    `SELECT rp.period_end AS date,
            COALESCE(rp.statement_closing_balance, rp.computed_closing_balance) AS balance,
            (a.institution || ' · ' || a.account_name)                          AS accountLabel
       FROM reconciliation_periods rp
       JOIN accounts a ON a.id = rp.account_id
      WHERE a.account_type = 'savings'
      ORDER BY rp.period_end ASC`,
  );

  const hysaPoints: HysaTrajectoryPoint[] = hysaPointRows
    .filter((row) => row.balance !== null)
    .map((row) => ({ date: row.date, balance: row.balance ?? 0 }));
  const hysaTrajectory: HysaTrajectory = {
    points: hysaPoints,
    targetAmount: HYSA_GATE_TARGET,
    projection: projectHysaGate(hysaPoints, HYSA_GATE_TARGET),
    accountLabel: hysaPointRows[0]?.accountLabel ?? null,
  };

  const reconciliations: ReconciliationPeriod[] = reconciliationRows.map((row) => ({
    accountId: row.accountId,
    accountLabel: row.accountLabel,
    accountType: row.accountType,
    periodStart: row.periodStart,
    periodEnd: row.periodEnd,
    statementOpeningBalance: row.statementOpeningBalance,
    statementClosingBalance: row.statementClosingBalance,
    closingBalanceSource: row.closingBalanceSource,
    computedClosingBalance: row.computedClosingBalance,
    varianceAmount: row.varianceAmount,
    varianceExplanation: row.varianceExplanation ?? '',
  }));

  // If there is no real data at all, return the explicit demo fallback so the
  // empty dashboard still communicates expected composition. Once any real
  // data lands (months/gates/leakage/reconciliations/subscriptions/HYSA), use
  // only what's in the DB.
  const hasRealData =
    months.length > 0 ||
    gates.length > 0 ||
    leakageCategories.length > 0 ||
    reconciliations.length > 0 ||
    subscriptionAudit.length > 0 ||
    hysaTrajectory.points.length > 0;
  if (!hasRealData) {
    return { ...dashboardMock };
  }

  return {
    months,
    gates,
    leakageCategories,
    reconciliations,
    subscriptionAudit,
    hysaTrajectory,
  };
}

export const TRANSACTION_PAGE_SIZE = 50;

interface TxRow {
  id: string;
  occurred_on: string;
  account_id: string;
  account_label: string;
  description_raw: string;
  merchant_normalized: string | null;
  primary_category: string;
  subcategory: string | null;
  household_role: string;
  lifecycle: string;
  amount: number;
  direction: string;
}

export async function getTransactionPage(filter: {
  page: number;
  direction: TransactionDirectionFilter;
}): Promise<TransactionPage> {
  await bootstrapLocalDatabase();
  const database = await getDatabase();
  if (!database) {
    return { rows: [], total: 0, page: filter.page, pageSize: TRANSACTION_PAGE_SIZE };
  }

  const offset = filter.page * TRANSACTION_PAGE_SIZE;

  // whereClause is built from a controlled union type — no user-supplied string
  // ever reaches the query, so conditional string construction is safe here.
  const whereClause =
    filter.direction === 'inflow'
      ? "t.direction = 'inflow'"
      : filter.direction === 'outflow'
        ? "t.direction = 'outflow'"
        : "t.direction != 'transfer'";

  const countRows = await database.select<{ total: number }>(
    `SELECT COUNT(*) AS total
       FROM transactions t
       JOIN accounts a ON a.id = t.account_id
      WHERE ${whereClause}`,
  );
  const total = countRows[0]?.total ?? 0;

  const rows = await database.select<TxRow>(
    `SELECT
         t.id,
         t.occurred_on,
         t.account_id,
         (a.institution || ' · ' || a.account_name) AS account_label,
         t.description_raw,
         t.merchant_normalized,
         t.primary_category,
         t.subcategory,
         t.household_role,
         t.lifecycle,
         t.amount,
         t.direction
       FROM transactions t
       JOIN accounts a ON a.id = t.account_id
      WHERE ${whereClause}
      ORDER BY t.occurred_on DESC, t.id DESC
      LIMIT $1 OFFSET $2`,
    [TRANSACTION_PAGE_SIZE, offset],
  );

  const mapped: Transaction[] = rows.map((r) => ({
    id: r.id,
    occurredOn: r.occurred_on,
    accountId: r.account_id,
    accountLabel: r.account_label,
    descriptionRaw: r.description_raw,
    merchantNormalized: r.merchant_normalized,
    primaryCategory: r.primary_category,
    subcategory: r.subcategory,
    householdRole: r.household_role,
    lifecycle: r.lifecycle,
    amount: r.amount,
    direction: r.direction as Transaction['direction'],
  }));

  return { rows: mapped, total, page: filter.page, pageSize: TRANSACTION_PAGE_SIZE };
}

// ── Transaction override ─────────────────────────────────────────────────────

export interface UpsertTransactionOverrideParams {
  /** `transactions.id` of the row being overridden. */
  transactionId: string;
  /** Must be present on the `Transaction` object returned by getTransactionPage. */
  accountId: string;
  occurredOn: string;
  amount: number;
  descriptionRaw: string;
  merchantNormalized?: string | null;
  primaryCategory?: string | null;
  subcategory?: string | null;
  householdRole?: string | null;
  lifecycle?: string | null;
  note?: string;
}

/**
 * Normalise a raw description the same way the Python backend does before
 * hashing: collapse all whitespace to single spaces and lower-case.
 */
function normalizeOverrideDescription(raw: string): string {
  return raw.replace(/\s+/g, ' ').trim().toLowerCase();
}

/**
 * Compute the 24-hex-char match key used by `transaction_overrides`.
 * Algorithm mirrors Python: SHA-256 of "{account_id}|{occurred_on}|{amount_2dp}|{normalized_desc}",
 * truncated to the first 24 hex characters.
 */
async function computeOverrideMatchKey(
  accountId: string,
  occurredOn: string,
  amount: number,
  descriptionRaw: string,
): Promise<string> {
  const amountStr = amount.toFixed(2);
  const normalized = normalizeOverrideDescription(descriptionRaw);
  const payload = `${accountId}|${occurredOn}|${amountStr}|${normalized}`;

  const encoded = new TextEncoder().encode(payload);
  const hashBuffer = await crypto.subtle.digest('SHA-256', encoded);
  const hex = Array.from(new Uint8Array(hashBuffer))
    .map((b) => b.toString(16).padStart(2, '0'))
    .join('');
  return hex.slice(0, 24);
}

function newUuid(): string {
  // crypto.randomUUID is available in secure contexts (Tauri webview qualifies).
  return crypto.randomUUID();
}

export async function upsertTransactionOverride(
  params: UpsertTransactionOverrideParams,
): Promise<void> {
  const database = await getDatabase();
  if (!database) return;

  const matchKey = await computeOverrideMatchKey(
    params.accountId,
    params.occurredOn,
    params.amount,
    params.descriptionRaw,
  );

  // Persist the override row.
  await database.execute(
    `INSERT INTO transaction_overrides (
       id, match_key, account_id, occurred_on, amount, description_raw,
       merchant_normalized, primary_category, subcategory,
       household_role, lifecycle, note
     ) VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12)
     ON CONFLICT(match_key) DO UPDATE SET
       merchant_normalized = COALESCE($7, transaction_overrides.merchant_normalized),
       primary_category    = COALESCE($8, transaction_overrides.primary_category),
       subcategory         = COALESCE($9, transaction_overrides.subcategory),
       household_role      = COALESCE($10, transaction_overrides.household_role),
       lifecycle           = COALESCE($11, transaction_overrides.lifecycle),
       note                = CASE
                               WHEN $12 <> '' THEN $12
                               ELSE transaction_overrides.note
                             END,
       updated_at          = CURRENT_TIMESTAMP`,
    [
      newUuid(),
      matchKey,
      params.accountId,
      params.occurredOn,
      params.amount,
      params.descriptionRaw,
      params.merchantNormalized ?? null,
      params.primaryCategory ?? null,
      params.subcategory ?? null,
      params.householdRole ?? null,
      params.lifecycle ?? null,
      params.note ?? '',
    ],
  );

  // Apply the stored override back to any matching transactions.
  // Only non-null fields from the override replace transaction values.
  await database.execute(
    `UPDATE transactions
        SET merchant_normalized = COALESCE(
              (SELECT merchant_normalized FROM transaction_overrides WHERE match_key = $1),
              merchant_normalized),
            primary_category    = COALESCE(
              (SELECT primary_category FROM transaction_overrides WHERE match_key = $1),
              primary_category),
            subcategory         = COALESCE(
              (SELECT subcategory FROM transaction_overrides WHERE match_key = $1),
              subcategory),
            household_role      = COALESCE(
              (SELECT household_role FROM transaction_overrides WHERE match_key = $1),
              household_role),
            lifecycle           = COALESCE(
              (SELECT lifecycle FROM transaction_overrides WHERE match_key = $1),
              lifecycle)
      WHERE id = $2`,
    [matchKey, params.transactionId],
  );
}

// ── Reconciliation variance explanation ─────────────────────────────────────

export interface UpdateVarianceExplanationParams {
  accountId: string;
  periodStart: string;
  periodEnd: string;
  explanation: string;
}

/**
 * Persist a human-authored variance explanation for a reconciliation period.
 * The Python `reconcile_periods` tool preserves this text across reruns, so
 * the note survives future recomputation as long as the (account_id,
 * period_start, period_end) triple stays stable.
 */
export async function updateVarianceExplanation(
  params: UpdateVarianceExplanationParams,
): Promise<void> {
  const database = await getDatabase();
  if (!database) return;

  await database.execute(
    `UPDATE reconciliation_periods
        SET variance_explanation = $1
      WHERE account_id = $2
        AND period_start = $3
        AND period_end = $4`,
    [params.explanation, params.accountId, params.periodStart, params.periodEnd],
  );
}