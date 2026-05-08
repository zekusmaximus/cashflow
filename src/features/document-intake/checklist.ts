import { parseCsv } from '../../lib/csv';
import { isTauriRuntime } from '../../lib/tauri';
import type { ChecklistDataset, ChecklistItem } from './types';

const trackerRelativePath = 'docs/Spreadsheet_checklist_for_document_tracking.csv';
const tauriPathModuleName: string = '@tauri-apps/api/path';
const tauriFsModuleName: string = '@tauri-apps/plugin-fs';

export function parseObtainedFlag(value: string): boolean {
  const normalized = value.trim().toLowerCase();
  return normalized === 'yes' || normalized === 'true' || normalized.includes('✓') || normalized.includes('☑');
}

export class TrackerCsvUnavailableError extends Error {
  constructor(message: string) {
    super(message);
    this.name = 'TrackerCsvUnavailableError';
  }
}

async function loadTrackerCsv(): Promise<string> {
  if (isTauriRuntime()) {
    try {
      const pathModule = (await import(/* @vite-ignore */ tauriPathModuleName)) as {
        resourceDir: () => Promise<string>;
        join: (...parts: string[]) => Promise<string>;
      };
      const fsModule = (await import(/* @vite-ignore */ tauriFsModuleName)) as {
        readTextFile: (path: string) => Promise<string>;
      };
      const base = await pathModule.resourceDir();
      const fullPath = await pathModule.join(base, trackerRelativePath);
      return await fsModule.readTextFile(fullPath);
    } catch (error) {
      throw new TrackerCsvUnavailableError(
        `Unable to read tracker CSV at ${trackerRelativePath}: ${(error as Error).message}`,
      );
    }
  }

  // Web/dev fallback: fetch the CSV bundled by Vite as a static asset so
  // npm run dev / npm run build still work without the Tauri runtime.
  const url = (await import('../../../docs/Spreadsheet_checklist_for_document_tracking.csv?url')).default;
  const response = await fetch(url);
  if (!response.ok) {
    throw new TrackerCsvUnavailableError(
      `Unable to fetch tracker CSV from ${url} (status ${response.status}).`,
    );
  }
  return response.text();
}

export async function loadChecklistDataset(): Promise<ChecklistDataset> {
  const csv = await loadTrackerCsv();
  const rows = parseCsv(csv);

  const items: ChecklistItem[] = rows.map((row, index) => ({
    id: `doc-${index + 1}`,
    category: row['Category'] ?? '',
    document: row['Document'] ?? '',
    subjectMatter: row['Subject Matter'] ?? '',
    format: row['Format'] ?? '',
    priority: row['Priority'] ?? '',
    source: row['Source / Where to Get'] ?? '',
    whyNeeded: row['Why Needed'] ?? '',
    obtained: parseObtainedFlag(row['Obtained ✓'] ?? ''),
    dateAdded: row['Date Added'] ?? '',
    notes: row['Notes'] ?? '',
  }));

  const categoryMap = new Map<string, { total: number; obtained: number }>();

  for (const item of items) {
    const current = categoryMap.get(item.category) ?? { total: 0, obtained: 0 };
    current.total += 1;
    if (item.obtained) {
      current.obtained += 1;
    }
    categoryMap.set(item.category, current);
  }

  return {
    items,
    summary: {
      totalItems: items.length,
      obtainedCount: items.filter((item) => item.obtained).length,
      missingCount: items.filter((item) => !item.obtained).length,
      categories: [...categoryMap.entries()].map(([category, counts]) => ({
        category,
        total: counts.total,
        obtained: counts.obtained,
        missing: counts.total - counts.obtained,
      })),
    },
  };
}
