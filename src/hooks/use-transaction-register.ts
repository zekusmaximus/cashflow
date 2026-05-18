import { useQuery } from '@tanstack/react-query';
import { getTransactionPage } from '../services/sqlite';
import type { TransactionDirectionFilter } from '../features/dashboard/types';

export function useTransactionRegister(filter: {
  page: number;
  direction: TransactionDirectionFilter;
}) {
  return useQuery({
    queryKey: ['transaction-register', filter.page, filter.direction],
    queryFn: () => getTransactionPage(filter),
  });
}
