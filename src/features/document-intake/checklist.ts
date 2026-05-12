import { parseCsv } from '../../lib/csv';
import { isTauriRuntime } from '../../lib/tauri';
import { MATCH_THRESHOLD, scoreCandidate, type CandidateFile, type ScoringRow } from './matcher';
import type { ChecklistDataset, ChecklistItem, MatchedFile, WatchRootStatus } from './types';

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
    return {
      id: `doc-${index + 1}`,
      category: scoringRow.category,
      document: scoringRow.document,
      subjectMatter: scoringRow.subjectMatter,
      format: scoringRow.format,
      priority: row['Priority'] ?? '',
      source: row['Source / Where to Get'] ?? '',
      whyNeeded: scoringRow.whyNeeded,
      obtained,
      obtainedSource,
      matchedFiles,
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
        total: counts.total,
        obtained: counts.obtained,
        missing: counts.total - counts.obtained,
      })),
    },
  };
}
