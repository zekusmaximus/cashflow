import { useQuery } from '@tanstack/react-query';
import { getUnclassifiedCount } from '../services/sqlite';

/**
 * Live count of `unclassified` transactions. Polls on the same ~5s cadence as
 * `use-transaction-counts` so the header pill tracks classification progress
 * without a manual refresh.
 */
export function useUnclassifiedCount() {
  return useQuery({
    queryKey: ['unclassified-count'],
    queryFn: getUnclassifiedCount,
    refetchInterval: 5000,
    refetchOnWindowFocus: true,
  });
}
