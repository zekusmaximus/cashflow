# Handoff: Liquidity Gate UI pass — Document Intake & Cash-Flow Dashboard

## TL;DR for Claude Code

You're working in the `liquidity-gate` repo (React + TypeScript + Tailwind + Tauri).
A designer produced a calmer, more compact pass over the **Document Intake** and **Cash-Flow Dashboard** views. The visual reference is `redesign.html` in this folder — open it in a browser to see both views (top-right tab toggle). Click "Show notes" to surface inline annotations explaining each change.

**Do not ship the HTML.** Port the design back into the existing React components in `src/`. Keep the existing TanStack Query plumbing, hooks, types, matcher logic, and SQLite/Tauri bridges — only the visual layer changes.

The three files that change:

1. `src/components/layout/app-shell.tsx` — replace gradient megabanner with a quiet sticky chrome bar.
2. `src/features/document-intake/document-intake-view.tsx` — restructure header, fix table column widths, fix watch-root overflow.
3. `src/features/dashboard/dashboard-view.tsx` — restructure chart, fix gate label/badge collision, fix the buggy leakage bar.

Tailwind tokens (`ink`, `fog`, `sand`, `clay`, `moss`, `tide`, `ember`) and the `Card`/`Badge`/`Button` primitives stay as they are. You may want to add one paper-white token; see Design Tokens.

---

## Fidelity

**High-fidelity.** All colors, spacing, type sizes, and component shapes in `redesign.html` are the targets. Recreate them pixel-faithfully in the React components using Tailwind utility classes against the existing `tailwind.config.ts`.

---

## Why this pass exists

The user reported two things:

1. **Text runs off containers** in several windows.
2. **The UI feels busy.**

Both are real. Specific causes found in the current source:

| Symptom | Root cause | Where |
|---|---|---|
| 5xl headline + paragraph + 3 stat tiles + 2 nav buttons in one gradient banner eats ~30% of viewport | `AppShell` header is overbuilt | `app-shell.tsx:29-62` |
| Long filesystem paths push the indexed-file counter off-screen | `WatchRootBanner` is a single `<p>` with no `min-w-0` / `truncate` | `document-intake-view.tsx:WatchRootBanner` |
| Checklist table overflows; "Why Needed" sentences and matched filenames push the row wider than the card | `<table>` has `min-w-full` but no `table-fixed`, no `<colgroup>`, no per-column widths | `document-intake-view.tsx:104-156` |
| "HYSA gate before 2027 Roth re-engagement" collides with the % badge | The label `<div>` and badge are siblings in a `flex` with no `min-w-0` on the label and no `shrink-0` on the badge | `dashboard-view.tsx:70-80` |
| Lifestyle leakage cards render a horizontal orange bar inside a vertical 28px box, sometimes extending past the card | `<div class="h-full ..." style={{ width: '${ratio*100}%' }}>` is set to `width: 140%` when ratio > 1, inside an `h-28` container — wrong axis and unclamped | `dashboard-view.tsx:117-128` |
| Net YTD pill crowds the chart H2 | Chart card holds title + paragraph + huge "Net YTD" panel in a single flex row | `dashboard-view.tsx:30-42` |
| Inflow/outflow chart looks crammed at standard widths | 5 sub-cards each with their own mini bars and a value table | `dashboard-view.tsx:43-58` |

---

## Design tokens (already in `tailwind.config.ts`, no changes needed)

```
ink   #162126   (primary text)
fog   #f3f0e8   (inverted text on dark surfaces)
sand  #d2c4ad   (deprecated for borders — use ink/8 instead)
clay  #c7744f   (warning / over-cap)
moss  #4e6a57   (success / inflow / obtained)
tide  #2d5f73   (info / hover state on dark button)
ember #8e392d   (danger / error / overage)
```

**Add one new utility shade** (or just use arbitrary value):

- Background page color: `#faf8f3` (warmer than fog, cooler than white). Either add `paper: '#faf8f3'` to the Tailwind config or use `bg-[#faf8f3]` directly.

**New shadow utility** (optional, replaces `shadow-soft` for cards in this pass):

```ts
boxShadow: {
  soft: '0 24px 80px rgba(22, 33, 38, 0.12)',   // keep — used elsewhere
  card: '0 1px 0 rgba(22,33,38,0.04), 0 8px 24px -16px rgba(22,33,38,0.18)',  // add
}
```

Type scale used in the redesign:
- Page H1: `text-[22px] font-semibold tracking-tight`
- Section title: `text-[15px] font-semibold`
- Eyebrow label: `text-[10px] font-medium uppercase tracking-[0.18em] text-ink/45`
- Body: `text-[13px] leading-relaxed text-ink/65-70`
- Caption: `text-[11px] text-ink/45-55`
- Numbers: add `tnum` class (`font-variant-numeric: tabular-nums`) so currency columns align

Borders: use `border-ink/8` for hairlines on white surfaces (replaces `border-sand/30-35`).
Tracks/rails: use `bg-ink/[0.06]` for empty progress backgrounds (replaces `bg-sand/25`).

---

## File 1 — `src/components/layout/app-shell.tsx`

### Before
A 36px-radius gradient hero containing: Liquidity Gate badge, 5xl headline, 2xl paragraph, two nav buttons, and a 3-up StatCard grid. The whole banner sits inside a `max-w-7xl` page with `px-4 sm:px-6 lg:px-10`.

### After
A sticky top chrome bar with three regions: brand mark (left), tab nav (middle), inline status pills (right). The page title/description moves *into* each view as a contextual H1.

Replace the entire `AppShell` component with this structure:

```tsx
import type { PropsWithChildren } from 'react';
import { cn, currency } from '../../lib/utils';

export type AppView = 'intake' | 'dashboard';

interface AppShellProps extends PropsWithChildren {
  view: AppView;
  onViewChange: (view: AppView) => void;
  totalDocuments: number;
  obtainedDocuments: number;       // NEW — currently computed inline by callers; add to props
  missingDocuments: number;
  liquidityGateCurrent: number;    // NEW — current HYSA gate balance (was implicit)
  liquidityGate: number;
}

export function AppShell({
  children,
  view,
  onViewChange,
  totalDocuments,
  obtainedDocuments,
  missingDocuments,
  liquidityGateCurrent,
  liquidityGate,
}: AppShellProps) {
  return (
    <div className="min-h-screen bg-[#faf8f3]">
      <header className="sticky top-0 z-20 bg-[#faf8f3]/85 backdrop-blur" style={{ boxShadow: 'inset 0 -1px 0 rgba(22,33,38,0.08)' }}>
        <div className="mx-auto flex max-w-[1280px] items-center gap-6 px-6 py-3">
          <BrandMark />
          <TabNav view={view} onViewChange={onViewChange} />
          <StatusPills
            obtained={obtainedDocuments}
            total={totalDocuments}
            missing={missingDocuments}
            gateCurrent={liquidityGateCurrent}
            gateTarget={liquidityGate}
          />
        </div>
      </header>

      <main className="mx-auto max-w-[1280px] px-6 py-6">
        {children}
      </main>
    </div>
  );
}
```

`BrandMark`, `TabNav`, and `StatusPills` are small private components — see `redesign.html` lines ~89–135 for exact markup. Tab nav uses the segmented-pill pattern (`rounded-full bg-ink/[0.04] p-1` wrapper, each pill `rounded-full px-3.5 py-1.5 text-[13px]` with active state `bg-white shadow-card`).

Key constraints when porting:
- `max-w-[1280px]` page width replaces `max-w-7xl` (a bit tighter — content reads better at typical laptop widths).
- Background lives on the outer `<div>`, NOT in `index.css`'s `:root`. **Remove the radial-gradient + linear-gradient from `:root` in `index.css`** — it conflicts with the calmer page color and was contributing to the "busy" feeling. Replace with a flat `background: #faf8f3`.

### Caller change
`App.tsx` currently passes `totalDocuments`, `missingDocuments`, `liquidityGate`. Add:

```tsx
obtainedDocuments={checklistQuery.data?.summary.obtainedCount ?? 0}
liquidityGateCurrent={dashboardQuery.data?.gates[0]?.currentAmount ?? 0}
```

---

## File 2 — `src/features/document-intake/document-intake-view.tsx`

### Before
A hero card with a 2xl headline, paragraph, watch-root banner, and 4 metric tiles in one flex row. Below: a left "Phase Focus" tip card and right `Card` containing the table. Table has `min-w-full` but no fixed layout.

### After
- Page H1 row at top (eyebrow + H1 + one-line description + action buttons)
- 4 equal metric tiles in a `grid-cols-4` row (replaces the metric block that was crammed next to the headline)
- A thin watch-root strip below the metrics, with `min-w-0 + truncate` on the path
- Two-column body: 280px-wide category sidebar + flex-1 checklist table. **Critical:** the grid must use `minmax(0, ...)` for both tracks so the right column can shrink.
- Drop the "Phase Focus" tip card entirely. It's filler. If the user wants the guidance preserved, surface it as a one-line note above the table or behind a help icon.

### Layout skeleton

```tsx
return (
  <div>
    {/* Title row */}
    <div className="mb-5 flex items-end justify-between gap-4">
      <div className="min-w-0">
        <div className="text-[11px] font-medium uppercase tracking-[0.18em] text-ink/45">Phase 1 · Intake</div>
        <h1 className="mt-1 text-[22px] font-semibold tracking-tight text-ink">Document checklist</h1>
        <p className="mt-1 max-w-2xl text-[13px] leading-relaxed text-ink/60">
          Driven by the tracker CSV and auto-matched against your watch folder.
        </p>
      </div>
      <ActionButtons />
    </div>

    {/* Metric tiles */}
    <div className="mb-3 grid grid-cols-4 gap-3">
      <MetricTile label="Tracked"    value={summary.totalItems} />
      <MetricTile label="Obtained"   value={summary.obtainedCount} tone="moss" caption={`${Math.round(summary.obtainedCount/summary.totalItems*100)}%`} />
      <MetricTile label="Missing"    value={summary.missingCount}  tone="clay" caption={`${essentialMissing} essential`} />
      <MetricTile label="Categories" value={summary.categories.length} />
    </div>

    {/* Watch root strip */}
    <WatchRootStrip status={watchRoot} />

    {/* Body grid */}
    <div className="grid gap-5" style={{ gridTemplateColumns: 'minmax(0, 280px) minmax(0, 1fr)' }}>
      <CategorySidebar categories={summary.categories} />
      <ChecklistTable items={items} transactionCounts={transactionCounts} />
    </div>
  </div>
);
```

### The table — must use `table-fixed` + `<colgroup>`

```tsx
<table className="w-full table-fixed border-collapse text-left text-[13px]">
  <colgroup>
    <col style={{ width: '92px' }} />   {/* Status */}
    <col />                              {/* Document — flexes */}
    <col style={{ width: '110px' }} />  {/* Category */}
    <col style={{ width: '96px' }} />   {/* Priority */}
    <col style={{ width: '30%' }} />    {/* Why needed */}
  </colgroup>
  ...
</table>
```

Wrap in `<div className="max-h-[640px] overflow-auto">` for vertical scroll. Sticky header: `<thead className="sticky top-0 z-[1] bg-white/95 backdrop-blur">`.

Per-row text handling:
- Document name: `font-medium text-ink leading-snug` (allowed to wrap)
- Format: `text-[11px] text-ink/45` below name
- Matched files list: each `<li>` is a `flex min-w-0 items-baseline gap-2`; filename gets `truncate font-mono text-ink/75`, score `shrink-0`, tx count `shrink-0`
- Why needed: `text-[12px] leading-relaxed text-ink/65` with `style={{ textWrap: 'pretty' }}`

### Watch-root strip

Replace the existing `WatchRootBanner` with a strip containing: small status dot icon, "Watching" label, `truncate` path span, indexed-file count, scan interval — all in a single horizontal row. Path container needs `min-w-0 flex-1` so it can shrink. Right-side counts need `shrink-0`.

```tsx
function WatchRootStrip({ status }: { status: WatchRootStatus }) {
  if (!status.root) {
    return (
      <div className="mb-6 rounded-xl border border-ink/8 bg-white px-3.5 py-2.5 text-[12px] text-ink/55 shadow-card">
        Filesystem matching is only active inside the Tauri desktop app.
      </div>
    );
  }
  const tone = status.exists ? 'moss' : 'ember';
  return (
    <div className="mb-6 flex items-center gap-3 rounded-xl border border-ink/8 bg-white px-3.5 py-2.5 shadow-card">
      <span className={`grid h-6 w-6 shrink-0 place-items-center rounded-md bg-${tone}/12 text-${tone}`}>
        {/* folder icon */}
      </span>
      <div className="min-w-0 flex-1">
        <div className="flex items-baseline gap-2 text-[12px]">
          <span className="text-ink/55">{status.exists ? 'Watching' : 'Missing'}</span>
          <span className="truncate font-mono text-ink/85">{status.root}</span>
        </div>
      </div>
      <div className="flex shrink-0 items-center gap-3 text-[11px] text-ink/55">
        <span className="tnum">
          <b className="font-semibold text-ink">{status.fileCount}</b> files indexed
        </span>
        <span className="h-3 w-px bg-ink/15" />
        <span>Rescans every 5s</span>
      </div>
    </div>
  );
}
```

### Category sidebar

Each row: a single-letter badge (`A`–`I`) + truncated category name + obtained/total count + a 1px-tall progress rail. Source the letter by splitting the category string on `". "` (e.g. `"A. Core Transactions"` → `"A"`). If the category doesn't follow that pattern, fall back to the first character. See `redesign.html` `data-cat-rows` section for exact markup.

---

## File 3 — `src/features/dashboard/dashboard-view.tsx`

### Before
Top section: a chart card holding 5 month sub-cards (each with mini bars + a 2-row value table) next to a "Net YTD" pill. Right: a Liquidity Gates card with gate rows whose label and percent badge collide. Bottom: a Lifestyle Leakage section with 3 cards using the broken horizontal-bar-in-vertical-box pattern.

### After
- Page title row (same pattern as intake) with a YTD/12mo/All time-range segmented control.
- **4-up KPI strip:** Net YTD, Avg inflow, Avg outflow, HYSA gate percentage with mini rail. Pulls "Net YTD" out of the chart card.
- **Chart + Gates row** at `grid-template-columns: minmax(0, 1.45fr) minmax(0, 1fr)`.
- Chart is a *single* canvas: 5 grouped bar pairs sharing one Y-axis with reference labels at $0/$5k/$10k/$15k/$20k. No per-month sub-cards.
- Gates: each gate's label is in a `min-w-0` block, % badge is `shrink-0` (fixes the collision).
- **Leakage cards: fully restructured bar.** This is critical — the old code was buggy.

### The new leakage bar (the bug fix)

The bar represents *total monthly burn* (100% of the rendered width). The cap segment fills `cap/burn` of the bar; the overage segment fills the remainder. A 1px tick sits at the cap boundary. Everything is inside an `overflow-hidden` container — no absolute-positioned children at `left: 100%`.

```tsx
function LeakageCard({ category }: { category: LeakageCategory }) {
  const ratio  = category.cap === 0 ? 0 : category.monthlyBurn / category.cap;
  const overBy = category.monthlyBurn - category.cap;
  const capPct  = ratio > 1 ? (category.cap / category.monthlyBurn) * 100 : 100;
  const overPct = ratio > 1 ? 100 - capPct : 0;
  const overPercent = Math.round((ratio - 1) * 100);

  return (
    <div className="rounded-lg border border-ink/8 bg-[#faf8f3]/50 p-4">
      <div className="flex items-start justify-between gap-2">
        <p className="text-[13px] font-medium leading-snug text-ink">{category.name}</p>
        {ratio > 1 && (
          <span className="shrink-0 rounded-full bg-ember/10 px-2 py-0.5 text-[11px] font-semibold text-ember tnum">
            +{overPercent}%
          </span>
        )}
      </div>

      {/* Contained horizontal bar. Cap + overage share one 100%-wide flex track. */}
      <div className="relative mt-4 h-2 w-full overflow-hidden rounded-full bg-ink/[0.06]">
        <div className="flex h-full w-full">
          <div className="h-full bg-clay"  style={{ width: `${capPct}%` }} />
          <div className="h-full bg-ember" style={{ width: `${overPct}%` }} />
        </div>
      </div>
      {ratio > 1 && (
        <div className="relative -mt-3 mb-1 h-3">
          <div className="absolute top-0 h-3 w-px bg-ink/35" style={{ left: `${capPct}%` }} />
        </div>
      )}

      <div className="mt-1 flex items-center justify-between text-[11px] text-ink/55 tnum">
        <span><b className="font-semibold text-ink">{currency(category.monthlyBurn)}</b> burn</span>
        <span>
          {currency(category.cap)} cap
          {ratio > 1 && <span className="text-ember"> · +{currency(overBy)} over</span>}
        </span>
      </div>
    </div>
  );
}
```

### The chart

Single grid:

```tsx
<div className="relative">
  <div className="absolute inset-y-0 left-0 flex w-10 flex-col justify-between py-1 text-right text-[10px] text-ink/35 tnum">
    <span>$20k</span><span>$15k</span><span>$10k</span><span>$5k</span><span>$0</span>
  </div>
  <div className="ml-12 grid grid-cols-5 gap-6">
    {months.map(m => <MonthBars key={m.label} month={m} max={chartMax} />)}
  </div>
  <div className="ml-12 mt-2 h-px w-[calc(100%-3rem)] bg-ink/10" />
</div>
<div className="ml-12 mt-2 grid grid-cols-5 gap-6 text-center text-[11px] font-medium text-ink/55">
  {months.map(m => <div key={m.label}>{m.label}</div>)}
</div>
```

`chartMax` should round up to the nearest $5k above the actual max, not the literal max — this keeps the Y-axis labels honest.

`MonthBars` renders two `w-7` rounded-top rects side-by-side with a small value label above each. Bar height: `${(value/chartMax)*160}px` against a 192px (`h-48`) container.

### The gates

```tsx
<div className="rounded-lg border border-ink/8 bg-[#faf8f3]/50 p-3.5">
  <div className="flex items-start justify-between gap-3">
    <div className="min-w-0">
      <p className="text-[13px] font-medium leading-snug text-ink">{gate.label}</p>
      <p className="mt-0.5 text-[11px] uppercase tracking-[0.16em] text-ink/45">Due {gate.targetDate}</p>
    </div>
    <span className={`shrink-0 rounded-full px-2 py-0.5 text-[11px] font-semibold tnum ${progress >= 1 ? 'bg-moss/12 text-moss' : 'bg-clay/12 text-clay'}`}>
      {percent(Math.min(progress, 1))}
    </span>
  </div>
  <div className="mt-3 h-1.5 w-full rounded-full bg-ink/[0.06]">
    <div className={`h-full rounded-full ${progress >= 1 ? 'bg-moss' : 'bg-clay'}`} style={{ width: `${Math.min(progress, 1) * 100}%` }} />
  </div>
  <div className="mt-2 flex items-center justify-between text-[11px] text-ink/55 tnum">
    <span>{currency(gate.currentAmount)} current</span>
    <span>{currency(gate.targetAmount)} target</span>
  </div>
</div>
```

The single linear-gradient bar in the old code (`bg-[linear-gradient(90deg,#c7744f,#8e392d)]`) goes away — flat `bg-clay` reads cleaner.

---

## Things NOT to change

- `useDashboard`, `useDocumentChecklist`, `useTransactionCounts` hooks
- `loadChecklistDataset` and `matcher.ts` logic
- `bootstrapLocalDatabase` and the SQLite layer
- The `ChecklistDataset` / `DashboardSnapshot` types
- Any Tauri command names or capabilities
- The MCP server / Python side
- The `Card`, `Badge`, `Button` primitives (they're still useful elsewhere; this redesign opts out of `Card` in a few places because it uses tighter custom containers — that's fine)

You may use the `Card` primitive where it fits, but most surfaces in this pass are bespoke (`rounded-xl border border-ink/8 bg-white shadow-card`) and intentionally smaller-radius / quieter than the existing `rounded-[28px]` cards.

---

## After implementing

1. Run `npm run dev` and verify both views render at 1280px, 1024px, and 768px without horizontal overflow.
2. Resize the watch-root path to something long like `/Users/jeff/Library/Application Support/Cashflow/intake/2026-archive/long-subfolder/` and confirm it truncates instead of pushing the file-count off-screen.
3. Run `npm test` — none of the matcher / checklist tests should break (no logic changed).
4. Open the Tauri desktop build (`npm run tauri dev`) and confirm the watch-root invocation still works.

---

## Files in this handoff

- `README.md` — this file (the full spec)
- `redesign.html` — open in a browser, click "Show notes" for inline annotations. Top-right tabs switch between Document Intake and Cash-Flow Dashboard views. This is a **reference mockup**, not code to ship.
