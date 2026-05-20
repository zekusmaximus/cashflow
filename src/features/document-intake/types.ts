export interface MatchedFile {
  relativePath: string;
  filename: string;
  score: number;
  /** Epoch milliseconds of the file's last modification; null when unavailable. */
  modifiedMs: number | null;
}

export interface ChecklistItem {
  id: string;
  category: string;
  /** Stable filter key derived from the category prefix (e.g. 'A' from 'A. Core'). */
  categoryId: string;
  document: string;
  subjectMatter: string;
  format: string;
  priority: string;
  source: string;
  whyNeeded: string;
  obtained: boolean;
  obtainedSource: 'tracker' | 'filesystem' | 'none';
  matchedFiles: MatchedFile[];
  /** Most recent mtime across matched files, in epoch ms; null when no match. */
  lastModifiedMs: number | null;
  dateAdded: string;
  notes: string;
}

export interface ChecklistCategorySummary {
  category: string;
  /** Stable filter key, mirrors ChecklistItem.categoryId. */
  categoryId: string;
  total: number;
  obtained: number;
  missing: number;
}

export interface WatchRootStatus {
  root: string;
  exists: boolean;
  fileCount: number;
}

export interface ChecklistDataset {
  items: ChecklistItem[];
  watchRoot: WatchRootStatus;
  summary: {
    totalItems: number;
    obtainedCount: number;
    missingCount: number;
    categories: ChecklistCategorySummary[];
  };
}
