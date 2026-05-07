import { useQuery } from '@tanstack/react-query';
import { loadChecklistDataset } from '../features/document-intake/checklist';

export function useDocumentChecklist() {
  return useQuery({
    queryKey: ['document-checklist'],
    queryFn: loadChecklistDataset,
  });
}
