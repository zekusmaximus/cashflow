import { useState, useEffect } from 'react';

const HINT_HIDDEN_KEY = 'lg.keyboardHintHidden';

interface KeyboardHintProps {
  onOpen: () => void;
}

/**
 * Persistent bottom-right pill. Dismissible via × button.
 * Auto-hides after the user opens the command palette via keyboard shortcut
 * (signals they've learned the surface; hint is no longer needed).
 */
export function KeyboardHint({ onOpen }: KeyboardHintProps) {
  const [hidden, setHidden] = useState(
    () => localStorage.getItem(HINT_HIDDEN_KEY) === '1',
  );

  // Auto-hide when App.tsx signals the shortcut was used
  useEffect(() => {
    function onPaletteOpened() {
      setHidden(true);
      localStorage.setItem(HINT_HIDDEN_KEY, '1');
    }
    window.addEventListener('cmd-palette-opened', onPaletteOpened);
    return () => window.removeEventListener('cmd-palette-opened', onPaletteOpened);
  }, []);

  if (hidden) return null;

  return (
    // z-30 keeps hint below the palette overlay (z-50) and above normal content.
    // bottom-4 right-4 avoids overlap with the transaction drawer (which anchors
    // from the right edge but doesn't extend to the bottom-right corner).
    <div
      className="fixed bottom-4 right-4 z-30 flex items-center gap-2 rounded-full border border-ink/12 bg-paper/95 px-3 py-1.5 text-[11px] text-ink/65 shadow-card backdrop-blur"
      role="group"
      aria-label="Keyboard shortcuts hint"
    >
      <button
        type="button"
        onClick={onOpen}
        className="flex items-center gap-2 hover:text-ink"
        aria-label="Open command palette"
      >
        <kbd className="rounded border border-ink/15 bg-white px-1 font-mono text-[10px] text-ink">
          ⌘K
        </kbd>
        <span>commands</span>
      </button>
      <button
        type="button"
        onClick={() => {
          setHidden(true);
          localStorage.setItem(HINT_HIDDEN_KEY, '1');
        }}
        aria-label="Dismiss keyboard hint"
        className="text-ink/35 hover:text-ink"
      >
        ×
      </button>
    </div>
  );
}
