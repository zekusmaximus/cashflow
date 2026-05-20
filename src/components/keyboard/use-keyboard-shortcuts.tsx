import { useEffect } from 'react';

interface ShortcutMap {
  [chord: string]: () => void;
}

/**
 * Global keyboard shortcut hook. Install once at the app root.
 *
 * Key events sourced from text inputs, textareas, and contenteditable elements
 * are ignored — EXCEPT for the ⌘K / Ctrl+K chord, which always opens the
 * command palette regardless of focus state.
 */
export function useKeyboardShortcuts(shortcuts: ShortcutMap, enabled = true) {
  useEffect(() => {
    if (!enabled) return;

    function onKey(e: KeyboardEvent) {
      const t = e.target as HTMLElement | null;

      // Skip text-input elements for most chords, but let ⌘K / Ctrl+K through.
      if (
        t &&
        (t.tagName === 'INPUT' || t.tagName === 'TEXTAREA' || t.isContentEditable)
      ) {
        if (!((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === 'k')) return;
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
