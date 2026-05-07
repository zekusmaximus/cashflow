import type { DashboardSnapshot, LiquidityGate } from '../features/dashboard/types';
import { dashboardMock } from '../features/dashboard/mock';
import { isTauriRuntime } from '../lib/tauri';

interface SqlDatabase {
  execute(query: string, bindValues?: unknown[]): Promise<unknown>;
  select<T>(query: string, bindValues?: unknown[]): Promise<T[]>;
}

let databasePromise: Promise<SqlDatabase | null> | null = null;
let schemaPromise: Promise<string> | null = null;

const seedGates: LiquidityGate[] = dashboardMock.gates;
const schemaAssetUrl = new URL('../../server/sql/schema.sql', import.meta.url).href;
const tauriSqlModuleName: string = '@tauri-apps/plugin-sql';

function splitSqlStatements(sql: string): string[] {
  return sql
    .split(';')
    .map((statement) => statement.trim())
    .filter((statement) => statement.length > 0 && !statement.startsWith('--'));
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

  const sqlModule = (await import(/* @vite-ignore */ tauriSqlModuleName)) as {
    default: { load(connection: string): Promise<SqlDatabase> };
  };

  return (sqlModule.default as { load: (connection: string) => Promise<SqlDatabase> }).load(
    'sqlite:liquidity-gate.db',
  );
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

  for (const gate of seedGates) {
    await database.execute(
      `INSERT OR IGNORE INTO liquidity_gates (gate_key, label, current_amount, target_amount, target_date)
       VALUES ($1, $2, $3, $4, $5)`,
      [gate.gateKey, gate.label, gate.currentAmount, gate.targetAmount, gate.targetDate],
    );
  }
}

export async function getDashboardSnapshot(): Promise<DashboardSnapshot> {
  await bootstrapLocalDatabase();

  const database = await getDatabase();
  if (!database) {
    return dashboardMock;
  }

  const gates = await database.select<LiquidityGate>(
    `SELECT gate_key as gateKey, label, current_amount as currentAmount, target_amount as targetAmount, target_date as targetDate
     FROM liquidity_gates
     ORDER BY target_date ASC`,
  );

  return {
    ...dashboardMock,
    gates: gates.length > 0 ? gates : dashboardMock.gates,
  };
}