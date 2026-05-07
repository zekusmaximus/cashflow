import { useQuery } from '@tanstack/react-query';
import { getDashboardSnapshot } from '../services/sqlite';

export function useDashboard() {
  return useQuery({
    queryKey: ['dashboard-snapshot'],
    queryFn: getDashboardSnapshot,
  });
}
