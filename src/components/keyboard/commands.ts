import type { AppView } from '../layout/app-shell';
import type { ChecklistFilter } from '../../features/document-intake/types';
import type { DashboardRange } from '../../features/dashboard/types';

const isMac =
  typeof navigator !== 'undefined' && navigator.platform.toLowerCase().includes('mac');
export const MOD_KEY = isMac ? '⌘' : 'Ctrl';
const SHIFT_KEY = isMac ? '⇧' : 'Shift';

export interface Command {
  id: string;
  label: string;
  hint?: string;
  keywords?: string[];
  shortcut?: string;
  action: () => void;
}

export interface CategoryRef {
  id: string;
  name: string;
}

export interface CommandContext {
  view: AppView;
  setView: (view: AppView) => void;
  filter: ChecklistFilter;
  setFilter: (filter: ChecklistFilter) => void;
  setRange: (range: DashboardRange) => void;
  categories: CategoryRef[];
  triggerForceRescan: () => void;
  revealWatchFolder: () => void;
  copyWatchPath: () => void;
}

export function buildCommandRegistry(ctx: CommandContext): Command[] {
  return [
    // Navigation
    {
      id: 'view:intake',
      label: 'Go to Source intake',
      shortcut: `${MOD_KEY}1`,
      hint: 'view',
      action: () => ctx.setView('intake'),
    },
    {
      id: 'view:dashboard',
      label: 'Go to Cash flow',
      shortcut: `${MOD_KEY}2`,
      hint: 'view',
      action: () => ctx.setView('dashboard'),
    },

    // Intake filters
    {
      id: 'filter:essentials',
      label: 'Start with essentials',
      hint: 'intake · filter',
      keywords: ['essential', 'priority', 'open', 'missing', 'urgent'],
      action: () => {
        ctx.setView('intake');
        ctx.setFilter({ status: 'open', priority: 'essential', category: 'all' });
      },
    },
    {
      id: 'filter:open',
      label: 'Filter to still-open sources',
      hint: 'intake · filter',
      keywords: ['missing', 'outstanding'],
      action: () => {
        ctx.setView('intake');
        ctx.setFilter({ ...ctx.filter, status: 'open' });
      },
    },
    {
      id: 'filter:clear',
      label: 'Clear all filters',
      hint: 'intake · filter',
      keywords: ['reset', 'all', 'show all'],
      action: () => ctx.setFilter({ status: 'all', priority: 'all', category: 'all' }),
    },

    // Categories (dynamic — one entry per category)
    ...ctx.categories.map((c) => ({
      id: `category:${c.id}`,
      label: `Jump to ${c.name}`,
      hint: 'intake · category',
      action: () => {
        ctx.setView('intake');
        ctx.setFilter({ ...ctx.filter, category: c.id });
      },
    })),

    // System
    {
      id: 'system:rescan',
      label: 'Force rescan watch folder',
      shortcut: `${MOD_KEY}${SHIFT_KEY}R`,
      hint: 'system',
      keywords: ['refresh', 'reload', 'scan', 'sync'],
      action: () => ctx.triggerForceRescan(),
    },
    {
      id: 'system:reveal',
      label: 'Reveal watch folder in Finder',
      hint: 'system',
      keywords: ['open', 'folder', 'explorer', 'finder'],
      action: () => ctx.revealWatchFolder(),
    },
    {
      id: 'system:copyPath',
      label: 'Copy watch folder path',
      hint: 'system',
      keywords: ['clipboard', 'path', 'copy'],
      action: () => ctx.copyWatchPath(),
    },

    // Dashboard ranges
    {
      id: 'range:ytd',
      label: 'Set range to YTD',
      hint: 'dashboard · range',
      keywords: ['year', 'ytd'],
      action: () => {
        ctx.setView('dashboard');
        ctx.setRange('ytd');
      },
    },
    {
      id: 'range:12mo',
      label: 'Set range to 12 months',
      hint: 'dashboard · range',
      keywords: ['twelve', '12', 'months'],
      action: () => {
        ctx.setView('dashboard');
        ctx.setRange('12mo');
      },
    },
    {
      id: 'range:all',
      label: 'Set range to all time',
      hint: 'dashboard · range',
      keywords: ['all', 'history', 'everything'],
      action: () => {
        ctx.setView('dashboard');
        ctx.setRange('all');
      },
    },
  ];
}
