import type { UseQueryResult } from '@tanstack/react-query';
import { AlertTriangle, CheckCircle2, FileSearch, FolderSync, FileCheck2 } from 'lucide-react';
import { Badge } from '../../components/ui/badge';
import { Card } from '../../components/ui/card';
import type { ChecklistDataset, WatchRootStatus } from './types';

interface DocumentIntakeViewProps {
  query: UseQueryResult<ChecklistDataset, Error>;
}

export function DocumentIntakeView({ query }: DocumentIntakeViewProps) {
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

  return (
    <div className="grid gap-6">
      <section className="grid gap-4 lg:grid-cols-[1.2fr,0.8fr]">
        <Card className="overflow-hidden bg-[linear-gradient(180deg,rgba(255,255,255,0.86),rgba(255,255,255,0.7))]">
          <div className="flex flex-col gap-5 lg:flex-row lg:items-end lg:justify-between">
            <div className="space-y-3">
              <Badge tone="info">Document Intake</Badge>
              <h2 className="text-2xl font-semibold text-ink">86 reference items, one local source of truth.</h2>
              <p className="max-w-2xl text-sm text-ink/70">
                The intake board is driven by the extracted tracker CSV in docs and is designed to stay in lockstep with the Python MCP server’s metadata scan.
              </p>
              <WatchRootBanner watchRoot={watchRoot} />
            </div>
            <div className="grid grid-cols-2 gap-3 text-sm sm:grid-cols-4">
              <Metric icon={FileSearch} label="Tracked" value={summary.totalItems} />
              <Metric icon={CheckCircle2} label="Obtained" value={summary.obtainedCount} />
              <Metric icon={AlertTriangle} label="Missing" value={summary.missingCount} />
              <Metric icon={FolderSync} label="Categories" value={summary.categories.length} />
            </div>
          </div>
        </Card>
        <Card>
          <div className="space-y-4">
            <div>
              <p className="text-xs uppercase tracking-[0.18em] text-ink/55">Phase Focus</p>
              <h3 className="mt-2 text-xl font-semibold text-ink">Minimum viable dashboard inputs</h3>
            </div>
            <ul className="space-y-3 text-sm text-ink/75">
              <li>Chase, Beacon, Ally, and Ashley transaction coverage close the reconciliation loop.</li>
              <li>Both payroll streams, RSU events, and contribution elections anchor forward cash-flow modeling.</li>
              <li>Debt, insurance, and rental documents define the non-negotiable burn rate and liquidity pressure.</li>
            </ul>
          </div>
        </Card>
      </section>

      <section className="grid gap-6 xl:grid-cols-[0.68fr,1.32fr]">
        <Card>
          <div className="space-y-4">
            <div>
              <p className="text-xs uppercase tracking-[0.18em] text-ink/55">Checklist Heatmap</p>
              <h3 className="mt-2 text-xl font-semibold text-ink">Status by category</h3>
            </div>
            <div className="space-y-4">
              {summary.categories.map((category) => {
                const completion = category.total === 0 ? 0 : category.obtained / category.total;

                return (
                  <div key={category.category} className="space-y-2">
                    <div className="flex items-center justify-between gap-3 text-sm">
                      <span className="font-medium text-ink">{category.category}</span>
                      <span className="text-ink/55">
                        {category.obtained}/{category.total}
                      </span>
                    </div>
                    <div className="h-3 rounded-full bg-sand/25">
                      <div
                        className="h-3 rounded-full bg-[linear-gradient(90deg,#4e6a57,#2d5f73)] transition-all"
                        style={{ width: `${completion * 100}%` }}
                      />
                    </div>
                  </div>
                );
              })}
            </div>
          </div>
        </Card>

        <Card className="overflow-hidden p-0">
          <div className="border-b border-sand/35 px-6 py-5">
            <p className="text-xs uppercase tracking-[0.18em] text-ink/55">Primary Tracker</p>
            <h3 className="mt-2 text-xl font-semibold text-ink">Document checklist</h3>
          </div>
          <div className="max-h-[46rem] overflow-auto">
            <table className="min-w-full border-collapse text-left text-sm">
              <thead className="sticky top-0 bg-fog/95 backdrop-blur">
                <tr className="border-b border-sand/35 text-xs uppercase tracking-[0.16em] text-ink/55">
                  <th className="px-6 py-4">Status</th>
                  <th className="px-4 py-4">Document</th>
                  <th className="px-4 py-4">Category</th>
                  <th className="px-4 py-4">Priority</th>
                  <th className="px-4 py-4">Why Needed</th>
                </tr>
              </thead>
              <tbody>
                {items.map((item) => (
                  <tr key={item.id} className="border-b border-sand/20 align-top hover:bg-white/50">
                    <td className="px-6 py-4">
                      <Badge tone={item.obtained ? 'success' : 'warning'}>
                        {item.obtained ? 'Obtained' : 'Missing'}
                      </Badge>
                      {item.obtainedSource === 'filesystem' ? (
                        <div className="mt-1 flex items-center gap-1 text-[10px] uppercase tracking-[0.14em] text-moss">
                          <FileCheck2 className="h-3 w-3" />
                          Auto-matched
                        </div>
                      ) : null}
                    </td>
                    <td className="px-4 py-4">
                      <div className="font-medium text-ink">{item.document}</div>
                      <div className="mt-1 text-xs text-ink/55">{item.format}</div>
                      {item.matchedFiles.length > 0 ? (
                        <ul className="mt-2 space-y-0.5 text-xs text-ink/70">
                          {item.matchedFiles.map((file) => (
                            <li key={file.relativePath} className="flex items-baseline gap-2">
                              <span className="font-mono text-ink/80">{file.filename}</span>
                              <span className="text-ink/45">{file.score.toFixed(2)}</span>
                            </li>
                          ))}
                        </ul>
                      ) : null}
                    </td>
                    <td className="px-4 py-4 text-ink/75">{item.category}</td>
                    <td className="px-4 py-4">
                      <Badge tone={item.priority.includes('Essential') ? 'danger' : 'neutral'}>
                        {item.priority}
                      </Badge>
                    </td>
                    <td className="px-4 py-4 text-ink/75">{item.whyNeeded}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </Card>
      </section>
    </div>
  );
}

function Metric({
  icon: Icon,
  label,
  value,
}: {
  icon: typeof FileSearch;
  label: string;
  value: number;
}) {
  return (
    <div className="rounded-[22px] border border-sand/30 bg-fog/70 p-4">
      <div className="flex items-center gap-2 text-ink/55">
        <Icon className="h-4 w-4" />
        <span className="text-xs uppercase tracking-[0.18em]">{label}</span>
      </div>
      <div className="mt-3 text-2xl font-semibold text-ink">{value}</div>
    </div>
  );
}

function LoadingState() {
  return (
    <Card className="text-sm text-ink/70">
      Building the intake board from the tracker CSV.
    </Card>
  );
}

function WatchRootBanner({ watchRoot }: { watchRoot: WatchRootStatus }) {
  if (!watchRoot.root) {
    return (
      <p className="text-xs text-ink/55">
        Filesystem matching is only active inside the Tauri desktop app.
      </p>
    );
  }
  if (!watchRoot.exists) {
    return (
      <p className="text-xs text-ember">
        Watch root {' '}<span className="font-mono">{watchRoot.root}</span>{' '}
        does not exist yet. Create it and drop your statements there to auto-match against the tracker.
      </p>
    );
  }
  return (
    <p className="text-xs text-ink/60">
      Watching {' '}<span className="font-mono text-ink/80">{watchRoot.root}</span>{' '}
      — {watchRoot.fileCount} file{watchRoot.fileCount === 1 ? '' : 's'} indexed. Rescans every 5 seconds.
    </p>
  );
}
