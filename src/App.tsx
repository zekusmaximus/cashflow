import { useEffect, useState } from 'react';
import { AppShell, type AppView } from './components/layout/app-shell';
import { DashboardView } from './features/dashboard/dashboard-view';
import { DocumentIntakeView } from './features/document-intake/document-intake-view';
import { useDashboard } from './hooks/use-dashboard';
import { useDocumentChecklist } from './hooks/use-document-checklist';
import { bootstrapLocalDatabase } from './services/sqlite';

export default function App() {
  const [view, setView] = useState<AppView>('intake');
  const checklistQuery = useDocumentChecklist();
  const dashboardQuery = useDashboard();
  const checklistItems = checklistQuery.data?.items ?? [];
  const coreSources = checklistItems.filter((item) => item.priority.includes('Essential')).length;
  const coreReady = checklistItems.filter(
    (item) => item.obtained && item.priority.includes('Essential'),
  ).length;
  const openLater = checklistItems.filter(
    (item) => !item.obtained && !item.priority.includes('Essential'),
  ).length;

  useEffect(() => {
    void bootstrapLocalDatabase();
  }, []);

  return (
    <AppShell
      view={view}
      onViewChange={setView}
      coreSources={coreSources}
      coreReady={coreReady}
      openLater={openLater}
      liquidityGateCurrent={dashboardQuery.data?.gates[0]?.currentAmount ?? 0}
      liquidityGate={dashboardQuery.data?.gates[0]?.targetAmount ?? 80000}
    >
      {view === 'intake' ? (
        <DocumentIntakeView query={checklistQuery} />
      ) : (
        <DashboardView query={dashboardQuery} />
      )}
    </AppShell>
  );
}
