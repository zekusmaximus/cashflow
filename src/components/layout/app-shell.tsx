import type { PropsWithChildren } from 'react';
import { cn, currency } from '../../lib/utils';

export type AppView = 'intake' | 'dashboard';

interface AppShellProps extends PropsWithChildren {
  view: AppView;
  onViewChange: (view: AppView) => void;
  liquidityGateCurrent: number;
  liquidityGate: number;
}

export function AppShell({
  children,
  view,
  onViewChange,
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
          <HysaRail current={liquidityGateCurrent} target={liquidityGate} />
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
      <div className="text-[13px] font-semibold tracking-tight text-ink">Liquidity Gate</div>
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
        Cash flow
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

interface HysaRailProps {
  current: number;
  target: number;
}

function HysaRail({ current, target }: HysaRailProps) {
  const ratio = target <= 0 ? 0 : Math.min(Math.max(current / target, 0), 1);
  return (
    <div className="ml-auto hidden items-center gap-3 lg:flex">
      <div className="text-[10px] font-medium uppercase tracking-[0.16em] text-ink/55">
        HYSA gate
      </div>
      <div className="h-1 w-32 overflow-hidden rounded-full bg-ink/[0.08]">
        <div className="h-full bg-tide" style={{ width: `${ratio * 100}%` }} />
      </div>
      <div className="text-[12px] font-semibold text-ink tnum">{Math.round(ratio * 100)}%</div>
      <div className="text-[11px] text-ink/45 tnum">
        {currency(current)} / {currency(target)}
      </div>
    </div>
  );
}
