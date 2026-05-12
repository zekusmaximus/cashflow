export interface MatchedFile {
  relativePath: string;
  filename: string;
  score: number;
}

export interface ChecklistItem {
  id: string;
  category: string;
  document: string;
  subjectMatter: string;
  format: string;
  priority: string;
  source: string;
  whyNeeded: string;
  obtained: boolean;
  obtainedSource: 'tracker' | 'filesystem' | 'none';
  matchedFiles: MatchedFile[];
  dateAdded: string;
  notes: string;
}

export interface ChecklistCategorySummary {
  category: string;
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
