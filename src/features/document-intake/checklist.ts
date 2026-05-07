import checklistCsv from '../../../docs/Spreadsheet_checklist_for_document_tracking.csv?raw';
import { parseCsv } from '../../lib/csv';
import type { ChecklistDataset, ChecklistItem } from './types';

function parseObtainedFlag(value: string): boolean {
  const normalized = value.trim().toLowerCase();
  return normalized === 'yes' || normalized === 'true' || normalized.includes('✓') || normalized.includes('☑');
}

export async function loadChecklistDataset(): Promise<ChecklistDataset> {
  const rows = parseCsv(checklistCsv);

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
