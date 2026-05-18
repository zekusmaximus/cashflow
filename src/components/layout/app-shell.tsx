import type { PropsWithChildren } from 'react';
import { cn, currency } from '../../lib/utils';

export type AppView = 'intake' | 'dashboard';

interface AppShellProps extends PropsWithChildren {
  view: AppView;
  onViewChange: (view: AppView) => void;
  coreSources: number;
  coreReady: number;
  openLater: number;
  liquidityGateCurrent: number;
  liquidityGate: number;
}

export function AppShell({
  children,
  view,
  onViewChange,
  coreSources,
  coreReady,
  openLater,
  liquidityGateCurrent,
  liquidityGate,
}: AppShellProps) {
  return (
    <div className="min-h-screen bg-paper">
      <header
        className="sticky top-0 z-20 bg-paper/85 backdrop-blur"
        style={{ boxShadow: 'inset 0 -1px 0 rgba(22,33,38,0.08)' }}
      >
        <div className="mx-auto flex max-w-[1280px] items-center gap-6 px-6 py-3">
          <BrandMark />
          <TabNav view={view} onViewChange={onViewChange} />
          <StatusPills
            coreReady={coreReady}
            coreSources={coreSources}
            openLater={openLater}
            gateCurrent={liquidityGateCurrent}
            gateTarget={liquidityGate}
          />
        </div>
      </header>

      <main className="mx-auto max-w-[1280px] px-6 py-6">{children}</main>
    </div>
  );
}

function BrandMark() {
  return (
    <div className="flex shrink-0 items-center gap-2.5">
      <div className="grid h-7 w-7 place-items-center rounded-md bg-ink text-fog">
        <svg
          viewBox="0 0 16 16"
          className="h-3.5 w-3.5"
          fill="none"
          stroke="currentColor"
          strokeWidth={2}
          strokeLinecap="round"
        >
          <path d="M3 11h10M3 7h10M5 4v9M11 4v9" />
        </svg>
      </div>
      <div className="leading-tight">
        <div className="text-[13px] font-semibold tracking-tight text-ink">Liquidity Gate</div>
        <div className="text-[10px] uppercase tracking-[0.18em] text-ink/45">Local · 2026</div>
      </div>
    </div>
  );
}

interface TabNavProps {
  view: AppView;
  onViewChange: (view: AppView) => void;
}

function TabNav({ view, onViewChange }: TabNavProps) {
  return (
    <nav className="ml-2 flex shrink-0 items-center gap-1 rounded-full bg-ink/[0.04] p-1">
      <TabButton active={view === 'intake'} onClick={() => onViewChange('intake')}>
        Source intake
      </TabButton>
      <TabButton active={view === 'dashboard'} onClick={() => onViewChange('dashboard')}>
        Cash-flow dashboard
      </TabButton>
    </nav>
  );
}

interface TabButtonProps {
  active: boolean;
  onClick: () => void;
  children: React.ReactNode;
}

function TabButton({ active, onClick, children }: TabButtonProps) {
  return (
    <button
      type="button"
      onClick={onClick}
      className={cn(
        'rounded-full px-3.5 py-1.5 text-[13px] font-medium transition-colors',
        active ? 'bg-white text-ink shadow-card' : 'text-ink/65 hover:text-ink',
      )}
    >
      {children}
    </button>
  );
}

interface StatusPillsProps {
  coreReady: number;
  coreSources: number;
  openLater: number;
  gateCurrent: number;
  gateTarget: number;
}

function StatusPills({ coreReady, coreSources, openLater, gateCurrent, gateTarget }: StatusPillsProps) {
  return (
    <div className="ml-auto hidden items-center gap-5 text-[12px] text-ink/65 lg:flex">
      <div className="flex items-center gap-1.5">
        <span className="h-1.5 w-1.5 rounded-full bg-moss" />
        <span className="tnum">
          <b className="font-semibold text-ink">{coreReady}</b> / {coreSources} core ready
        </span>
      </div>
      <div className="flex items-center gap-1.5">
        <span className="h-1.5 w-1.5 rounded-full bg-ink/35" />
        <span className="tnum">
          <b className="font-semibold text-ink">{openLater}</b> open later
        </span>
      </div>
      <div className="h-4 w-px bg-ink/15" />
      <div className="tnum">
        HYSA gate <b className="font-semibold text-ink">{currency(gateCurrent)}</b> / {currency(gateTarget)}
      </div>
    </div>
  );
}
