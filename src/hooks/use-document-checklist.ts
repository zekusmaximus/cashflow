import { useQuery } from '@tanstack/react-query';
import { loadChecklistDataset } from '../features/document-intake/checklist';

export function useDocumentChecklist() {
  return useQuery({
    queryKey: ['document-checklist'],
    queryFn: loadChecklistDataset,
    // Re-scan the watch root every 5s and on window focus so dropping a file
    // surfaces in the intake view without needing a manual refresh.
    refetchInterval: 5000,
    refetchOnWindowFocus: true,
  });
}
