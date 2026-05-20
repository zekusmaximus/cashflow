# 01 — Color semantics

**Audit finding:** [Clay is doing three jobs at once](../Design%20Audit.html#f-color-conflict) (high severity, cross-cutting)

**Scope:** Class-name swaps across three files. No state changes, no new components, no logic changes. This is the safest pass in the package and should ship first.

**Estimated effort:** 1–2 hours.

---

## The problem

`#c7744f` (the `clay` token) currently represents three different stories:

1. **Missing documents** — a warning state
2. **Monthly outflow** — neutral expense
3. **HYSA gate progress** — a partially-complete goal

The eye can't learn what clay means when it carries this much weight. The fix is a token-level reassignment that pushes each story onto the appropriate accent.

---

## The new color contract

| Token | Hex | Used for |
|---|---|---|
| `moss` | `#4e6a57` | **Received / income / target met.** Obtained docs, inflow bars, completed gates. (no change) |
| `tide` | `#2d5f73` | **Progress in motion.** HYSA gate fill, safe-harbor gate fill, any non-blocking partial progress. (new role — currently used only as a hover state on the dark button) |
| `clay` | `#c7744f` | **Outflow / neutral spending.** Outflow bars, cap-fill portion of leakage bars, monthly burn label. (narrower role) |
| `ember` | `#8e392d` | **Blocked / over budget / missing.** Missing-doc chips, over-cap leakage overage segment, essential-missing CTAs. (broader role) |

**Mental model the user learns:**
- Moss = it's fine
- Tide = work in progress
- Clay = spending (neutral)
- Ember = blocked or over

No Tailwind config changes — all four tokens already exist.

---

## File changes

### 1. `src/components/layout/app-shell.tsx`

The `StatusPills` component currently renders a clay dot next to the missing count. Swap that single class.

**Before:**
```tsx
<span className="h-1.5 w-1.5 rounded-full bg-clay" />
<span className="tnum"><b className="font-semibold text-ink">{missing}</b> missing</span>
```

**After:**
```tsx
<span className="h-1.5 w-1.5 rounded-full bg-ember" />
<span className="tnum"><b className="font-semibold text-ink">{missing}</b> missing</span>
```

That's the only change in this file.

---

### 2. `src/features/document-intake/document-intake-view.tsx`

Two surfaces use clay today:

#### a. The "Missing" KPI tile

**Before:**
```tsx
<div className="flex items-center gap-1.5 text-[10px] uppercase tracking-[0.18em] text-clay">
  <MissingIcon className="h-3 w-3" />
  Missing
</div>
<div className="mt-1.5 flex items-baseline gap-2">
  <span className="text-[22px] font-semibold tnum text-clay">{summary.missingCount}</span>
  <span className="text-[11px] text-ink/45 tnum">{summary.essentialMissingCount} essential</span>
</div>
```

**After:**
```tsx
<div className="flex items-center gap-1.5 text-[10px] uppercase tracking-[0.18em] text-ember">
  <MissingIcon className="h-3 w-3" />
  Missing
</div>
<div className="mt-1.5 flex items-baseline gap-2">
  <span className="text-[22px] font-semibold tnum text-ember">{summary.missingCount}</span>
  <span className="text-[11px] text-ink/45 tnum">{summary.essentialMissingCount} essential</span>
</div>
```

(This tile will be removed entirely in pass **02** — but for now, recolor it cleanly so 01 lands as a self-contained PR.)

#### b. The "Missing" status chip in checklist rows

Search for the renderer that produces the missing badge. In the current code it looks like this:

**Before:**
```tsx
<span className="inline-flex items-center gap-1.5 rounded-full bg-clay/12 px-2 py-0.5 text-[11px] font-medium text-clay">
  <span className="h-1.5 w-1.5 rounded-full bg-clay" />
  Missing
</span>
```

**After:**
```tsx
<span className="inline-flex items-center gap-1.5 rounded-full bg-ember/12 px-2 py-0.5 text-[11px] font-medium text-ember">
  <span className="h-1.5 w-1.5 rounded-full bg-ember" />
  Missing
</span>
```

**Do NOT change:** the obtained chip (`bg-moss/12 text-moss`), the priority badge tones (`border-ember/30 bg-ember/8 text-ember` for Essential is already correct), or the category sidebar progress rails (`bg-moss`, also correct).

---

### 3. `src/features/dashboard/dashboard-view.tsx`

Four surfaces use clay; only two should change.

#### a. HYSA gate KPI tile — change to tide

**Before:**
```tsx
<div className="mt-2 h-1 w-full rounded-full bg-ink/[0.06]">
  <div className="h-full rounded-full bg-clay" style={{ width: `${pct}%` }} />
</div>
```

**After:**
```tsx
<div className="mt-2 h-1 w-full rounded-full bg-ink/[0.06]">
  <div className="h-full rounded-full bg-tide" style={{ width: `${pct}%` }} />
</div>
```

#### b. Liquidity Gates section — both gate cards change to tide

For each gate card, the percentage badge and the progress bar fill switch from clay to tide:

**Before:**
```tsx
<span className="shrink-0 rounded-full bg-clay/12 px-2 py-0.5 text-[11px] font-semibold text-clay tnum">
  {percent(progress)}
</span>
{/* ... */}
<div className="h-full rounded-full bg-clay" style={{ width: `${progress * 100}%` }} />
```

**After:**
```tsx
<span className={`shrink-0 rounded-full px-2 py-0.5 text-[11px] font-semibold tnum ${progress >= 1 ? 'bg-moss/12 text-moss' : 'bg-tide/12 text-tide'}`}>
  {percent(Math.min(progress, 1))}
</span>
{/* ... */}
<div className={`h-full rounded-full ${progress >= 1 ? 'bg-moss' : 'bg-tide'}`} style={{ width: `${Math.min(progress, 1) * 100}%` }} />
```

The conditional moss-on-met treatment was already in the previous handoff spec but the in-progress branch used clay. Switch that branch to tide.

#### c. DO NOT CHANGE — outflow bars

**Keep as clay.** Outflow is the canonical neutral-spending use of clay; this is the surface that earns the token.

```tsx
<div className="w-7 rounded-t-md bg-clay" style={{ height: `${(m.outflow / chartMax) * 160}px` }} />
```

The legend dot (`<span className="h-2 w-2 rounded-sm bg-clay" />`) and the Y-axis labels stay too.

#### d. DO NOT CHANGE — leakage cap-fill segment

The leakage bar today is `<div bg-clay /><div bg-ember />` representing within-cap and over-cap spending. This already matches the new contract perfectly:

- Within-cap = spending = clay ✓
- Over-cap = over budget = ember ✓

Leave it alone.

#### e. Optional polish — outflow KPI delta caption

The "Avg monthly outflow" tile shows `+5.8% vs. Jan` in clay. This is fine for now (it's an outflow delta), but flag for the team that the comparison baseline itself is misleading — pass 04 will address the delta math.

---

## Acceptance criteria

- [ ] `npm run dev`: header missing dot renders ember, not clay
- [ ] Intake view: Missing KPI tile and Missing chips in the checklist render ember
- [ ] Dashboard view: HYSA gate KPI rail, both Liquidity Gate cards (rail + percentage badge) render tide
- [ ] Dashboard view: inflow bars stay moss, outflow bars stay clay, over-cap leakage segment stays ember
- [ ] `npm test` passes
- [ ] No new TypeScript errors
- [ ] Visual diff against the audit's color-conflict mock looks right (`Design Audit.html#f-color-conflict`)

---

## Notes for the implementer

- This is a search-and-replace pass, but **don't blanket-replace `clay` → `ember`** — outflow and leakage cap-fill must stay clay. Read each occurrence in context.
- Eight call sites total across the three files (give or take). If you find more than ~12, you're over-replacing.
- No new color tokens needed. No config edits needed. No prop changes needed.
- Pass 02 will remove the Missing KPI tile entirely, so the recolor there is short-lived — but recolor it anyway so each PR is independently consistent.
