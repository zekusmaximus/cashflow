import { useEffect } from 'react';
import { useLeakageTransactions } from '../../hooks/use-leakage-transactions';
import type { LeakageTransaction } from './types';

interface TransactionDrawerProps {
  open: boolean;
  categoryKey: string | null;
  categoryName: string | null;
  range: { start: string; end: string };
  onClose: () => void;
}

export function TransactionDrawer({
  open,
  categoryKey,
  categoryName,
  range,
  onClose,
}: TransactionDrawerProps) {
  const { data, isLoading } = useLeakageTransactions(categoryKey, range, { enabled: open });

  // Escape key to close
  useEffect(() => {
    if (!open) return;
    const onKeyDown = (e: KeyboardEvent) => {
      if (e.key === 'Escape') onClose();
    };
    window.addEventListener('keydown', onKeyDown);
    return () => window.removeEventListener('keydown', onKeyDown);
  }, [open, onClose]);

  // Detect prefers-reduced-motion
  const reducedMotion =
    typeof window !== 'undefined' &&
    window.matchMedia('(prefers-reduced-motion: reduce)').matches;

  const slideClass = reducedMotion
    ? open
      ? ''
      : 'translate-x-full'
    : open
      ? 'transition-transform duration-300 translate-x-0'
      : 'transition-transform duration-300 translate-x-full';

  return (
    <>
      {/* Backdrop */}
      <div
        aria-hidden
        className={`fixed inset-0 z-40 bg-ink/30 backdrop-blur-sm transition-opacity duration-200 ${
          open ? 'opacity-100' : 'pointer-events-none opacity-0'
        }`}
        onClick={onClose}
      />

      {/* Drawer panel */}
      <aside
        role="dialog"
        aria-modal="true"
        aria-label={categoryName ? `Transactions — ${categoryName}` : 'Transactions'}
        className={`fixed right-0 top-0 z-50 flex h-full w-[420px] flex-col bg-paper ${slideClass}`}
        style={{ boxShadow: '-12px 0 40px -8px rgba(22,33,38,0.18)' }}
      >
        {/* Header */}
        <div className="flex shrink-0 items-center justify-between border-b border-ink/8 px-5 py-4">
          <div>
            <div className="text-[10px] font-medium uppercase tracking-[0.18em] text-ink/45">
              Transactions
            </div>
            <div className="mt-0.5 text-[15px] font-semibold text-ink">
              {categoryName ?? '—'}
            </div>
          </div>
          <button
            type="button"
            onClick={onClose}
            aria-label="Close drawer"
            className="grid h-7 w-7 place-items-center rounded-md text-ink/55 hover:bg-ink/[0.06] hover:text-ink"
          >
            <CloseIcon />
          </button>
        </div>

        {/* Date range label */}
        {range.start && range.end && (
          <div className="shrink-0 border-b border-ink/[0.05] px-5 py-2 text-[11px] text-ink/45">
            {range.start} → {range.end}
          </div>
        )}

        {/* Body */}
        <div className="min-h-0 flex-1 overflow-y-auto px-5 py-4">
          {isLoading && <DrawerSkeleton />}
          {!isLoading && (!data || data.length === 0) && <DrawerEmptyState />}
          {!isLoading && data && data.length > 0 && (
            <ul className="divide-y divide-ink/8">
              {data.map((tx) => (
                <DrawerTransactionRow key={tx.id} tx={tx} />
              ))}
            </ul>
          )}
        </div>
      </aside>
    </>
  );
}

function DrawerTransactionRow({ tx }: { tx: LeakageTransaction }) {
  return (
    <li className="py-2.5 first:pt-0 last:pb-0">
      <div className="flex items-baseline justify-between gap-3 text-[13px]">
        <span className="min-w-0 truncate font-medium text-ink">{tx.vendor}</span>
        <span className="shrink-0 tnum text-clay">
          −${tx.amount.toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 })}
        </span>
      </div>
      <div className="mt-0.5 flex items-center gap-2 text-[11px] text-ink/55">
        <span className="tnum">{tx.date}</span>
        <span aria-hidden>·</span>
        <span className="truncate font-mono text-[10px]">{tx.rawDescription}</span>
        {tx.matchScore !== undefined && (
          <span className="ml-auto shrink-0 tnum text-ink/40">
            match {tx.matchScore.toFixed(2)}
          </span>
        )}
      </div>
    </li>
  );
}

function DrawerSkeleton() {
  return (
    <div className="space-y-3" aria-label="Loading transactions">
      {[1, 2, 3, 4, 5].map((i) => (
        <div key={i} className="animate-pulse space-y-1.5">
          <div className="flex items-center justify-between">
            <div className="h-3 w-2/5 rounded bg-ink/[0.08]" />
            <div className="h-3 w-16 rounded bg-ink/[0.06]" />
          </div>
          <div className="h-2.5 w-3/4 rounded bg-ink/[0.05]" />
        </div>
      ))}
    </div>
  );
}

function DrawerEmptyState() {
  return (
    <div className="flex flex-col items-center justify-center py-16 text-center">
      <div className="text-3xl">🔍</div>
      <div className="mt-3 text-[13px] font-medium text-ink">No transactions found</div>
      <div className="mt-1 max-w-[240px] text-[11px] leading-relaxed text-ink/50">
        No outflow transactions matched this category in the selected date range.
      </div>
    </div>
  );
}

function CloseIcon() {
  return (
    <svg
      xmlns="http://www.w3.org/2000/svg"
      viewBox="0 0 16 16"
      width="14"
      height="14"
      fill="none"
      stroke="currentColor"
      strokeWidth="1.75"
      strokeLinecap="round"
    >
      <path d="M3 3l10 10M13 3L3 13" />
    </svg>
  );
}
