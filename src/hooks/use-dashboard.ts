import { useQuery } from '@tanstack/react-query';
import { getDashboardSnapshot } from '../services/sqlite';
import type { DashboardRange } from '../features/dashboard/types';

export function useDashboard(range: DashboardRange = 'ytd') {
  return useQuery({
    queryKey: ['dashboard-snapshot', range],
    queryFn: () => getDashboardSnapshot(range),
  });
}
