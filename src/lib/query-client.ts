import { QueryClient } from '@tanstack/react-query';

export const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      // We hit a local SQLite file via the Tauri plugin — refresh cost is
      // essentially zero, so refetch aggressively so dropped files and
      // ingestion runs surface in the UI without a manual reload.
      refetchOnWindowFocus: true,
      staleTime: 5_000,
      retry: 1,
    },
  },
});
