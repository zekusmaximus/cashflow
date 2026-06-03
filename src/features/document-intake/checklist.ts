import { parseCsv } from '../../lib/csv';
import { isTauriRuntime } from '../../lib/tauri';
import { MATCH_THRESHOLD, scoreCandidate, type CandidateFile, type ScoringRow } from './matcher';
import type {
  ChecklistDataset,
  ChecklistItem,
  ChecklistScope,
  ChecklistStatus,
  MatchedFile,
  WatchRootStatus,
} from './types';

export function parseObtainedFlag(value: string): boolean {
  const normalized = value.trim().toLowerCase();
  return normalized === 'yes' || normalized === 'true' || normalized.includes('✓') || normalized.includes('☑');
}

/**
 * Derives a stable category id from a category label. Tracker categories carry
 * a single-letter prefix ('A. Core Transactions') used as the filter key; rows
 * without a recognizable prefix fall back to the full label so ids stay unique.
 */
export function deriveCategoryId(category: string): string {
  const match = category.match(/^([A-Za-z])\.\s/);
  return match ? match[1].toUpperCase() : category.trim();
}

/**
 * Applies the 2026-06-03 coverage-review dispositions to a checklist row.
 *
 * The intake checklist began as a "gather everything" template. A live
 * classification-coverage session confirmed that most listed sources are either
 * already covered by the transaction feed at high confidence (Amazon, Instacart,
 * subscriptions, utilities, Venmo overrides) or are financial-planning reference
 * documents that Liquidity Gate — which ingests bank/credit-card CSV feeds only —
 * can never ingest (insurance policies, tax returns, leases). This function maps
 * each row to its reviewed {@link ChecklistStatus} and {@link ChecklistScope}.
 *
 * Matching is intentionally fuzzy (category id + key nouns in the document label)
 * so wording tweaks in the tracker CSV don't silently drop a disposition. Order
 * matters: the more specific rules run before the category-wide fallbacks.
 */
export function classifyDisposition(
  categoryId: string,
  document: string,
  obtained: boolean,
): { status: ChecklistStatus; scope: ChecklistScope } {
  const doc = document.toLowerCase();
  const hasAny = (...keys: string[]) => keys.some((key) => doc.includes(key));

  const enrichment: ChecklistScope = 'transaction_enrichment';
  const planningRef: ChecklistScope = 'financial_planning_ref';

  // Resolved via transaction overrides: Venmo pet rows reclassified 2026-06-03.
  if (hasAny('dog grooming', 'daycare', 'boarding')) {
    return { status: 'resolved', scope: enrichment };
  }

  // Deferred planning references — not transaction sources, never ingested.
  // Entire Insurance & Protection (E) and Tax Documents (I) categories.
  if (categoryId === 'E' || categoryId === 'I') {
    return { status: 'deferred_planning', scope: planningRef };
  }
  // Rental Property (F) — everything except the actual rent payment history.
  if (categoryId === 'F' && !doc.includes('rent payment history')) {
    return { status: 'deferred_planning', scope: planningRef };
  }
  // Household service contracts (HVAC / pest / appliance) are reference only.
  if (categoryId === 'D' && doc.includes('household service contract')) {
    return { status: 'deferred_planning', scope: planningRef };
  }
  // Fidelity HSA *statements* are a planning ref; the HSA debit-card CSV is not.
  if (categoryId === 'G' && doc.includes('hsa statement')) {
    return { status: 'deferred_planning', scope: planningRef };
  }
  // YTD medical deductible / OOP-max tracker is a planning worksheet.
  if (categoryId === 'G' && hasAny('deductible', 'oop-max', 'oop max')) {
    return { status: 'deferred_planning', scope: planningRef };
  }

  // Not needed — the transaction feed already covers these at high confidence.
  // P2P apps: Venmo rows in Beacon were manually overridden (household_role/pet).
  if (hasAny('venmo', 'paypal', 'zelle', 'cash app')) {
    return { status: 'not_needed', scope: enrichment };
  }
  // Amazon / Instacart line-item exports — base rows already classified.
  if (doc.includes('amazon order history') || hasAny('instacart', 'grocery delivery')) {
    return { status: 'not_needed', scope: enrichment };
  }
  // Every Subscriptions & Apps (H) item — the charges land on Chase/Citi already.
  if (categoryId === 'H') {
    return { status: 'not_needed', scope: enrichment };
  }
  // Utility / service bills — vendors are identifiable from transaction descriptions.
  if (
    categoryId === 'D' &&
    hasAny('electric', 'gas', 'water', 'internet', 'cell phone', 'security system')
  ) {
    return { status: 'not_needed', scope: enrichment };
  }

  // Default: still actionable transaction enrichment. Obtained rows are resolved.
  return { status: obtained ? 'resolved' : 'open', scope: enrichment };
}

export class TrackerCsvUnavailableError extends Error {
  constructor(message: string) {
    super(message);
    this.name = 'TrackerCsvUnavailableError';
  }
}

interface WatchRootSnapshot {
  root: string;
  exists: boolean;
  files: CandidateFile[];
}

async function loadTrackerCsv(): Promise<string> {
  // Vite bundles the CSV as a same-origin static asset, which works in both
  // the web dev server and the Tauri webview. Avoids the Tauri fs plugin
  // (no extra capability needed) and Tauri v2's `_up_` resource-path escaping.
  const url = (await import('../../../docs/Spreadsheet_checklist_for_document_tracking.csv?url')).default;
  const response = await fetch(url);
  if (!response.ok) {
    throw new TrackerCsvUnavailableError(
      `Unable to fetch tracker CSV from ${url} (status ${response.status}).`,
    );
  }
  return response.text();
}

async function loadWatchRootSnapshot(): Promise<WatchRootSnapshot> {
  if (!isTauriRuntime()) {
    return { root: '', exists: false, files: [] };
  }
  const { invoke } = await import('@tauri-apps/api/core');
  try {
    return await invoke<WatchRootSnapshot>('list_watch_root_files');
  } catch {
    return { root: '', exists: false, files: [] };
  }
}

export async function loadChecklistDataset(): Promise<ChecklistDataset> {
  const [csv, snapshot] = await Promise.all([loadTrackerCsv(), loadWatchRootSnapshot()]);
  const rows = parseCsv(csv);

  const scoringRows: ScoringRow[] = rows.map((row) => ({
    document: row['Document'] ?? '',
    category: row['Category'] ?? '',
    subjectMatter: row['Subject Matter'] ?? '',
    whyNeeded: row['Why Needed'] ?? '',
    format: row['Format'] ?? '',
  }));

  // Assign each file to its single best-scoring tracker row above threshold.
  // Without this, a single CSV can list under several rows whose category text
  // also overlaps it (e.g. Chase_Credit_Card.csv hitting both doc-001 Chase and
  // doc-005 Other credit cards). Per-file winner-takes-all eliminates the dup.
  const matchedByRow: MatchedFile[][] = scoringRows.map(() => []);
  for (const file of snapshot.files) {
    let bestRowIndex = -1;
    let bestScore = -1;
    for (let i = 0; i < scoringRows.length; i += 1) {
      const score = scoreCandidate(scoringRows[i], file);
      if (score > bestScore) {
        bestScore = score;
        bestRowIndex = i;
      }
    }
    if (bestRowIndex >= 0 && bestScore >= MATCH_THRESHOLD) {
      matchedByRow[bestRowIndex].push({
        relativePath: file.relativePath,
        filename: file.filename,
        score: bestScore,
        modifiedMs: file.modifiedMs ?? null,
      });
    }
  }
  for (const matches of matchedByRow) {
    matches.sort((a, b) => b.score - a.score);
  }

  const items: ChecklistItem[] = rows.map((row, index) => {
    const scoringRow = scoringRows[index];
    const matchedFiles = matchedByRow[index];
    const trackerObtained = parseObtainedFlag(row['Obtained ✓'] ?? '');
    const filesystemObtained = matchedFiles.length > 0;
    const obtained = trackerObtained || filesystemObtained;
    const obtainedSource: ChecklistItem['obtainedSource'] = trackerObtained
      ? 'tracker'
      : filesystemObtained
        ? 'filesystem'
        : 'none';
    const lastModifiedMs = matchedFiles.reduce<number | null>((latest, file) => {
      if (file.modifiedMs == null) return latest;
      return latest == null || file.modifiedMs > latest ? file.modifiedMs : latest;
    }, null);
    const categoryId = deriveCategoryId(scoringRow.category);
    const { status, scope } = classifyDisposition(categoryId, scoringRow.document, obtained);
    return {
      id: `doc-${index + 1}`,
      category: scoringRow.category,
      categoryId,
      document: scoringRow.document,
      subjectMatter: scoringRow.subjectMatter,
      format: scoringRow.format,
      priority: row['Priority'] ?? '',
      source: row['Source / Where to Get'] ?? '',
      whyNeeded: scoringRow.whyNeeded,
      obtained,
      obtainedSource,
      status,
      scope,
      matchedFiles,
      lastModifiedMs,
      dateAdded: row['Date Added'] ?? '',
      notes: row['Notes'] ?? '',
    };
  });

  const categoryMap = new Map<string, { total: number; obtained: number }>();
  for (const item of items) {
    const current = categoryMap.get(item.category) ?? { total: 0, obtained: 0 };
    current.total += 1;
    if (item.obtained) {
      current.obtained += 1;
    }
    categoryMap.set(item.category, current);
  }

  const watchRoot: WatchRootStatus = {
    root: snapshot.root,
    exists: snapshot.exists,
    fileCount: snapshot.files.length,
  };

  return {
    items,
    watchRoot,
    summary: {
      totalItems: items.length,
      obtainedCount: items.filter((item) => item.obtained).length,
      missingCount: items.filter((item) => !item.obtained).length,
      categories: [...categoryMap.entries()].map(([category, counts]) => ({
        category,
        categoryId: deriveCategoryId(category),
        total: counts.total,
        obtained: counts.obtained,
        missing: counts.total - counts.obtained,
      })),
    },
  };
}
