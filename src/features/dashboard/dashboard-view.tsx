import { useState } from 'react';
import { useMutation, useQueryClient, type UseQueryResult } from '@tanstack/react-query';
import { cn, currency, percent } from '../../lib/utils';
import { useTransactionRegister } from '../../hooks/use-transaction-register';
import {
  updateVarianceExplanation,
  upsertTransactionOverride,
  type UpdateVarianceExplanationParams,
  type UpsertTransactionOverrideParams,
} from '../../services/sqlite';
import type {
  CashFlowMonth,
  DashboardRange,
  DashboardSnapshot,
  LeakageCategory,
  LiquidityGate,
  ReconciliationPeriod,
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

export function DashboardView({ query, activeRange, onRangeChange }: DashboardViewProps) {
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

  const { months, gates, leakageCategories, reconciliations } = query.data;
  const totalInflow = months.reduce((total, m) => total + m.inflow, 0);
  const totalOutflow = months.reduce((total, m) => total + m.outflow, 0);
  const netYtd = totalInflow - totalOutflow;
  const avgInflow = months.length === 0 ? 0 : totalInflow / months.length;
  const avgOutflow = months.length === 0 ? 0 : totalOutflow / months.length;
  const primaryGate = gates[0];
  const gateProgress =
    primaryGate && primaryGate.targetAmount > 0
      ? Math.min(primaryGate.currentAmount / primaryGate.targetAmount, 1)
      : 0;

  const dataMax = Math.max(0, ...months.flatMap((m) => [m.inflow, m.outflow]));
  const chartMax = Math.max(20000, Math.ceil(dataMax / 5000) * 5000);
  const yAxisLabels = [chartMax, chartMax * 0.75, chartMax * 0.5, chartMax * 0.25, 0];

  return (
    <div>
      <div className="mb-5 flex items-end justify-between gap-4">
        <div className="min-w-0">
          <div className="text-[11px] font-medium uppercase tracking-[0.18em] text-ink/45">
            Phase 2 · Reconstruction
          </div>
          <h1 className="mt-1 text-[22px] font-semibold tracking-tight text-ink">
            Cash-flow dashboard
          </h1>
          <p className="mt-1 max-w-2xl text-[13px] leading-relaxed text-ink/60">
            Reconstructed from the local transactions table. Transfers excluded.
          </p>
        </div>
        <div className="flex shrink-0 items-center gap-1 rounded-lg bg-ink/[0.05] p-0.5 text-[12px]">
          <RangeButton active={activeRange === 'ytd'} onClick={() => onRangeChange('ytd')}>YTD</RangeButton>
          <RangeButton active={activeRange === '12mo'} onClick={() => onRangeChange('12mo')}>12 mo</RangeButton>
          <RangeButton active={activeRange === 'all'} onClick={() => onRangeChange('all')}>All</RangeButton>
        </div>
      </div>

      <div className="mb-6 grid grid-cols-2 gap-3 lg:grid-cols-4">
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
          <div className="mt-1 text-[11px] text-ink/50">Over {months.length} months</div>
        </KpiTile>
        <KpiTile label="Avg monthly outflow">
          <div className="mt-1.5 text-[24px] font-semibold tnum text-ink">{currency(avgOutflow)}</div>
          <div className="mt-1 text-[11px] text-ink/50">Over {months.length} months</div>
        </KpiTile>
        <KpiTile label="HYSA gate">
          {primaryGate ? (
            <>
              <div className="mt-1.5 flex items-baseline gap-2">
                <span className="text-[24px] font-semibold tnum">
                  {percent(gateProgress)}
                </span>
                <span className="text-[11px] text-ink/45 tnum">
                  {currency(primaryGate.currentAmount)} / {currency(primaryGate.targetAmount)}
                </span>
              </div>
              <div className="mt-2 h-1 w-full rounded-full bg-ink/[0.06]">
                <div
                  className="h-full rounded-full bg-clay"
                  style={{ width: `${gateProgress * 100}%` }}
                />
              </div>
            </>
          ) : (
            <div className="mt-1.5 text-[13px] text-ink/45">No gates configured.</div>
          )}
        </KpiTile>
      </div>

      <div
        className="grid gap-5"
        style={{ gridTemplateColumns: 'minmax(0, 1.45fr) minmax(0, 1fr)' }}
      >
        <CashFlowChart months={months} chartMax={chartMax} yAxisLabels={yAxisLabels} />
        <GatesPanel gates={gates} />
      </div>

      <ReconciliationSection periods={reconciliations} />

      <LeakageSection categories={leakageCategories} />

      <TransactionRegisterSection />
    </div>
  );
}

function ReconciliationSection({ periods }: { periods: ReconciliationPeriod[] }) {
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
            explained) before downstream tabs are trusted.
          </p>
        </div>
      </div>
      <div className="grid grid-cols-1 gap-3 px-5 pb-5 md:grid-cols-2 xl:grid-cols-4">
        {periods.length === 0 ? (
          <div className="col-span-full rounded-lg border border-dashed border-ink/15 bg-paper/40 p-4 text-[12px] leading-relaxed text-ink/55">
            No reconciliation periods computed yet. Run the
            <code className="mx-1 rounded bg-ink/[0.06] px-1 py-0.5 text-[11px] text-ink/75">
              reconcile_periods
            </code>
            MCP tool to populate.
          </div>
        ) : (
          periods.map((period) => (
            <ReconciliationCard key={period.accountId} period={period} />
          ))
        )}
      </div>
    </section>
  );
}

function ReconciliationCard({ period }: { period: ReconciliationPeriod }) {
  const badge = varianceBadge(period.varianceAmount);
  const isLiability = period.accountType === 'credit_card';
  const closingLabel = isLiability ? 'amount owed' : 'balance';

  const badgeStyles: Record<typeof badge, string> = {
    green: 'bg-moss/12 text-moss',
    yellow: 'bg-clay/12 text-clay',
    red: 'bg-ember/12 text-ember',
    unknown: 'bg-ink/[0.06] text-ink/55',
  };

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
            badgeStyles[badge],
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
    </div>
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
          <div className="absolute inset-y-0 left-0 flex w-10 flex-col justify-between py-1 text-right text-[10px] text-ink/35 tnum">
            {yAxisLabels.map((value) => (
              <span key={value}>{formatAxisLabel(value)}</span>
            ))}
          </div>
          <div
            className="ml-12 grid gap-6"
            style={{ gridTemplateColumns: `repeat(${Math.max(months.length, 1)}, minmax(0, 1fr))` }}
          >
            {months.map((month) => (
              <MonthBars key={month.label} month={month} chartMax={chartMax} />
            ))}
          </div>
          <div className="ml-12 mt-2 h-px w-[calc(100%-3rem)] bg-ink/10" />
        </div>
        <div
          className="ml-12 mt-2 grid gap-6 text-center text-[11px] font-medium text-ink/55"
          style={{ gridTemplateColumns: `repeat(${Math.max(months.length, 1)}, minmax(0, 1fr))` }}
        >
          {months.map((m) => (
            <div key={m.label}>{m.label}</div>
          ))}
        </div>
      </div>
    </section>
  );
}

function MonthBars({ month, chartMax }: { month: CashFlowMonth; chartMax: number }) {
  const inflowHeight = chartMax === 0 ? 0 : (month.inflow / chartMax) * 160;
  const outflowHeight = chartMax === 0 ? 0 : (month.outflow / chartMax) * 160;
  return (
    <div className="flex h-48 items-end justify-center gap-2">
      <div className="flex flex-col items-center gap-1">
        <span className="text-[10px] font-medium text-ink/55 tnum">
          {(month.inflow / 1000).toFixed(1)}k
        </span>
        <div className="w-7 rounded-t-md bg-moss" style={{ height: `${inflowHeight}px` }} />
      </div>
      <div className="flex flex-col items-center gap-1">
        <span className="text-[10px] font-medium text-ink/55 tnum">
          {(month.outflow / 1000).toFixed(1)}k
        </span>
        <div className="w-7 rounded-t-md bg-clay" style={{ height: `${outflowHeight}px` }} />
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
      <div className="space-y-4 px-5 pb-5">
        {gates.map((gate) => (
          <GateCard key={gate.gateKey} gate={gate} />
        ))}
      </div>
    </section>
  );
}

function GateCard({ gate }: { gate: LiquidityGate }) {
  const rawProgress = gate.targetAmount === 0 ? 0 : gate.currentAmount / gate.targetAmount;
  const progress = Math.min(rawProgress, 1);
  const complete = rawProgress >= 1;
  return (
    <div className="rounded-lg border border-ink/8 bg-paper/50 p-3.5">
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0">
          <p className="text-[13px] font-medium leading-snug text-ink">{gate.label}</p>
          <p className="mt-0.5 text-[11px] uppercase tracking-[0.16em] text-ink/45">
            Due {gate.targetDate}
          </p>
        </div>
        <span
          className={cn(
            'shrink-0 rounded-full px-2 py-0.5 text-[11px] font-semibold tnum',
            complete ? 'bg-moss/12 text-moss' : 'bg-clay/12 text-clay',
          )}
        >
          {percent(progress)}
        </span>
      </div>
      <div className="mt-3 h-1.5 w-full rounded-full bg-ink/[0.06]">
        <div
          className={cn('h-full rounded-full', complete ? 'bg-moss' : 'bg-clay')}
          style={{ width: `${progress * 100}%` }}
        />
      </div>
      <div className="mt-2 flex items-center justify-between text-[11px] text-ink/55 tnum">
        <span>{currency(gate.currentAmount)} current</span>
        <span>{currency(gate.targetAmount)} target</span>
      </div>
    </div>
  );
}

function LeakageSection({ categories }: { categories: LeakageCategory[] }) {
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
          <LeakageCard key={category.name} category={category} />
        ))}
      </div>
    </section>
  );
}

function LeakageCard({ category }: { category: LeakageCategory }) {
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
    </div>
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
