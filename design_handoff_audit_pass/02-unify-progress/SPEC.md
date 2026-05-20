# 02 — Unify the progress story

**Audit findings addressed:**
- [Status numbers are echoed in three places](../Design%20Audit.html#f-stat-dupes) (high severity, intake & dashboard)
- [The essential-open items deserve a primary CTA](../Design%20Audit.html#f-essentials-cta) (high severity, intake)
- Brand mark's second line ("Local · 2026") is decorative (low, header)
- Surface the HYSA gate as a permanent micro-progress rail (medium, header)
- "Rescan folder" duplicates the 5-second auto-scan (medium, intake)
- "Cash-flow dashboard" tab label is heavy (low, tabs)
- Eyebrow + H1 + sentence is 3 lines for low-density information (low, page title)

**Scope:** Replaces the duplicate KPI strip + status pills with a single progress object and an essentials CTA. Tightens the sticky header. No new data calls; uses values already on the query results.

**Estimated effort:** ½ day.

**Prereq:** Pass 01 (color semantics) merged. This pass's components reference the new contract (ember for essentials-still-open, tide for HYSA progress).

---

## Live-code vocabulary

The SPEC is written against the **actual** terminology already in the codebase, not the audit's HTML mockup vocabulary. Quick reference:

| Concept | Live code |
|---|---|
| The view tab | `Source intake` (keep — already correct) |
| Total essential source count | `coreSources` |
| Essential sources obtained | `coreReady` |
| Non-essential (deferrable) source count | `openLater` |
| Per-row priority flag | `isEssential` (boolean) |
| Per-row status enum | `'ready' \| 'open'` (assumed — verify) |
| The "still need it" badge text | `Open` |

**Derived shorthand used throughout this SPEC:**
```ts
const essentialsGap = coreSources - coreReady;  // = essential sources still open
```

If your data model differs from these assumptions (e.g. status enum uses different values, or `openLater` semantics differ from "non-essential"), adapt locally and flag in the PR description.

---

## The shape of the change

```
BEFORE                                  AFTER
┌──────────────────────────────────┐    ┌──────────────────────────────────┐
│ Brand | Tabs | StatusPills       │    │ Brand | Tabs | HysaRail          │
└──────────────────────────────────┘    └──────────────────────────────────┘
┌──────────────────────────────────┐    ┌─────────────────────────────────┐
│ Phase 1 · Intake                 │    │ Phase 1 — Source intake         │
│ Source intake (H1)               │    └─────────────────────────────────┘
│ Driven by the tracker CSV...     │    ┌─────────────────────────────────┐
│                  [+ Add] [Rescan]│    │ 23 / 30 core ready              │
└──────────────────────────────────┘    │ ▓▓▓░░░░░░░░░░░░░░░ 7 essential  │
┌────┬────┬────┬────┐                   │                       still open │
│ 30 │ 23 │ 7  │ 56 │                   └─────────────────────────────────┘
│Trk │Got │Mis │Cat │                   ┌─────────────────────────────────┐
└────┴────┴────┴────┘                   │ ▲ 7 essential sources still open│
┌──────────────────────────────────┐    │   blocking downstream… [Start →]│
│ ◉ /Users/.../intake  14 indexed  │    └─────────────────────────────────┘
└──────────────────────────────────┘    ┌─────────────────────────────────┐
                                        │ ◉ /Users/.../intake  14 indexed │
                                        └─────────────────────────────────┘
```

Vertical chrome above the table: **~280px → ~140px.**

---

## File changes

### 1. `src/components/layout/app-shell.tsx`

#### a. Trim the brand block

**Before:**
```tsx
<div className="leading-tight">
  <div className="text-[13px] font-semibold tracking-tight text-ink">Liquidity Gate</div>
  <div className="text-[10px] uppercase tracking-[0.18em] text-ink/45">Local · 2026</div>
</div>
```

**After:**
```tsx
<div className="text-[13px] font-semibold tracking-tight text-ink">Liquidity Gate</div>
```

The "Local · 2026" line is implicit (it's a Tauri desktop app; the date control lives on the dashboard).

#### b. Shorten one tab label

`Source intake` — **keep**. (Already concise.)
`Cash-flow dashboard` → `Cash flow`.

The long form lives in the dashboard view's eyebrow.

#### c. Replace `StatusPills` with `HysaRail`

`StatusPills` goes away entirely. The `coreReady` / `coreSources` / `openLater` numbers move into the intake view's new `IntakeProgress` block — they were duplicates of what the view itself already needs to render. The header keeps one number: HYSA gate progress, the cross-cutting constraint that earns permanent chrome.

Add a new `HysaRail` component in the same file:

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
      <div className="text-[12px] font-semibold tnum text-ink">{Math.round(pct)}%</div>
      <div className="text-[11px] text-ink/45 tnum">
        {currency(current)} / {currency(target)}
      </div>
    </div>
  );
}
```

This appears on **both** views — the rail is the cross-cutting progress anchor.

#### d. Simplified `AppShellProps`

**Before:**
```tsx
interface AppShellProps extends PropsWithChildren {
  view: AppView;
  onViewChange: (view: AppView) => void;
  coreSources: number;
  coreReady: number;
  openLater: number;
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

The shell stops needing the three source-count props — the intake view holds them now.

#### e. Updated `App.tsx` caller

```tsx
<AppShell
  view={view}
  onViewChange={setView}
  liquidityGateCurrent={dashboardQuery.data?.gates[0]?.currentAmount ?? 0}
  liquidityGate={dashboardQuery.data?.gates[0]?.targetAmount ?? 0}
>
```

Drop `coreSources`, `coreReady`, `openLater` from this call site. They still need to reach the intake view — pass them down through the existing intake-view props (or wherever the view reads from the checklist query).

---

### 2. `src/features/document-intake/document-intake-view.tsx`

The end-state ordering above the table:

1. **Page title** — one line (eyebrow inline with H1)
2. **`<IntakeProgress />`** — one progress object with segmented rail
3. **`<EssentialsBanner />`** — only renders when `essentialsGap > 0`
4. **Watch-root strip** — unchanged from pass 01 (gets reveal-in-Finder actions in pass 03)

#### a. Collapse the page title

**Before:**
```tsx
<div className="mb-5 flex items-end justify-between gap-4">
  <div className="min-w-0">
    <div className="text-[11px] font-medium uppercase tracking-[0.18em] text-ink/45">Phase 1 · Intake</div>
    <h1 className="mt-1 text-[22px] font-semibold tracking-tight text-ink">Source intake</h1>
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
  <h1 className="text-[18px] font-semibold tracking-tight text-ink">Source intake</h1>
</div>
```

- Single-line H1 paired with the phase eyebrow inline.
- Body sentence dropped — the columns explain themselves.
- "Add item" removed: sources come from the tracker CSV per the data model.
- "Rescan folder" removed: the watch root auto-rescans every 5s — the manual button suggests the auto-scan is untrustworthy. If a force-rescan affordance is wanted, demote to a small icon button on the watch-root strip (pass 03's territory).

#### b. Delete the 4-tile KPI strip

The entire `<div>` block holding the four MetricTiles (Tracked / Core ready / Open later / Categories) goes away. The numbers it carried are now in `<IntakeProgress />`.

`MetricTile` itself can be deleted from the file if nothing else consumes it. Check first.

#### c. Add `<IntakeProgress />`

New component, inline in the file (extract to `components/intake-progress.tsx` if you prefer):

```tsx
interface IntakeProgressProps {
  coreReady: number;
  coreSources: number;
  openLater: number;
}

function IntakeProgress({ coreReady, coreSources, openLater }: IntakeProgressProps) {
  const essentialsGap = Math.max(0, coreSources - coreReady);
  // Three segments of the rail:
  //   moss   = core ready
  //   ember  = essentials still open (the actionable gap)
  //   ink/10 = open later (deferrable, non-essential)
  const total = coreSources + openLater;
  const readyPct = total === 0 ? 0 : (coreReady / total) * 100;
  const gapPct = total === 0 ? 0 : (essentialsGap / total) * 100;
  const laterPct = total === 0 ? 0 : (openLater / total) * 100;
  const readyOfCorePct = coreSources === 0 ? 0 : Math.round((coreReady / coreSources) * 100);

  return (
    <div className="mb-3 rounded-xl border border-ink/8 bg-white p-4 shadow-card">
      <div className="flex items-baseline gap-3">
        <div className="min-w-0">
          <div className="text-[10px] font-medium uppercase tracking-[0.18em] text-ink/45">
            Source intake
          </div>
          <div className="mt-0.5 flex items-baseline gap-2">
            <span className="text-[24px] font-semibold tnum text-ink">{coreReady}</span>
            <span className="text-[13px] text-ink/55 tnum">of {coreSources} core sources ready</span>
            {essentialsGap > 0 && (
              <span className="text-[12px] text-ink/45">
                · <span className="font-semibold text-ember">{essentialsGap} essential still open</span>
              </span>
            )}
          </div>
        </div>
      </div>
      <div
        className="mt-3 grid h-1.5 gap-px overflow-hidden rounded-full bg-ink/[0.06]"
        style={{
          gridTemplateColumns: `${readyPct}fr ${gapPct}fr ${laterPct}fr`,
        }}
      >
        <div className="bg-moss" />
        <div className="bg-ember" />
        <div className="bg-ink/10" />
      </div>
      <div className="mt-1.5 flex items-center justify-between text-[11px] text-ink/50 tnum">
        <span>{readyOfCorePct}% of core ready</span>
        <span>
          {essentialsGap > 0 && `${essentialsGap} essential · `}
          {openLater} open later
        </span>
      </div>
    </div>
  );
}
```

Edge cases this SPEC requires you to handle:
- `coreSources === 0` → readiness percentage is 0; no NaN; rail renders empty
- `essentialsGap === 0` → the essential callout in the headline disappears; the rail's middle segment is 0fr
- `coreReady === coreSources` AND `openLater === 0` → rail is fully moss
- All `tnum` numeric values render via `font-variant-numeric: tabular-nums` (the `.tnum` utility from your existing Tailwind setup)

#### d. Add `<EssentialsBanner />`

Renders only when `essentialsGap > 0`. Clicking the button **filters the table to essential + open** and scrolls to the table.

```tsx
interface EssentialsBannerProps {
  count: number;          // essentialsGap
  onStart: () => void;    // sets table filter to { status: 'open', priority: 'essential' }
}

function EssentialsBanner({ count, onStart }: EssentialsBannerProps) {
  if (count === 0) return null;
  return (
    <div className="mb-6 flex items-center gap-3 rounded-xl border border-ember/30 bg-ember/[0.05] px-4 py-3">
      <span className="grid h-7 w-7 shrink-0 place-items-center rounded-full bg-ember text-fog">
        <svg viewBox="0 0 16 16" className="h-3.5 w-3.5" fill="none" stroke="currentColor" strokeWidth={2} strokeLinecap="round">
          <path d="M8 1.5l6.5 11.5h-13L8 1.5zM8 6v3M8 11v.01" />
        </svg>
      </span>
      <div className="min-w-0">
        <div className="text-[13px] font-semibold text-ink">
          {count} essential {count === 1 ? 'source' : 'sources'} still open
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

The table's filter state needs to live in this view (or be lifted to a parent that already exists) so the banner can drive it. If the table's "Essential" / "All / Missing / Essential" pills are uncontrolled internal state, lift them up in this pass — the banner needs to set them.

The filter shape that the table consumes:

```ts
type TableFilter = {
  status: 'all' | 'open' | 'ready';
  priority: 'all' | 'essential' | 'useful';
};
```

(The exact field names should match whatever the table component already accepts. Adapt if it uses different keys — e.g. if it uses `isEssential: true | false | null`. The point is: the banner pushes a state object that narrows the table to "essential AND open".)

#### e. Wire it all up

```tsx
export function DocumentIntakeView() {
  const { data } = useDocumentChecklist();
  const transactionCounts = useTransactionCounts();
  const [filter, setFilter] = useState<TableFilter>({ status: 'all', priority: 'all' });

  if (!data) return <IntakeSkeleton />;

  const { coreReady, coreSources, openLater } = data.summary;
  const essentialsGap = Math.max(0, coreSources - coreReady);

  return (
    <div>
      <div className="mb-4 flex items-baseline gap-3">
        <span className="text-[11px] font-medium uppercase tracking-[0.18em] text-ink/45">Phase 1</span>
        <h1 className="text-[18px] font-semibold tracking-tight text-ink">Source intake</h1>
      </div>

      <IntakeProgress
        coreReady={coreReady}
        coreSources={coreSources}
        openLater={openLater}
      />

      <EssentialsBanner
        count={essentialsGap}
        onStart={() => setFilter({ status: 'open', priority: 'essential' })}
      />

      <WatchRootStrip status={data.watchRoot} />

      <div className="grid gap-5" style={{ gridTemplateColumns: 'minmax(0, 280px) minmax(0, 1fr)' }}>
        <CategorySidebar categories={data.summary.categories} />
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

If your `data.summary` doesn't currently expose `coreReady`, `coreSources`, `openLater` as a single shape (they might be top-level on `data` or derived inside `useDocumentChecklist`), wire from wherever they live. **No new query, no new derived field needed** — the AppShell was already consuming these numbers, so they're already on the query result somewhere.

---

### 3. `src/features/dashboard/dashboard-view.tsx`

Minimal changes — pass 02 is mostly intake-focused.

#### a. Drop the page-title body paragraph

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

The "Transfers excluded" detail moves to a footnote near the chart, or to a tooltip on the chart title (pass 04 concern).

#### b. Drop the HYSA gate from the KPI strip

The HYSA gate now lives permanently in the header rail. Showing it again in the KPI strip below the page title is the duplication this pass removes.

The KPI strip becomes a 3-up: `Net YTD`, `Avg monthly inflow`, `Avg monthly outflow`. Change the grid from `grid-cols-4` to `grid-cols-3`.

If you'd rather keep the slot at 4-up, put the `Range` segmented control (YTD / 12mo / All) in the fourth tile. Either is fine.

---

## Acceptance criteria

- [ ] Header: brand is one line, tabs read `Source intake` and `Cash flow`, `HysaRail` appears on both views, `StatusPills` is removed
- [ ] `AppShellProps` no longer contains `coreReady` / `coreSources` / `openLater`; `App.tsx` caller updated
- [ ] Intake view above the table is ≤ 4 visual blocks tall (title row + IntakeProgress + EssentialsBanner + watch-root)
- [ ] IntakeProgress rail has three segments (core ready / essentials gap / open later) summing to 100%
- [ ] Edge case: `essentialsGap === 0` → EssentialsBanner does not render; headline drops its essential callout
- [ ] Edge case: `coreSources === 0` → no NaN; rail renders as the empty track
- [ ] Clicking "Start with essentials" filters the table to `status=open, priority=essential` (or your equivalent enum values) and the table is visible without scrolling
- [ ] Dashboard KPI strip is 3-up (or 3 + range control); HYSA gate tile is removed
- [ ] No new TypeScript errors; `npm test` passes
- [ ] Vertical chrome above the intake table at 1280px is ~140px (measure with devtools — should be roughly half of the current ~280px)

---

## Notes for the implementer

- **Verify the assumptions in "Live-code vocabulary" before starting.** If the row `status` enum uses different string values (e.g. `'pending'` instead of `'open'`), adapt the EssentialsBanner's `onStart` action. The semantic intent ("filter to essential + still-needed") is the contract; the literal string is local detail.
- **No new derived field on `ChecklistSummary` should be needed.** `essentialsGap = coreSources - coreReady` is computed at the consumer (the intake view). If the data model already exposes a "core gap" field by a different name, use it.
- One new piece of state (`filter` lifted into `DocumentIntakeView`) and one removed component (`MetricTile`). Resist the temptation to thread filter state up further; it's intake-local.
- The `<IntakeProgress />` rail uses CSS grid. Don't switch it to absolute-positioned children; the grid version handles zero-width segments cleanly.
- If pass 01 (color semantics) hasn't shipped yet, this pass will look wrong (the new components reference `text-ember`, `bg-ember`, and `bg-tide` from the recolored contract). Don't merge this without 01.
