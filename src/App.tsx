import { useCallback, useEffect, useMemo, useState } from 'react';
import { AppShell, type AppView } from './components/layout/app-shell';
import { CommandPalette } from './components/keyboard/command-palette';
import { KeyboardHint } from './components/keyboard/keyboard-hint';
import { buildCommandRegistry, type CategoryRef } from './components/keyboard/commands';
import { useKeyboardShortcuts } from './components/keyboard/use-keyboard-shortcuts';
import { DashboardView } from './features/dashboard/dashboard-view';
import { DocumentIntakeView } from './features/document-intake/document-intake-view';
import { useDashboard } from './hooks/use-dashboard';
import { useDocumentChecklist } from './hooks/use-document-checklist';
import { revealInFileManager } from './lib/tauri';
import { bootstrapLocalDatabase } from './services/sqlite';
import type { DashboardRange } from './features/dashboard/types';
import { DEFAULT_CHECKLIST_FILTER } from './features/document-intake/types';
import type { ChecklistFilter } from './features/document-intake/types';

/** Strip "A. " prefix from category names like "A. Core Income Sources" */
function categoryDisplayName(category: string): string {
  const idx = category.indexOf('. ');
  if (idx > 0 && idx <= 2) return category.slice(idx + 2);
  return category;
}

export default function App() {
  const [view, setView] = useState<AppView>('intake');
  const [dashboardRange, setDashboardRange] = useState<DashboardRange>('ytd');
  const [filter, setFilter] = useState<ChecklistFilter>(DEFAULT_CHECKLIST_FILTER);
  const [paletteOpen, setPaletteOpen] = useState(false);

  const checklistQuery = useDocumentChecklist();
  const dashboardQuery = useDashboard(dashboardRange);

  useEffect(() => {
    void bootstrapLocalDatabase();
  }, []);

  // Force-rescan: invalidate the checklist query immediately
  const triggerForceRescan = useCallback(() => {
    void checklistQuery.refetch();
  }, [checklistQuery]);

  const revealWatchFolder = useCallback(() => {
    const root = checklistQuery.data?.watchRoot?.root;
    if (root) revealInFileManager(root).catch(() => void 0);
  }, [checklistQuery.data?.watchRoot?.root]);

  const copyWatchPath = useCallback(() => {
    const root = checklistQuery.data?.watchRoot?.root;
    if (root) navigator.clipboard.writeText(root).catch(() => void 0);
  }, [checklistQuery.data?.watchRoot?.root]);

  const categories: CategoryRef[] = useMemo(
    () =>
      (checklistQuery.data?.summary.categories ?? []).map((c) => ({
        id: c.categoryId,
        name: categoryDisplayName(c.category),
      })),
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [checklistQuery.data?.summary.categories],
  );

  const commands = useMemo(
    () =>
      buildCommandRegistry({
        view,
        setView,
        filter,
        setFilter,
        setRange: setDashboardRange,
        categories,
        triggerForceRescan,
        revealWatchFolder,
        copyWatchPath,
      }),
    [view, filter, categories, triggerForceRescan, revealWatchFolder, copyWatchPath],
  );

  const openPalette = useCallback(() => {
    setPaletteOpen(true);
    // Notify KeyboardHint so it auto-hides after first shortcut use
    window.dispatchEvent(new CustomEvent('cmd-palette-opened'));
  }, []);

  // ⌘⇧R for force-rescan (avoids conflict with Tauri's webview reload on ⌘R).
  // ⌘1 / ⌘2 are global nav; ⌘K opens the palette.
  useKeyboardShortcuts({
    'mod+k': openPalette,
    'mod+1': () => setView('intake'),
    'mod+2': () => setView('dashboard'),
    'mod+shift+r': triggerForceRescan,
  });

  return (
    <>
      <AppShell
        view={view}
        onViewChange={setView}
        liquidityGateCurrent={dashboardQuery.data?.gates[0]?.currentAmount ?? 0}
        liquidityGate={dashboardQuery.data?.gates[0]?.targetAmount ?? 80000}
      >
        {view === 'intake' ? (
          <DocumentIntakeView
            query={checklistQuery}
            filter={filter}
            onFilterChange={setFilter}
          />
        ) : (
          <DashboardView
            query={dashboardQuery}
            activeRange={dashboardRange}
            onRangeChange={setDashboardRange}
          />
        )}
      </AppShell>

      <CommandPalette
        open={paletteOpen}
        onClose={() => setPaletteOpen(false)}
        commands={commands}
      />

      <KeyboardHint onOpen={() => setPaletteOpen(true)} />
    </>
  );
}
