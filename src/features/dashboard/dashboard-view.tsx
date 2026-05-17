import type { UseQueryResult } from '@tanstack/react-query';
import { cn, currency, percent } from '../../lib/utils';
import type {
  CashFlowMonth,
  DashboardSnapshot,
  LeakageCategory,
  LiquidityGate,
  ReconciliationPeriod,
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
}

export function DashboardView({ query }: DashboardViewProps) {
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
          <RangeButton active>YTD</RangeButton>
          <RangeButton>12 mo</RangeButton>
          <RangeButton>All</RangeButton>
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

      {period.varianceExplanation ? (
        <p className="mt-2 text-[11px] italic leading-relaxed text-ink/65">
          {period.varianceExplanation}
        </p>
      ) : null}
    </div>
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
}: {
  children: React.ReactNode;
  active?: boolean;
}) {
  return (
    <button
      type="button"
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
