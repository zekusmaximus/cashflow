# 04 — Quiet the chart, add leakage drill-down, fix the delta math

**Audit findings addressed:**
- [Ten floating numbers above ten thin bars is visual noise](../Design%20Audit.html#f-chart-density) (high, chart)
- [Clay is doing three jobs at once](../Design%20Audit.html#f-color-conflict) — outflow tooltip cleanup tail (cross-cutting; pass 01 did the token work, this pass cleans up labels)
- [Two gates, two giant cards — the panel is half-empty](../Design%20Audit.html) (medium, gates)
- ["+1.8% vs. Jan" is a misleading delta](../Design%20Audit.html) (medium, KPIs)
- [Leakage cards need a drill-down](../Design%20Audit.html) (medium, leakage)
- [Time-range pills should include a custom range](../Design%20Audit.html) (low, time control)

**Scope:** Dashboard-only. Replaces the per-bar labels with hover tooltips, adds a net-by-month strip beneath the chart, tightens the gates panel to a 3-up strip with room for a forward projection, fixes the KPI delta math to use trailing-3-month vs prior-3-month, and wires a transaction drawer behind a "See transactions" button on each leakage card. New `Custom` option on the time-range control.

**Estimated effort:** 1–2 days. The transaction drawer is the heaviest piece.

**Prereq:** Pass 01 merged (this pass's tooltip + delta surfaces depend on the new color contract). Passes 02 and 03 are independent — pass 04 only touches the dashboard view.

---

## File changes

### 1. `src/features/dashboard/dashboard-view.tsx`

#### a. Replace per-bar labels with a hover tooltip

**Before:** every bar carries a `<span>` with its rounded-thousands value directly above it. Ten such labels per chart at default density.

**After:** a single shared tooltip element, positioned absolutely over the chart, that follows the user's mouse and reports the hovered month's inflow / outflow / net. The bar labels disappear from the default render entirely.

```tsx
function CashFlowChart({ months, max }: { months: MonthData[]; max: number }) {
  const [hovered, setHovered] = useState<number | null>(null);
  const chartRef = useRef<HTMLDivElement>(null);

  return (
    <div className="relative" ref={chartRef}>
      {/* Y axis */}
      <div className="absolute inset-y-0 left-0 flex w-10 flex-col justify-between py-1 text-right text-[10px] text-ink/35 tnum">
        {axisLabels(max).map(v => <span key={v}>${formatK(v)}</span>)}
      </div>

      <div className="ml-12 grid grid-cols-5 gap-6">
        {months.map((m, i) => (
          <button
            key={m.label}
            type="button"
            onMouseEnter={() => setHovered(i)}
            onMouseLeave={() => setHovered(null)}
            onFocus={() => setHovered(i)}
            onBlur={() => setHovered(null)}
            className={`flex h-48 items-end justify-center gap-2 rounded-md transition-colors ${
              hovered === i ? 'bg-paper/60' : ''
            }`}
          >
            <div
              className="w-7 rounded-t-md bg-moss transition-all"
              style={{ height: `${(m.inflow / max) * 160}px`, opacity: hovered === i ? 1 : 0.85 }}
            />
            <div
              className="w-7 rounded-t-md bg-clay transition-all"
              style={{ height: `${(m.outflow / max) * 160}px`, opacity: hovered === i ? 1 : 0.85 }}
            />
          </button>
        ))}
      </div>

      <div className="ml-12 mt-2 h-px w-[calc(100%-3rem)] bg-ink/10" />

      {/* X-axis labels */}
      <div className="ml-12 mt-2 grid grid-cols-5 gap-6 text-center text-[11px] font-medium text-ink/55">
        {months.map(m => <div key={m.label}>{m.label}</div>)}
      </div>

      {/* Tooltip */}
      {hovered !== null && <ChartTooltip month={months[hovered]} anchorIndex={hovered} total={months.length} />}
    </div>
  );
}

function ChartTooltip({ month, anchorIndex, total }: { /* ... */ }) {
  const net = month.inflow - month.outflow;
  // Position above the hovered column. Use percentage so it stays anchored on resize.
  const leftPct = ((anchorIndex + 0.5) / total) * 100;
  return (
    <div
      className="pointer-events-none absolute -top-2 -translate-x-1/2 -translate-y-full rounded-md bg-ink px-3 py-2 text-fog shadow-card"
      style={{ left: `calc(${leftPct}% + 3rem - 3rem * ${anchorIndex / (total - 1)})` }}
    >
      <div className="text-[10px] uppercase tracking-[0.16em] text-fog/60">{month.label}</div>
      <div className="mt-1 grid grid-cols-2 gap-x-3 gap-y-0.5 text-[11px]">
        <span className="text-fog/70">Inflow</span>
        <span className="text-right tnum text-moss">${month.inflow.toLocaleString()}</span>
        <span className="text-fog/70">Outflow</span>
        <span className="text-right tnum text-clay">${month.outflow.toLocaleString()}</span>
        <span className="border-t border-fog/15 pt-1 text-fog">Net</span>
        <span className={`border-t border-fog/15 pt-1 text-right tnum ${net >= 0 ? 'text-moss' : 'text-ember'}`}>
          {net >= 0 ? '+' : ''}${net.toLocaleString()}
        </span>
      </div>
    </div>
  );
}
```

Notes:
- Use `<button>` rather than `<div>` for each column so keyboard focus works for free.
- Don't render the tooltip absolutely-positioned by mouse coords. Anchor it to the column index — that way the tooltip pins to the bar it describes, even when the user mouses between bars quickly. The current `left` calc has a minor offset because of the y-axis-label gutter; tune as needed during dev.
- Bars dim slightly when another column is hovered (opacity 0.85 → 1) to focus attention.

#### b. Add a Net-by-month strip beneath the chart

Below the X-axis labels, add a `grid-cols-5` row of small net chips. Color them moss for positive, ember for negative.

```tsx
<div className="ml-12 mt-3 grid grid-cols-5 gap-6">
  {months.map(m => {
    const net = m.inflow - m.outflow;
    const positive = net >= 0;
    return (
      <div
        key={m.label}
        className={`rounded py-1 text-center text-[11px] font-semibold tnum ${
          positive ? 'bg-moss/10 text-moss' : 'bg-ember/10 text-ember'
        }`}
      >
        {positive ? '+' : ''}${(net / 1000).toFixed(1)}k
      </div>
    );
  })}
</div>
```

This is the chart's actual story — the inflow/outflow magnitudes hover in the same range every month; what changes is the gap between them. Surfacing it explicitly answers "are we trending up or down?" without forcing the user to mentally subtract.

#### c. Fix the KPI delta math

**Problem:** "Avg inflow +1.8% vs. Jan" compares the YTD average against a single anchor month. If income drifted up then back down, the headline can be positive while the trend is flat or negative.

**Fix:** Compare trailing-3-month vs prior-3-month.

```ts
function trailingDelta(months: MonthData[], key: 'inflow' | 'outflow'): { delta: number; label: string } {
  if (months.length < 6) {
    // Not enough data for a 3-vs-3 comparison. Fall back to MoM.
    const last = months[months.length - 1];
    const prev = months[months.length - 2];
    if (!prev) return { delta: 0, label: 'no comparison yet' };
    const delta = (last[key] - prev[key]) / prev[key];
    return { delta, label: 'vs. prior month' };
  }
  const recent = months.slice(-3).reduce((s, m) => s + m[key], 0) / 3;
  const prior = months.slice(-6, -3).reduce((s, m) => s + m[key], 0) / 3;
  if (prior === 0) return { delta: 0, label: 'vs. trailing 3-mo' };
  return { delta: (recent - prior) / prior, label: 'vs. trailing 3-mo' };
}
```

Render in the KPI tile:
```tsx
<div className="mt-1 text-[11px] tnum flex items-baseline gap-1">
  <span className={inflowDelta.delta >= 0 ? 'text-moss' : 'text-ember'}>
    {inflowDelta.delta >= 0 ? '+' : ''}{(inflowDelta.delta * 100).toFixed(1)}%
  </span>
  <span className="text-ink/45">{inflowDelta.label}</span>
</div>
```

For the **outflow** delta: rising outflow is bad. Flip the polarity color (positive delta = ember, negative = moss). Reuse the same `trailingDelta` function but with `key: 'outflow'` and a different render path.

#### d. Tighten the Liquidity Gates panel to a 3-up strip

Today's two gate cards become two strips, freeing room for a third forward-looking projection.

```tsx
<section className="rounded-xl border border-ink/8 bg-white shadow-card">
  <div className="flex items-center justify-between px-5 pt-5 pb-3">
    <div>
      <div className="text-[10px] font-medium uppercase tracking-[0.18em] text-ink/45">Liquidity gates</div>
      <div className="mt-0.5 text-[15px] font-semibold text-ink">Constraint-first reserves</div>
    </div>
  </div>
  <div className="space-y-2 px-5 pb-5">
    {gates.map(gate => <GateStrip key={gate.id} gate={gate} />)}
    <RothProjection /> {/* New: forward-looking, not a real gate yet */}
  </div>
</section>
```

`GateStrip`:
```tsx
function GateStrip({ gate }: { gate: Gate }) {
  const progress = gate.targetAmount === 0 ? 0 : Math.min(gate.currentAmount / gate.targetAmount, 1);
  const met = progress >= 1;
  return (
    <div className="rounded-lg border border-ink/8 bg-paper/50 px-3.5 py-3">
      <div className="flex items-center gap-3">
        <div className="min-w-0 flex-1">
          <div className="text-[13px] font-medium text-ink leading-tight">{gate.label}</div>
          <div className="text-[10px] uppercase tracking-[0.14em] text-ink/45">Due {gate.targetDate}</div>
        </div>
        <div className="text-[11px] text-ink/55 tnum">
          {currency(gate.currentAmount, { compact: true })} / {currency(gate.targetAmount, { compact: true })}
        </div>
        <div className={`text-[13px] font-semibold tnum ${met ? 'text-moss' : 'text-tide'}`}>
          {Math.round(progress * 100)}%
        </div>
      </div>
      <div className="mt-2 h-1 w-full overflow-hidden rounded-full bg-ink/[0.06]">
        <div className={`h-full ${met ? 'bg-moss' : 'bg-tide'}`} style={{ width: `${progress * 100}%` }} />
      </div>
    </div>
  );
}
```

The `RothProjection` slot is a "next gate" preview — based on the current HYSA gate progress and trailing-3-month savings rate, project whether Roth re-engagement is on track. If you don't want to wire the projection math in this pass, leave the slot as a "Coming soon: Roth re-engagement readiness" placeholder. Either way the visual rhythm is fixed.

#### e. Wire "See transactions" on each leakage card

Add a small button at the bottom of each card:

```tsx
<button
  onClick={() => openTransactionDrawer({ categoryId: leak.id, monthRange: 'current' })}
  className="mt-3 inline-flex items-center gap-1 text-[11px] font-medium text-tide hover:text-ink"
>
  See transactions
  <ArrowRightIcon className="h-3 w-3" />
</button>
```

The drawer is described in section 2.

#### f. Time-range control gets a Custom option

```tsx
<div className="flex items-center gap-1 rounded-lg bg-ink/[0.05] p-0.5 text-[12px]">
  {(['YTD', '12mo', 'All'] as const).map(opt => (
    <button key={opt} onClick={() => setRange(opt)} className={range === opt ? 'rounded-md bg-white px-2.5 py-1 font-medium shadow-card' : 'px-2.5 py-1 text-ink/60 hover:text-ink'}>
      {opt}
    </button>
  ))}
  <button
    onClick={() => setCustomOpen(true)}
    className={range === 'custom' ? 'rounded-md bg-white px-2.5 py-1 font-medium shadow-card' : 'px-2.5 py-1 text-ink/60 hover:text-ink'}
  >
    Custom
  </button>
</div>
{customOpen && <CustomRangePopover onApply={r => { setRange('custom'); setCustomRange(r); setCustomOpen(false); }} onClose={() => setCustomOpen(false)} />}
```

The popover is two month pickers (start, end) constrained to the data's available range. Keep it minimal — a `<select>` for month + a `<select>` for year on each side is fine. No third-party date picker.

---

### 2. New: `src/features/dashboard/transaction-drawer.tsx`

A right-side drawer that slides in when "See transactions" is clicked. Fixed positioning, semi-transparent backdrop, escape-to-close, click-backdrop-to-close.

```tsx
interface TransactionDrawerProps {
  open: boolean;
  categoryId: string | null;
  monthRange: 'current' | { start: string; end: string };
  onClose: () => void;
}

export function TransactionDrawer({ open, categoryId, monthRange, onClose }: TransactionDrawerProps) {
  const { data } = useLeakageTransactions(categoryId, monthRange, { enabled: open });
  useEscapeKey(open, onClose);

  return (
    <>
      <div
        className={`fixed inset-0 z-40 bg-ink/30 backdrop-blur-sm transition-opacity ${
          open ? 'opacity-100' : 'pointer-events-none opacity-0'
        }`}
        onClick={onClose}
      />
      <aside
        className={`fixed right-0 top-0 z-50 h-full w-[420px] bg-paper shadow-card transition-transform ${
          open ? 'translate-x-0' : 'translate-x-full'
        }`}
        style={{ boxShadow: '-12px 0 40px -8px rgba(22,33,38,0.18)' }}
      >
        {/* Header */}
        <div className="flex items-center justify-between px-5 py-4 rule-b">
          <div>
            <div className="text-[10px] uppercase tracking-[0.18em] text-ink/45">Transactions</div>
            <div className="mt-0.5 text-[15px] font-semibold text-ink">{categoryName(categoryId)}</div>
          </div>
          <button onClick={onClose} className="grid h-7 w-7 place-items-center rounded-md hover:bg-ink/[0.06]">
            <XIcon className="h-3.5 w-3.5" />
          </button>
        </div>

        {/* Body */}
        <div className="max-h-[calc(100vh-60px)] overflow-auto px-5 py-4">
          {!data && <DrawerSkeleton />}
          {data && data.length === 0 && <EmptyState />}
          {data && data.length > 0 && (
            <ul className="divide-y divide-ink/8">
              {data.map(tx => <TransactionRow key={tx.id} tx={tx} />)}
            </ul>
          )}
        </div>
      </aside>
    </>
  );
}
```

#### Transaction row

```tsx
function TransactionRow({ tx }: { tx: Transaction }) {
  return (
    <li className="py-2.5 first:pt-0 last:pb-0">
      <div className="flex items-baseline justify-between gap-3 text-[13px]">
        <span className="min-w-0 truncate font-medium text-ink">{tx.vendor}</span>
        <span className="shrink-0 tnum text-clay">−${tx.amount.toLocaleString()}</span>
      </div>
      <div className="mt-0.5 flex items-center gap-2 text-[11px] text-ink/55">
        <span className="tnum">{formatDate(tx.date)}</span>
        <span>·</span>
        <span className="truncate font-mono">{tx.rawDescription}</span>
        {tx.matchScore !== undefined && (
          <span className="ml-auto shrink-0 tnum text-ink/40">match {tx.matchScore.toFixed(2)}</span>
        )}
      </div>
    </li>
  );
}
```

#### Hook + query

`useLeakageTransactions` is a new TanStack Query hook that fetches matched transactions for a category + month range from the local SQLite database. The query parameters: category id, start date, end date. The result is sorted by date desc.

The SQLite query itself: do NOT touch the matcher logic. Use the existing transactions table and `category_id` foreign key. If `category_id` is denormalized differently in the schema (e.g. stored as the parent category letter), adapt — the constraint is no schema migration.

```ts
export function useLeakageTransactions(categoryId: string | null, range: TimeRange, opts?: { enabled?: boolean }) {
  return useQuery({
    queryKey: ['leakage-transactions', categoryId, range],
    queryFn: () => invoke<Transaction[]>('list_leakage_transactions', { categoryId, range }),
    enabled: Boolean(categoryId) && (opts?.enabled ?? true),
    staleTime: 30_000,
  });
}
```

The Tauri command `list_leakage_transactions` is new. Its implementation reads from the existing `transactions` table and filters by category + date. Keep the SQL straightforward; no joins beyond what already exists.

---

## Acceptance criteria

- [ ] Bars no longer carry per-column value labels in the default render
- [ ] Hovering or keyboard-focusing a column shows a single tooltip with month / inflow / outflow / net
- [ ] Net-by-month strip renders beneath the X-axis labels; moss for positive net, ember for negative
- [ ] Inflow KPI delta uses trailing-3-mo vs prior-3-mo (falls back to MoM if < 6 months of data)
- [ ] Outflow KPI delta uses the same math but flipped polarity (rising outflow renders ember)
- [ ] Delta caption explicitly says "vs. trailing 3-mo" (or "vs. prior month" in the fallback)
- [ ] Liquidity Gates section renders as a 3-up strip (two real gates + one forward projection slot, even if the slot is a placeholder)
- [ ] Each leakage card has a "See transactions" button
- [ ] Clicking it opens a right-side drawer showing the matching transactions, sorted date desc
- [ ] Drawer closes on Escape, backdrop click, or X button
- [ ] Drawer respects `prefers-reduced-motion` (drop the slide animation if set)
- [ ] Time-range control has 4 options now: YTD / 12mo / All / Custom
- [ ] Custom opens a small popover with month + year selects for start/end
- [ ] New Tauri command `list_leakage_transactions` is registered, capability declared (Tauri v2)
- [ ] No new schema migrations
- [ ] `npm test` passes
- [ ] `npm run tauri dev` works end-to-end

---

## Notes for the implementer

- The drawer is the biggest piece of net-new UI in the whole audit pass. Build it first — if the query shape is wrong, you'll know early. Stub the data with hardcoded rows initially if the SQL takes time to write.
- The "Roth re-engagement readiness" projection is genuinely useful but it's also feature creep. If the math feels like a rabbit hole, ship the placeholder and open a follow-up issue. The visual rhythm fix (3-up strip) is what matters for this pass.
- Don't introduce a charting library. The existing inline bar+grid approach is fine; we're just adding interactivity to it.
- The Custom range popover should NOT depend on any date-fns / dayjs / luxon install. Native `<select>` is fine and matches the desktop-app density.
- When mousing fast between columns, the tooltip should snap to the new column instantly — no transition delay that would make it feel laggy. Transitions are for the bars (slight opacity), not the tooltip.
