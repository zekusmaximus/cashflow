import { useEffect, useMemo, useState } from 'react';
import { useMutation, useQueryClient } from '@tanstack/react-query';
import { cn, currency } from '../../lib/utils';
import { useTransactionRegister } from '../../hooks/use-transaction-register';
import { useAccountOptions } from '../../hooks/use-account-options';
import {
  upsertTransactionOverride,
  type UpsertTransactionOverrideParams,
} from '../../services/sqlite';
import type {
  Transaction,
  TransactionDirectionFilter,
  TransactionPage,
} from '../dashboard/types';

// ── Classification taxonomy ──────────────────────────────────────────────────
// These are the canonical values recognised by the classifier rule engine and
// downstream SQL queries. Keeping them co-located with TransactionRow makes
// it obvious which values the selects surface.
//
// `unclassified` is intentionally absent: it is a *state*, not a save target.
// Rows in that state still render the `unclassified` badge, but the editor
// never offers it as a destination category.

const PRIMARY_CATEGORIES = [
  'income',
  'fixed_obligation',
  'variable_lifestyle',
  'transfer',
  'tax',
  'rental',
  'medical',
  'investment',
  'abnormal',
  'business_expense',
] as const;

// Subcategories are grouped by their parent primary category. The select
// resets its value whenever the user switches the primary category to a value
// for which the current subcategory is not valid.
// `dining` retired 2026-05-27 in favor of `dining_out` — see docs/DECISION_LOG.md.
// `landscaping`, `home_maintenance` added under fixed_obligation; `home_improvement`
// added under abnormal; `business_income` added under income — see the same entry.
// `rental_mortgage` retired 2026-05-27 in favor of `mortgage` under primary_category=rental —
// the rental context is carried by primary_category + household_role, so the subcategory
// describes the payment kind, not whose property. See docs/DECISION_LOG.md.
const SUBCATEGORY_BY_PRIMARY: Record<string, readonly string[]> = {
  income: [
    'payroll', 'bonus', 'rsu_proceeds', 'interest',
    'check_deposit', 'rental_income', 'business_income',
    'reimbursement', 'refund', 'other_income',
  ],
  fixed_obligation: [
    'mortgage', 'rent', 'insurance', 'utilities',
    'subscriptions_apps', 'streaming', 'loan_payment', 'property_fees',
    'landscaping', 'home_maintenance',
    'tuition', 'other_fixed',
  ],
  variable_lifestyle: [
    'groceries', 'dining_out', 'amazon', 'pets', 'travel', 'clothing',
    'entertainment', 'gifts', 'software', 'personal_care',
    'home_goods', 'intra_household', 'other_variable',
  ],
  transfer: [],
  tax: ['federal_tax', 'state_tax', 'estimated_tax', 'tax_payment', 'tax_refund'],
  rental: [
    'rental_income', 'rental_expense', 'mortgage', 'hoa',
    'rental_insurance', 'rental_repairs', 'rental_management',
  ],
  medical: ['doctor', 'pharmacy', 'dental', 'vision', 'hsa_expense', 'insurance_premium', 'vet', 'pet_medical'],
  investment: ['401k', 'hsa_contribution', 'roth_ira', 'brokerage', 'rsu', 'other_investment'],
  abnormal: ['home_repair', 'home_improvement', 'vehicle', 'legal', 'emergency', 'one_time_purchase', 'other_abnormal'],
  // TODO confirm — no business_expense subcategories existed in the taxonomy
  // before this batch; this is a reasonable seed set pending product sign-off.
  business_expense: [
    'software', 'hardware', 'professional_services', 'travel',
    'supplies', 'fees', 'other_business',
  ],
};

const HOUSEHOLD_ROLES = ['jeff', 'ashley', 'joint', 'rental', 'pet', 'professional', 'tax'] as const;

const LIFECYCLES = ['recurring', 'seasonal', 'one_time', 'unknown'] as const;

const DIRECTION_OPTIONS: { value: TransactionDirectionFilter; label: string }[] = [
  { value: 'outflow', label: 'Outflows' },
  { value: 'inflow', label: 'Inflows' },
  { value: 'all', label: 'All' },
];

// Primary-category filter options. `all` and `unclassified` are register-only
// sentinels; the rest mirror the taxonomy.
const CATEGORY_FILTER_OPTIONS: { value: string; label: string }[] = [
  { value: 'all', label: 'All categories' },
  { value: 'unclassified', label: 'Unclassified' },
  ...PRIMARY_CATEGORIES.map((c) => ({ value: c, label: c })),
];

// ── Register filter state ────────────────────────────────────────────────────

export interface RegisterFilterState {
  page: number;
  direction: TransactionDirectionFilter;
  /** `'all'`, `'unclassified'`, or a taxonomy value. */
  primaryCategory: string;
  /** `'all'` or an `accounts.id`. */
  accountId: string;
  search: string;
}

export const DEFAULT_REGISTER_FILTER: RegisterFilterState = {
  page: 0,
  direction: 'outflow',
  primaryCategory: 'all',
  accountId: 'all',
  search: '',
};

const REGISTER_FILTER_STORAGE_KEY = 'register-filter:v1';

/** Load the persisted filter, falling back to defaults. Page is never restored. */
export function loadPersistedRegisterFilter(): RegisterFilterState {
  if (typeof localStorage === 'undefined') return DEFAULT_REGISTER_FILTER;
  try {
    const raw = localStorage.getItem(REGISTER_FILTER_STORAGE_KEY);
    if (!raw) return DEFAULT_REGISTER_FILTER;
    const parsed = JSON.parse(raw) as Partial<RegisterFilterState>;
    return {
      ...DEFAULT_REGISTER_FILTER,
      ...parsed,
      page: 0,
    };
  } catch {
    return DEFAULT_REGISTER_FILTER;
  }
}

function persistRegisterFilter(filter: RegisterFilterState) {
  if (typeof localStorage === 'undefined') return;
  try {
    localStorage.setItem(REGISTER_FILTER_STORAGE_KEY, JSON.stringify({ ...filter, page: 0 }));
  } catch {
    // Non-fatal — persistence is a convenience, not a requirement.
  }
}

// ── Bulk classification draft ────────────────────────────────────────────────

interface ClassificationDraft {
  primary: string;
  subcategory: string;
  role: string;
  lifecycle: string;
}

const EMPTY_DRAFT: ClassificationDraft = { primary: '', subcategory: '', role: '', lifecycle: '' };

/** Build override params for a row from a classification draft. */
function buildOverrideParams(
  tx: Transaction,
  draft: ClassificationDraft,
): UpsertTransactionOverrideParams {
  return {
    transactionId: tx.id,
    accountId: tx.accountId,
    occurredOn: tx.occurredOn,
    amount: tx.amount,
    descriptionRaw: tx.descriptionRaw,
    primaryCategory: draft.primary || undefined,
    // Only touch subcategory when a primary was chosen; an empty sub then
    // clears it (null), otherwise leave it untouched (undefined).
    subcategory: draft.primary ? (draft.subcategory || null) : undefined,
    householdRole: draft.role || undefined,
    lifecycle: draft.lifecycle || undefined,
  };
}

// ── Public entry points ──────────────────────────────────────────────────────

/**
 * Embedded register card for the Cash flow dashboard. Owns its own filter
 * state so it stays independent of the standalone Classify view.
 */
export function TransactionRegisterSection() {
  const [filter, setFilter] = useState<RegisterFilterState>(DEFAULT_REGISTER_FILTER);
  return <RegisterPanel filter={filter} onFilterChange={setFilter} embedded />;
}

// Note: only the standalone Classify view persists its filter — the embedded
// dashboard register keeps ephemeral state so it never clobbers the saved set.

/**
 * Full-height standalone register — the Classify landing view. Filter state is
 * lifted to the app shell so the unclassified pill can deep-link into it.
 */
export function RegisterView({
  filter,
  onFilterChange,
}: {
  filter: RegisterFilterState;
  onFilterChange: (filter: RegisterFilterState) => void;
}) {
  return (
    <div className="min-h-[calc(100vh-9rem)]">
      <RegisterPanel filter={filter} onFilterChange={onFilterChange} persist />
    </div>
  );
}

// ── Register panel ───────────────────────────────────────────────────────────

interface RegisterPanelProps {
  filter: RegisterFilterState;
  onFilterChange: (filter: RegisterFilterState) => void;
  /** Adds the dashboard's top margin when embedded under other sections. */
  embedded?: boolean;
  /** Persist the filter to localStorage (standalone Classify view only). */
  persist?: boolean;
}

function RegisterPanel({
  filter,
  onFilterChange,
  embedded = false,
  persist = false,
}: RegisterPanelProps) {
  const queryClient = useQueryClient();
  const accountsQuery = useAccountOptions();

  const query = useTransactionRegister({
    page: filter.page,
    direction: filter.direction,
    primaryCategory: filter.primaryCategory,
    accountId: filter.accountId,
    search: filter.search,
  });

  // Persist last-used filter (minus page) so a cold open restores the working set.
  useEffect(() => {
    if (persist) persistRegisterFilter(filter);
  }, [persist, filter]);

  // Debounced search input → controlled filter.
  const [searchDraft, setSearchDraft] = useState(filter.search);
  useEffect(() => {
    const timer = setTimeout(() => {
      if (searchDraft !== filter.search) {
        onFilterChange({ ...filter, search: searchDraft, page: 0 });
      }
    }, 250);
    return () => clearTimeout(timer);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [searchDraft]);
  // Pull external resets (e.g. the unclassified deep-link) back into the input.
  useEffect(() => {
    setSearchDraft(filter.search);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [filter.search]);

  // Selection is scoped to the current page; clear it when the working set changes.
  const [selectedIds, setSelectedIds] = useState<Set<string>>(new Set());
  useEffect(() => {
    setSelectedIds(new Set());
  }, [filter.page, filter.direction, filter.primaryCategory, filter.accountId, filter.search]);

  const patch = (partial: Partial<RegisterFilterState>) =>
    onFilterChange({ ...filter, ...partial });

  // Bulk / merchant-wide override mutation. Lifted here so it can run over a set
  // while preserving the optimistic-update + invalidation contract.
  const bulkMutation = useMutation({
    mutationFn: async (paramsList: UpsertTransactionOverrideParams[]) => {
      for (const params of paramsList) {
        await upsertTransactionOverride(params);
      }
    },
    onMutate: async (paramsList) => {
      await queryClient.cancelQueries({ queryKey: ['transaction-register'] });
      const previous = queryClient.getQueriesData<TransactionPage>({
        queryKey: ['transaction-register'],
      });
      const byId = new Map(paramsList.map((p) => [p.transactionId, p]));
      queryClient.setQueriesData<TransactionPage>(
        { queryKey: ['transaction-register'] },
        (old) => {
          if (!old) return old;
          return {
            ...old,
            rows: old.rows.map((row) => {
              const params = byId.get(row.id);
              if (!params) return row;
              return {
                ...row,
                merchantNormalized: params.merchantNormalized ?? row.merchantNormalized,
                primaryCategory: params.primaryCategory ?? row.primaryCategory,
                subcategory:
                  params.subcategory !== undefined ? params.subcategory : row.subcategory,
                householdRole: params.householdRole ?? row.householdRole,
                lifecycle: params.lifecycle ?? row.lifecycle,
              };
            }),
          };
        },
      );
      return { previous };
    },
    onError: (_err, _params, context) => {
      if (!context?.previous) return;
      for (const [key, data] of context.previous) {
        queryClient.setQueryData(key, data);
      }
    },
    onSettled: () => {
      queryClient.invalidateQueries({ queryKey: ['transaction-register'] });
      queryClient.invalidateQueries({ queryKey: ['dashboard-snapshot'] });
      queryClient.invalidateQueries({ queryKey: ['unclassified-count'] });
    },
  });

  const data = query.data;
  const rows = data?.rows ?? [];
  const totalPages = data ? Math.max(1, Math.ceil(data.total / data.pageSize)) : 1;

  const allSelected = rows.length > 0 && rows.every((r) => selectedIds.has(r.id));
  const someSelected = selectedIds.size > 0;

  const toggleSelectAll = () => {
    setSelectedIds(allSelected ? new Set() : new Set(rows.map((r) => r.id)));
  };
  const toggleSelect = (id: string) => {
    setSelectedIds((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  };

  const applyBulk = (draft: ClassificationDraft) => {
    const targets = rows.filter((r) => selectedIds.has(r.id));
    if (targets.length === 0) return;
    bulkMutation.mutate(targets.map((tx) => buildOverrideParams(tx, draft)));
    setSelectedIds(new Set());
  };

  // "Apply to all matching this merchant" — bulk-override every currently-loaded
  // row that shares the source row's merchant (or, if none, its raw description).
  const applyToMatching = (source: Transaction, draft: ClassificationDraft) => {
    const matchKey = source.merchantNormalized;
    const targets = matchKey
      ? rows.filter((r) => r.merchantNormalized === matchKey)
      : rows.filter((r) => r.descriptionRaw === source.descriptionRaw);
    if (targets.length === 0) return;
    bulkMutation.mutate(targets.map((tx) => buildOverrideParams(tx, draft)));
    setSelectedIds(new Set());
  };

  return (
    <section
      className={cn(
        'rounded-xl border border-ink/8 bg-white shadow-card',
        embedded && 'mt-5',
      )}
    >
      <div className="flex flex-wrap items-start justify-between gap-4 px-5 pt-5 pb-3">
        <div>
          <div className="text-[10px] font-medium uppercase tracking-[0.18em] text-ink/45">
            Transaction register
          </div>
          <div className="mt-0.5 text-[15px] font-semibold text-ink">
            Line-item ledger — most recent first
          </div>
          <p className="mt-1 text-[11px] leading-relaxed text-ink/55">
            Transfers excluded from spending views. Unclassified rows show{' '}
            <span className="rounded bg-ink/[0.06] px-1 py-0.5 font-mono text-[10px] text-ink/70">
              unclassified
            </span>{' '}
            in the Category column.
          </p>
        </div>
        <div className="flex shrink-0 items-center gap-0.5 rounded-lg bg-ink/[0.05] p-0.5 text-[12px]">
          {DIRECTION_OPTIONS.map(({ value, label }) => (
            <button
              key={value}
              type="button"
              onClick={() => patch({ direction: value, page: 0 })}
              className={cn(
                'rounded-md px-2.5 py-1 transition-colors',
                filter.direction === value
                  ? 'bg-white font-medium text-ink shadow-card'
                  : 'text-ink/60 hover:text-ink',
              )}
            >
              {label}
            </button>
          ))}
        </div>
      </div>

      {/* Filter bar */}
      <div className="flex flex-wrap items-center gap-2 px-5 pb-3">
        <select
          value={filter.primaryCategory}
          onChange={(e) => patch({ primaryCategory: e.target.value, page: 0 })}
          className="rounded-md border border-ink/12 bg-white px-2 py-1 text-[12px] text-ink outline-none focus:border-tide"
          aria-label="Filter by primary category"
        >
          {CATEGORY_FILTER_OPTIONS.map(({ value, label }) => (
            <option key={value} value={value}>{label}</option>
          ))}
        </select>

        <select
          value={filter.accountId}
          onChange={(e) => patch({ accountId: e.target.value, page: 0 })}
          className="max-w-[220px] rounded-md border border-ink/12 bg-white px-2 py-1 text-[12px] text-ink outline-none focus:border-tide"
          aria-label="Filter by account"
        >
          <option value="all">All accounts</option>
          {(accountsQuery.data ?? []).map((acct) => (
            <option key={acct.id} value={acct.id}>{acct.label}</option>
          ))}
        </select>

        <input
          type="search"
          value={searchDraft}
          onChange={(e) => setSearchDraft(e.target.value)}
          placeholder="Search merchant or description…"
          className="min-w-[200px] flex-1 rounded-md border border-ink/12 bg-white px-2.5 py-1 text-[12px] text-ink outline-none focus:border-tide"
          aria-label="Search transactions"
        />
      </div>

      {/* Bulk action bar */}
      {someSelected && (
        <BulkActionBar
          count={selectedIds.size}
          pending={bulkMutation.isPending}
          onApply={applyBulk}
          onClear={() => setSelectedIds(new Set())}
        />
      )}

      {query.isLoading ? (
        <div className="px-5 pb-5 text-[13px] text-ink/50">Loading transactions…</div>
      ) : query.isError || !data ? (
        <div className="px-5 pb-5 text-[13px] text-ember">Unable to load transactions.</div>
      ) : rows.length === 0 ? (
        <div className="px-5 pb-5">
          <div className="rounded-lg border border-dashed border-ink/15 bg-paper/40 p-4 text-[12px] leading-relaxed text-ink/55">
            No transactions match the current filters. Adjust the category, account, or
            search above — or ingest documents via the MCP{' '}
            <code className="rounded bg-ink/[0.06] px-1 text-[11px] text-ink/75">
              ingest_documents
            </code>{' '}
            tool to populate.
          </div>
        </div>
      ) : (
        <>
          <div className="overflow-x-auto">
            <table className="w-full min-w-[680px] text-[12px]">
              <thead>
                <tr className="border-y border-ink/8 bg-paper/60 text-left text-[10px] font-medium uppercase tracking-[0.14em] text-ink/45">
                  <th className="w-[40px] px-4 py-2">
                    <input
                      type="checkbox"
                      checked={allSelected}
                      onChange={toggleSelectAll}
                      aria-label="Select all rows on this page"
                      className="cursor-pointer accent-tide"
                    />
                  </th>
                  <th className="w-[88px] px-4 py-2">Date</th>
                  <th className="px-4 py-2">Account</th>
                  <th className="px-4 py-2">Merchant / Description</th>
                  <th className="px-4 py-2">Category</th>
                  <th className="w-[96px] px-4 py-2 text-right">Amount</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-ink/[0.05]">
                {rows.map((tx) => (
                  <TransactionRow
                    key={tx.id}
                    tx={tx}
                    selected={selectedIds.has(tx.id)}
                    onToggleSelect={() => toggleSelect(tx.id)}
                    onApplyToMatching={applyToMatching}
                  />
                ))}
              </tbody>
            </table>
          </div>

          <div className="flex items-center justify-between border-t border-ink/8 px-5 py-3">
            <span className="text-[11px] text-ink/45 tnum">
              {data.total.toLocaleString()} transaction{data.total === 1 ? '' : 's'} ·
              page {filter.page + 1} of {totalPages}
            </span>
            <div className="flex items-center gap-1">
              <PaginationButton
                disabled={filter.page === 0}
                onClick={() => patch({ page: Math.max(0, filter.page - 1) })}
              >
                ← Prev
              </PaginationButton>
              <PaginationButton
                disabled={filter.page >= totalPages - 1}
                onClick={() => patch({ page: Math.min(totalPages - 1, filter.page + 1) })}
              >
                Next →
              </PaginationButton>
            </div>
          </div>
        </>
      )}
    </section>
  );
}

// ── Bulk action bar ──────────────────────────────────────────────────────────

function BulkActionBar({
  count,
  pending,
  onApply,
  onClear,
}: {
  count: number;
  pending: boolean;
  onApply: (draft: ClassificationDraft) => void;
  onClear: () => void;
}) {
  const [draft, setDraft] = useState<ClassificationDraft>(EMPTY_DRAFT);
  const subOptions = SUBCATEGORY_BY_PRIMARY[draft.primary] ?? [];
  const canApply = Boolean(draft.primary || draft.role || draft.lifecycle);

  const selectClasses =
    'rounded-md border border-ink/15 bg-white px-2 py-1 text-[12px] font-mono text-ink outline-none focus:border-tide';

  return (
    <div className="mx-5 mb-3 flex flex-wrap items-center gap-2 rounded-lg border border-tide/30 bg-tide/[0.06] px-3 py-2">
      <span className="text-[12px] font-medium text-ink">
        {count} selected
      </span>

      <select
        value={draft.primary}
        onChange={(e) => {
          const next = e.target.value;
          setDraft((d) => ({
            ...d,
            primary: next,
            subcategory: (SUBCATEGORY_BY_PRIMARY[next] ?? []).includes(d.subcategory)
              ? d.subcategory
              : '',
          }));
        }}
        className={selectClasses}
        aria-label="Bulk primary category"
      >
        <option value="">— category —</option>
        {PRIMARY_CATEGORIES.map((cat) => (
          <option key={cat} value={cat}>{cat}</option>
        ))}
      </select>

      {subOptions.length > 0 && (
        <select
          value={draft.subcategory}
          onChange={(e) => setDraft((d) => ({ ...d, subcategory: e.target.value }))}
          className={selectClasses}
          aria-label="Bulk subcategory"
        >
          <option value="">— subcategory —</option>
          {subOptions.map((sub) => (
            <option key={sub} value={sub}>{sub}</option>
          ))}
        </select>
      )}

      <select
        value={draft.role}
        onChange={(e) => setDraft((d) => ({ ...d, role: e.target.value }))}
        className={selectClasses}
        aria-label="Bulk household role"
      >
        <option value="">— owner —</option>
        {HOUSEHOLD_ROLES.map((role) => (
          <option key={role} value={role}>{role}</option>
        ))}
      </select>

      <select
        value={draft.lifecycle}
        onChange={(e) => setDraft((d) => ({ ...d, lifecycle: e.target.value }))}
        className={selectClasses}
        aria-label="Bulk lifecycle"
      >
        <option value="">— lifecycle —</option>
        {LIFECYCLES.map((lc) => (
          <option key={lc} value={lc}>{lc}</option>
        ))}
      </select>

      <button
        type="button"
        disabled={!canApply || pending}
        onClick={() => {
          onApply(draft);
          setDraft(EMPTY_DRAFT);
        }}
        className={cn(
          'rounded-md px-3 py-1 text-[12px] font-medium transition-colors',
          !canApply || pending
            ? 'cursor-not-allowed bg-ink/[0.06] text-ink/30'
            : 'bg-tide text-white hover:bg-tide/90',
        )}
      >
        {pending ? 'Applying…' : `Apply to ${count}`}
      </button>

      <span
        title="coming soon — needs classifier wiring"
        className="cursor-not-allowed select-none rounded-md border border-dashed border-ink/20 px-2 py-1 text-[11px] text-ink/35"
      >
        Save as rule
      </span>

      <button
        type="button"
        onClick={onClear}
        className="ml-auto rounded-md px-2 py-1 text-[12px] text-ink/55 hover:bg-ink/[0.06] hover:text-ink"
      >
        Clear
      </button>
    </div>
  );
}

// ── Transaction row ──────────────────────────────────────────────────────────

type EditField = 'merchant' | 'category';

function TransactionRow({
  tx,
  selected,
  onToggleSelect,
  onApplyToMatching,
}: {
  tx: Transaction;
  selected: boolean;
  onToggleSelect: () => void;
  onApplyToMatching: (source: Transaction, draft: ClassificationDraft) => void;
}) {
  const queryClient = useQueryClient();
  const [editField, setEditField] = useState<EditField | null>(null);

  // Merchant edit state
  const [merchantDraft, setMerchantDraft] = useState('');

  // Category edit state (separate fields)
  const [primaryDraft, setPrimaryDraft] = useState('');
  const [subcategoryDraft, setSubcategoryDraft] = useState('');
  const [roleDraft, setRoleDraft] = useState('');
  const [lifecycleDraft, setLifecycleDraft] = useState('');

  const mutation = useMutation({
    mutationFn: (params: UpsertTransactionOverrideParams) =>
      upsertTransactionOverride(params),
    onMutate: async (params) => {
      await queryClient.cancelQueries({ queryKey: ['transaction-register'] });
      const previous = queryClient.getQueriesData<TransactionPage>({
        queryKey: ['transaction-register'],
      });
      queryClient.setQueriesData<TransactionPage>(
        { queryKey: ['transaction-register'] },
        (old) => {
          if (!old) return old;
          return {
            ...old,
            rows: old.rows.map((row) =>
              row.id === params.transactionId
                ? {
                    ...row,
                    merchantNormalized: params.merchantNormalized ?? row.merchantNormalized,
                    primaryCategory: params.primaryCategory ?? row.primaryCategory,
                    // undefined means "field not included in this save" — leave existing value
                    subcategory: params.subcategory !== undefined ? params.subcategory : row.subcategory,
                    householdRole: params.householdRole ?? row.householdRole,
                    lifecycle: params.lifecycle ?? row.lifecycle,
                  }
                : row,
            ),
          };
        },
      );
      return { previous };
    },
    onError: (_err, _params, context) => {
      if (!context?.previous) return;
      for (const [key, data] of context.previous) {
        queryClient.setQueryData(key, data);
      }
    },
    onSettled: () => {
      queryClient.invalidateQueries({ queryKey: ['transaction-register'] });
      queryClient.invalidateQueries({ queryKey: ['dashboard-snapshot'] });
      queryClient.invalidateQueries({ queryKey: ['unclassified-count'] });
    },
  });

  const startEdit = (field: EditField) => {
    setEditField(field);
    if (field === 'merchant') {
      setMerchantDraft(tx.merchantNormalized ?? '');
    } else {
      // `unclassified` is not a selectable target — start from an empty primary
      // so the user must pick a real category.
      setPrimaryDraft(tx.primaryCategory === 'unclassified' ? '' : tx.primaryCategory);
      setSubcategoryDraft(tx.subcategory ?? '');
      setRoleDraft(tx.householdRole ?? '');
      setLifecycleDraft(tx.lifecycle ?? '');
    }
  };

  const cancelEdit = () => setEditField(null);

  const currentDraft = (): ClassificationDraft => ({
    primary: primaryDraft.trim(),
    subcategory: subcategoryDraft.trim(),
    role: roleDraft.trim(),
    lifecycle: lifecycleDraft.trim(),
  });

  const saveMerchantEdit = () => {
    const trimmed = merchantDraft.trim();
    if (!trimmed || trimmed === (tx.merchantNormalized ?? '')) {
      cancelEdit();
      return;
    }
    mutation.mutate({
      transactionId: tx.id,
      accountId: tx.accountId,
      occurredOn: tx.occurredOn,
      amount: tx.amount,
      descriptionRaw: tx.descriptionRaw,
      merchantNormalized: trimmed,
    });
    cancelEdit();
  };

  const saveCategoryEdit = () => {
    const primary = primaryDraft.trim();
    const sub = subcategoryDraft.trim() || null;
    const role = roleDraft.trim() || undefined;
    const lifecycle = lifecycleDraft.trim() || undefined;
    const unchanged =
      primary === tx.primaryCategory &&
      (sub ?? '') === (tx.subcategory ?? '') &&
      (role ?? '') === (tx.householdRole ?? '') &&
      (lifecycle ?? '') === (tx.lifecycle ?? '');
    if (!primary || unchanged) {
      cancelEdit();
      return;
    }
    mutation.mutate({
      transactionId: tx.id,
      accountId: tx.accountId,
      occurredOn: tx.occurredOn,
      amount: tx.amount,
      descriptionRaw: tx.descriptionRaw,
      primaryCategory: primary,
      subcategory: sub,
      householdRole: role,
      lifecycle,
    });
    cancelEdit();
  };

  const applyMatchingMerchant = () => {
    const draft = currentDraft();
    if (!draft.primary) return;
    onApplyToMatching(tx, draft);
    cancelEdit();
  };

  const handleMerchantKeyDown = (event: React.KeyboardEvent<HTMLInputElement>) => {
    if (event.key === 'Enter') {
      event.preventDefault();
      saveMerchantEdit();
    } else if (event.key === 'Escape') {
      event.preventDefault();
      cancelEdit();
    }
  };

  const handleSelectKeyDown = (event: React.KeyboardEvent<HTMLSelectElement>) => {
    if (event.key === 'Escape') {
      event.preventDefault();
      cancelEdit();
    }
  };

  const isOutflow = tx.direction === 'outflow';
  const isUnclassified = tx.primaryCategory === 'unclassified';
  const displayLabel = tx.merchantNormalized ?? tx.descriptionRaw;
  const categoryLabel = tx.subcategory
    ? `${tx.primaryCategory} / ${tx.subcategory}`
    : tx.primaryCategory;

  const inputClasses =
    'w-full rounded border border-tide/50 bg-white px-1.5 py-0.5 text-[12px] text-ink outline-none focus:border-tide';
  const selectClasses =
    'w-full rounded border border-tide/50 bg-white px-1 py-0.5 text-[11px] font-mono text-ink outline-none focus:border-tide cursor-pointer';

  const subcategoryOptions = SUBCATEGORY_BY_PRIMARY[primaryDraft] ?? [];
  const matchLabel = tx.merchantNormalized ?? 'this description';

  return (
    <tr className={cn('transition-colors', selected ? 'bg-tide/[0.06]' : 'hover:bg-paper/40')}>
      <td className="px-4 py-2.5 align-top">
        <input
          type="checkbox"
          checked={selected}
          onChange={onToggleSelect}
          aria-label="Select transaction"
          className="mt-0.5 cursor-pointer accent-tide"
        />
      </td>
      <td className="whitespace-nowrap px-4 py-2.5 font-mono text-[11px] text-ink/55 tnum">
        {tx.occurredOn}
      </td>
      <td className="px-4 py-2.5 text-ink/60">
        <span className="max-w-[140px] truncate block">{tx.accountLabel}</span>
      </td>
      <td className="px-4 py-2.5">
        {editField === 'merchant' ? (
          <input
            autoFocus
            value={merchantDraft}
            onChange={(event) => setMerchantDraft(event.target.value)}
            onKeyDown={handleMerchantKeyDown}
            onBlur={saveMerchantEdit}
            placeholder="Merchant name"
            className={cn(inputClasses, 'max-w-[260px]')}
            aria-label="Edit merchant"
          />
        ) : (
          <button
            type="button"
            onClick={() => startEdit('merchant')}
            title={`${tx.descriptionRaw}\n\nClick to edit merchant`}
            className="block w-full max-w-[260px] truncate rounded px-1 py-0.5 text-left text-ink hover:bg-ink/[0.05] focus:bg-ink/[0.05] focus:outline-none"
          >
            {displayLabel}
          </button>
        )}
      </td>
      <td className="px-4 py-2.5">
        {editField === 'category' ? (
          <div className="flex flex-col gap-1.5 min-w-[200px]">
            {/* Primary category */}
            <select
              autoFocus
              value={primaryDraft}
              onChange={(e) => {
                const next = e.target.value;
                setPrimaryDraft(next);
                if (!(SUBCATEGORY_BY_PRIMARY[next] ?? []).includes(subcategoryDraft)) {
                  setSubcategoryDraft('');
                }
              }}
              onKeyDown={handleSelectKeyDown}
              className={selectClasses}
              aria-label="Primary category"
            >
              <option value="">— select category —</option>
              {PRIMARY_CATEGORIES.map((cat) => (
                <option key={cat} value={cat}>{cat}</option>
              ))}
            </select>
            {/* Subcategory — only shown when the primary has valid options */}
            {subcategoryOptions.length > 0 && (
              <select
                value={subcategoryDraft}
                onChange={(e) => setSubcategoryDraft(e.target.value)}
                onKeyDown={handleSelectKeyDown}
                className={selectClasses}
                aria-label="Subcategory"
              >
                <option value="">— subcategory —</option>
                {subcategoryOptions.map((sub) => (
                  <option key={sub} value={sub}>{sub}</option>
                ))}
              </select>
            )}
            {/* Household role */}
            <select
              value={roleDraft}
              onChange={(e) => setRoleDraft(e.target.value)}
              onKeyDown={handleSelectKeyDown}
              className={selectClasses}
              aria-label="Household role"
            >
              <option value="">— owner —</option>
              {HOUSEHOLD_ROLES.map((role) => (
                <option key={role} value={role}>{role}</option>
              ))}
            </select>
            {/* Lifecycle */}
            <select
              value={lifecycleDraft}
              onChange={(e) => setLifecycleDraft(e.target.value)}
              onKeyDown={handleSelectKeyDown}
              className={selectClasses}
              aria-label="Lifecycle"
            >
              <option value="">— lifecycle —</option>
              {LIFECYCLES.map((lc) => (
                <option key={lc} value={lc}>{lc}</option>
              ))}
            </select>
            <div className="flex flex-wrap items-center gap-1">
              <button
                type="button"
                onMouseDown={(e) => { e.preventDefault(); saveCategoryEdit(); }}
                className="rounded bg-tide/15 px-1.5 py-0.5 text-[10px] font-medium text-tide hover:bg-tide/25"
              >
                Save
              </button>
              <button
                type="button"
                disabled={!primaryDraft.trim()}
                onMouseDown={(e) => { e.preventDefault(); applyMatchingMerchant(); }}
                title={`Apply this category to every loaded row matching ${matchLabel}`}
                className={cn(
                  'rounded px-1.5 py-0.5 text-[10px] font-medium',
                  primaryDraft.trim()
                    ? 'bg-ink/[0.06] text-ink/70 hover:bg-ink/[0.1]'
                    : 'cursor-not-allowed text-ink/25',
                )}
              >
                Apply to all matching
              </button>
              <button
                type="button"
                onMouseDown={(e) => { e.preventDefault(); cancelEdit(); }}
                className="rounded px-1.5 py-0.5 text-[10px] text-ink/55 hover:bg-ink/[0.06]"
              >
                Cancel
              </button>
            </div>
          </div>
        ) : (
          <button
            type="button"
            onClick={() => startEdit('category')}
            title="Click to edit category, subcategory, owner, and lifecycle"
            className={cn(
              'inline-block rounded px-1.5 py-0.5 text-[10px] font-medium hover:ring-1 hover:ring-tide/40 focus:outline-none focus:ring-1 focus:ring-tide/60',
              isUnclassified ? 'bg-clay/12 text-clay' : 'bg-ink/[0.05] text-ink/65',
            )}
          >
            {categoryLabel}
          </button>
        )}
      </td>
      <td
        className={cn(
          'whitespace-nowrap px-4 py-2.5 text-right font-medium tnum',
          isOutflow ? 'text-ember' : 'text-moss',
        )}
      >
        {isOutflow ? '−' : '+'}{currency(Math.abs(tx.amount))}
      </td>
    </tr>
  );
}

function PaginationButton({
  children,
  disabled,
  onClick,
}: {
  children: React.ReactNode;
  disabled: boolean;
  onClick: () => void;
}) {
  return (
    <button
      type="button"
      disabled={disabled}
      onClick={onClick}
      className={cn(
        'rounded-md px-3 py-1 text-[12px] transition-colors',
        disabled
          ? 'cursor-not-allowed text-ink/25'
          : 'text-ink/60 hover:bg-ink/[0.06] hover:text-ink',
      )}
    >
      {children}
    </button>
  );
}
