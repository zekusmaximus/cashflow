# 03 — Sidebar as filter, collapse "Why needed", reveal-in-Finder

**Audit findings addressed:**
- [Category sidebar should be the primary filter](../Design%20Audit.html) (medium, intake)
- ["Why needed" column eats 30% width for text users glance at once](../Design%20Audit.html) (medium, intake)
- [Watch-root strip needs a "reveal in Finder" action](../Design%20Audit.html) (low, intake)
- [Letter-prefix badges (A–I) without a legend feel arbitrary](../Design%20Audit.html) (sub-finding, sidebar)

**Scope:** Adds composable filter state in the intake view (`category` joins `status` and `priority`), converts the read-only category sidebar into a clickable filter rail, collapses the rationale text into a row-expand, and wires two Tauri shell invocations onto the watch-root strip.

**Estimated effort:** 1 day.

**Prereq:** Passes 01 and 02 merged. Pass 02 already lifted filter state into the view; this pass extends the filter shape.

---

## Live-code vocabulary

The SPEC uses the codebase's actual terminology, not the audit's HTML mockup terms:

| Concept | Live code |
|---|---|
| Per-row status (still needed) | `'open'` |
| Per-row status (have it) | `'ready'` |
| Per-row priority flag | `isEssential` (boolean) |
| Category summary fields | **verify before coding** — likely `obtained` / `total`, but may be `ready` / `sources` or similar |

If any of these differ in your live code (especially the category-summary field names), adapt the property accesses; the semantic intent is the contract.

---

## File changes

### 1. `src/features/document-intake/document-intake-view.tsx`

#### a. Extend the filter shape

Pass 02 introduced (with live terminology):
```ts
type TableFilter = { status: 'all' | 'open'; priority: 'all' | 'essential' };
```

Extend it to add a third dimension and broaden the existing two:
```ts
type TableFilter = {
  status: 'all' | 'open' | 'ready';
  priority: 'all' | 'essential' | 'useful';
  category: string | 'all';  // category id, e.g. 'A', or 'all'
};
```

If pass 02 used a different literal shape (e.g. `{ isEssential: true | false | null, status: 'open' | 'ready' | 'all' }`), keep that shape and just add the `category` field. The semantic intent — "narrow the table to: this category × this status × this priority" — is the contract.

The shape composes — selecting a category narrows what the status / priority pills filter from. The EssentialsBanner's `onStart` still works the same: it sets `{ status: 'open', priority: 'essential', category: 'all' }` (or your equivalent literal).

#### b. Make `<CategorySidebar />` clickable

**Before:** read-only display.

**After:** each row is a `<button>`. Clicking it toggles that category as the active filter. Clicking the currently-active category clears it back to `'all'`.

```tsx
interface CategorySidebarProps {
  categories: CategorySummary[];
  activeCategory: string | 'all';
  onCategoryChange: (id: string | 'all') => void;
}

function CategorySidebar({ categories, activeCategory, onCategoryChange }: CategorySidebarProps) {
  return (
    <aside className="rounded-xl border border-ink/8 bg-white shadow-card">
      <div className="flex items-center justify-between px-4 pt-4 pb-3 rule-b">
        <div>
          <div className="text-[10px] font-medium uppercase tracking-[0.18em] text-ink/45">Filter by</div>
          <div className="text-[14px] font-semibold text-ink">Category</div>
        </div>
        {activeCategory !== 'all' && (
          <button onClick={() => onCategoryChange('all')} className="text-[11px] text-tide hover:text-ink">
            Clear
          </button>
        )}
      </div>
      <div className="space-y-1 px-2 py-2">
        <CategoryRow
          id="all"
          name="All categories"
          obtained={categories.reduce((s, c) => s + c.obtained, 0)}
          total={categories.reduce((s, c) => s + c.total, 0)}
          active={activeCategory === 'all'}
          onClick={() => onCategoryChange('all')}
        />
        {categories.map(c => (
          <CategoryRow
            key={c.id}
            id={c.id}
            name={c.name}
            obtained={c.obtained}
            total={c.total}
            active={activeCategory === c.id}
            onClick={() => onCategoryChange(c.id)}
          />
        ))}
      </div>
    </aside>
  );
}

function CategoryRow({ id, name, obtained, total, active, onClick }: { /* ... */ }) {
  // Field names on the live `CategorySummary` may differ — see the Live-code vocabulary table.
  // The contract here: `obtained` = items in this category that are ready, `total` = all items.
  const pct = total === 0 ? 0 : (obtained / total) * 100;
  return (
    <button
      onClick={onClick}
      className={`w-full rounded-md px-2 py-2 text-left transition-colors ${
        active ? 'bg-tide/[0.08] ring-1 ring-inset ring-tide/30' : 'hover:bg-paper/80'
      }`}
    >
      <div className="flex items-center justify-between gap-2 text-[12px]">
        <span className={`truncate ${active ? 'font-semibold text-tide' : 'text-ink/85'}`}>{name}</span>
        <span className="shrink-0 text-[11px] text-ink/50 tnum">{obtained}/{total}</span>
      </div>
      <div className="mt-1.5 h-1 w-full rounded-full bg-ink/[0.06]">
        <div className="h-full rounded-full bg-moss" style={{ width: `${pct}%` }} />
      </div>
    </button>
  );
}
```

**Drop the single-letter badge entirely.** The audit's secondary finding — "letter prefix without a legend feels arbitrary" — is resolved by removing the badge. Category names are descriptive enough (`Core Transactions`, `Income & Payroll`, etc.) without the `A.` / `B.` prefix.

If the source data labels categories with letter prefixes (`"A. Core Transactions"`), strip the prefix in display: `name.replace(/^[A-Z]\.\s*/, '')`. Keep the letter as the category `id` for filter state.

#### c. Collapse "Why needed" into row-expand

The checklist table loses the 5th column. Each row gains a chevron button at the right edge; clicking it expands a sub-row that holds the rationale text and the matched-files list (if any).

**Column reshape:**

```tsx
<colgroup>
  <col style={{ width: '92px' }} />   {/* Status */}
  <col />                              {/* Document — flexes */}
  <col style={{ width: '120px' }} />  {/* Last modified — NEW */}
  <col style={{ width: '110px' }} />  {/* Category */}
  <col style={{ width: '96px' }} />   {/* Priority */}
  <col style={{ width: '32px' }} />   {/* Expand chevron */}
</colgroup>
```

The reclaimed width gives the **Document** column ~30% more breathing room. The audit suggested a new **Last modified** column to surface stale-data signals — add it. The value comes from the `mtime` of the matched file (already exposed by the watch-root indexer); show `—` for missing rows.

**Row body:**

```tsx
function ChecklistRow({ item, expanded, onToggleExpand }: { /* ... */ }) {
  return (
    <>
      <tr className="rule-b align-top hover:bg-paper/60 cursor-pointer" onClick={onToggleExpand}>
        <td className="px-4 py-3">{renderStatusChip(item)}</td>
        <td className="px-3 py-3">
          <div className="font-medium text-ink leading-snug">{item.name}</div>
          <div className="mt-0.5 text-[11px] text-ink/45">{item.fmt}</div>
        </td>
        <td className="px-3 py-3 text-[12px] text-ink/65 tnum">
          {item.lastModified ? formatRelative(item.lastModified) : '—'}
        </td>
        <td className="px-3 py-3 text-[12px] text-ink/70">{item.cat}</td>
        <td className="px-3 py-3">{renderPriorityBadge(item.pri)}</td>
        <td className="px-3 py-3 text-right">
          <span className={`inline-block transition-transform ${expanded ? 'rotate-180' : ''}`}>
            <ChevronDown className="h-3 w-3 text-ink/45" />
          </span>
        </td>
      </tr>
      {expanded && (
        <tr className="rule-b bg-paper/40">
          <td />
          <td colSpan={5} className="px-3 pb-4 pt-1">
            <div className="rounded-md border border-ink/8 bg-white p-3">
              <div className="text-[10px] uppercase tracking-[0.16em] text-ink/45">Why this is needed</div>
              <p className="mt-1 text-[12px] leading-relaxed text-ink/75" style={{ textWrap: 'pretty' }}>
                {item.why}
              </p>
              {item.files && item.files.length > 0 && (
                <>
                  <div className="mt-3 text-[10px] uppercase tracking-[0.16em] text-ink/45">Matched files</div>
                  <ul className="mt-1 space-y-0.5">
                    {item.files.map(f => (
                      <li key={f.n} className="flex min-w-0 items-baseline gap-2 text-[11px]">
                        <FileIcon className="h-3 w-3 shrink-0 text-ink/35" />
                        <span className="truncate font-mono text-ink/75">{f.n}</span>
                        <span className="shrink-0 text-ink/35 tnum">{f.s.toFixed(2)}</span>
                        {f.tx && <span className="shrink-0 text-moss tnum">· {f.tx.toLocaleString()} tx</span>}
                      </li>
                    ))}
                  </ul>
                </>
              )}
            </div>
          </td>
        </tr>
      )}
    </>
  );
}
```

State for which rows are expanded:
```ts
const [expandedIds, setExpandedIds] = useState<Set<string>>(new Set());
const toggle = (id: string) => setExpandedIds(prev => {
  const next = new Set(prev);
  next.has(id) ? next.delete(id) : next.add(id);
  return next;
});
```

Resist the urge to make it controlled / persistent. Expansion is ephemeral per-session — no localStorage.

#### d. Apply the filter to the row list

```ts
const filtered = useMemo(() => {
  return items.filter(i => {
    if (filter.status !== 'all' && i.status !== filter.status) return false;
    if (filter.priority !== 'all' && i.priority !== filter.priority) return false;
    if (filter.category !== 'all' && i.categoryId !== filter.category) return false;
    return true;
  });
}, [items, filter]);
```

**Adapt to live shape:** if your `ChecklistItem` uses `isEssential: boolean` instead of `priority: 'essential' | 'useful'`, the priority predicate becomes:
```ts
if (filter.priority === 'essential' && !i.isEssential) return false;
if (filter.priority === 'useful' && i.isEssential) return false;
```
Match whatever pass 02 established in this codebase.

When the active filter narrows to zero rows, show a small empty state in the table body:
```tsx
{filtered.length === 0 && (
  <tr><td colSpan={6} className="px-4 py-12 text-center text-[12px] text-ink/55">
    No documents match this filter. <button onClick={clearFilter} className="text-tide underline">Clear filter</button>
  </td></tr>
)}
```

#### e. Show active filter state above the table

A small chip row above the table toolbar, only rendered when at least one filter is non-default. Lets the user see what's narrowing the list at a glance.

```tsx
{(filter.status !== 'all' || filter.priority !== 'all' || filter.category !== 'all') && (
  <div className="flex items-center gap-1.5 px-4 py-2 rule-b text-[11px]">
    <span className="text-ink/55">Filtering by</span>
    {filter.category !== 'all' && (
      <FilterChip onClear={() => setFilter(f => ({ ...f, category: 'all' }))}>
        {categoryNameById(filter.category)}
      </FilterChip>
    )}
    {filter.status !== 'all' && (
      <FilterChip onClear={() => setFilter(f => ({ ...f, status: 'all' }))}>
        {filter.status}
      </FilterChip>
    )}
    {filter.priority !== 'all' && (
      <FilterChip onClear={() => setFilter(f => ({ ...f, priority: 'all' }))}>
        {filter.priority}
      </FilterChip>
    )}
  </div>
)}
```

---

### 2. `src/features/document-intake/watch-root-strip.tsx` (or wherever it lives)

Add two icon buttons to the right side of the strip.

```tsx
<div className="flex shrink-0 items-center gap-1.5 text-[11px] text-ink/55">
  <span className="tnum"><b className="font-semibold text-ink">{fileCount}</b> files indexed</span>
  <span className="mx-1 h-3 w-px bg-ink/15" />
  <span>Rescans every 5s</span>
  <button
    onClick={() => invoke('reveal_in_file_manager', { path: status.root })}
    title="Reveal in Finder"
    className="ml-2 grid h-6 w-6 place-items-center rounded-md text-ink/55 hover:bg-ink/[0.06] hover:text-ink"
  >
    <FolderOpenIcon className="h-3.5 w-3.5" />
  </button>
  <button
    onClick={() => { navigator.clipboard.writeText(status.root); toast('Path copied'); }}
    title="Copy path"
    className="grid h-6 w-6 place-items-center rounded-md text-ink/55 hover:bg-ink/[0.06] hover:text-ink"
  >
    <CopyIcon className="h-3.5 w-3.5" />
  </button>
</div>
```

If you already have a toast primitive in the app, use it; otherwise a one-second floating div is fine. Don't introduce a new toast library for this.

---

### 3. New Tauri command — `reveal_in_file_manager`

Add to `src-tauri/src/main.rs` (or wherever existing commands live):

```rust
#[tauri::command]
fn reveal_in_file_manager(path: String) -> Result<(), String> {
    use std::process::Command;

    #[cfg(target_os = "macos")]
    let result = Command::new("open").args(["-R", &path]).spawn();

    #[cfg(target_os = "windows")]
    let result = Command::new("explorer").args(["/select,", &path]).spawn();

    #[cfg(target_os = "linux")]
    let result = Command::new("xdg-open").arg(&path).spawn();

    result.map(|_| ()).map_err(|e| e.to_string())
}
```

Register it on the `Builder::default().invoke_handler(...)` line.

For Tauri v2, you may also need to declare the capability in `src-tauri/capabilities/main.json` (or equivalent). Don't add `shell:allow-open` blanketly — register only this one command's permission.

**Note:** the `-R` flag on macOS selects the file/folder in its parent in Finder. On Linux there's no equivalent universal selector; `xdg-open` opens the folder itself, which is the next best thing. The README for this pass should call that platform difference out so the user isn't surprised.

---

## Acceptance criteria

- [ ] Clicking a category in the sidebar narrows the table to that category; the active row is visually distinct (tide-tinted background + ring)
- [ ] Clicking the active category, or the explicit "Clear" link, returns to all-categories
- [ ] "All categories" virtual row appears at the top of the sidebar; selected by default
- [ ] Letter prefix (`A.`, `B.`) is no longer visible in category names; preserved as filter `id`
- [ ] Table loses its "Why needed" column; gains a "Last modified" column (`—` when no match) and a 32px expand-chevron column
- [ ] Clicking any row toggles its expansion; expanded row shows rationale + matched files; chevron rotates 180°
- [ ] Multiple rows can be expanded at once; expansion state is session-only (does not survive reload)
- [ ] Empty state renders when filters narrow to zero rows; the "Clear filter" link resets the filter to defaults
- [ ] Active-filter chip row appears above the table when filters are non-default; each chip's X-button removes that one dimension
- [ ] Watch-root strip: two icon buttons appear on the right (reveal-in-Finder + copy-path)
- [ ] Clicking reveal-in-Finder opens the OS file manager (verified manually on macOS at minimum; document Windows/Linux behavior in the PR description)
- [ ] Clicking copy-path puts the path on the clipboard and shows a transient confirmation
- [ ] `npm test` passes
- [ ] `npm run tauri dev`: the new command is registered, the capability is declared (Tauri v2 only), and no permission errors appear in the console

---

## Notes for the implementer

- The Tauri command is the only piece of this pass that's NOT pure frontend. Get it scaffolded and tested early — if the capability declaration is wrong, you'll find out in `tauri dev`, not `npm run dev`.
- Don't try to make the rationale row-expand persist across reloads. It's not worth the localStorage thrash and the user re-reads the rationale rarely.
- The `categoryId` on `ChecklistItem` is probably derivable from the existing `cat` field (`"A. Core"` → `"A"`). Compute it once at the ingest layer rather than at every filter pass. If `ChecklistDataset` doesn't expose `categoryId` directly, add it as a derived field — same pattern as `essentialMissingCount` in pass 02.
- This pass touches no chart / dashboard surfaces. All changes are intake-side. Keep that boundary clean.
