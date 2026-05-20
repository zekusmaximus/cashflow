import { useQuery } from '@tanstack/react-query';
import { isTauriRuntime } from '../lib/tauri';
import type { LeakageTransaction } from '../features/dashboard/types';

export function useLeakageTransactions(
  categoryKey: string | null,
  range: { start: string; end: string },
  opts?: { enabled?: boolean },
) {
  return useQuery({
    queryKey: ['leakage-transactions', categoryKey, range.start, range.end],
    queryFn: async (): Promise<LeakageTransaction[]> => {
      if (!isTauriRuntime()) return [];
      const { invoke } = await import('@tauri-apps/api/core');
      return invoke<LeakageTransaction[]>('list_leakage_transactions', {
        categoryId: categoryKey,
        range,
      });
    },
    enabled: Boolean(categoryKey) && (opts?.enabled ?? true),
    staleTime: 30_000,
  });
}
