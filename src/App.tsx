import { useEffect, useState } from 'react';
import { AppShell, type AppView } from './components/layout/app-shell';
import { DashboardView } from './features/dashboard/dashboard-view';
import { DocumentIntakeView } from './features/document-intake/document-intake-view';
import { useDashboard } from './hooks/use-dashboard';
import { useDocumentChecklist } from './hooks/use-document-checklist';
import { bootstrapLocalDatabase } from './services/sqlite';
import type { DashboardRange } from './features/dashboard/types';

export default function App() {
  const [view, setView] = useState<AppView>('intake');
  const [dashboardRange, setDashboardRange] = useState<DashboardRange>('ytd');
  const checklistQuery = useDocumentChecklist();
  const dashboardQuery = useDashboard(dashboardRange);

  useEffect(() => {
    void bootstrapLocalDatabase();
  }, []);

  return (
    <AppShell
      view={view}
      onViewChange={setView}
      liquidityGateCurrent={dashboardQuery.data?.gates[0]?.currentAmount ?? 0}
      liquidityGate={dashboardQuery.data?.gates[0]?.targetAmount ?? 80000}
    >
      {view === 'intake' ? (
        <DocumentIntakeView query={checklistQuery} />
      ) : (
        <DashboardView
          query={dashboardQuery}
          activeRange={dashboardRange}
          onRangeChange={setDashboardRange}
        />
      )}
    </AppShell>
  );
}
