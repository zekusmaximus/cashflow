import { useQuery } from '@tanstack/react-query';
import { bootstrapLocalDatabase, getDatabase } from '../services/sqlite';

interface CountRow {
  source_document_name: string;
  n: number;
}

async function loadTransactionCounts(): Promise<Map<string, number>> {
  // Web/dev fallback: no Tauri SQLite backend, so no counts to show. The
  // intake view treats an empty map as "nothing ingested yet" and hides
  // the indicator without erroring out.
  await bootstrapLocalDatabase();
  const database = await getDatabase();
  if (!database) {
    return new Map();
  }

  const rows = await database.select<CountRow>(
    `SELECT source_document_name, COUNT(*) AS n
       FROM transactions
       GROUP BY source_document_name`,
  );

  return new Map(rows.map((row) => [row.source_document_name, row.n]));
}

export function useTransactionCounts() {
  return useQuery({
    queryKey: ['transaction-counts'],
    queryFn: loadTransactionCounts,
    // Match the checklist hook's cadence so newly ingested files surface
    // alongside the matched-filename row without a manual refresh.
    refetchInterval: 5000,
    refetchOnWindowFocus: true,
  });
}
