import type { PropsWithChildren } from 'react';
import { Database, ShieldCheck, WalletCards } from 'lucide-react';
import { Button } from '../ui/button';
import { Badge } from '../ui/badge';
import { cn, currency } from '../../lib/utils';

export type AppView = 'intake' | 'dashboard';

interface AppShellProps extends PropsWithChildren {
  view: AppView;
  onViewChange: (view: AppView) => void;
  totalDocuments: number;
  missingDocuments: number;
  liquidityGate: number;
}

export function AppShell({
  children,
  view,
  onViewChange,
  totalDocuments,
  missingDocuments,
  liquidityGate,
}: AppShellProps) {
  return (
    <div className="min-h-screen px-4 py-6 sm:px-6 lg:px-10">
      <div className="mx-auto flex max-w-7xl flex-col gap-6">
        <header className="overflow-hidden rounded-[36px] border border-white/80 bg-[linear-gradient(135deg,rgba(22,33,38,0.96),rgba(45,95,115,0.92))] p-6 text-fog shadow-soft sm:p-8">
          <div className="flex flex-col gap-6 lg:flex-row lg:items-end lg:justify-between">
            <div className="max-w-3xl space-y-4">
              <Badge className="bg-white/10 text-fog" tone="info">
                Liquidity Gate
              </Badge>
              <div className="space-y-3">
                <h1 className="text-4xl font-semibold tracking-tight sm:text-5xl">
                  Local-first cash-flow reconstruction built for financial detective work.
                </h1>
                <p className="max-w-2xl text-sm text-fog/80 sm:text-base">
                  Tauri desktop shell, typed React workspace, SQLite-backed modeling, and a Python MCP bridge that keeps every document and transaction local.
                </p>
              </div>
              <div className="flex flex-wrap gap-3">
                <Button
                  variant={view === 'intake' ? 'default' : 'subtle'}
                  onClick={() => onViewChange('intake')}
                >
                  Document Intake
                </Button>
                <Button
                  variant={view === 'dashboard' ? 'default' : 'subtle'}
                  onClick={() => onViewChange('dashboard')}
                >
                  Cash Flow Dashboard
                </Button>
              </div>
            </div>
            <div className="grid gap-3 sm:grid-cols-3 lg:w-[28rem]">
              <StatCard icon={ShieldCheck} label="Documents tracked" value={String(totalDocuments)} />
              <StatCard icon={Database} label="Missing references" value={String(missingDocuments)} />
              <StatCard icon={WalletCards} label="Primary liquidity gate" value={currency(liquidityGate)} />
            </div>
          </div>
        </header>
        {children}
      </div>
    </div>
  );
}

interface StatCardProps {
  icon: typeof ShieldCheck;
  label: string;
  value: string;
}

function StatCard({ icon: Icon, label, value }: StatCardProps) {
  return (
    <div className="rounded-[24px] border border-white/10 bg-white/10 p-4 backdrop-blur-sm">
      <div className="flex items-center gap-3 text-fog/80">
        <div className="rounded-full bg-white/10 p-2">
          <Icon className="h-4 w-4" />
        </div>
        <p className="text-xs uppercase tracking-[0.18em]">{label}</p>
      </div>
      <p className={cn('mt-4 text-2xl font-semibold text-fog')}>{value}</p>
    </div>
  );
}
