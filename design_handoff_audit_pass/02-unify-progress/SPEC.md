# 02 — Unify the progress story

**Audit findings addressed:**
- [Status numbers are echoed in three places](../Design%20Audit.html#f-stat-dupes) (high severity, intake & dashboard)
- [The 9 essential-missing items deserve a primary CTA](../Design%20Audit.html#f-essentials-cta) (high severity, intake)
- "Show notes" toggle is a preview affordance, not a product feature (medium, header)
- Brand mark's second line ("Local · 2026") is decorative (low, header)
- Surface the HYSA gate as a permanent micro-progress rail (medium, header)
- "Rescan folder" duplicates the 5-second auto-scan (medium, intake)
- Tab labels read like form-field names (low, tabs)
- Eyebrow + H1 + sentence is 3 lines for low-density information (low, page title)

**Scope:** Replaces the duplicate KPI strip + status pills with a single progress object and an essentials CTA. Tightens the sticky header. No new data calls; uses values already on the query results.

**Estimated effort:** ½ day.

**Prereq:** Pass 01 (color semantics) must be merged first. This pass relies on the new contract — Missing surfaces use ember, HYSA progress uses tide.

---

## The shape of the change

```
BEFORE                                  AFTER
┌──────────────────────────────────┐    ┌──────────────────────────────────┐
│ Brand | Tabs | Pills | Notes btn │    │ Brand | Tabs | HYSA rail         │
└──────────────────────────────────┘    └──────────────────────────────────┘
┌──────────────────────────────────┐    ┌─────────────────────────────────┐
│ Phase 1 · Intake                 │    │ Phase 1 · Intake — Document     │
│ Document checklist               │    │ checklist                       │
│ Driven by the tracker CSV...     │    └─────────────────────────────────┘
│                  [+ Add] [Rescan]│    ┌─────────────────────────────────┐
└──────────────────────────────────┘    │ 23 / 86 docs · 9 essential miss │
┌────┬────┬────┬────┐                   │ ▓▓▓░░░░░░░░░░░░░░░░░ [Start ess]│
│ 86 │ 23 │ 63 │ 9  │                   └─────────────────────────────────┘
│Trk │Got │Mis │Cat │                   ┌─────────────────────────────────┐
└────┴────┴────┴────┘                   │ ◉ /Users/jeff/.../intake [📁] │
┌──────────────────────────────────┐    │   14 files indexed · scans 5s  │
│ ◉ /Users/.../intake  14 indexed  │    └─────────────────────────────────┘
└──────────────────────────────────┘
```

Vertical chrome above the table: **~280px → ~140px.**

---

## File changes

### 1. `src/components/layout/app-shell.tsx`

#### a. Trim the brand block

**Before:**
```tsx
<div className="leading-tight">
  <div className="text-[13px] font-semibold tracking-tight">Liquidity Gate</div>
  <div className="text-[10px] uppercase tracking-[0.18em] text-ink/45">Local · 2026</div>
</div>
```

**After:**
```tsx
<div className="text-[13px] font-semibold tracking-tight">Liquidity Gate</div>
```

The "Local · 2026" line is implicit (it's a Tauri desktop app, the date control lives on the dashboard).

#### b. Shorten the tab labels

`Document intake` → `Intake`
`Cash-flow dashboard` → `Cash flow`

The long-form lives inside each view's eyebrow.

#### c. Replace `StatusPills` with `HysaRail`

`StatusPills` is gone. Define a new `HysaRail` component that lives in the same file (or extracted if you prefer):

```tsx
interface HysaRailProps {
  current: number;
  target: number;
}

function HysaRail({ current, target }: HysaRailProps) {
  const pct = target === 0 ? 0 : Math.min(current / target, 1) * 100;
  return (
    <div className="ml-auto hidden items-center gap-3 lg:flex">
      <div className="text-[10px] uppercase tracking-[0.16em] text-ink/55">HYSA gate</div>
      <div className="h-1 w-32 overflow-hidden rounded-full bg-ink/[0.08]">
        <div className="h-full bg-tide" style={{ width: `${pct}%` }} />
      </div>
      <div className="text-[12px] font-semibold tnum">{Math.round(pct)}%</div>
      <div className="text-[11px] text-ink/45 tnum">
        {currency(current, { compact: true })} / {currency(target, { compact: true })}
      </div>
    </div>
  );
}
```

This appears on **both** views — it's the cross-cutting progress anchor.

#### d. Drop the "Show notes" toggle from `AppShell`

It's a preview-only affordance from `redesign.html`. Removed entirely.

#### e. Updated prop signature

The `AppShell` props simplify. `obtainedDocuments`, `missingDocuments`, and `totalDocuments` are no longer consumed by the header (they're rendered inside the intake view now), so they can move out of the shell entirely.

**Before:**
```tsx
interface AppShellProps extends PropsWithChildren {
  view: AppView;
  onViewChange: (view: AppView) => void;
  totalDocuments: number;
  obtainedDocuments: number;
  missingDocuments: number;
  liquidityGateCurrent: number;
  liquidityGate: number;
}
```

**After:**
```tsx
interface AppShellProps extends PropsWithChildren {
  view: AppView;
  onViewChange: (view: AppView) => void;
  liquidityGateCurrent: number;
  liquidityGate: number;
}
```

#### f. Updated `App.tsx` caller

```tsx
<AppShell
  view={view}
  onViewChange={setView}
  liquidityGateCurrent={dashboardQuery.data?.gates[0]?.currentAmount ?? 0}
  liquidityGate={dashboardQuery.data?.gates[0]?.targetAmount ?? 0}
>
```

(Drop the three doc-count props.)

---

### 2. `src/features/document-intake/document-intake-view.tsx`

This view changes the most. The end-state ordering above the table is:

1. **Page title** — one line (eyebrow inline with H1)
2. **`<IntakeProgress />`** — one progress object with segmented rail
3. **`<EssentialsBanner />`** — only renders when `essentialMissingCount > 0`
4. **`<WatchRootStrip />`** — unchanged from pass 01 (gets reveal-in-Finder action in pass 03)

#### a. Collapse the page title

**Before:**
```tsx
<div className="mb-5 flex items-end justify-between gap-4">
  <div className="min-w-0">
    <div className="text-[11px] font-medium uppercase tracking-[0.18em] text-ink/45">Phase 1 · Intake</div>
    <h1 className="mt-1 text-[22px] font-semibold tracking-tight text-ink">Document checklist</h1>
    <p className="mt-1 max-w-2xl text-[13px] leading-relaxed text-ink/60">
      Driven by the tracker CSV and auto-matched against your watch folder.
    </p>
  </div>
  <div className="flex shrink-0 items-center gap-2">
    <button>Add item</button>
    <button>Rescan folder</button>
  </div>
</div>
```

**After:**
```tsx
<div className="mb-4 flex items-baseline gap-3">
  <span className="text-[11px] font-medium uppercase tracking-[0.18em] text-ink/45">Phase 1</span>
  <h1 className="text-[18px] font-semibold tracking-tight text-ink">Document checklist</h1>
</div>
```

- Single-line H1 paired with the phase eyebrow inline.
- Body sentence dropped — the column headers explain themselves.
- "Add item" removed: items come from the tracker CSV per the data model. (If ad-hoc additions are ever needed, surface inside the table toolbar as "Add custom item" in a later pass.)
- "Rescan folder" removed: the watch root auto-rescans every 5s — the manual button suggests the auto-scan is untrustworthy. If a force-rescan affordance is wanted, demote to a small icon button on the `WatchRootStrip` (see pass 03).

#### b. Delete the 4-tile KPI strip

The entire `<div className="mb-3 grid grid-cols-4 gap-3">…</div>` block goes away. The numbers it carried are now in `<IntakeProgress />`.

#### c. Add `<IntakeProgress />`

New component, inline in the file (extract to `components/intake-progress.tsx` if you prefer):

```tsx
interface IntakeProgressProps {
  total: number;
  obtained: number;
  missing: number;
  essentialMissing: number;
}

function IntakeProgress({ total, obtained, missing, essentialMissing }: IntakeProgressProps) {
  // Three segments: obtained (moss) | other missing (neutral) | essential missing (ember)
  // Widths are integer fractions so the bar reads honestly even at tiny scales.
  const otherMissing = Math.max(0, missing - essentialMissing);
  const obtainedPct = total === 0 ? 0 : (obtained / total) * 100;
  const otherPct = total === 0 ? 0 : (otherMissing / total) * 100;
  const essentialPct = total === 0 ? 0 : (essentialMissing / total) * 100;

  return (
    <div className="mb-3 rounded-xl border border-ink/8 bg-white p-4 shadow-card">
      <div className="flex items-baseline justify-between gap-3">
        <div className="min-w-0">
          <div className="text-[10px] font-medium uppercase tracking-[0.18em] text-ink/45">
            Intake progress
          </div>
          <div className="mt-0.5 flex items-baseline gap-2">
            <span className="text-[24px] font-semibold tnum text-ink">{obtained}</span>
            <span className="text-[13px] text-ink/55 tnum">of {total} documents</span>
            {essentialMissing > 0 && (
              <span className="text-[12px] text-ink/45">
                · <span className="font-semibold text-ember">{essentialMissing} essential missing</span>
              </span>
            )}
          </div>
        </div>
      </div>
      <div
        className="mt-3 grid h-1.5 gap-px overflow-hidden rounded-full bg-ink/[0.06]"
        style={{
          gridTemplateColumns: `${obtainedPct}fr ${otherPct}fr ${essentialPct}fr`,
        }}
      >
        <div className="bg-moss" />
        <div className="bg-ink/10" />
        <div className="bg-ember" />
      </div>
      <div className="mt-1.5 flex items-center justify-between text-[11px] text-ink/50 tnum">
        <span>{Math.round(obtainedPct)}% obtained</span>
        <span>
          {missing} missing
          {essentialMissing > 0 && ` · ${essentialMissing} essential`}
        </span>
      </div>
    </div>
  );
}
```

Edge cases the SPEC requires you to handle:
- `total === 0` → all three segments render as zero-width; the rail is the empty track. No NaNs.
- `essentialMissing === 0` → the essential callout in the headline disappears; the rail still renders three segments but the third is 0fr.
- `obtained === total` → the rail is fully moss.

#### d. Add `<EssentialsBanner />`

Renders only when `essentialMissingCount > 0`. Clicking the button **filters the table to Essential + Missing** and scrolls to the table. For pass 02, wiring the filter is in-scope — the table already supports the "Essential" filter pill in its toolbar, so this banner just programmatically activates it.

```tsx
interface EssentialsBannerProps {
  count: number;
  onStart: () => void;  // sets table filter to { status: 'missing', priority: 'essential' }
}

function EssentialsBanner({ count, onStart }: EssentialsBannerProps) {
  if (count === 0) return null;
  return (
    <div className="mb-6 flex items-center gap-3 rounded-xl border border-ember/30 bg-ember/[0.05] px-4 py-3">
      <span className="grid h-7 w-7 shrink-0 place-items-center rounded-full bg-ember text-fog">
        <AlertIcon className="h-3.5 w-3.5" />
      </span>
      <div className="min-w-0">
        <div className="text-[13px] font-semibold text-ink">
          {count} essential {count === 1 ? 'document' : 'documents'} still missing
        </div>
        <div className="text-[11px] text-ink/60">
          These block downstream phases — tax returns, payroll, mortgage statements, insurance.
        </div>
      </div>
      <button
        onClick={onStart}
        className="ml-auto shrink-0 rounded-full bg-ember px-3 py-1.5 text-[12px] font-semibold text-fog hover:bg-ember/90"
      >
        Start with essentials
      </button>
    </div>
  );
}
```

The intake view holds the table's filter state already (or needs to lift it into a shared parent). If the toolbar pills are currently uncontrolled internal state on the table component, lift them up in this pass — the banner needs to drive them.

#### e. Wire it all up

```tsx
export function DocumentIntakeView() {
  const { data, isLoading } = useDocumentChecklist();
  const transactionCounts = useTransactionCounts();
  const [filter, setFilter] = useState<TableFilter>({ status: 'all', priority: 'all' });

  if (!data) return <IntakeSkeleton />;

  const summary = data.summary;

  return (
    <div>
      <div className="mb-4 flex items-baseline gap-3">
        <span className="text-[11px] font-medium uppercase tracking-[0.18em] text-ink/45">Phase 1</span>
        <h1 className="text-[18px] font-semibold tracking-tight text-ink">Document checklist</h1>
      </div>

      <IntakeProgress
        total={summary.totalItems}
        obtained={summary.obtainedCount}
        missing={summary.missingCount}
        essentialMissing={summary.essentialMissingCount}
      />

      <EssentialsBanner
        count={summary.essentialMissingCount}
        onStart={() => setFilter({ status: 'missing', priority: 'essential' })}
      />

      <WatchRootStrip status={data.watchRoot} />

      <div className="grid gap-5" style={{ gridTemplateColumns: 'minmax(0, 280px) minmax(0, 1fr)' }}>
        <CategorySidebar categories={summary.categories} />
        <ChecklistTable
          items={data.items}
          transactionCounts={transactionCounts}
          filter={filter}
          onFilterChange={setFilter}
        />
      </div>
    </div>
  );
}
```

#### f. New summary field — `essentialMissingCount`

If `ChecklistDataset.summary` doesn't already expose `essentialMissingCount`, add it.

**Important:** this is a derived field, not a new data source. It comes from the same `items` array the checklist already has. Compute it in the existing summary builder (likely in `loadChecklistDataset` or a `summarize()` helper):

```ts
essentialMissingCount: items.filter(
  i => i.status === 'missing' && i.priority === 'essential'
).length,
```

Update the `ChecklistSummary` type with the new field. Adjust any test fixtures.

---

### 3. `src/features/dashboard/dashboard-view.tsx`

Minimal changes here — pass 02 is mostly intake-focused. Just:

#### a. Drop the "Phase 2 · Reconstruction" body paragraph

Same collapse as intake. Eyebrow + H1 inline.

**Before:**
```tsx
<div className="text-[11px] font-medium uppercase tracking-[0.18em] text-ink/45">Phase 2 · Reconstruction</div>
<h1 className="mt-1 text-[22px] font-semibold tracking-tight text-ink">Cash-flow dashboard</h1>
<p className="mt-1 max-w-2xl text-[13px] leading-relaxed text-ink/60">
  Reconstructed from the local transactions table. Transfers excluded.
</p>
```

**After:**
```tsx
<div className="mb-4 flex items-baseline gap-3">
  <span className="text-[11px] font-medium uppercase tracking-[0.18em] text-ink/45">Phase 2</span>
  <h1 className="text-[18px] font-semibold tracking-tight text-ink">Cash flow</h1>
</div>
```

The "Transfers excluded" detail moves to a footnote near the chart, or to a tooltip on the chart title — that's a pass 04 concern.

#### b. Drop the HYSA gate from the KPI strip

The HYSA gate now lives permanently in the header rail. Showing it again in the KPI strip below the page title is the very duplication this pass is meant to remove. Delete the fourth KPI tile.

The KPI strip becomes a 3-up: `Net YTD`, `Avg monthly inflow`, `Avg monthly outflow`. The grid changes from `grid-cols-4` to `grid-cols-3`.

If you want to keep the slot at 4-up width, the audit suggests a `Range` segmented control as the fourth tile — that's worth doing if it doesn't pull in pass 04 scope. Otherwise let the three tiles breathe.

---

## Acceptance criteria

- [ ] Header: brand has one line, tabs read "Intake" and "Cash flow", HYSA progress rail appears on both views, "Show notes" is gone
- [ ] Intake view above the table is ≤ 3 visual blocks tall (title row + progress object + essentials banner + watch-root)
- [ ] Intake progress rail has three segments (obtained / other missing / essential missing) with correct widths summing to 100%
- [ ] Edge case: when `essentialMissingCount === 0`, the EssentialsBanner does not render and the headline drops its essential callout
- [ ] Edge case: when `total === 0`, no NaN values; rail renders as the empty track
- [ ] Clicking "Start with essentials" filters the table to `status=missing, priority=essential` and scrolls to it
- [ ] Dashboard KPI strip is 3-up (or 3+range-control); HYSA tile is removed
- [ ] `summary.essentialMissingCount` is a real derived field on `ChecklistSummary`; type passes
- [ ] `npm test` passes (existing tests on the matcher should be untouched; if any summary-related tests existed, update fixtures with the new field)
- [ ] No new TypeScript errors
- [ ] Vertical chrome above the intake table at 1280px is ~140px (measure with devtools — should be roughly half of the current ~280px)

---

## Notes for the implementer

- This pass introduces one new piece of state (`filter` lifted into `DocumentIntakeView`) and one new derived summary field (`essentialMissingCount`). Both are small. Resist the temptation to thread filter state up further; it's intake-local.
- If you find yourself building a new icon library, stop — re-use the existing inline SVGs from `redesign.html` as the source of truth.
- The `<IntakeProgress />` rail is a CSS-grid trick. Don't switch it to absolute-positioned children; the grid version handles zero-width segments cleanly.
- If pass 01 (color semantics) hasn't shipped yet, this pass will look wrong (the new `IntakeProgress` headline references `text-ember`, the `EssentialsBanner` uses `bg-ember`, and the header `HysaRail` uses `bg-tide`). Don't merge this without 01.
