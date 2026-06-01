import { useQuery } from '@tanstack/react-query';
import { getAccountOptions } from '../services/sqlite';

export function useAccountOptions() {
  return useQuery({
    queryKey: ['account-options'],
    queryFn: getAccountOptions,
    staleTime: 60_000,
  });
}
