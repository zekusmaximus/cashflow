import type { UseQueryResult } from '@tanstack/react-query';
import {
  AlertTriangle,
  Check,
  CheckCircle2,
  FileText,
  Folder,
  LayoutGrid,
  Plus,
  Search,
  ScanLine,
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

export function DocumentIntakeView({ query }: DocumentIntakeViewProps) {
  const transactionCountsQuery = useTransactionCounts();

  if (query.isLoading) {
    return <LoadingState />;
  }

  if (query.isError || !query.data) {
    return (
      <Card className="text-sm text-ember">
        Unable to load the intake checklist. Verify that the tracker CSV exists in the docs folder.
      </Card>
    );
  }

  const { items, summary, watchRoot } = query.data;
  const transactionCounts = transactionCountsQuery.data ?? new Map<string, number>();
  const essentialMissing = items.filter(
    (item) => !item.obtained && item.priority.includes('Essential'),
  ).length;
  const obtainedPercent =
    summary.totalItems === 0
      ? 0
      : Math.round((summary.obtainedCount / summary.totalItems) * 100);

  return (
    <div>
      <div className="mb-5 flex items-end justify-between gap-4">
        <div className="min-w-0">
          <div className="text-[11px] font-medium uppercase tracking-[0.18em] text-ink/45">
            Phase 1 · Intake
          </div>
          <h1 className="mt-1 text-[22px] font-semibold tracking-tight text-ink">
            Document checklist
          </h1>
          <p className="mt-1 max-w-2xl text-[13px] leading-relaxed text-ink/60">
            Driven by the tracker CSV and auto-matched against your watch folder.
          </p>
        </div>
        <div className="flex shrink-0 items-center gap-2">
          <button
            type="button"
            className="inline-flex items-center gap-1.5 rounded-full border border-ink/15 bg-white px-3 py-1.5 text-[12px] font-medium text-ink/75 hover:border-ink/35"
          >
            <Plus className="h-3.5 w-3.5" />
            Add item
          </button>
          <button
            type="button"
            className="inline-flex items-center gap-1.5 rounded-full bg-ink px-3 py-1.5 text-[12px] font-medium text-fog hover:bg-tide"
          >
            <ScanLine className="h-3.5 w-3.5" />
            Rescan folder
          </button>
        </div>
      </div>

      <div className="mb-3 grid grid-cols-2 gap-3 sm:grid-cols-4">
        <MetricTile icon={Search} label="Tracked" value={summary.totalItems} />
        <MetricTile
          icon={CheckCircle2}
          label="Obtained"
          value={summary.obtainedCount}
          tone="moss"
          caption={`${obtainedPercent}%`}
        />
        <MetricTile
          icon={AlertTriangle}
          label="Missing"
          value={summary.missingCount}
          tone="clay"
          caption={`${essentialMissing} essential`}
        />
        <MetricTile icon={LayoutGrid} label="Categories" value={summary.categories.length} />
      </div>

      <WatchRootStrip status={watchRoot} />

      <div
        className="grid gap-5"
        style={{ gridTemplateColumns: 'minmax(0, 280px) minmax(0, 1fr)' }}
      >
        <CategorySidebar categories={summary.categories} />
        <ChecklistTable items={items} transactionCounts={transactionCounts} />
      </div>
    </div>
  );
}

interface MetricTileProps {
  icon: typeof Search;
  label: string;
  value: number;
  tone?: 'moss' | 'clay';
  caption?: string;
}

function MetricTile({ icon: Icon, label, value, tone, caption }: MetricTileProps) {
  const labelToneClass =
    tone === 'moss' ? 'text-moss' : tone === 'clay' ? 'text-clay' : 'text-ink/50';
  const valueToneClass =
    tone === 'moss' ? 'text-moss' : tone === 'clay' ? 'text-clay' : 'text-ink';
  return (
    <div className="rounded-xl border border-ink/8 bg-white p-3.5 shadow-card">
      <div
        className={cn(
          'flex items-center gap-1.5 text-[10px] font-medium uppercase tracking-[0.18em]',
          labelToneClass,
        )}
      >
        <Icon className="h-3 w-3" />
        {label}
      </div>
      <div className="mt-1.5 flex items-baseline gap-2">
        <span className={cn('text-[22px] font-semibold tnum', valueToneClass)}>{value}</span>
        {caption ? <span className="text-[11px] text-ink/45 tnum">{caption}</span> : null}
      </div>
    </div>
  );
}

function WatchRootStrip({ status }: { status: WatchRootStatus }) {
  if (!status.root) {
    return (
      <div className="mb-6 rounded-xl border border-ink/8 bg-white px-3.5 py-2.5 text-[12px] text-ink/55 shadow-card">
        Filesystem matching is only active inside the Tauri desktop app.
      </div>
    );
  }
  const ok = status.exists;
  const stateLabel = ok ? 'Watching' : 'Missing';
  const iconBgClass = ok ? 'bg-moss/12 text-moss' : 'bg-ember/12 text-ember';
  return (
    <div className="mb-6 flex items-center gap-3 rounded-xl border border-ink/8 bg-white px-3.5 py-2.5 shadow-card">
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
            Status by
          </div>
          <div className="text-[14px] font-semibold text-ink">Category</div>
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
}

function ChecklistTable({ items, transactionCounts }: ChecklistTableProps) {
  return (
    <section className="relative min-w-0 overflow-hidden rounded-xl border border-ink/8 bg-white shadow-card">
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
              <th className="px-3 py-2.5 font-medium">Document</th>
              <th className="px-3 py-2.5 font-medium">Category</th>
              <th className="px-3 py-2.5 font-medium">Priority</th>
              <th className="px-4 py-2.5 font-medium">Why needed</th>
            </tr>
          </thead>
          <tbody>
            {items.map((item) => (
              <ChecklistRow
                key={item.id}
                item={item}
                transactionCounts={transactionCounts}
              />
            ))}
          </tbody>
        </table>
      </div>
    </section>
  );
}

function ChecklistRow({
  item,
  transactionCounts,
}: {
  item: ChecklistItem;
  transactionCounts: Map<string, number>;
}) {
  const isEssential = item.priority.includes('Essential');
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
              Got it
            </span>
            {item.obtainedSource === 'filesystem' ? (
              <div className="mt-1 flex items-center gap-1 text-[10px] uppercase tracking-[0.14em] text-moss/80">
                <Check className="h-2.5 w-2.5" />
                Auto-matched
              </div>
            ) : null}
          </>
        ) : (
          <span className="inline-flex items-center gap-1.5 rounded-full bg-clay/12 px-2 py-0.5 text-[11px] font-medium text-clay">
            <span className="h-1.5 w-1.5 rounded-full bg-clay" />
            Missing
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
              ? 'border-ember/30 bg-ember/8 text-ember'
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
      Building the intake board from the tracker CSV.
    </div>
  );
}
