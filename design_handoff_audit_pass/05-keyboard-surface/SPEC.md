# 05 — Keyboard surface

**Audit findings addressed:**
- [No keyboard surface — and this is a desktop app](../Design%20Audit.html) (medium, cross-cutting)

**Scope:** Adds a command palette (`⌘K`), global keyboard shortcuts for the highest-frequency actions, focus-ring polish across interactive elements, and a small persistent hint cluster. Earns "snappy" outright — power users discover it once and never touch the mouse again.

**Estimated effort:** 1 day.

**Prereq:** All earlier passes (01–04) merged. The command palette's commands target selectors and state that earlier passes establish — the table filter, the intake banner, the watch-root strip, the dashboard time-range, the transaction drawer.

---

## What "keyboard surface" means in practice

Three interlocking pieces:

1. **Global shortcuts.** Always-on key handlers for the highest-frequency actions (tab switching, command palette, force-rescan).
2. **Command palette.** A `⌘K` modal that searches across an action registry: jump to category, filter to missing/essential, switch tabs, open watch folder, scroll to chart, etc. Type to fuzzy-match. Enter to execute.
3. **Discoverability.** A small `⌘K` hint pinned to the bottom-right of the window. Users need to know the surface exists.

---

## File changes

### 1. New: `src/components/keyboard/command-palette.tsx`

The modal itself. Centered, ~520px wide, with an input at the top and a list of matching commands below.

```tsx
interface CommandPaletteProps {
  open: boolean;
  onClose: () => void;
  commands: Command[];
}

interface Command {
  id: string;
  label: string;
  hint?: string;          // e.g. "intake · filter"
  keywords?: string[];    // for fuzzy match
  shortcut?: string;      // displayed; not bound here
  action: () => void;
}

export function CommandPalette({ open, onClose, commands }: CommandPaletteProps) {
  const [query, setQuery] = useState('');
  const [selectedIndex, setSelectedIndex] = useState(0);

  const filtered = useMemo(() => fuzzyMatch(commands, query), [commands, query]);
  // Clamp selected index when filter narrows
  useEffect(() => { setSelectedIndex(0); }, [query]);

  useEffect(() => {
    if (!open) return;
    function onKey(e: KeyboardEvent) {
      if (e.key === 'Escape') onClose();
      if (e.key === 'ArrowDown') { e.preventDefault(); setSelectedIndex(i => Math.min(i + 1, filtered.length - 1)); }
      if (e.key === 'ArrowUp')   { e.preventDefault(); setSelectedIndex(i => Math.max(i - 1, 0)); }
      if (e.key === 'Enter') {
        const cmd = filtered[selectedIndex];
        if (cmd) { cmd.action(); onClose(); }
      }
    }
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [open, filtered, selectedIndex, onClose]);

  if (!open) return null;

  return createPortal(
    <>
      <div className="fixed inset-0 z-50 bg-ink/30 backdrop-blur-sm" onClick={onClose} />
      <div className="fixed left-1/2 top-[20vh] z-50 w-[520px] -translate-x-1/2 rounded-xl border border-ink/8 bg-paper shadow-card">
        <div className="flex items-center gap-3 border-b border-ink/8 px-4 py-3">
          <SearchIcon className="h-4 w-4 text-ink/45" />
          <input
            autoFocus
            value={query}
            onChange={e => setQuery(e.target.value)}
            placeholder="Type a command, document, or category…"
            className="flex-1 bg-transparent text-[14px] text-ink placeholder:text-ink/40 focus:outline-none"
          />
          <kbd className="rounded border border-ink/15 bg-white px-1.5 py-0.5 text-[10px] font-mono text-ink/55">esc</kbd>
        </div>
        <ul className="max-h-[60vh] overflow-auto p-2">
          {filtered.length === 0 && (
            <li className="px-3 py-6 text-center text-[12px] text-ink/55">No matches</li>
          )}
          {filtered.map((cmd, i) => (
            <li key={cmd.id}>
              <button
                onClick={() => { cmd.action(); onClose(); }}
                onMouseEnter={() => setSelectedIndex(i)}
                className={`flex w-full items-center gap-3 rounded-md px-3 py-2 text-left ${
                  i === selectedIndex ? 'bg-tide/[0.08]' : ''
                }`}
              >
                <span className="text-[13px] text-ink">{cmd.label}</span>
                {cmd.hint && <span className="text-[11px] text-ink/45">{cmd.hint}</span>}
                {cmd.shortcut && (
                  <kbd className="ml-auto rounded border border-ink/15 bg-white px-1.5 py-0.5 text-[10px] font-mono text-ink/55">
                    {cmd.shortcut}
                  </kbd>
                )}
              </button>
            </li>
          ))}
        </ul>
      </div>
    </>,
    document.body
  );
}

function fuzzyMatch(commands: Command[], query: string): Command[] {
  if (!query.trim()) return commands;
  const q = query.toLowerCase();
  return commands
    .map(c => ({
      cmd: c,
      score: scoreMatch([c.label, ...(c.keywords ?? [])].join(' ').toLowerCase(), q),
    }))
    .filter(x => x.score > 0)
    .sort((a, b) => b.score - a.score)
    .map(x => x.cmd);
}

function scoreMatch(haystack: string, needle: string): number {
  // Simple: contains = 1, starts-with-word = 2. Replace with fuse.js if needed later.
  if (haystack.includes(needle)) return haystack.startsWith(needle) ? 3 : 1;
  // Substring word boundary
  if (haystack.split(/\s+/).some(w => w.startsWith(needle))) return 2;
  return 0;
}
```

Keep the fuzzy matcher simple. The command set is small (~20 commands); a no-dependency scorer is fine. Don't pull in fuse.js for this.

---

### 2. New: `src/components/keyboard/use-keyboard-shortcuts.tsx`

A global hook installed once at the app root. Maps key chords to actions.

```tsx
interface ShortcutMap {
  [chord: string]: () => void;
}

export function useKeyboardShortcuts(shortcuts: ShortcutMap, enabled = true) {
  useEffect(() => {
    if (!enabled) return;
    function onKey(e: KeyboardEvent) {
      // Ignore key events sourced from inputs / textareas / contenteditable
      const t = e.target as HTMLElement | null;
      if (t && (t.tagName === 'INPUT' || t.tagName === 'TEXTAREA' || t.isContentEditable)) {
        // Exception: ⌘K still works inside inputs (it opens the palette)
        if (!(e.metaKey && e.key.toLowerCase() === 'k')) return;
      }

      const chord = chordFromEvent(e);
      const handler = shortcuts[chord];
      if (handler) {
        e.preventDefault();
        handler();
      }
    }
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [shortcuts, enabled]);
}

function chordFromEvent(e: KeyboardEvent): string {
  const parts: string[] = [];
  if (e.metaKey || e.ctrlKey) parts.push('mod');
  if (e.shiftKey) parts.push('shift');
  if (e.altKey) parts.push('alt');
  parts.push(e.key.toLowerCase());
  return parts.join('+');
}
```

The `mod` key is `⌘` on macOS and `Ctrl` elsewhere. Display in the UI based on platform:
```ts
const isMac = navigator.platform.toLowerCase().includes('mac');
const modKey = isMac ? '⌘' : 'Ctrl';
```

---

### 3. `src/App.tsx`

Wire the global shortcuts and the palette state.

```tsx
function App() {
  const [view, setView] = useState<AppView>('intake');
  const [paletteOpen, setPaletteOpen] = useState(false);

  const commands = useMemo(() => buildCommandRegistry({ view, setView, /* other state hooks */ }), [view]);

  useKeyboardShortcuts({
    'mod+k': () => setPaletteOpen(true),
    'mod+1': () => setView('intake'),
    'mod+2': () => setView('dashboard'),
    'mod+r': () => triggerForceRescan(),
  });

  return (
    <>
      <AppShell view={view} onViewChange={setView}>
        {view === 'intake' ? <DocumentIntakeView /> : <DashboardView />}
      </AppShell>
      <CommandPalette open={paletteOpen} onClose={() => setPaletteOpen(false)} commands={commands} />
      <KeyboardHint />
    </>
  );
}
```

**Important:** Tauri intercepts some shortcuts before the webview sees them. `⌘R` typically reloads the webview by default. Decide:
- If keeping `⌘R` for force-rescan, disable Tauri's webview reload binding in the Tauri config OR pick a different shortcut (`⌘⇧R` is a common compromise).
- If keeping webview reload, use `⌘⇧R` for force-rescan.

Document the choice in the PR description.

---

### 4. The command registry

```ts
// src/components/keyboard/commands.ts
export function buildCommandRegistry(ctx: CommandContext): Command[] {
  return [
    // Navigation
    { id: 'view:intake',    label: 'Go to Intake',     shortcut: `${modKey}1`, hint: 'view', action: () => ctx.setView('intake') },
    { id: 'view:dashboard', label: 'Go to Cash flow',  shortcut: `${modKey}2`, hint: 'view', action: () => ctx.setView('dashboard') },

    // Intake filters
    { id: 'filter:essentials', label: 'Filter to essential missing', hint: 'intake · filter',
      keywords: ['urgent', 'priority'],
      action: () => { ctx.setView('intake'); ctx.setFilter({ status: 'missing', priority: 'essential', category: 'all' }); } },
    { id: 'filter:missing', label: 'Filter to missing', hint: 'intake · filter',
      action: () => { ctx.setView('intake'); ctx.setFilter({ ...ctx.filter, status: 'missing' }); } },
    { id: 'filter:clear', label: 'Clear all filters', hint: 'intake · filter',
      action: () => ctx.setFilter({ status: 'all', priority: 'all', category: 'all' }) },

    // Categories (dynamic — one entry per category)
    ...ctx.categories.map(c => ({
      id: `category:${c.id}`,
      label: `Jump to ${c.name}`,
      hint: 'intake · category',
      action: () => { ctx.setView('intake'); ctx.setFilter({ ...ctx.filter, category: c.id }); },
    })),

    // System
    { id: 'system:rescan',   label: 'Force rescan watch folder', shortcut: `${modKey}R`, hint: 'system',
      action: () => ctx.triggerForceRescan() },
    { id: 'system:reveal',   label: 'Reveal watch folder in Finder', hint: 'system',
      action: () => ctx.revealWatchFolder() },
    { id: 'system:copyPath', label: 'Copy watch folder path',     hint: 'system',
      action: () => ctx.copyWatchPath() },

    // Dashboard
    { id: 'range:ytd',   label: 'Set range to YTD',  hint: 'dashboard · range',
      action: () => { ctx.setView('dashboard'); ctx.setRange('YTD'); } },
    { id: 'range:12mo',  label: 'Set range to 12 months', hint: 'dashboard · range',
      action: () => { ctx.setView('dashboard'); ctx.setRange('12mo'); } },
  ];
}
```

Wire each command's action to the appropriate setter or invocation that earlier passes established. Don't duplicate logic — the palette is a remote control, not a parallel implementation.

The context object is built up at the App level by composing state from the intake view, dashboard view, etc. Passing setters around is fine for this app's size; resist Redux / Zustand pressure.

---

### 5. New: `src/components/keyboard/keyboard-hint.tsx`

Small pill in the bottom-right corner. Persistent. Click to open the palette (alternative to the keyboard shortcut).

```tsx
export function KeyboardHint() {
  const [hidden, setHidden] = useState(() => localStorage.getItem('lg.keyboardHintHidden') === '1');

  if (hidden) return null;

  return (
    <button
      onClick={() => window.dispatchEvent(new CustomEvent('open-command-palette'))}
      className="fixed bottom-4 right-4 z-30 flex items-center gap-2 rounded-full border border-ink/12 bg-paper/95 px-3 py-1.5 text-[11px] text-ink/65 shadow-card backdrop-blur hover:border-ink/30 hover:text-ink"
    >
      <kbd className="rounded border border-ink/15 bg-white px-1 font-mono text-[10px] text-ink">⌘K</kbd>
      <span>commands</span>
      <span
        onClick={e => { e.stopPropagation(); setHidden(true); localStorage.setItem('lg.keyboardHintHidden', '1'); }}
        className="text-ink/35 hover:text-ink"
        role="button"
      >
        ×
      </span>
    </button>
  );
}
```

Alternative: emit the open via prop callback rather than a CustomEvent. CustomEvent is fine for decoupling; either is acceptable.

**Auto-hide after first palette open:** when the user opens the palette via `⌘K` once, set the localStorage flag automatically. They've learned the shortcut; the hint is no longer earning its space.

---

### 6. Focus-ring polish

Audit every interactive element across all three views and confirm it shows a clean focus ring. The current Tailwind setup likely defaults to the browser ring, which on macOS Safari is acceptable. On Chrome / Edge / WebView2 it can be ugly. Add to globals:

```css
*:focus-visible {
  outline: 2px solid #2d5f73; /* tide */
  outline-offset: 2px;
  border-radius: 4px;
}

button:focus-visible,
[role="button"]:focus-visible {
  outline-offset: 1px;
}
```

This is a cross-cutting polish item. Verify tab-cycling through the intake view hits every interactive element in a sensible order; if it doesn't, fix tab order with `tabindex` only where necessary (don't litter the codebase with it).

---

## Acceptance criteria

- [ ] `⌘K` (Mac) / `Ctrl+K` (Windows/Linux) opens the command palette from anywhere in the app, including inside text inputs
- [ ] Escape closes the palette; arrow keys navigate the result list; Enter executes
- [ ] Typing fuzzy-matches commands; "ess" surfaces "Filter to essential missing"; "cash" surfaces "Go to Cash flow"
- [ ] `⌘1` switches to Intake; `⌘2` switches to Cash flow; both work from any focus state except text inputs
- [ ] `⌘R` (or chosen alternative) triggers force-rescan
- [ ] Each category in the data produces a "Jump to X" command in the palette
- [ ] Watch-folder commands (reveal, copy path) work via palette as well as via the strip buttons (pass 03)
- [ ] Bottom-right `⌘K commands` hint renders by default; dismissible with the × button; auto-hides after first palette open via shortcut
- [ ] Focus ring is consistent and visible on all interactive elements (tab-cycle through both views to verify)
- [ ] No new third-party deps (no fuse.js, kbar, cmdk, etc.)
- [ ] `npm test` passes
- [ ] `npm run tauri dev`: `⌘R` does not conflict with webview reload (document which side won)

---

## Notes for the implementer

- **The biggest gotcha:** Tauri intercepts some keys before the webview sees them. Try every shortcut in `tauri dev`, not just `npm run dev`. If `⌘R` fights webview reload, change one or the other; don't ship a broken shortcut.
- **Text-input behavior matters.** Inside the search field of the intake table, `⌘1` should still switch tabs (it's a global navigation key), but plain `1` should type "1" into the input. The hook handles this by checking the event target, but verify in practice.
- **Don't pre-build for shortcuts you don't ship.** No "press `g` then `i`" Vim-style chords, no leader keys. Keep it to single-mod-key combos and `⌘K`.
- **`prefers-reduced-motion`:** the palette has no animation in this spec; if you add one, gate it on `prefers-reduced-motion`.
- **The hint pill should NEVER cover the watch-root strip or the transaction drawer.** It lives in bottom-right; the drawer pushes from right but doesn't reach the bottom 60px. If they overlap, raise the drawer's stacking context above the hint.
- **The palette is a remote control, not a parallel implementation.** Every command's action calls into setters that already exist — don't replicate filter logic, range logic, or rescan logic. If a command's action needs new logic, that's a sign the underlying feature isn't shaped right and should be pulled into the appropriate earlier pass instead.
