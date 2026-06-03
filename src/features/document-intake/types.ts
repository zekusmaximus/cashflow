export interface MatchedFile {
  relativePath: string;
  filename: string;
  score: number;
  /** Epoch milliseconds of the file's last modification; null when unavailable. */
  modifiedMs: number | null;
}

/**
 * Disposition of a checklist item after the 2026-06-03 coverage review.
 *  - 'open'              still actionable; a transaction source worth ingesting.
 *  - 'resolved'          handled (file obtained or fixed via transaction overrides).
 *  - 'not_needed'        evaluated and unnecessary — the transaction feed already
 *                        covers it at high confidence, so no enrichment is required.
 *  - 'deferred_planning' a financial-planning reference document, not a transaction
 *                        source; out of scope for Liquidity Gate ingestion.
 */
export type ChecklistStatus = 'open' | 'resolved' | 'not_needed' | 'deferred_planning';

/**
 * Coarse scope used to separate sources that feed the database from reference
 * documents that only inform planning discussions.
 *  - 'transaction_enrichment'  adds rows or context to the transaction DB.
 *  - 'financial_planning_ref'  a planning reference document, never ingested.
 */
export type ChecklistScope = 'transaction_enrichment' | 'financial_planning_ref';

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
  /** Disposition after the coverage review; drives status badge + scope grouping. */
  status: ChecklistStatus;
  /** Whether this item enriches the transaction DB or is a planning reference. */
  scope: ChecklistScope;
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

export interface ChecklistFilter {
  status: 'all' | 'open';
  priority: 'all' | 'essential';
  category: string | 'all';
}

export const DEFAULT_CHECKLIST_FILTER: ChecklistFilter = {
  status: 'all',
  priority: 'all',
  category: 'all',
};
