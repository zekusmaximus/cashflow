import { useQuery } from '@tanstack/react-query';
import { getTransactionPage } from '../services/sqlite';
import type { TransactionRegisterFilter } from '../features/dashboard/types';

export function useTransactionRegister(filter: TransactionRegisterFilter) {
  return useQuery({
    queryKey: [
      'transaction-register',
      filter.page,
      filter.direction,
      filter.primaryCategory ?? 'all',
      filter.accountId ?? 'all',
      filter.search ?? '',
    ],
    queryFn: () => getTransactionPage(filter),
  });
}
