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

  useEffect(() => {
    void bootstrapLocalDatabase();
  }, []);

  return (
    <AppShell
      view={view}
      onViewChange={setView}
      totalDocuments={checklistQuery.data?.summary.totalItems ?? 0}
      obtainedDocuments={checklistQuery.data?.summary.obtainedCount ?? 0}
      missingDocuments={checklistQuery.data?.summary.missingCount ?? 0}
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
