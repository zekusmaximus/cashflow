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
  dateAdded: string;
  notes: string;
}

export interface ChecklistCategorySummary {
  category: string;
  total: number;
  obtained: number;
  missing: number;
}

export interface ChecklistDataset {
  items: ChecklistItem[];
  summary: {
    totalItems: number;
    obtainedCount: number;
    missingCount: number;
    categories: ChecklistCategorySummary[];
  };
}
