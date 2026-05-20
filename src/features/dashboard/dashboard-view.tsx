import { useState, useRef } from 'react';
import { useMutation, useQueryClient, type UseQueryResult } from '@tanstack/react-query';
import { cn, currency, percent } from '../../lib/utils';
import { useTransactionRegister } from '../../hooks/use-transaction-register';
import { TransactionDrawer } from './transaction-drawer';
import {
  updateVarianceExplanation,
  upsertTransactionOverride,
  type UpdateVarianceExplanationParams,
  type UpsertTransactionOverrideParams,
} from '../../services/sqlite';
import {
  assessCapitalPlanFeasibility,
  type FeasibilityAssessment,
  type FeasibilityStatus,
} from './feasibility';
import type {
  CashFlowMonth,
  DashboardRange,
  DashboardSnapshot,
  HysaTrajectory,
  LeakageCategory,
  LiquidityGate,
  ReconciliationPeriod,
  SubscriptionAuditEntry,
  Transaction,
  TransactionDirectionFilter,
  TransactionPage,
} from './types';

// Variance thresholds match master index §6 #2: zero or explained variance
// is required before downstream tabs are trusted. Sub-cent slop is green,
// $1–$10 is yellow (a real but small data gap), >$10 is red.
const VARIANCE_GREEN_MAX = 1;
const VARIANCE_YELLOW_MAX = 10;

function varianceBadge(amount: number | null): 'green' | 'yellow' | 'red' | 'unknown' {
  if (amount === null) return 'unknown';
  const abs = Math.abs(amount);
  if (abs < VARIANCE_GREEN_MAX) return 'green';
  if (abs <= VARIANCE_YELLOW_MAX) return 'yellow';
  return 'red';
}

function currencyCents(value: number): string {
  return new Intl.NumberFormat('en-US', {
    style: 'currency',
    currency: 'USD',
    minimumFractionDigits: 2,
    maximumFractionDigits: 2,
  }).format(value);
}

interface DashboardViewProps {
  query: UseQueryResult<DashboardSnapshot, Error>;
  activeRange: DashboardRange;
  onRangeChange: (range: DashboardRange) => void;
}

// ── Trailing delta helper ────────────────────────────────────────────────────

function trailingDelta(
  months: CashFlowMonth[],
  key: 'inflow' | 'outflow',
): { delta: number; label: string } | null {
  if (months.length < 2) return null;
  if (months.length >= 6) {
    const recent = months.slice(-3).reduce((s, m) => s + m[key], 0) / 3;
    const prior = months.slice(-6, -3).reduce((s, m) => s + m[key], 0) / 3;
    if (prior === 0) return null;
    return { delta: (recent - prior) / prior, label: 'vs. trailing 3-mo' };
  }
  const last = months[months.length - 1];
  const prev = months[months.length - 2];
  const prevVal = prev[key];
  if (prevVal === 0) return null;
  return { delta: (last[key] - prevVal) / prevVal, label: 'vs. prior month' };
}

// ── Custom range popover ─────────────────────────────────────────────────────

const MONTH_NAMES = [
  'Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun',
  'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec',
];

function CustomRangePopover({
  onApply,
  onClose,
}: {
  onApply: (range: { start: string; end: string }) => void;
  onClose: () => void;
}) {
  const currentYear = new Date().getFullYear();
  const years = [currentYear - 2, currentYear - 1, currentYear];

  const [startMonth, setStartMonth] = useState('01');
  const [startYear, setStartYear] = useState(String(currentYear));
  const [endMonth, setEndMonth] = useState(String(new Date().getMonth() + 1).padStart(2, '0'));
  const [endYear, setEndYear] = useState(String(currentYear));

  const selectClass =
    'rounded border border-ink/15 bg-white px-2 py-1 text-[12px] text-ink outline-none focus:border-tide';

  return (
    <div className="absolute right-0 top-full z-30 mt-1 rounded-lg border border-ink/10 bg-white p-4 shadow-card">
      <div className="mb-3 text-[10px] font-medium uppercase tracking-[0.16em] text-ink/45">
        Custom range
      </div>
      <div className="grid grid-cols-2 gap-4">
        <div>
          <div className="mb-1 text-[10px] text-ink/55">Start</div>
          <div className="flex gap-1.5">
            <select value={startMonth} onChange={(e) => setStartMonth(e.target.value)} className={selectClass}>
              {MONTH_NAMES.map((m, i) => (
                <option key={m} value={String(i + 1).padStart(2, '0')}>{m}</option>
              ))}
            </select>
            <select value={startYear} onChange={(e) => setStartYear(e.target.value)} className={selectClass}>
              {years.map((y) => <option key={y} value={String(y)}>{y}</option>)}
            </select>
          </div>
        </div>
        <div>
          <div className="mb-1 text-[10px] text-ink/55">End</div>
          <div className="flex gap-1.5">
            <select value={endMonth} onChange={(e) => setEndMonth(e.target.value)} className={selectClass}>
              {MONTH_NAMES.map((m, i) => (
                <option key={m} value={String(i + 1).padStart(2, '0')}>{m}</option>
              ))}
            </select>
            <select value={endYear} onChange={(e) => setEndYear(e.target.value)} className={selectClass}>
              {years.map((y) => <option key={y} value={String(y)}>{y}</option>)}
            </select>
          </div>
        </div>
      </div>
      <div className="mt-3 flex items-center justify-end gap-2">
        <button
          type="button"
          onClick={onClose}
          className="rounded px-2.5 py-1 text-[12px] text-ink/55 hover:bg-ink/[0.05]"
        >
          Cancel
        </button>
        <button
          type="button"
          onClick={() =>
            onApply({ start: `${startYear}-${startMonth}`, end: `${endYear}-${endMonth}` })
          }
          className="rounded bg-tide/15 px-2.5 py-1 text-[12px] font-medium text-tide hover:bg-tide/25"
        >
          Apply
        </button>
      </div>
    </div>
  );
}

// ── Dashboard view ───────────────────────────────────────────────────────────

export function DashboardView({ query, activeRange, onRangeChange }: DashboardViewProps) {
  // Drawer state
  const [drawerOpen, setDrawerOpen] = useState(false);
  const [drawerCategory, setDrawerCategory] = useState<{
    key: string;
    name: string;
  } | null>(null);

  // Custom range state
  const [customRangeOpen, setCustomRangeOpen] = useState(false);
  const [customRange, setCustomRange] = useState<{ start: string; end: string } | null>(null);

  if (query.isLoading) {
    return (
      <div className="rounded-xl border border-ink/8 bg-white p-6 text-sm text-ink/70 shadow-card">
        Preparing the local dashboard snapshot.
      </div>
    );
  }

  if (query.isError || !query.data) {
    return (
      <div className="rounded-xl border border-ink/8 bg-white p-6 text-sm text-ember shadow-card">
        Unable to load the cash-flow dashboard snapshot.
      </div>
    );
  }

  const {
    months: allMonths,
    gates,
    leakageCategories,
    reconciliations,
    subscriptionAudit,
    hysaTrajectory,
  } = query.data;

  // When custom range is active and months have YYYY-MM data, filter locally.
  const months =
    activeRange === 'custom' && customRange
      ? allMonths.filter(
          (m) =>
            m.month === undefined ||
            (m.month >= customRange.start && m.month <= customRange.end),
        )
      : allMonths;

  const totalInflow = months.reduce((total, m) => total + m.inflow, 0);
  const totalOutflow = months.reduce((total, m) => total + m.outflow, 0);
  const netYtd = totalInflow - totalOutflow;
  const avgInflow = months.length === 0 ? 0 : totalInflow / months.length;
  const avgOutflow = months.length === 0 ? 0 : totalOutflow / months.length;

  const dataMax = Math.max(0, ...months.flatMap((m) => [m.inflow, m.outflow]));
  const chartMax = Math.max(20000, Math.ceil(dataMax / 5000) * 5000);
  const yAxisLabels = [chartMax, chartMax * 0.75, chartMax * 0.5, chartMax * 0.25, 0];

  const feasibility = assessCapitalPlanFeasibility(
    months,
    hysaTrajectory,
    leakageCategories,
  );

  const inflowDelta = trailingDelta(months, 'inflow');
  const outflowDelta = trailingDelta(months, 'outflow');

  // Drawer range: span the currently-displayed months
  const drawerRange =
    months.length > 0 && months[0].month && months[months.length - 1].month
      ? { start: months[0].month, end: months[months.length - 1].month as string }
      : { start: '', end: '' };

  const openDrawer = (category: { key: string; name: string }) => {
    setDrawerCategory(category);
    setDrawerOpen(true);
  };

  return (
    <>
      <div>
        <div className="mb-4 flex items-baseline justify-between gap-4">
          <div className="min-w-0 flex items-baseline gap-3">
            <span className="text-[11px] font-medium uppercase tracking-[0.18em] text-ink/45">
              Phase 2
            </span>
            <h1 className="text-[18px] font-semibold tracking-tight text-ink">Cash flow</h1>
          </div>
          <div className="relative flex shrink-0 items-center gap-1 rounded-lg bg-ink/[0.05] p-0.5 text-[12px]">
            <RangeButton active={activeRange === 'ytd'} onClick={() => onRangeChange('ytd')}>YTD</RangeButton>
            <RangeButton active={activeRange === '12mo'} onClick={() => onRangeChange('12mo')}>12 mo</RangeButton>
            <RangeButton active={activeRange === 'all'} onClick={() => onRangeChange('all')}>All</RangeButton>
            <RangeButton
              active={activeRange === 'custom'}
              onClick={() => setCustomRangeOpen((v) => !v)}
            >
              Custom
            </RangeButton>
            {customRangeOpen && (
              <CustomRangePopover
                onApply={(r) => {
                  setCustomRange(r);
                  onRangeChange('custom');
                  setCustomRangeOpen(false);
                }}
                onClose={() => setCustomRangeOpen(false)}
              />
            )}
          </div>
        </div>

        <div className="mb-6 grid grid-cols-1 gap-3 md:grid-cols-3">
          <KpiTile label="Net YTD" tone={netYtd >= 0 ? 'moss' : 'ember'}>
            <div className="mt-1.5 text-[24px] font-semibold tnum">
              {netYtd >= 0 ? '+' : ''}
              {currency(netYtd)}
            </div>
            <div className="mt-1 text-[11px] text-ink/50 tnum">
              {months.length} month{months.length === 1 ? '' : 's'} · inflow − outflow
            </div>
          </KpiTile>
          <KpiTile label="Avg monthly inflow">
            <div className="mt-1.5 text-[24px] font-semibold tnum text-ink">{currency(avgInflow)}</div>
            {inflowDelta ? (
              <div className="mt-1 flex items-baseline gap-1 text-[11px] tnum">
                <span className={inflowDelta.delta >= 0 ? 'text-moss' : 'text-ember'}>
                  {inflowDelta.delta >= 0 ? '+' : ''}
                  {(inflowDelta.delta * 100).toFixed(1)}%
                </span>
                <span className="text-ink/45">{inflowDelta.label}</span>
              </div>
            ) : (
              <div className="mt-1 text-[11px] text-ink/45">no comparison yet</div>
            )}
          </KpiTile>
          <KpiTile label="Avg monthly outflow">
            <div className="mt-1.5 text-[24px] font-semibold tnum text-ink">{currency(avgOutflow)}</div>
            {outflowDelta ? (
              <div className="mt-1 flex items-baseline gap-1 text-[11px] tnum">
                {/* Rising outflow is bad → positive delta = ember */}
                <span className={outflowDelta.delta <= 0 ? 'text-moss' : 'text-ember'}>
                  {outflowDelta.delta >= 0 ? '+' : ''}
                  {(outflowDelta.delta * 100).toFixed(1)}%
                </span>
                <span className="text-ink/45">{outflowDelta.label}</span>
              </div>
            ) : (
              <div className="mt-1 text-[11px] text-ink/45">no comparison yet</div>
            )}
          </KpiTile>
        </div>

        <FeasibilitySection assessment={feasibility} />

        <div
          className="grid gap-5"
          style={{ gridTemplateColumns: 'minmax(0, 1.45fr) minmax(0, 1fr)' }}
        >
          <CashFlowChart months={months} chartMax={chartMax} yAxisLabels={yAxisLabels} />
          <GatesPanel gates={gates} />
        </div>

        <HysaTrajectorySection trajectory={hysaTrajectory} />

        <ReconciliationSection periods={reconciliations} />

        <LeakageSection categories={leakageCategories} onOpenDrawer={openDrawer} />

        <SubscriptionAuditSection entries={subscriptionAudit} />

        <TransactionRegisterSection />
      </div>

      <TransactionDrawer
        open={drawerOpen}
        categoryKey={drawerCategory?.key ?? null}
        categoryName={drawerCategory?.name ?? null}
        range={drawerRange}
        onClose={() => setDrawerOpen(false)}
      />
    </>
  );
}

export function groupReconciliationsByAccount(
  periods: ReconciliationPeriod[],
): { latest: ReconciliationPeriod; history: ReconciliationPeriod[] }[] {
  // Preserves the SQL ordering: first row per account is the most recent
  // because the snapshot query sorts by period_end DESC within each account.
  const byAccount = new Map<
    string,
    { latest: ReconciliationPeriod; history: ReconciliationPeriod[] }
  >();
  for (const period of periods) {
    const existing = byAccount.get(period.accountId);
    if (!existing) {
      byAccount.set(period.accountId, { latest: period, history: [] });
    } else {
      existing.history.push(period);
    }
  }
  return Array.from(byAccount.values());
}

function ReconciliationSection({ periods }: { periods: ReconciliationPeriod[] }) {
  const groups = groupReconciliationsByAccount(periods);
  return (
    <section className="mt-5 rounded-xl border border-ink/8 bg-white shadow-card">
      <div className="flex items-center justify-between px-5 pt-5 pb-3">
        <div>
          <div className="text-[10px] font-medium uppercase tracking-[0.18em] text-ink/45">
            Reconciliation
          </div>
          <div className="mt-0.5 text-[15px] font-semibold text-ink">
            Per-account variance — most recent period
          </div>
          <p className="mt-1 text-[11px] leading-relaxed text-ink/55">
            Opening + inflows − outflows = computed closing, compared against the
            statement-reported closing balance. Variance must be zero (or
            explained) before downstream tabs are trusted. Expand a card to see
            prior periods.
          </p>
        </div>
      </div>
      <div className="grid grid-cols-1 gap-3 px-5 pb-5 md:grid-cols-2 xl:grid-cols-4">
        {groups.length === 0 ? (
          <div className="col-span-full rounded-lg border border-dashed border-ink/15 bg-paper/40 p-4 text-[12px] leading-relaxed text-ink/55">
            No reconciliation periods computed yet. Run the
            <code className="mx-1 rounded bg-ink/[0.06] px-1 py-0.5 text-[11px] text-ink/75">
              reconcile_periods
            </code>
            MCP tool to populate.
          </div>
        ) : (
          groups.map((group) => (
            <ReconciliationCard
              key={group.latest.accountId}
              period={group.latest}
              history={group.history}
            />
          ))
        )}
      </div>
    </section>
  );
}

function ReconciliationCard({
  period,
  history,
}: {
  period: ReconciliationPeriod;
  history: ReconciliationPeriod[];
}) {
  const [historyOpen, setHistoryOpen] = useState(false);
  const badge = varianceBadge(period.varianceAmount);
  const isLiability = period.accountType === 'credit_card';
  const closingLabel = isLiability ? 'amount owed' : 'balance';

  const badgeText =
    badge === 'unknown'
      ? 'No statement'
      : period.varianceAmount === 0
        ? 'Reconciled'
        : `${period.varianceAmount! > 0 ? '+' : ''}${currencyCents(period.varianceAmount!)}`;

  return (
    <div className="rounded-lg border border-ink/8 bg-paper/50 p-3.5">
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0">
          <p className="text-[13px] font-medium leading-snug text-ink">
            {period.accountLabel}
          </p>
          <p className="mt-0.5 text-[11px] uppercase tracking-[0.16em] text-ink/45">
            {period.periodStart} → {period.periodEnd}
          </p>
        </div>
        <span
          className={cn(
            'shrink-0 rounded-full px-2 py-0.5 text-[11px] font-semibold tnum',
            varianceBadgeStyles[badge],
          )}
        >
          {badgeText}
        </span>
      </div>

      <dl className="mt-3 space-y-1 text-[11px] tnum">
        <BalanceRow
          label={`Opening ${closingLabel}`}
          value={period.statementOpeningBalance}
        />
        <BalanceRow
          label={`Computed ${closingLabel}`}
          value={period.computedClosingBalance}
        />
        <BalanceRow
          label={`Statement ${closingLabel}`}
          value={period.statementClosingBalance}
          muted={period.closingBalanceSource === null}
        />
      </dl>

      {period.closingBalanceSource ? (
        <p className="mt-2 text-[10px] uppercase tracking-[0.16em] text-ink/40">
          Source · {sourceLabel(period.closingBalanceSource)}
        </p>
      ) : null}

      <VarianceExplanationEditor period={period} />

      {history.length > 0 ? (
        <div className="mt-3 border-t border-ink/[0.06] pt-2">
          <button
            type="button"
            onClick={() => setHistoryOpen((open) => !open)}
            aria-expanded={historyOpen}
            className="flex w-full items-center justify-between rounded px-1 py-0.5 text-[11px] text-ink/55 hover:bg-ink/[0.05] hover:text-ink/80 focus:bg-ink/[0.05] focus:outline-none"
          >
            <span>
              {historyOpen ? 'Hide' : 'Show'} {history.length} earlier period
              {history.length === 1 ? '' : 's'}
            </span>
            <span aria-hidden className="text-ink/40">
              {historyOpen ? '▾' : '▸'}
            </span>
          </button>
          {historyOpen ? (
            <ul className="mt-2 space-y-2">
              {history.map((prior) => (
                <ReconciliationHistoryRow
                  key={`${prior.periodStart}-${prior.periodEnd}`}
                  period={prior}
                  isLiability={isLiability}
                />
              ))}
            </ul>
          ) : null}
        </div>
      ) : null}
    </div>
  );
}

const varianceBadgeStyles: Record<'green' | 'yellow' | 'red' | 'unknown', string> = {
  green: 'bg-moss/12 text-moss',
  yellow: 'bg-clay/12 text-clay',
  red: 'bg-ember/12 text-ember',
  unknown: 'bg-ink/[0.06] text-ink/55',
};

function ReconciliationHistoryRow({
  period,
  isLiability,
}: {
  period: ReconciliationPeriod;
  isLiability: boolean;
}) {
  const badge = varianceBadge(period.varianceAmount);
  const closingLabel = isLiability ? 'amount owed' : 'balance';
  const badgeText =
    badge === 'unknown'
      ? 'No statement'
      : period.varianceAmount === 0
        ? 'Reconciled'
        : `${period.varianceAmount! > 0 ? '+' : ''}${currencyCents(period.varianceAmount!)}`;

  return (
    <li className="rounded border border-ink/[0.06] bg-white/60 px-2 py-1.5 text-[11px]">
      <div className="flex items-start justify-between gap-2">
        <div className="font-mono text-[10px] uppercase tracking-[0.14em] text-ink/55 tnum">
          {period.periodStart} → {period.periodEnd}
        </div>
        <span
          className={cn(
            'shrink-0 rounded-full px-1.5 py-0.5 text-[10px] font-semibold tnum',
            varianceBadgeStyles[badge],
          )}
        >
          {badgeText}
        </span>
      </div>
      <div className="mt-1 flex items-center justify-between text-[10px] text-ink/55 tnum">
        <span>
          Computed{' '}
          <span className="font-medium text-ink/75">
            {period.computedClosingBalance === null
              ? '—'
              : currency(period.computedClosingBalance)}
          </span>
        </span>
        <span>
          Statement {closingLabel}{' '}
          <span
            className={cn(
              'font-medium',
              period.closingBalanceSource === null ? 'text-ink/35' : 'text-ink/75',
            )}
          >
            {period.statementClosingBalance === null
              ? '—'
              : currency(period.statementClosingBalance)}
          </span>
        </span>
      </div>
      {period.varianceExplanation ? (
        <p className="mt-1 italic leading-relaxed text-ink/60">
          {period.varianceExplanation}
        </p>
      ) : null}
    </li>
  );
}

function VarianceExplanationEditor({ period }: { period: ReconciliationPeriod }) {
  const queryClient = useQueryClient();
  const [isEditing, setIsEditing] = useState(false);
  const [draft, setDraft] = useState(period.varianceExplanation);

  const mutation = useMutation({
    mutationFn: (params: UpdateVarianceExplanationParams) =>
      updateVarianceExplanation(params),
    onMutate: async (params) => {
      await queryClient.cancelQueries({ queryKey: ['dashboard-snapshot'] });
      const previous = queryClient.getQueriesData<DashboardSnapshot>({
        queryKey: ['dashboard-snapshot'],
      });
      queryClient.setQueriesData<DashboardSnapshot>(
        { queryKey: ['dashboard-snapshot'] },
        (old) => {
          if (!old) return old;
          return {
            ...old,
            reconciliations: old.reconciliations.map((r) =>
              r.accountId === params.accountId &&
              r.periodStart === params.periodStart &&
              r.periodEnd === params.periodEnd
                ? { ...r, varianceExplanation: params.explanation }
                : r,
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
      queryClient.invalidateQueries({ queryKey: ['dashboard-snapshot'] });
    },
  });

  const startEdit = () => {
    setDraft(period.varianceExplanation);
    setIsEditing(true);
  };

  const cancelEdit = () => {
    setIsEditing(false);
    setDraft(period.varianceExplanation);
  };

  const saveEdit = () => {
    const trimmed = draft.trim();
    if (trimmed === period.varianceExplanation.trim()) {
      setIsEditing(false);
      return;
    }
    mutation.mutate({
      accountId: period.accountId,
      periodStart: period.periodStart,
      periodEnd: period.periodEnd,
      explanation: trimmed,
    });
    setIsEditing(false);
  };

  const handleKeyDown = (event: React.KeyboardEvent<HTMLTextAreaElement>) => {
    // Multi-line note: Enter inserts newline; Ctrl/Cmd+Enter saves; Esc cancels.
    if (event.key === 'Escape') {
      event.preventDefault();
      cancelEdit();
    } else if (event.key === 'Enter' && (event.metaKey || event.ctrlKey)) {
      event.preventDefault();
      saveEdit();
    }
  };

  if (isEditing) {
    return (
      <div className="mt-2">
        <textarea
          autoFocus
          value={draft}
          onChange={(event) => setDraft(event.target.value)}
          onKeyDown={handleKeyDown}
          onBlur={saveEdit}
          placeholder="Explain the variance (e.g. timing of a pending charge, statement cut date drift)…"
          rows={3}
          className="w-full rounded border border-tide/50 bg-white px-2 py-1 text-[11px] leading-relaxed text-ink outline-none focus:border-tide"
          aria-label="Edit variance explanation"
        />
        <div className="mt-1 flex items-center justify-between text-[10px] text-ink/45">
          <span>Esc cancels · ⌘/Ctrl+Enter saves · blur saves</span>
          <div className="flex items-center gap-1">
            <button
              type="button"
              onMouseDown={(event) => {
                // Prevent the textarea's onBlur from firing before this click.
                event.preventDefault();
                cancelEdit();
              }}
              className="rounded px-1.5 py-0.5 text-ink/55 hover:bg-ink/[0.06]"
            >
              Cancel
            </button>
            <button
              type="button"
              onMouseDown={(event) => {
                event.preventDefault();
                saveEdit();
              }}
              className="rounded bg-tide/15 px-1.5 py-0.5 font-medium text-tide hover:bg-tide/25"
            >
              Save
            </button>
          </div>
        </div>
      </div>
    );
  }

  if (period.varianceExplanation) {
    return (
      <button
        type="button"
        onClick={startEdit}
        title="Click to edit explanation"
        className="mt-2 block w-full rounded px-1 py-0.5 text-left text-[11px] italic leading-relaxed text-ink/65 hover:bg-ink/[0.05] focus:bg-ink/[0.05] focus:outline-none"
      >
        {period.varianceExplanation}
      </button>
    );
  }

  return (
    <button
      type="button"
      onClick={startEdit}
      className="mt-2 inline-flex items-center gap-1 rounded px-1 py-0.5 text-[11px] text-ink/45 hover:bg-ink/[0.05] hover:text-ink/70 focus:bg-ink/[0.05] focus:outline-none"
    >
      + Add variance explanation
    </button>
  );
}

function BalanceRow({
  label,
  value,
  muted = false,
}: {
  label: string;
  value: number | null;
  muted?: boolean;
}) {
  return (
    <div className="flex items-center justify-between">
      <dt className="text-ink/55">{label}</dt>
      <dd className={cn('font-medium', muted ? 'text-ink/35' : 'text-ink')}>
        {value === null ? '—' : currency(value)}
      </dd>
    </div>
  );
}

function sourceLabel(source: string): string {
  if (source === 'metadata_running_balance') return 'CSV running balance';
  if (source === 'balances_toml') return 'balances.toml';
  return source;
}

function RangeButton({
  children,
  active = false,
  onClick,
}: {
  children: React.ReactNode;
  active?: boolean;
  onClick?: () => void;
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      className={cn(
        'rounded-md px-2.5 py-1 transition-colors',
        active ? 'bg-white font-medium text-ink shadow-card' : 'text-ink/60 hover:text-ink',
      )}
    >
      {children}
    </button>
  );
}

function KpiTile({
  label,
  tone,
  children,
}: {
  label: string;
  tone?: 'moss' | 'ember';
  children: React.ReactNode;
}) {
  return (
    <div className="rounded-xl border border-ink/8 bg-white p-4 shadow-card">
      <div className="text-[10px] font-medium uppercase tracking-[0.18em] text-ink/50">
        {label}
      </div>
      <div
        className={cn(
          tone === 'moss' && 'text-moss',
          tone === 'ember' && 'text-ember',
        )}
      >
        {children}
      </div>
    </div>
  );
}

function formatAxisLabel(value: number): string {
  if (value === 0) return '$0';
  return `$${Math.round(value / 1000)}k`;
}

function CashFlowChart({
  months,
  chartMax,
  yAxisLabels,
}: {
  months: CashFlowMonth[];
  chartMax: number;
  yAxisLabels: number[];
}) {
  const [hovered, setHovered] = useState<number | null>(null);
  const colCount = Math.max(months.length, 1);

  return (
    <section className="rounded-xl border border-ink/8 bg-white shadow-card">
      <div className="flex items-center justify-between gap-4 px-5 pt-5 pb-3">
        <div>
          <div className="text-[10px] font-medium uppercase tracking-[0.18em] text-ink/45">
            Cash flow
          </div>
          <div className="mt-0.5 text-[15px] font-semibold text-ink">
            Inflow vs. outflow by month
          </div>
        </div>
        <div className="flex items-center gap-3 text-[11px] text-ink/65">
          <span className="flex items-center gap-1.5">
            <span className="h-2 w-2 rounded-sm bg-moss" />
            Inflow
          </span>
          <span className="flex items-center gap-1.5">
            <span className="h-2 w-2 rounded-sm bg-clay" />
            Outflow
          </span>
        </div>
      </div>

      <div className="px-5 pb-5">
        <div className="relative">
          {/* Y-axis labels */}
          <div className="absolute inset-y-0 left-0 flex w-10 flex-col justify-between py-1 text-right text-[10px] text-ink/35 tnum">
            {yAxisLabels.map((value) => (
              <span key={value}>{formatAxisLabel(value)}</span>
            ))}
          </div>

          {/* Bar columns */}
          <div
            className="ml-12 grid gap-6"
            style={{ gridTemplateColumns: `repeat(${colCount}, minmax(0, 1fr))` }}
          >
            {months.map((month, i) => {
              const inflowHeight = chartMax === 0 ? 0 : (month.inflow / chartMax) * 160;
              const outflowHeight = chartMax === 0 ? 0 : (month.outflow / chartMax) * 160;
              const isHovered = hovered === i;
              return (
                <button
                  key={month.label}
                  type="button"
                  onMouseEnter={() => setHovered(i)}
                  onMouseLeave={() => setHovered(null)}
                  onFocus={() => setHovered(i)}
                  onBlur={() => setHovered(null)}
                  className={`flex h-48 w-full items-end justify-center gap-2 rounded-md transition-colors ${
                    isHovered ? 'bg-ink/[0.03]' : ''
                  }`}
                >
                  <div
                    className="w-7 rounded-t-md bg-moss transition-opacity"
                    style={{
                      height: `${inflowHeight}px`,
                      opacity: hovered !== null && !isHovered ? 0.55 : 1,
                    }}
                  />
                  <div
                    className="w-7 rounded-t-md bg-clay transition-opacity"
                    style={{
                      height: `${outflowHeight}px`,
                      opacity: hovered !== null && !isHovered ? 0.55 : 1,
                    }}
                  />
                </button>
              );
            })}
          </div>

          {/* Shared hover tooltip – anchored to column index, not mouse coords */}
          {hovered !== null && months[hovered] && (
            <ChartTooltip
              month={months[hovered]}
              anchorIndex={hovered}
              total={colCount}
            />
          )}

          <div className="ml-12 mt-2 h-px w-[calc(100%-3rem)] bg-ink/10" />
        </div>

        {/* X-axis labels */}
        <div
          className="ml-12 mt-2 grid gap-6 text-center text-[11px] font-medium text-ink/55"
          style={{ gridTemplateColumns: `repeat(${colCount}, minmax(0, 1fr))` }}
        >
          {months.map((m) => (
            <div key={m.label}>{m.label}</div>
          ))}
        </div>

        {/* Net-by-month strip */}
        <div
          className="ml-12 mt-3 grid gap-6"
          style={{ gridTemplateColumns: `repeat(${colCount}, minmax(0, 1fr))` }}
        >
          {months.map((m) => {
            const net = m.inflow - m.outflow;
            const positive = net >= 0;
            return (
              <div
                key={m.label}
                className={`rounded py-1 text-center text-[11px] font-semibold tnum ${
                  positive ? 'bg-moss/10 text-moss' : 'bg-ember/10 text-ember'
                }`}
              >
                {positive ? '+' : ''}
                {(net / 1000).toFixed(1)}k
              </div>
            );
          })}
        </div>
      </div>
    </section>
  );
}

function ChartTooltip({
  month,
  anchorIndex,
  total,
}: {
  month: CashFlowMonth;
  anchorIndex: number;
  total: number;
}) {
  const net = month.inflow - month.outflow;
  // Position centered on the column. The grid starts at ml-12 (3rem) from the
  // left of the relative container; each column center is at 3rem + (i+0.5)/n * (100%-3rem).
  const leftCalc = `calc(3rem + (100% - 3rem) * ${(anchorIndex + 0.5) / total})`;
  return (
    <div
      className="pointer-events-none absolute z-20 -translate-x-1/2 rounded-md bg-ink px-3 py-2 text-fog shadow-card"
      style={{ left: leftCalc, top: '0', transform: 'translateX(-50%) translateY(-108%)' }}
    >
      <div className="text-[10px] uppercase tracking-[0.16em] text-fog/60">{month.label}</div>
      <div className="mt-1 grid grid-cols-2 gap-x-3 gap-y-0.5 text-[11px]">
        <span className="text-fog/70">Inflow</span>
        <span className="text-right tnum text-moss">
          ${month.inflow.toLocaleString()}
        </span>
        <span className="text-fog/70">Outflow</span>
        <span className="text-right tnum text-clay">
          ${month.outflow.toLocaleString()}
        </span>
        <span className="border-t border-fog/15 pt-1 text-fog/70">Net</span>
        <span
          className={`border-t border-fog/15 pt-1 text-right tnum ${
            net >= 0 ? 'text-moss' : 'text-ember'
          }`}
        >
          {net >= 0 ? '+' : ''}${net.toLocaleString()}
        </span>
      </div>
    </div>
  );
}

function GatesPanel({ gates }: { gates: LiquidityGate[] }) {
  return (
    <section className="rounded-xl border border-ink/8 bg-white shadow-card">
      <div className="flex items-center justify-between px-5 pt-5 pb-3">
        <div>
          <div className="text-[10px] font-medium uppercase tracking-[0.18em] text-ink/45">
            Liquidity gates
          </div>
          <div className="mt-0.5 text-[15px] font-semibold text-ink">
            Constraint-first reserves
          </div>
        </div>
      </div>
      <div className="space-y-2 px-5 pb-5">
        {gates.map((gate) => (
          <GateStrip key={gate.gateKey} gate={gate} />
        ))}
        <RothProjectionSlot />
      </div>
    </section>
  );
}

function GateStrip({ gate }: { gate: LiquidityGate }) {
  const rawProgress = gate.targetAmount === 0 ? 0 : gate.currentAmount / gate.targetAmount;
  const progress = Math.min(rawProgress, 1);
  const complete = rawProgress >= 1;
  return (
    <div className="rounded-lg border border-ink/8 bg-paper/50 px-3.5 py-3">
      <div className="flex items-center gap-3">
        <div className="min-w-0 flex-1">
          <div className="text-[13px] font-medium leading-tight text-ink">{gate.label}</div>
          <div className="text-[10px] uppercase tracking-[0.14em] text-ink/45">
            Due {gate.targetDate}
          </div>
        </div>
        <div className="shrink-0 text-right">
          <div className="text-[11px] text-ink/55 tnum">
            {currency(gate.currentAmount)} / {currency(gate.targetAmount)}
          </div>
          <div
            className={`text-[13px] font-semibold tnum ${complete ? 'text-moss' : 'text-tide'}`}
          >
            {percent(progress)}
          </div>
        </div>
      </div>
      <div className="mt-2 h-1 w-full overflow-hidden rounded-full bg-ink/[0.06]">
        <div
          className={`h-full rounded-full ${complete ? 'bg-moss' : 'bg-tide'}`}
          style={{ width: `${progress * 100}%` }}
        />
      </div>
    </div>
  );
}

function RothProjectionSlot() {
  return (
    <div className="rounded-lg border border-dashed border-ink/15 bg-paper/30 px-3.5 py-3">
      <div className="flex items-center gap-2">
        <div className="min-w-0 flex-1">
          <div className="text-[12px] font-medium leading-tight text-ink/55">
            Roth re-engagement readiness
          </div>
          <div className="text-[10px] uppercase tracking-[0.14em] text-ink/35">
            Forward projection
          </div>
        </div>
        <span className="shrink-0 rounded-full bg-ink/[0.04] px-2 py-0.5 text-[10px] text-ink/40">
          Coming soon
        </span>
      </div>
    </div>
  );
}

function LeakageSection({
  categories,
  onOpenDrawer,
}: {
  categories: LeakageCategory[];
  onOpenDrawer: (category: { key: string; name: string }) => void;
}) {
  return (
    <section className="mt-5 rounded-xl border border-ink/8 bg-white shadow-card">
      <div className="flex items-center justify-between px-5 pt-5 pb-3">
        <div>
          <div className="text-[10px] font-medium uppercase tracking-[0.18em] text-ember">
            Lifestyle leakage
          </div>
          <div className="mt-0.5 text-[15px] font-semibold text-ink">
            Categories tracking above cap
          </div>
        </div>
      </div>
      <div className="grid grid-cols-1 gap-3 px-5 pb-5 md:grid-cols-3">
        {categories.map((category) => (
          <LeakageCard
            key={category.name}
            category={category}
            onOpenDrawer={onOpenDrawer}
          />
        ))}
      </div>
    </section>
  );
}

function LeakageCard({
  category,
  onOpenDrawer,
}: {
  category: LeakageCategory;
  onOpenDrawer: (category: { key: string; name: string }) => void;
}) {
  const ratio = category.cap === 0 ? 0 : category.monthlyBurn / category.cap;
  const overBy = category.monthlyBurn - category.cap;
  const overage = ratio > 1;
  const capPct = overage ? (category.cap / category.monthlyBurn) * 100 : Math.min(ratio * 100, 100);
  const overPct = overage ? 100 - capPct : 0;
  const overPercent = Math.round((ratio - 1) * 100);

  return (
    <div className="rounded-lg border border-ink/8 bg-paper/50 p-4">
      <div className="flex items-start justify-between gap-2">
        <p className="text-[13px] font-medium leading-snug text-ink">{category.name}</p>
        {overage ? (
          <span className="shrink-0 rounded-full bg-ember/10 px-2 py-0.5 text-[11px] font-semibold text-ember tnum">
            +{overPercent}%
          </span>
        ) : null}
      </div>

      <div className="relative mt-4 h-2 w-full overflow-hidden rounded-full bg-ink/[0.06]">
        <div className="flex h-full w-full">
          <div className="h-full bg-clay" style={{ width: `${capPct}%` }} />
          <div className="h-full bg-ember" style={{ width: `${overPct}%` }} />
        </div>
      </div>
      {overage ? (
        <div className="relative -mt-3 mb-1 h-3">
          <div
            className="absolute top-0 h-3 w-px bg-ink/35"
            style={{ left: `${capPct}%` }}
          />
        </div>
      ) : null}

      <div className="mt-1 flex items-center justify-between text-[11px] text-ink/55 tnum">
        <span>
          <b className="font-semibold text-ink">{currency(category.monthlyBurn)}</b> burn
        </span>
        <span>
          {currency(category.cap)} cap
          {overage ? <span className="text-ember"> · +{currency(overBy)} over</span> : null}
        </span>
      </div>

      {category.categoryKey && (
        <button
          type="button"
          onClick={() => onOpenDrawer({ key: category.categoryKey!, name: category.name })}
          className="mt-3 inline-flex items-center gap-1 text-[11px] font-medium text-tide hover:text-ink"
        >
          See transactions
          <svg
            xmlns="http://www.w3.org/2000/svg"
            viewBox="0 0 16 16"
            width="10"
            height="10"
            fill="none"
            stroke="currentColor"
            strokeWidth="2"
            strokeLinecap="round"
            strokeLinejoin="round"
            aria-hidden
          >
            <path d="M3 8h10M9 4l4 4-4 4" />
          </svg>
        </button>
      )}
    </div>
  );
}

// ── Capital-Plan Feasibility ────────────────────────────────────────────────

const FEASIBILITY_BADGE_STYLES: Record<FeasibilityStatus, string> = {
  green: 'bg-moss/12 text-moss',
  yellow: 'bg-clay/12 text-clay',
  red: 'bg-ember/12 text-ember',
  unknown: 'bg-ink/[0.06] text-ink/55',
};

const FEASIBILITY_BADGE_LABEL: Record<FeasibilityStatus, string> = {
  green: 'Green',
  yellow: 'Yellow',
  red: 'Red',
  unknown: 'No signal',
};

const FEASIBILITY_DOT_STYLES: Record<FeasibilityStatus, string> = {
  green: 'bg-moss',
  yellow: 'bg-clay',
  red: 'bg-ember',
  unknown: 'bg-ink/25',
};

function FeasibilitySection({ assessment }: { assessment: FeasibilityAssessment }) {
  return (
    <section className="mb-5 rounded-xl border border-ink/8 bg-white shadow-card">
      <div className="flex items-start justify-between gap-4 px-5 pt-5 pb-3">
        <div className="min-w-0">
          <div className="text-[10px] font-medium uppercase tracking-[0.18em] text-ink/45">
            Capital-plan feasibility · v1 heuristic
          </div>
          <div className="mt-0.5 text-[15px] font-semibold text-ink">
            {assessment.headline}
          </div>
          <p className="mt-1 text-[11px] leading-relaxed text-ink/55">
            Heuristic combination of cash-flow, HYSA gate, and leakage signals.
            Master-index §10 capital-plan tests (401(k)/HSA/Roth/rental net)
            need paystub extraction, the normalization layer, and the forward
            projection engine before they can drive this badge.
          </p>
        </div>
        <span
          className={cn(
            'shrink-0 rounded-full px-3 py-1 text-[12px] font-semibold uppercase tracking-[0.16em]',
            FEASIBILITY_BADGE_STYLES[assessment.status],
          )}
        >
          {FEASIBILITY_BADGE_LABEL[assessment.status]}
        </span>
      </div>
      <ul className="grid grid-cols-1 gap-2 px-5 pb-5 md:grid-cols-3">
        {assessment.drivers.map((driver) => (
          <li
            key={driver.label}
            className="rounded-lg border border-ink/8 bg-paper/50 p-3"
          >
            <div className="flex items-center gap-2">
              <span
                aria-hidden
                className={cn(
                  'inline-block h-2 w-2 rounded-full',
                  FEASIBILITY_DOT_STYLES[driver.status],
                )}
              />
              <span className="text-[11px] font-medium uppercase tracking-[0.12em] text-ink/55">
                {driver.label}
              </span>
            </div>
            <p className="mt-1.5 text-[12px] leading-relaxed text-ink/75">
              {driver.detail}
            </p>
          </li>
        ))}
      </ul>
    </section>
  );
}

// ── HYSA trajectory ──────────────────────────────────────────────────────────

const HYSA_CHART_WIDTH = 800;
const HYSA_CHART_HEIGHT = 220;
const HYSA_CHART_PADDING = { top: 16, right: 16, bottom: 28, left: 56 };

function formatGateDate(iso: string): string {
  // YYYY-MM-DD → "May 2026"
  const ts = Date.parse(`${iso}T00:00:00Z`);
  if (Number.isNaN(ts)) return iso;
  return new Date(ts).toLocaleDateString('en-US', {
    month: 'short',
    year: 'numeric',
    timeZone: 'UTC',
  });
}

function HysaTrajectorySection({ trajectory }: { trajectory: HysaTrajectory }) {
  const { points, targetAmount, accountLabel } = trajectory;
  const latest = points[points.length - 1] ?? null;
  const progressPct = latest
    ? Math.min(1, Math.max(0, latest.balance / targetAmount))
    : 0;

  return (
    <section className="mt-5 rounded-xl border border-ink/8 bg-white shadow-card">
      <div className="flex items-start justify-between gap-4 px-5 pt-5 pb-3">
        <div>
          <div className="text-[10px] font-medium uppercase tracking-[0.18em] text-ink/45">
            HYSA trajectory
          </div>
          <div className="mt-0.5 text-[15px] font-semibold text-ink">
            {accountLabel ?? 'High-yield savings'} progress toward {currency(targetAmount)} gate
          </div>
          <p className="mt-1 text-[11px] leading-relaxed text-ink/55">
            Sourced from <code className="rounded bg-ink/[0.06] px-1 py-0.5 text-[10px] text-ink/75">reconciliation_periods</code>{' '}
            closing balances. Projection is a least-squares linear fit;
            extrapolation assumes the same monthly trajectory holds.
          </p>
        </div>
        {latest ? (
          <div className="shrink-0 rounded-lg bg-ink/[0.04] px-3 py-1.5 text-right">
            <div className="text-[10px] uppercase tracking-[0.14em] text-ink/45">
              Latest balance
            </div>
            <div className="text-[14px] font-semibold tnum text-ink">
              {currency(latest.balance)}
            </div>
            <div className="mt-0.5 text-[10px] text-ink/45 tnum">
              {percent(progressPct)} of gate · as of {latest.date}
            </div>
          </div>
        ) : null}
      </div>

      <div className="px-5 pb-5">
        {points.length < 2 ? (
          <div className="rounded-lg border border-dashed border-ink/15 bg-paper/40 p-4 text-[12px] leading-relaxed text-ink/55">
            {points.length === 0
              ? 'No HYSA reconciliation periods yet. Run the '
              : 'Only one HYSA reconciliation period so far. Run the '}
            <code className="rounded bg-ink/[0.06] px-1 py-0.5 text-[11px] text-ink/75">
              reconcile_periods
            </code>{' '}
            MCP tool against the Ally HYSA monthly statements to populate the
            trajectory.
          </div>
        ) : (
          <>
            <HysaChart trajectory={trajectory} />
            <HysaProjectionSummary trajectory={trajectory} />
          </>
        )}
      </div>
    </section>
  );
}

function HysaProjectionSummary({ trajectory }: { trajectory: HysaTrajectory }) {
  const { projection, targetAmount } = trajectory;
  if (!projection) {
    return (
      <p className="mt-3 text-[11px] leading-relaxed text-ink/55">
        Need at least two periods to project a gate date.
      </p>
    );
  }
  if (projection.gateDate === null) {
    return (
      <p className="mt-3 text-[11px] leading-relaxed text-ember">
        Trajectory is flat or shrinking ({currency(projection.dailyRate * 30)} / month) — the
        gate is not reachable on the current trend.
      </p>
    );
  }
  const monthly = projection.dailyRate * 30;
  return (
    <div className="mt-3 grid grid-cols-2 gap-3 text-[11px] sm:grid-cols-3">
      <div>
        <div className="text-[10px] uppercase tracking-[0.14em] text-ink/45">
          Projected gate date
        </div>
        <div className="mt-0.5 text-[13px] font-semibold text-ink">
          {formatGateDate(projection.gateDate)}
        </div>
        <div className="mt-0.5 text-[10px] text-ink/45 tnum">{projection.gateDate}</div>
      </div>
      <div>
        <div className="text-[10px] uppercase tracking-[0.14em] text-ink/45">
          Implied monthly add
        </div>
        <div className="mt-0.5 text-[13px] font-semibold text-ink tnum">
          {monthly >= 0 ? '+' : ''}
          {currency(monthly)}
        </div>
      </div>
      <div>
        <div className="text-[10px] uppercase tracking-[0.14em] text-ink/45">
          Gate target
        </div>
        <div className="mt-0.5 text-[13px] font-semibold text-ink tnum">
          {currency(targetAmount)}
        </div>
      </div>
    </div>
  );
}

function HysaChart({ trajectory }: { trajectory: HysaTrajectory }) {
  const { points, targetAmount, projection } = trajectory;
  if (points.length < 2) return null;

  const firstTs = Date.parse(`${points[0].date}T00:00:00Z`);
  const lastObservedTs = Date.parse(`${points[points.length - 1].date}T00:00:00Z`);
  const projectedTs = projection?.gateDate
    ? Date.parse(`${projection.gateDate}T00:00:00Z`)
    : null;
  const lastTs = projectedTs && projectedTs > lastObservedTs ? projectedTs : lastObservedTs;
  const tsRange = Math.max(1, lastTs - firstTs);

  const maxBalance = Math.max(targetAmount, ...points.map((p) => p.balance));
  // Round up to a tidy axis ceiling.
  const chartCeiling = Math.ceil(maxBalance / 10_000) * 10_000;
  const chartMin = 0;
  const chartRange = chartCeiling - chartMin;

  const innerWidth = HYSA_CHART_WIDTH - HYSA_CHART_PADDING.left - HYSA_CHART_PADDING.right;
  const innerHeight = HYSA_CHART_HEIGHT - HYSA_CHART_PADDING.top - HYSA_CHART_PADDING.bottom;

  const xForTs = (ts: number) =>
    HYSA_CHART_PADDING.left + ((ts - firstTs) / tsRange) * innerWidth;
  const yForBalance = (balance: number) =>
    HYSA_CHART_PADDING.top + (1 - (balance - chartMin) / chartRange) * innerHeight;

  const actualPath = points
    .map((p, i) => {
      const ts = Date.parse(`${p.date}T00:00:00Z`);
      const command = i === 0 ? 'M' : 'L';
      return `${command}${xForTs(ts).toFixed(2)},${yForBalance(p.balance).toFixed(2)}`;
    })
    .join(' ');

  const targetY = yForBalance(targetAmount);

  const projectionPath =
    projection && projection.gateDate && projectedTs && projectedTs > lastObservedTs
      ? `M${xForTs(lastObservedTs).toFixed(2)},${yForBalance(points[points.length - 1].balance).toFixed(2)} L${xForTs(projectedTs).toFixed(2)},${targetY.toFixed(2)}`
      : null;

  const yTicks = [chartCeiling, chartCeiling * 0.75, chartCeiling * 0.5, chartCeiling * 0.25, 0];

  return (
    <div className="overflow-x-auto">
      <svg
        viewBox={`0 0 ${HYSA_CHART_WIDTH} ${HYSA_CHART_HEIGHT}`}
        className="block w-full min-w-[560px]"
        role="img"
        aria-label="HYSA balance trajectory chart"
      >
        {/* Y-axis gridlines + labels */}
        {yTicks.map((tick) => {
          const y = yForBalance(tick);
          return (
            <g key={tick}>
              <line
                x1={HYSA_CHART_PADDING.left}
                x2={HYSA_CHART_WIDTH - HYSA_CHART_PADDING.right}
                y1={y}
                y2={y}
                stroke="rgba(15,23,42,0.06)"
                strokeWidth={1}
              />
              <text
                x={HYSA_CHART_PADDING.left - 6}
                y={y + 3}
                textAnchor="end"
                className="fill-ink/45 text-[10px] tnum"
              >
                ${Math.round(tick / 1000)}k
              </text>
            </g>
          );
        })}

        {/* $80K gate horizontal line */}
        <line
          x1={HYSA_CHART_PADDING.left}
          x2={HYSA_CHART_WIDTH - HYSA_CHART_PADDING.right}
          y1={targetY}
          y2={targetY}
          stroke="rgba(212,131,55,0.5)"
          strokeDasharray="4 4"
          strokeWidth={1}
        />
        <text
          x={HYSA_CHART_WIDTH - HYSA_CHART_PADDING.right - 4}
          y={targetY - 4}
          textAnchor="end"
          className="fill-clay text-[10px] font-medium tnum"
        >
          {currency(targetAmount)} gate
        </text>

        {/* Projection segment (dashed) */}
        {projectionPath ? (
          <path
            d={projectionPath}
            fill="none"
            stroke="rgba(85,107,47,0.5)"
            strokeDasharray="5 4"
            strokeWidth={2}
          />
        ) : null}

        {/* Actual trajectory line */}
        <path d={actualPath} fill="none" stroke="rgb(85,107,47)" strokeWidth={2} />

        {/* Data points */}
        {points.map((p) => {
          const ts = Date.parse(`${p.date}T00:00:00Z`);
          return (
            <circle
              key={p.date}
              cx={xForTs(ts)}
              cy={yForBalance(p.balance)}
              r={3}
              className="fill-moss"
            >
              <title>{`${p.date}: ${currency(p.balance)}`}</title>
            </circle>
          );
        })}

        {/* Projected gate marker */}
        {projection?.gateDate && projectedTs && projectedTs > lastObservedTs ? (
          <circle
            cx={xForTs(projectedTs)}
            cy={targetY}
            r={4}
            className="fill-clay"
          >
            <title>{`Projected gate: ${projection.gateDate}`}</title>
          </circle>
        ) : null}

        {/* X-axis labels: first and last observed dates, plus projection */}
        <text
          x={xForTs(firstTs)}
          y={HYSA_CHART_HEIGHT - 6}
          textAnchor="start"
          className="fill-ink/55 text-[10px] tnum"
        >
          {points[0].date}
        </text>
        <text
          x={xForTs(lastObservedTs)}
          y={HYSA_CHART_HEIGHT - 6}
          textAnchor="middle"
          className="fill-ink/55 text-[10px] tnum"
        >
          {points[points.length - 1].date}
        </text>
        {projection?.gateDate && projectedTs && projectedTs > lastObservedTs ? (
          <text
            x={xForTs(projectedTs)}
            y={HYSA_CHART_HEIGHT - 6}
            textAnchor="end"
            className="fill-clay text-[10px] tnum"
          >
            {projection.gateDate}
          </text>
        ) : null}
      </svg>
    </div>
  );
}

// ── Subscription audit ───────────────────────────────────────────────────────

const HOUSEHOLD_ROLE_STYLES: Record<string, string> = {
  jeff: 'bg-tide/12 text-tide',
  ashley: 'bg-clay/14 text-clay',
  joint: 'bg-moss/12 text-moss',
};

function subscriptionTotalBurn(entries: SubscriptionAuditEntry[]): number {
  return entries.reduce((total, entry) => total + entry.monthlyBurn, 0);
}

function SubscriptionAuditSection({ entries }: { entries: SubscriptionAuditEntry[] }) {
  const totalBurn = subscriptionTotalBurn(entries);
  return (
    <section className="mt-5 rounded-xl border border-ink/8 bg-white shadow-card">
      <div className="flex items-start justify-between gap-4 px-5 pt-5 pb-3">
        <div>
          <div className="text-[10px] font-medium uppercase tracking-[0.18em] text-ink/45">
            Subscriptions / app audit
          </div>
          <div className="mt-0.5 text-[15px] font-semibold text-ink">
            Recurring software & streaming charges
          </div>
          <p className="mt-1 text-[11px] leading-relaxed text-ink/55">
            Sourced from classified <code className="rounded bg-ink/[0.06] px-1 py-0.5 text-[10px] text-ink/75">fixed_obligation</code> outflows with subcategory{' '}
            <code className="rounded bg-ink/[0.06] px-1 py-0.5 text-[10px] text-ink/75">subscriptions_apps</code> or{' '}
            <code className="rounded bg-ink/[0.06] px-1 py-0.5 text-[10px] text-ink/75">streaming</code>. Ordered by monthly burn; reclassify in the register to remove a row.
          </p>
        </div>
        {entries.length > 0 ? (
          <div className="shrink-0 rounded-lg bg-ink/[0.04] px-3 py-1.5 text-right">
            <div className="text-[10px] uppercase tracking-[0.14em] text-ink/45">
              Monthly burn
            </div>
            <div className="text-[14px] font-semibold tnum text-ink">
              {currency(totalBurn)}
            </div>
          </div>
        ) : null}
      </div>
      {entries.length === 0 ? (
        <div className="px-5 pb-5">
          <div className="rounded-lg border border-dashed border-ink/15 bg-paper/40 p-4 text-[12px] leading-relaxed text-ink/55">
            No recurring software or streaming charges classified yet. Build
            classifier rules with subcategory{' '}
            <code className="rounded bg-ink/[0.06] px-1 py-0.5 text-[11px] text-ink/75">
              subscriptions_apps
            </code>{' '}
            or{' '}
            <code className="rounded bg-ink/[0.06] px-1 py-0.5 text-[11px] text-ink/75">
              streaming
            </code>{' '}
            via the{' '}
            <code className="rounded bg-ink/[0.06] px-1 py-0.5 text-[11px] text-ink/75">
              upsert_classification_rule
            </code>{' '}
            MCP tool.
          </div>
        </div>
      ) : (
        <div className="overflow-x-auto">
          <table className="w-full min-w-[720px] text-[12px]">
            <thead>
              <tr className="border-y border-ink/8 bg-paper/60 text-left text-[10px] font-medium uppercase tracking-[0.14em] text-ink/45">
                <th className="px-4 py-2">Merchant</th>
                <th className="px-4 py-2">Owner</th>
                <th className="px-4 py-2 text-right">Charges</th>
                <th className="px-4 py-2 text-right">Months</th>
                <th className="px-4 py-2 text-right">Avg / charge</th>
                <th className="px-4 py-2 text-right">Monthly burn</th>
                <th className="w-[100px] px-4 py-2">Last charged</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-ink/[0.05]">
              {entries.map((entry) => (
                <SubscriptionAuditRow
                  key={`${entry.merchant}|${entry.subcategory ?? ''}|${entry.householdRole ?? ''}`}
                  entry={entry}
                />
              ))}
            </tbody>
          </table>
        </div>
      )}
    </section>
  );
}

function SubscriptionAuditRow({ entry }: { entry: SubscriptionAuditEntry }) {
  const role = entry.householdRole?.toLowerCase() ?? null;
  const roleClass = role ? HOUSEHOLD_ROLE_STYLES[role] ?? 'bg-ink/[0.05] text-ink/60' : 'bg-ink/[0.05] text-ink/45';
  return (
    <tr className="hover:bg-paper/40 transition-colors">
      <td className="px-4 py-2.5 text-ink">
        <div className="max-w-[260px] truncate font-medium">{entry.merchant}</div>
        {entry.subcategory ? (
          <div className="mt-0.5 font-mono text-[10px] uppercase tracking-[0.12em] text-ink/45">
            {entry.subcategory}
          </div>
        ) : null}
      </td>
      <td className="px-4 py-2.5">
        <span
          className={cn(
            'inline-block rounded-full px-2 py-0.5 text-[10px] font-medium uppercase tracking-[0.12em]',
            roleClass,
          )}
        >
          {role ?? 'unassigned'}
        </span>
      </td>
      <td className="whitespace-nowrap px-4 py-2.5 text-right tnum text-ink/65">
        {entry.chargeCount}
      </td>
      <td className="whitespace-nowrap px-4 py-2.5 text-right tnum text-ink/65">
        {entry.monthCount}
      </td>
      <td className="whitespace-nowrap px-4 py-2.5 text-right font-medium tnum text-ink">
        {currency(entry.avgAmount)}
      </td>
      <td className="whitespace-nowrap px-4 py-2.5 text-right font-semibold tnum text-ink">
        {currency(entry.monthlyBurn)}
      </td>
      <td className="whitespace-nowrap px-4 py-2.5 font-mono text-[11px] tnum text-ink/55">
        {entry.lastChargedOn}
      </td>
    </tr>
  );
}

// ── Transaction Register ─────────────────────────────────────────────────────

const DIRECTION_OPTIONS: { value: TransactionDirectionFilter; label: string }[] = [
  { value: 'outflow', label: 'Outflows' },
  { value: 'inflow', label: 'Inflows' },
  { value: 'all', label: 'All' },
];

function TransactionRegisterSection() {
  const [page, setPage] = useState(0);
  const [direction, setDirection] = useState<TransactionDirectionFilter>('outflow');

  const query = useTransactionRegister({ page, direction });

  const handleDirectionChange = (next: TransactionDirectionFilter) => {
    setPage(0);
    setDirection(next);
  };

  const data = query.data;
  const totalPages = data ? Math.max(1, Math.ceil(data.total / data.pageSize)) : 1;

  return (
    <section className="mt-5 rounded-xl border border-ink/8 bg-white shadow-card">
      <div className="flex items-start justify-between gap-4 px-5 pt-5 pb-3">
        <div>
          <div className="text-[10px] font-medium uppercase tracking-[0.18em] text-ink/45">
            Transaction register
          </div>
          <div className="mt-0.5 text-[15px] font-semibold text-ink">
            Line-item ledger — most recent first
          </div>
          <p className="mt-1 text-[11px] leading-relaxed text-ink/55">
            Transfers excluded. Unclassified rows show{' '}
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
              onClick={() => handleDirectionChange(value)}
              className={cn(
                'rounded-md px-2.5 py-1 transition-colors',
                direction === value
                  ? 'bg-white font-medium text-ink shadow-card'
                  : 'text-ink/60 hover:text-ink',
              )}
            >
              {label}
            </button>
          ))}
        </div>
      </div>

      {query.isLoading ? (
        <div className="px-5 pb-5 text-[13px] text-ink/50">Loading transactions…</div>
      ) : query.isError || !data ? (
        <div className="px-5 pb-5 text-[13px] text-ember">
          Unable to load transactions.
        </div>
      ) : data.rows.length === 0 ? (
        <div className="px-5 pb-5">
          <div className="rounded-lg border border-dashed border-ink/15 bg-paper/40 p-4 text-[12px] leading-relaxed text-ink/55">
            No transactions found. Ingest documents via the MCP{' '}
            <code className="rounded bg-ink/[0.06] px-1 text-[11px] text-ink/75">
              ingest_documents
            </code>{' '}
            tool to populate.
          </div>
        </div>
      ) : (
        <>
          <div className="overflow-x-auto">
            <table className="w-full min-w-[640px] text-[12px]">
              <thead>
                <tr className="border-y border-ink/8 bg-paper/60 text-left text-[10px] font-medium uppercase tracking-[0.14em] text-ink/45">
                  <th className="w-[88px] px-4 py-2">Date</th>
                  <th className="px-4 py-2">Account</th>
                  <th className="px-4 py-2">Merchant / Description</th>
                  <th className="px-4 py-2">Category</th>
                  <th className="w-[96px] px-4 py-2 text-right">Amount</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-ink/[0.05]">
                {data.rows.map((tx) => (
                  <TransactionRow key={tx.id} tx={tx} />
                ))}
              </tbody>
            </table>
          </div>

          <div className="flex items-center justify-between border-t border-ink/8 px-5 py-3">
            <span className="text-[11px] text-ink/45 tnum">
              {data.total.toLocaleString()} transaction{data.total === 1 ? '' : 's'} ·
              page {page + 1} of {totalPages}
            </span>
            <div className="flex items-center gap-1">
              <PaginationButton
                disabled={page === 0}
                onClick={() => setPage((p) => Math.max(0, p - 1))}
              >
                ← Prev
              </PaginationButton>
              <PaginationButton
                disabled={page >= totalPages - 1}
                onClick={() => setPage((p) => Math.min(totalPages - 1, p + 1))}
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

type EditField = 'merchant' | 'category';

function TransactionRow({ tx }: { tx: Transaction }) {
  const queryClient = useQueryClient();
  const [editField, setEditField] = useState<EditField | null>(null);
  const [draft, setDraft] = useState('');

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
                    merchantNormalized:
                      params.merchantNormalized ?? row.merchantNormalized,
                    primaryCategory:
                      params.primaryCategory ?? row.primaryCategory,
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
    },
  });

  const startEdit = (field: EditField) => {
    setEditField(field);
    setDraft(field === 'merchant' ? (tx.merchantNormalized ?? '') : tx.primaryCategory);
  };

  const cancelEdit = () => {
    setEditField(null);
    setDraft('');
  };

  const saveEdit = () => {
    if (!editField) return;
    const trimmed = draft.trim();
    const current =
      editField === 'merchant'
        ? (tx.merchantNormalized ?? '')
        : tx.primaryCategory;
    if (!trimmed || trimmed === current) {
      cancelEdit();
      return;
    }
    mutation.mutate({
      transactionId: tx.id,
      accountId: tx.accountId,
      occurredOn: tx.occurredOn,
      amount: tx.amount,
      descriptionRaw: tx.descriptionRaw,
      ...(editField === 'merchant'
        ? { merchantNormalized: trimmed }
        : { primaryCategory: trimmed }),
    });
    cancelEdit();
  };

  const handleKeyDown = (event: React.KeyboardEvent<HTMLInputElement>) => {
    if (event.key === 'Enter') {
      event.preventDefault();
      saveEdit();
    } else if (event.key === 'Escape') {
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

  return (
    <tr className="hover:bg-paper/40 transition-colors">
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
            value={draft}
            onChange={(event) => setDraft(event.target.value)}
            onKeyDown={handleKeyDown}
            onBlur={saveEdit}
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
          <input
            autoFocus
            value={draft}
            onChange={(event) => setDraft(event.target.value)}
            onKeyDown={handleKeyDown}
            onBlur={saveEdit}
            placeholder="primary_category"
            className={cn(inputClasses, 'max-w-[200px] font-mono text-[11px]')}
            aria-label="Edit category"
          />
        ) : (
          <button
            type="button"
            onClick={() => startEdit('category')}
            title="Click to edit category"
            className={cn(
              'inline-block rounded px-1.5 py-0.5 text-[10px] font-medium hover:ring-1 hover:ring-tide/40 focus:outline-none focus:ring-1 focus:ring-tide/60',
              isUnclassified
                ? 'bg-clay/12 text-clay'
                : 'bg-ink/[0.05] text-ink/65',
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
