import { useState, useEffect, useMemo, useRef } from 'react';
import { createPortal } from 'react-dom';
import { Search } from 'lucide-react';
import type { Command } from './commands';

interface CommandPaletteProps {
  open: boolean;
  onClose: () => void;
  commands: Command[];
}

export function CommandPalette({ open, onClose, commands }: CommandPaletteProps) {
  const [query, setQuery] = useState('');
  const [selectedIndex, setSelectedIndex] = useState(0);
  const inputRef = useRef<HTMLInputElement>(null);

  const filtered = useMemo(() => fuzzyMatch(commands, query), [commands, query]);

  // Reset selection when filter changes
  useEffect(() => {
    setSelectedIndex(0);
  }, [query]);

  // Reset query/selection each time the palette opens
  useEffect(() => {
    if (open) {
      setQuery('');
      setSelectedIndex(0);
    }
  }, [open]);

  useEffect(() => {
    if (!open) return;

    function onKey(e: KeyboardEvent) {
      if (e.key === 'Escape') {
        onClose();
        return;
      }
      if (e.key === 'ArrowDown') {
        e.preventDefault();
        setSelectedIndex((i) => Math.min(i + 1, filtered.length - 1));
        return;
      }
      if (e.key === 'ArrowUp') {
        e.preventDefault();
        setSelectedIndex((i) => Math.max(i - 1, 0));
        return;
      }
      if (e.key === 'Enter') {
        const cmd = filtered[selectedIndex];
        if (cmd) {
          cmd.action();
          onClose();
        }
      }
    }

    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [open, filtered, selectedIndex, onClose]);

  if (!open) return null;

  return createPortal(
    <>
      {/* Backdrop */}
      <div
        className="fixed inset-0 z-50 bg-ink/30 backdrop-blur-sm"
        onClick={onClose}
        aria-hidden="true"
      />

      {/* Dialog */}
      <div
        role="dialog"
        aria-modal="true"
        aria-label="Command palette"
        className="fixed left-1/2 top-[20vh] z-50 w-[520px] max-w-[calc(100vw-2rem)] -translate-x-1/2 rounded-xl border border-ink/8 bg-paper shadow-card"
      >
        {/* Input row */}
        <div className="flex items-center gap-3 border-b border-ink/8 px-4 py-3">
          <Search className="h-4 w-4 shrink-0 text-ink/45" aria-hidden="true" />
          <input
            ref={inputRef}
            autoFocus
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            placeholder="Type a command, category, or range…"
            aria-label="Search commands"
            className="flex-1 bg-transparent text-[14px] text-ink placeholder:text-ink/40 focus:outline-none"
          />
          <kbd className="shrink-0 rounded border border-ink/15 bg-white px-1.5 py-0.5 text-[10px] font-mono text-ink/55">
            esc
          </kbd>
        </div>

        {/* Results */}
        <ul className="max-h-[60vh] overflow-auto p-2" role="listbox" aria-label="Commands">
          {filtered.length === 0 && (
            <li className="px-3 py-6 text-center text-[12px] text-ink/55">No matches</li>
          )}
          {filtered.map((cmd, i) => (
            <li key={cmd.id} role="option" aria-selected={i === selectedIndex}>
              <button
                type="button"
                onClick={() => {
                  cmd.action();
                  onClose();
                }}
                onMouseEnter={() => setSelectedIndex(i)}
                className={`flex w-full items-center gap-3 rounded-md px-3 py-2 text-left ${
                  i === selectedIndex ? 'bg-tide/[0.08]' : ''
                }`}
              >
                <span className="flex-1 text-[13px] text-ink">{cmd.label}</span>
                {cmd.hint && (
                  <span className="shrink-0 text-[11px] text-ink/45">{cmd.hint}</span>
                )}
                {cmd.shortcut && (
                  <kbd className="ml-auto shrink-0 rounded border border-ink/15 bg-white px-1.5 py-0.5 text-[10px] font-mono text-ink/55">
                    {cmd.shortcut}
                  </kbd>
                )}
              </button>
            </li>
          ))}
        </ul>
      </div>
    </>,
    document.body,
  );
}

function fuzzyMatch(commands: Command[], query: string): Command[] {
  if (!query.trim()) return commands;
  const q = query.toLowerCase();
  return commands
    .map((c) => ({
      cmd: c,
      score: scoreMatch([c.label, ...(c.keywords ?? [])].join(' ').toLowerCase(), q),
    }))
    .filter((x) => x.score > 0)
    .sort((a, b) => b.score - a.score)
    .map((x) => x.cmd);
}

function scoreMatch(haystack: string, needle: string): number {
  if (haystack.includes(needle)) return haystack.startsWith(needle) ? 3 : 1;
  if (haystack.split(/\s+/).some((w) => w.startsWith(needle))) return 2;
  return 0;
}
