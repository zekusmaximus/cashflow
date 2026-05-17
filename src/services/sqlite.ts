import type {
  CashFlowMonth,
  DashboardSnapshot,
  LeakageCategory,
  LiquidityGate,
  ReconciliationPeriod,
} from '../features/dashboard/types';
import { dashboardMock } from '../features/dashboard/mock';
import { isTauriRuntime } from '../lib/tauri';

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
  monthlyBurn: number | null;
  cap: number | null;
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

export async function getDashboardSnapshot(): Promise<DashboardSnapshot> {
  await bootstrapLocalDatabase();

  const database = await getDatabase();
  if (!database) {
    // Web/dev fallback: surface demo data so the dashboard renders during
    // early iteration without a Tauri SQLite backend.
    return { ...dashboardMock };
  }

  const monthlyRows = await database.select<MonthlyRow>(
    `SELECT month, inflow, outflow
       FROM monthly_cashflow_summary
       ORDER BY month ASC
       LIMIT 12`,
  );

  const months: CashFlowMonth[] = monthlyRows.map((row) => ({
    label: monthLabel(row.month),
    inflow: row.inflow ?? 0,
    outflow: row.outflow ?? 0,
  }));

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
    monthlyBurn: row.monthlyBurn ?? 0,
    cap: row.cap ?? 0,
  }));

  // Latest reconciliation row per account. The subquery picks the max
  // period_end per account_id so the dashboard always shows the freshest
  // variance even when older months have also been reconciled.
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
       JOIN (
         SELECT account_id, MAX(period_end) AS latest_end
           FROM reconciliation_periods
          GROUP BY account_id
       ) latest
         ON latest.account_id = rp.account_id
        AND latest.latest_end = rp.period_end
      ORDER BY a.institution ASC, a.account_name ASC`,
  );

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
  // data lands (months/gates/leakage/reconciliations), use only what's in the DB.
  const hasRealData =
    months.length > 0 ||
    gates.length > 0 ||
    leakageCategories.length > 0 ||
    reconciliations.length > 0;
  if (!hasRealData) {
    return { ...dashboardMock };
  }

  return {
    months,
    gates,
    leakageCategories,
    reconciliations,
  };
}