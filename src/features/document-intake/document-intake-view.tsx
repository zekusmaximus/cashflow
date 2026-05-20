import { useRef, useState } from 'react';
import { type UseQueryResult } from '@tanstack/react-query';
import {
  AlertTriangle,
  Check,
  FileText,
  Folder,
} from 'lucide-react';
import { Card } from '../../components/ui/card';
import { useTransactionCounts } from '../../hooks/use-transaction-counts';
import { cn } from '../../lib/utils';
import type {
  ChecklistCategorySummary,
  ChecklistDataset,
  ChecklistItem,
  WatchRootStatus,
} from './types';

interface DocumentIntakeViewProps {
  query: UseQueryResult<ChecklistDataset, Error>;
}

interface ChecklistFilter {
  status: 'all' | 'open';
  priority: 'all' | 'essential';
}

const DEFAULT_CHECKLIST_FILTER: ChecklistFilter = {
  status: 'all',
  priority: 'all',
};

function isEssentialItem(item: ChecklistItem): boolean {
  return item.priority.includes('Essential');
}

function matchesChecklistFilter(item: ChecklistItem, filter: ChecklistFilter): boolean {
  if (filter.status === 'open' && item.obtained) {
    return false;
  }
  if (filter.priority === 'essential' && !isEssentialItem(item)) {
    return false;
  }
  return true;
}

export function DocumentIntakeView({ query }: DocumentIntakeViewProps) {
  const transactionCountsQuery = useTransactionCounts();
  const tableRef = useRef<HTMLDivElement | null>(null);
  const [filter, setFilter] = useState<ChecklistFilter>(DEFAULT_CHECKLIST_FILTER);

  if (query.isLoading) {
    return <LoadingState />;
  }

  if (query.isError || !query.data) {
    return (
      <Card className="text-sm text-ember">
        Unable to load source intake. Verify that the tracker CSV exists in the docs folder.
      </Card>
    );
  }

  const { items, summary, watchRoot } = query.data;
  const transactionCounts = transactionCountsQuery.data ?? new Map<string, number>();
  const coreItems = items.filter(isEssentialItem);
  const coreSources = coreItems.length;
  const coreReady = coreItems.filter((item) => item.obtained).length;
  const openLater = items.filter((item) => !item.obtained && !isEssentialItem(item)).length;
  const essentialsGap = Math.max(0, coreSources - coreReady);

  const handleStartWithEssentials = () => {
    setFilter({ status: 'open', priority: 'essential' });
    tableRef.current?.scrollIntoView({ behavior: 'smooth', block: 'start' });
  };

  return (
    <div>
      <div className="mb-2 flex items-baseline gap-3">
        <span className="text-[11px] font-medium uppercase tracking-[0.18em] text-ink/45">
          Phase 1
        </span>
        <h1 className="text-[18px] font-semibold tracking-tight text-ink">Source intake</h1>
      </div>

      <div className="mb-4 grid gap-2 lg:grid-cols-[minmax(0,1.35fr)_minmax(320px,0.95fr)] lg:items-start">
        <IntakeProgress
          coreReady={coreReady}
          coreSources={coreSources}
          openLater={openLater}
        />

        <div className="space-y-2">
          <EssentialsBanner count={essentialsGap} onStart={handleStartWithEssentials} />
          <WatchRootStrip status={watchRoot} />
        </div>
      </div>

      <div
        className="grid gap-5"
        style={{ gridTemplateColumns: 'minmax(0, 280px) minmax(0, 1fr)' }}
      >
        <CategorySidebar categories={summary.categories} />
        <div ref={tableRef}>
          <ChecklistTable
            items={items}
            transactionCounts={transactionCounts}
            filter={filter}
            onFilterChange={setFilter}
          />
        </div>
      </div>
    </div>
  );
}

interface IntakeProgressProps {
  coreReady: number;
  coreSources: number;
  openLater: number;
}

function IntakeProgress({ coreReady, coreSources, openLater }: IntakeProgressProps) {
  const essentialsGap = Math.max(0, coreSources - coreReady);
  const railTotal = coreSources > 0 ? coreReady + essentialsGap + openLater : 0;
  const readyPct = coreSources === 0 ? 0 : Math.round((coreReady / coreSources) * 100);
  return (
    <div className="rounded-xl border border-ink/8 bg-white px-4 py-3 shadow-card">
      <div className="flex flex-wrap items-baseline justify-between gap-3">
        <div className="min-w-0 flex flex-wrap items-baseline gap-x-2 gap-y-1">
          <span className="text-[10px] font-medium uppercase tracking-[0.18em] text-ink/45">
            Intake progress
          </span>
          <span className="text-[18px] font-semibold tnum text-ink">
            {coreReady} / {coreSources}
          </span>
          <span className="text-[12px] text-ink/55">core ready</span>
          {essentialsGap > 0 ? (
            <span className="text-[12px] font-semibold text-ember">
              {essentialsGap} essential open
            </span>
          ) : null}
        </div>
        <span className="text-[11px] text-ink/50 tnum">{openLater} open later</span>
      </div>

      <div
        className="mt-2 grid h-1.5 gap-px overflow-hidden rounded-full bg-ink/[0.06]"
        style={{
          gridTemplateColumns: `${railTotal === 0 ? 0 : coreReady}fr ${railTotal === 0 ? 0 : essentialsGap}fr ${railTotal === 0 ? 0 : openLater}fr`,
        }}
      >
        <div className="bg-moss" />
        <div className="bg-ember" />
        <div className="bg-ink/10" />
      </div>

      <div className="mt-1 flex flex-wrap items-center justify-between gap-2 text-[10px] text-ink/50 tnum">
        <span>{readyPct}% core ready</span>
        <span>
          {essentialsGap} essential open · {openLater} open later
        </span>
      </div>
    </div>
  );
}

interface EssentialsBannerProps {
  count: number;
  onStart: () => void;
}

function EssentialsBanner({ count, onStart }: EssentialsBannerProps) {
  if (count === 0) {
    return null;
  }

  return (
    <div className="flex items-center gap-3 rounded-xl border border-ember/30 bg-ember/[0.05] px-3.5 py-2.5 shadow-card">
      <span className="grid h-6 w-6 shrink-0 place-items-center rounded-full bg-ember text-fog">
        <AlertTriangle className="h-3.5 w-3.5" />
      </span>
      <div className="min-w-0 text-[12px] leading-relaxed text-ink/65">
        <span className="font-semibold text-ink">
          {count} essential {count === 1 ? 'source' : 'sources'} still open
        </span>{' '}
        Start with the items that unblock the baseline cash-flow read.
      </div>
      <button
        type="button"
        onClick={onStart}
        className="ml-auto shrink-0 rounded-full bg-ember px-3 py-1 text-[11px] font-semibold text-fog hover:bg-ember/90"
      >
        Start with essentials
      </button>
    </div>
  );
}

function WatchRootStrip({ status }: { status: WatchRootStatus }) {
  if (!status.root) {
    return (
      <div className="rounded-xl border border-ink/8 bg-white px-3.5 py-2.5 text-[12px] text-ink/55 shadow-card">
        Filesystem matching is only active inside the Tauri desktop app.
      </div>
    );
  }
  const ok = status.exists;
  const stateLabel = ok ? 'Watching' : 'Missing';
  const iconBgClass = ok ? 'bg-moss/12 text-moss' : 'bg-ember/12 text-ember';
  return (
    <div className="flex items-center gap-3 rounded-xl border border-ink/8 bg-white px-3.5 py-2.5 shadow-card">
      <span
        className={cn(
          'grid h-6 w-6 shrink-0 place-items-center rounded-md',
          iconBgClass,
        )}
      >
        <Folder className="h-3.5 w-3.5" />
      </span>
      <div className="min-w-0 flex-1">
        <div className="flex items-baseline gap-2 text-[12px]">
          <span className="text-ink/55">{stateLabel}</span>
          <span className="truncate font-mono text-ink/85">{status.root}</span>
        </div>
      </div>
      <div className="flex shrink-0 items-center gap-3 text-[11px] text-ink/55">
        <span className="tnum">
          <b className="font-semibold text-ink">{status.fileCount}</b> file
          {status.fileCount === 1 ? '' : 's'} indexed
        </span>
        <span className="h-3 w-px bg-ink/15" />
        <span>Rescans every 5s</span>
      </div>
    </div>
  );
}

function categoryLetter(category: string): string {
  const head = category.split('. ')[0]?.trim() ?? '';
  if (head.length === 1) return head.toUpperCase();
  return category.charAt(0).toUpperCase() || '·';
}

function categoryDisplayName(category: string): string {
  const idx = category.indexOf('. ');
  if (idx > 0 && idx <= 2) return category.slice(idx + 2);
  return category;
}

function CategorySidebar({ categories }: { categories: ChecklistCategorySummary[] }) {
  return (
    <aside className="rounded-xl border border-ink/8 bg-white shadow-card">
      <div
        className="flex items-center justify-between px-4 pt-4 pb-3"
        style={{ boxShadow: 'inset 0 -1px 0 rgba(22,33,38,0.08)' }}
      >
        <div>
          <div className="text-[10px] font-medium uppercase tracking-[0.18em] text-ink/45">
            Coverage by
          </div>
          <div className="text-[14px] font-semibold text-ink">Source group</div>
        </div>
      </div>
      <div className="space-y-3.5 px-4 py-4">
        {categories.map((category) => {
          const pct = category.total === 0 ? 0 : (category.obtained / category.total) * 100;
          return (
            <div key={category.category} className="space-y-1.5">
              <div className="flex items-center justify-between gap-2 text-[12px]">
                <div className="flex min-w-0 items-center gap-2">
                  <span className="grid h-4 w-4 shrink-0 place-items-center rounded bg-ink/[0.07] text-[9px] font-semibold text-ink/70">
                    {categoryLetter(category.category)}
                  </span>
                  <span className="truncate text-ink/85">
                    {categoryDisplayName(category.category)}
                  </span>
                </div>
                <span className="shrink-0 text-[11px] text-ink/50 tnum">
                  {category.obtained}/{category.total}
                </span>
              </div>
              <div className="h-1 w-full rounded-full bg-ink/[0.06]">
                <div
                  className="h-full rounded-full bg-moss"
                  style={{ width: `${pct}%` }}
                />
              </div>
            </div>
          );
        })}
      </div>
    </aside>
  );
}

interface ChecklistTableProps {
  items: ChecklistItem[];
  transactionCounts: Map<string, number>;
  filter: ChecklistFilter;
  onFilterChange: (filter: ChecklistFilter) => void;
}

function ChecklistTable({ items, transactionCounts, filter, onFilterChange }: ChecklistTableProps) {
  const filteredItems = items.filter((item) => matchesChecklistFilter(item, filter));
  const isDefaultFilter =
    filter.status === DEFAULT_CHECKLIST_FILTER.status &&
    filter.priority === DEFAULT_CHECKLIST_FILTER.priority;

  return (
    <section className="relative min-w-0 overflow-hidden rounded-xl border border-ink/8 bg-white shadow-card">
      <div className="flex flex-wrap items-center justify-between gap-3 border-b border-ink/[0.08] px-4 py-3">
        <span className="text-[11px] text-ink/50 tnum">
          {filteredItems.length} of {items.length} sources
        </span>
        <div className="flex flex-wrap items-center gap-1">
          <ChecklistFilterButton
            active={isDefaultFilter}
            onClick={() => onFilterChange(DEFAULT_CHECKLIST_FILTER)}
          >
            All
          </ChecklistFilterButton>
          <ChecklistFilterButton
            active={filter.status === 'open' && filter.priority === 'all'}
            onClick={() => onFilterChange({ status: 'open', priority: 'all' })}
          >
            Open
          </ChecklistFilterButton>
          <ChecklistFilterButton
            active={filter.status === 'open' && filter.priority === 'essential'}
            onClick={() => onFilterChange({ status: 'open', priority: 'essential' })}
          >
            Essential open
          </ChecklistFilterButton>
        </div>
      </div>

      {filteredItems.length === 0 ? (
        <div className="px-4 py-6 text-[12px] text-ink/55">
          No sources match the current filter.
        </div>
      ) : (
      <div className="max-h-[640px] overflow-auto">
        <table className="w-full table-fixed border-collapse text-left text-[13px]">
          <colgroup>
            <col style={{ width: '92px' }} />
            <col />
            <col style={{ width: '110px' }} />
            <col style={{ width: '96px' }} />
            <col style={{ width: '30%' }} />
          </colgroup>
          <thead
            className="sticky top-0 z-[1] bg-white/95 backdrop-blur"
            style={{ boxShadow: 'inset 0 -1px 0 rgba(22,33,38,0.08)' }}
          >
            <tr className="text-[10px] uppercase tracking-[0.16em] text-ink/45">
              <th className="px-4 py-2.5 font-medium">Status</th>
              <th className="px-3 py-2.5 font-medium">Source</th>
              <th className="px-3 py-2.5 font-medium">Category</th>
              <th className="px-3 py-2.5 font-medium">Priority</th>
              <th className="px-4 py-2.5 font-medium">Why it helps</th>
            </tr>
          </thead>
          <tbody>
            {filteredItems.map((item) => (
              <ChecklistRow
                key={item.id}
                item={item}
                transactionCounts={transactionCounts}
              />
            ))}
          </tbody>
        </table>
      </div>
      )}
    </section>
  );
}

function ChecklistFilterButton({
  active,
  onClick,
  children,
}: {
  active: boolean;
  onClick: () => void;
  children: React.ReactNode;
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      className={cn(
        'rounded-full px-2.5 py-1 text-[11px] font-medium transition-colors',
        active ? 'bg-ink text-fog' : 'bg-ink/[0.05] text-ink/60 hover:text-ink',
      )}
    >
      {children}
    </button>
  );
}

function ChecklistRow({
  item,
  transactionCounts,
}: {
  item: ChecklistItem;
  transactionCounts: Map<string, number>;
}) {
  const isEssential = isEssentialItem(item);
  const isDeferred = item.priority.includes('Deferred');
  return (
    <tr
      className="align-top hover:bg-paper/60"
      style={{ boxShadow: 'inset 0 -1px 0 rgba(22,33,38,0.08)' }}
    >
      <td className="px-4 py-3">
        {item.obtained ? (
          <>
            <span className="inline-flex items-center gap-1.5 rounded-full bg-moss/12 px-2 py-0.5 text-[11px] font-medium text-moss">
              <Check className="h-3 w-3" />
              Available
            </span>
            {item.obtainedSource === 'filesystem' ? (
              <div className="mt-1 flex items-center gap-1 text-[10px] uppercase tracking-[0.14em] text-moss/80">
                <Check className="h-2.5 w-2.5" />
                Auto-matched
              </div>
            ) : null}
          </>
        ) : (
          <span
            className={cn(
              'inline-flex items-center gap-1.5 rounded-full px-2 py-0.5 text-[11px] font-medium',
              isEssential
                ? 'bg-ember/12 text-ember'
                : 'bg-ink/[0.06] text-ink/55',
            )}
          >
            <span
              className={cn(
                'h-1.5 w-1.5 rounded-full',
                isEssential ? 'bg-ember' : 'bg-ink/35',
              )}
            />
            Open
          </span>
        )}
      </td>
      <td className="px-3 py-3">
        <div className="font-medium leading-snug text-ink">{item.document}</div>
        <div className="mt-0.5 text-[11px] text-ink/45">{item.format}</div>
        {item.matchedFiles.length > 0 ? (
          <ul className="mt-1.5 space-y-0.5">
            {item.matchedFiles.map((file) => {
              const ingestedCount = transactionCounts.get(file.filename) ?? 0;
              return (
                <li
                  key={file.relativePath}
                  className="flex min-w-0 items-baseline gap-2 text-[11px]"
                >
                  <FileText className="h-3 w-3 shrink-0 text-ink/35" />
                  <span className="truncate font-mono text-ink/75">{file.filename}</span>
                  <span className="shrink-0 text-ink/35 tnum">{file.score.toFixed(2)}</span>
                  {ingestedCount > 0 ? (
                    <span className="shrink-0 text-moss tnum">
                      · {ingestedCount.toLocaleString()} tx
                    </span>
                  ) : null}
                </li>
              );
            })}
          </ul>
        ) : null}
      </td>
      <td className="px-3 py-3 text-[12px] text-ink/70">{item.category}</td>
      <td className="px-3 py-3">
        <span
          className={cn(
            'inline-flex items-center rounded-md border px-1.5 py-0.5 text-[10px] font-medium uppercase tracking-[0.06em]',
            isEssential
              ? 'border-moss/30 bg-moss/10 text-moss'
              : isDeferred
                ? 'border-ink/10 bg-paper text-ink/45'
                : 'border-ink/15 bg-ink/[0.04] text-ink/65',
          )}
        >
          {item.priority}
        </span>
      </td>
      <td
        className="px-4 py-3 text-[12px] leading-relaxed text-ink/65"
        style={{ textWrap: 'pretty' } as React.CSSProperties}
      >
        {item.whyNeeded}
      </td>
    </tr>
  );
}

function LoadingState() {
  return (
    <div className="rounded-xl border border-ink/8 bg-white p-6 text-sm text-ink/70 shadow-card">
      Loading source coverage from the tracker CSV.
    </div>
  );
}
