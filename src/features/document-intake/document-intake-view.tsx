import { useRef, useState, type Dispatch, type SetStateAction } from 'react';
import { type UseQueryResult } from '@tanstack/react-query';
import {
  AlertTriangle,
  Check,
  ChevronDown,
  Copy,
  FileText,
  Folder,
  FolderOpen,
  X,
} from 'lucide-react';
import { Card } from '../../components/ui/card';
import { useTransactionCounts } from '../../hooks/use-transaction-counts';
import { revealInFileManager } from '../../lib/tauri';
import { cn } from '../../lib/utils';
import type {
  ChecklistCategorySummary,
  ChecklistDataset,
  ChecklistFilter,
  ChecklistItem,
  WatchRootStatus,
} from './types';
import { DEFAULT_CHECKLIST_FILTER } from './types';

interface DocumentIntakeViewProps {
  query: UseQueryResult<ChecklistDataset, Error>;
  filter: ChecklistFilter;
  onFilterChange: Dispatch<SetStateAction<ChecklistFilter>>;
}

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
  if (filter.category !== 'all' && item.categoryId !== filter.category) {
    return false;
  }
  return true;
}

function isDefaultFilter(filter: ChecklistFilter): boolean {
  return (
    filter.status === DEFAULT_CHECKLIST_FILTER.status &&
    filter.priority === DEFAULT_CHECKLIST_FILTER.priority &&
    filter.category === DEFAULT_CHECKLIST_FILTER.category
  );
}

function formatRelativeTime(ms: number): string {
  const diff = Date.now() - ms;
  if (diff < 0) return 'just now';
  const minute = 60_000;
  const hour = 60 * minute;
  const day = 24 * hour;
  if (diff < hour) return `${Math.max(1, Math.round(diff / minute))}m ago`;
  if (diff < day) return `${Math.round(diff / hour)}h ago`;
  if (diff < 30 * day) return `${Math.round(diff / day)}d ago`;
  return new Date(ms).toLocaleDateString();
}

export function DocumentIntakeView({ query, filter, onFilterChange }: DocumentIntakeViewProps) {
  const transactionCountsQuery = useTransactionCounts();
  const tableRef = useRef<HTMLDivElement | null>(null);

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
    onFilterChange({ status: 'open', priority: 'essential', category: 'all' });
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
        <CategorySidebar
          categories={summary.categories}
          activeCategory={filter.category}
          onCategoryChange={(id) => onFilterChange((f) => ({ ...f, category: id }))}
        />
        <div ref={tableRef}>
          <ChecklistTable
            items={items}
            categories={summary.categories}
            transactionCounts={transactionCounts}
            filter={filter}
            onFilterChange={onFilterChange}
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
  const [toast, setToast] = useState<string | null>(null);

  const flashToast = (message: string) => {
    setToast(message);
    window.setTimeout(() => setToast(null), 1000);
  };

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

  const handleReveal = () => {
    revealInFileManager(status.root).catch(() => flashToast('Could not open file manager'));
  };

  const handleCopy = () => {
    navigator.clipboard
      .writeText(status.root)
      .then(() => flashToast('Path copied'))
      .catch(() => flashToast('Copy failed'));
  };

  return (
    <div className="relative flex items-center gap-3 rounded-xl border border-ink/8 bg-white px-3.5 py-2.5 shadow-card">
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
      <div className="flex shrink-0 items-center gap-1.5 text-[11px] text-ink/55">
        <span className="tnum">
          <b className="font-semibold text-ink">{status.fileCount}</b> file
          {status.fileCount === 1 ? '' : 's'} indexed
        </span>
        <span className="mx-1 h-3 w-px bg-ink/15" />
        <span>Rescans every 5s</span>
        <button
          type="button"
          onClick={handleReveal}
          title="Reveal in file manager"
          className="ml-1 grid h-6 w-6 place-items-center rounded-md text-ink/55 transition-colors hover:bg-ink/[0.06] hover:text-ink"
        >
          <FolderOpen className="h-3.5 w-3.5" />
        </button>
        <button
          type="button"
          onClick={handleCopy}
          title="Copy path"
          className="grid h-6 w-6 place-items-center rounded-md text-ink/55 transition-colors hover:bg-ink/[0.06] hover:text-ink"
        >
          <Copy className="h-3.5 w-3.5" />
        </button>
      </div>
      {toast ? (
        <div className="absolute -top-2 right-3 -translate-y-full rounded-md bg-ink px-2 py-1 text-[11px] font-medium text-fog shadow-card">
          {toast}
        </div>
      ) : null}
    </div>
  );
}

function categoryDisplayName(category: string): string {
  const idx = category.indexOf('. ');
  if (idx > 0 && idx <= 2) return category.slice(idx + 2);
  return category;
}

function categoryNameById(categories: ChecklistCategorySummary[], id: string): string {
  const match = categories.find((c) => c.categoryId === id);
  return match ? categoryDisplayName(match.category) : id;
}

interface CategorySidebarProps {
  categories: ChecklistCategorySummary[];
  activeCategory: string | 'all';
  onCategoryChange: (id: string | 'all') => void;
}

function CategorySidebar({ categories, activeCategory, onCategoryChange }: CategorySidebarProps) {
  const totalObtained = categories.reduce((sum, c) => sum + c.obtained, 0);
  const totalAll = categories.reduce((sum, c) => sum + c.total, 0);

  return (
    <aside className="rounded-xl border border-ink/8 bg-white shadow-card">
      <div
        className="flex items-center justify-between px-4 pt-4 pb-3"
        style={{ boxShadow: 'inset 0 -1px 0 rgba(22,33,38,0.08)' }}
      >
        <div>
          <div className="text-[10px] font-medium uppercase tracking-[0.18em] text-ink/45">
            Filter by
          </div>
          <div className="text-[14px] font-semibold text-ink">Category</div>
        </div>
        {activeCategory !== 'all' ? (
          <button
            type="button"
            onClick={() => onCategoryChange('all')}
            className="text-[11px] text-tide hover:text-ink"
          >
            Clear
          </button>
        ) : null}
      </div>
      <div className="space-y-1 px-2 py-2">
        <CategoryRow
          name="All categories"
          obtained={totalObtained}
          total={totalAll}
          active={activeCategory === 'all'}
          onClick={() => onCategoryChange('all')}
        />
        {categories.map((category) => (
          <CategoryRow
            key={category.categoryId}
            name={categoryDisplayName(category.category)}
            obtained={category.obtained}
            total={category.total}
            active={activeCategory === category.categoryId}
            onClick={() =>
              onCategoryChange(
                activeCategory === category.categoryId ? 'all' : category.categoryId,
              )
            }
          />
        ))}
      </div>
    </aside>
  );
}

interface CategoryRowProps {
  name: string;
  obtained: number;
  total: number;
  active: boolean;
  onClick: () => void;
}

function CategoryRow({ name, obtained, total, active, onClick }: CategoryRowProps) {
  const pct = total === 0 ? 0 : (obtained / total) * 100;
  return (
    <button
      type="button"
      onClick={onClick}
      className={cn(
        'w-full rounded-md px-2 py-2 text-left transition-colors',
        active
          ? 'bg-tide/[0.08] ring-1 ring-inset ring-tide/30'
          : 'hover:bg-paper/80',
      )}
    >
      <div className="flex items-center justify-between gap-2 text-[12px]">
        <span className={cn('truncate', active ? 'font-semibold text-tide' : 'text-ink/85')}>
          {name}
        </span>
        <span className="shrink-0 text-[11px] text-ink/50 tnum">
          {obtained}/{total}
        </span>
      </div>
      <div className="mt-1.5 h-1 w-full rounded-full bg-ink/[0.06]">
        <div className="h-full rounded-full bg-moss" style={{ width: `${pct}%` }} />
      </div>
    </button>
  );
}

interface ChecklistTableProps {
  items: ChecklistItem[];
  categories: ChecklistCategorySummary[];
  transactionCounts: Map<string, number>;
  filter: ChecklistFilter;
  onFilterChange: Dispatch<SetStateAction<ChecklistFilter>>;
}

function ChecklistTable({
  items,
  categories,
  transactionCounts,
  filter,
  onFilterChange,
}: ChecklistTableProps) {
  const [expandedIds, setExpandedIds] = useState<Set<string>>(new Set());
  const filteredItems = items.filter((item) => matchesChecklistFilter(item, filter));
  const defaultFilter = isDefaultFilter(filter);

  const toggleExpand = (id: string) => {
    setExpandedIds((prev) => {
      const next = new Set(prev);
      if (next.has(id)) {
        next.delete(id);
      } else {
        next.add(id);
      }
      return next;
    });
  };

  return (
    <section className="relative min-w-0 overflow-hidden rounded-xl border border-ink/8 bg-white shadow-card">
      {!defaultFilter ? (
        <div
          className="flex flex-wrap items-center gap-1.5 px-4 py-2 text-[11px]"
          style={{ boxShadow: 'inset 0 -1px 0 rgba(22,33,38,0.08)' }}
        >
          <span className="text-ink/55">Filtering by</span>
          {filter.category !== 'all' ? (
            <FilterChip onClear={() => onFilterChange((f) => ({ ...f, category: 'all' }))}>
              {categoryNameById(categories, filter.category)}
            </FilterChip>
          ) : null}
          {filter.status !== 'all' ? (
            <FilterChip onClear={() => onFilterChange((f) => ({ ...f, status: 'all' }))}>
              {filter.status}
            </FilterChip>
          ) : null}
          {filter.priority !== 'all' ? (
            <FilterChip onClear={() => onFilterChange((f) => ({ ...f, priority: 'all' }))}>
              {filter.priority}
            </FilterChip>
          ) : null}
        </div>
      ) : null}

      <div className="flex flex-wrap items-center justify-between gap-3 border-b border-ink/[0.08] px-4 py-3">
        <span className="text-[11px] text-ink/50 tnum">
          {filteredItems.length} of {items.length} sources
        </span>
        <div className="flex flex-wrap items-center gap-1">
          <ChecklistFilterButton
            active={filter.status === 'all' && filter.priority === 'all'}
            onClick={() => onFilterChange((f) => ({ ...f, status: 'all', priority: 'all' }))}
          >
            All
          </ChecklistFilterButton>
          <ChecklistFilterButton
            active={filter.status === 'open' && filter.priority === 'all'}
            onClick={() => onFilterChange((f) => ({ ...f, status: 'open', priority: 'all' }))}
          >
            Open
          </ChecklistFilterButton>
          <ChecklistFilterButton
            active={filter.status === 'open' && filter.priority === 'essential'}
            onClick={() =>
              onFilterChange((f) => ({ ...f, status: 'open', priority: 'essential' }))
            }
          >
            Essential open
          </ChecklistFilterButton>
        </div>
      </div>

      <div className="max-h-[640px] overflow-auto">
        <table className="w-full table-fixed border-collapse text-left text-[13px]">
          <colgroup>
            <col style={{ width: '92px' }} />
            <col />
            <col style={{ width: '120px' }} />
            <col style={{ width: '110px' }} />
            <col style={{ width: '96px' }} />
            <col style={{ width: '32px' }} />
          </colgroup>
          <thead
            className="sticky top-0 z-[1] bg-white/95 backdrop-blur"
            style={{ boxShadow: 'inset 0 -1px 0 rgba(22,33,38,0.08)' }}
          >
            <tr className="text-[10px] uppercase tracking-[0.16em] text-ink/45">
              <th className="px-4 py-2.5 font-medium">Status</th>
              <th className="px-3 py-2.5 font-medium">Source</th>
              <th className="px-3 py-2.5 font-medium">Last modified</th>
              <th className="px-3 py-2.5 font-medium">Category</th>
              <th className="px-3 py-2.5 font-medium">Priority</th>
              <th className="px-3 py-2.5 font-medium" aria-label="Expand" />
            </tr>
          </thead>
          <tbody>
            {filteredItems.length === 0 ? (
              <tr>
                <td
                  colSpan={6}
                  className="px-4 py-12 text-center text-[12px] text-ink/55"
                >
                  No sources match this filter.{' '}
                  <button
                    type="button"
                    onClick={() => onFilterChange(DEFAULT_CHECKLIST_FILTER)}
                    className="text-tide underline hover:text-ink"
                  >
                    Clear filter
                  </button>
                </td>
              </tr>
            ) : (
              filteredItems.map((item) => (
                <ChecklistRow
                  key={item.id}
                  item={item}
                  transactionCounts={transactionCounts}
                  expanded={expandedIds.has(item.id)}
                  onToggleExpand={() => toggleExpand(item.id)}
                />
              ))
            )}
          </tbody>
        </table>
      </div>
    </section>
  );
}

function FilterChip({
  children,
  onClear,
}: {
  children: React.ReactNode;
  onClear: () => void;
}) {
  return (
    <span className="inline-flex items-center gap-1 rounded-full bg-tide/[0.1] py-0.5 pl-2 pr-1 text-[11px] font-medium capitalize text-tide">
      {children}
      <button
        type="button"
        onClick={onClear}
        aria-label="Remove filter"
        className="grid h-3.5 w-3.5 place-items-center rounded-full hover:bg-tide/20"
      >
        <X className="h-2.5 w-2.5" />
      </button>
    </span>
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
  expanded,
  onToggleExpand,
}: {
  item: ChecklistItem;
  transactionCounts: Map<string, number>;
  expanded: boolean;
  onToggleExpand: () => void;
}) {
  const isEssential = isEssentialItem(item);
  const isDeferred = item.priority.includes('Deferred');
  return (
    <>
      <tr
        className="cursor-pointer align-top hover:bg-paper/60"
        style={{ boxShadow: 'inset 0 -1px 0 rgba(22,33,38,0.08)' }}
        onClick={onToggleExpand}
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
                isEssential ? 'bg-ember/12 text-ember' : 'bg-ink/[0.06] text-ink/55',
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
        </td>
        <td className="px-3 py-3 text-[12px] text-ink/65 tnum">
          {item.lastModifiedMs ? formatRelativeTime(item.lastModifiedMs) : '—'}
        </td>
        <td className="px-3 py-3 text-[12px] text-ink/70">
          {categoryDisplayName(item.category)}
        </td>
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
        <td className="px-3 py-3 text-right">
          <span
            className={cn(
              'inline-block transition-transform',
              expanded ? 'rotate-180' : '',
            )}
          >
            <ChevronDown className="h-3.5 w-3.5 text-ink/45" />
          </span>
        </td>
      </tr>
      {expanded ? (
        <tr
          className="bg-paper/40"
          style={{ boxShadow: 'inset 0 -1px 0 rgba(22,33,38,0.08)' }}
        >
          <td />
          <td colSpan={5} className="px-3 pb-4 pt-1">
            <div className="rounded-md border border-ink/8 bg-white p-3">
              <div className="text-[10px] uppercase tracking-[0.16em] text-ink/45">
                Why this is needed
              </div>
              <p
                className="mt-1 text-[12px] leading-relaxed text-ink/75"
                style={{ textWrap: 'pretty' } as React.CSSProperties}
              >
                {item.whyNeeded}
              </p>
              {item.matchedFiles.length > 0 ? (
                <>
                  <div className="mt-3 text-[10px] uppercase tracking-[0.16em] text-ink/45">
                    Matched files
                  </div>
                  <ul className="mt-1 space-y-0.5">
                    {item.matchedFiles.map((file) => {
                      const ingestedCount = transactionCounts.get(file.filename) ?? 0;
                      return (
                        <li
                          key={file.relativePath}
                          className="flex min-w-0 items-baseline gap-2 text-[11px]"
                        >
                          <FileText className="h-3 w-3 shrink-0 text-ink/35" />
                          <span className="truncate font-mono text-ink/75">
                            {file.filename}
                          </span>
                          <span className="shrink-0 text-ink/35 tnum">
                            {file.score.toFixed(2)}
                          </span>
                          {ingestedCount > 0 ? (
                            <span className="shrink-0 text-moss tnum">
                              · {ingestedCount.toLocaleString()} tx
                            </span>
                          ) : null}
                        </li>
                      );
                    })}
                  </ul>
                </>
              ) : null}
            </div>
          </td>
        </tr>
      ) : null}
    </>
  );
}

function LoadingState() {
  return (
    <div className="rounded-xl border border-ink/8 bg-white p-6 text-sm text-ink/70 shadow-card">
      Loading source coverage from the tracker CSV.
    </div>
  );
}
