export function isTauriRuntime(): boolean {
  return typeof window !== 'undefined' && typeof window.__TAURI_INTERNALS__ !== 'undefined';
}

export async function revealInFileManager(path: string): Promise<void> {
  if (!isTauriRuntime()) return;
  const { invoke } = await import('@tauri-apps/api/core');
  await invoke('reveal_in_file_manager', { path });
}

/**
 * A classification rule captured from the UI. Field names are snake_case to
 * match the Python `UpsertClassificationRuleRequest` — the Rust command passes
 * this object straight through to the classifier sidecar as `--rule <json>`.
 */
export interface ClassificationRuleInput {
  id?: string | null;
  pattern: string;
  account_filter?: string | null;
  direction_filter?: string | null;
  primary_category?: string | null;
  subcategory?: string | null;
  merchant_normalized?: string | null;
  household_role?: string | null;
  lifecycle?: string | null;
  confidence?: string;
  priority?: number;
  notes?: string;
}

/** Shape of the single JSON object the classifier CLI prints on stdout. */
export interface ApplyClassificationRuleResult {
  ok: boolean;
  rule_id?: string | null;
  scanned?: number;
  matched?: number;
  unclassified?: number;
  dry_run?: boolean;
  error?: string;
}

/**
 * Create/run a classification rule through the single Python engine via the
 * Tauri sidecar. Existing matching rows re-bucket now (reclassify_all) while
 * manual overrides stay protected. Rejects outside the desktop runtime — the
 * classifier is unreachable from a plain browser context.
 */
export async function applyClassificationRule(
  rule: ClassificationRuleInput,
  reclassifyAll = true,
): Promise<ApplyClassificationRuleResult> {
  if (!isTauriRuntime()) {
    throw new Error('Applying classification rules requires the desktop app.');
  }
  const { invoke } = await import('@tauri-apps/api/core');
  return invoke<ApplyClassificationRuleResult>('apply_classification_rule', {
    args: { rule, reclassifyAll },
  });
}
