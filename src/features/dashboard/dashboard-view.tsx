import type { UseQueryResult } from '@tanstack/react-query';
import { ArrowDownRight, ArrowUpRight, Vault } from 'lucide-react';
import { Badge } from '../../components/ui/badge';
import { Card } from '../../components/ui/card';
import { currency, percent } from '../../lib/utils';
import type { DashboardSnapshot } from './types';

interface DashboardViewProps {
  query: UseQueryResult<DashboardSnapshot, Error>;
}

export function DashboardView({ query }: DashboardViewProps) {
  if (query.isLoading) {
    return <Card className="text-sm text-ink/70">Preparing the local dashboard snapshot.</Card>;
  }

  if (query.isError || !query.data) {
    return <Card className="text-sm text-ember">Unable to load the cash-flow dashboard snapshot.</Card>;
  }

  const { months, gates, leakageCategories } = query.data;
  const chartMax = Math.max(...months.flatMap((month) => [month.inflow, month.outflow]));

  return (
    <div className="grid gap-6">
      <section className="grid gap-6 xl:grid-cols-[1.2fr,0.8fr]">
        <Card>
          <div className="flex items-start justify-between gap-6">
            <div>
              <Badge tone="info">Cash Flow</Badge>
              <h2 className="mt-3 text-2xl font-semibold text-ink">Inflow vs. outflow placeholder model</h2>
              <p className="mt-2 max-w-2xl text-sm text-ink/70">
                This chart is wired for local SQLite reads once reconciled transactions land in the database. Until then, it shows the expected dashboard composition and the gate-oriented emphasis.
              </p>
            </div>
            <div className="rounded-[22px] border border-sand/35 bg-fog/80 p-4 text-right">
              <p className="text-xs uppercase tracking-[0.18em] text-ink/55">Net YTD</p>
              <p className="mt-2 text-3xl font-semibold text-moss">
                {currency(months.reduce((total, month) => total + month.inflow - month.outflow, 0))}
              </p>
            </div>
          </div>
          <div className="mt-8 grid gap-4 sm:grid-cols-5">
            {months.map((month) => (
              <div key={month.label} className="rounded-[22px] border border-sand/30 bg-fog/65 p-4">
                <div className="flex items-center justify-between">
                  <p className="text-sm font-semibold text-ink">{month.label}</p>
                  <Vault className="h-4 w-4 text-ink/45" />
                </div>
                <div className="mt-4 flex h-44 items-end gap-3">
                  <Bar height={(month.inflow / chartMax) * 100} tone="inflow" />
                  <Bar height={(month.outflow / chartMax) * 100} tone="outflow" />
                </div>
                <div className="mt-4 space-y-2 text-xs text-ink/65">
                  <ValueRow icon={ArrowUpRight} label="Inflow" value={currency(month.inflow)} />
                  <ValueRow icon={ArrowDownRight} label="Outflow" value={currency(month.outflow)} />
                </div>
              </div>
            ))}
          </div>
        </Card>

        <Card>
          <div>
            <Badge tone="warning">Liquidity Gates</Badge>
            <h3 className="mt-3 text-2xl font-semibold text-ink">Constraint-first cash reserves</h3>
          </div>
          <div className="mt-6 space-y-5">
            {gates.map((gate) => {
              const progress = gate.targetAmount === 0 ? 0 : gate.currentAmount / gate.targetAmount;
              return (
                <div key={gate.gateKey} className="rounded-[24px] border border-sand/30 bg-fog/65 p-4">
                  <div className="flex items-center justify-between gap-4">
                    <div>
                      <p className="font-semibold text-ink">{gate.label}</p>
                      <p className="mt-1 text-xs uppercase tracking-[0.18em] text-ink/50">Target date {gate.targetDate}</p>
                    </div>
                    <Badge tone={progress >= 1 ? 'success' : 'warning'}>
                      {percent(Math.min(progress, 1))}
                    </Badge>
                  </div>
                  <div className="mt-4 h-3 rounded-full bg-sand/25">
                    <div
                      className="h-3 rounded-full bg-[linear-gradient(90deg,#c7744f,#8e392d)]"
                      style={{ width: `${Math.min(progress, 1) * 100}%` }}
                    />
                  </div>
                  <div className="mt-3 flex items-center justify-between text-sm text-ink/70">
                    <span>{currency(gate.currentAmount)} current</span>
                    <span>{currency(gate.targetAmount)} target</span>
                  </div>
                </div>
              );
            })}
          </div>
        </Card>
      </section>

      <section>
        <Card>
          <div className="flex flex-col gap-3 sm:flex-row sm:items-end sm:justify-between">
            <div>
              <Badge tone="danger">Lifestyle Leakage</Badge>
              <h3 className="mt-3 text-2xl font-semibold text-ink">Categories that can break the capital plan</h3>
            </div>
            <p className="max-w-xl text-sm text-ink/70">
              The schema and the MCP server reserve dedicated leakage categories so an agent can flag recurring convenience spend without confusing it with core obligations.
            </p>
          </div>
          <div className="mt-6 grid gap-4 md:grid-cols-3">
            {leakageCategories.map((category) => {
              const ratio = category.cap === 0 ? 0 : category.monthlyBurn / category.cap;
              return (
                <div key={category.name} className="rounded-[24px] border border-sand/30 bg-fog/65 p-4">
                  <p className="font-semibold text-ink">{category.name}</p>
                  <div className="mt-5 h-28 rounded-[20px] bg-white/55 p-4">
                    <div
                      className="h-full rounded-[14px] bg-[linear-gradient(180deg,#2d5f73,#c7744f)]"
                      style={{ width: `${Math.min(ratio, 1.4) * 100}%` }}
                    />
                  </div>
                  <div className="mt-4 flex items-center justify-between text-sm text-ink/70">
                    <span>{currency(category.monthlyBurn)} burn</span>
                    <span>{currency(category.cap)} cap</span>
                  </div>
                </div>
              );
            })}
          </div>
        </Card>
      </section>
    </div>
  );
}

function Bar({ height, tone }: { height: number; tone: 'inflow' | 'outflow' }) {
  const className = tone === 'inflow' ? 'bg-moss' : 'bg-clay';

  return <div className={`w-full rounded-t-[16px] ${className}`} style={{ height: `${Math.max(height, 8)}%` }} />;
}

function ValueRow({
  icon: Icon,
  label,
  value,
}: {
  icon: typeof ArrowUpRight;
  label: string;
  value: string;
}) {
  return (
    <div className="flex items-center justify-between gap-3">
      <span className="flex items-center gap-2">
        <Icon className="h-3.5 w-3.5" />
        {label}
      </span>
      <span className="font-medium text-ink">{value}</span>
    </div>
  );
}
