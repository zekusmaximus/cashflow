import { describe, expect, it } from 'vitest';
import { groupReconciliationsByAccount } from '../features/dashboard/dashboard-view';
import type { ReconciliationPeriod } from '../features/dashboard/types';

function makePeriod(
  accountId: string,
  periodEnd: string,
  varianceAmount: number | null = 0,
): ReconciliationPeriod {
  return {
    accountId,
    accountLabel: `Account ${accountId}`,
    accountType: 'checking',
    periodStart: periodEnd.slice(0, 7) + '-01',
    periodEnd,
    statementOpeningBalance: 0,
    statementClosingBalance: 0,
    closingBalanceSource: 'metadata_running_balance',
    computedClosingBalance: 0,
    varianceAmount,
    varianceExplanation: '',
  };
}

describe('groupReconciliationsByAccount', () => {
  it('preserves SQL ordering: first row per account is treated as the latest', () => {
    // Snapshot SQL sorts by period_end DESC within each account, so the first
    // row seen per account is the most recent one.
    const periods: ReconciliationPeriod[] = [
      makePeriod('acct-1', '2026-04-30'),
      makePeriod('acct-1', '2026-03-31'),
      makePeriod('acct-1', '2026-02-28'),
      makePeriod('acct-2', '2026-04-30', -2),
      makePeriod('acct-2', '2026-03-31'),
    ];

    const groups = groupReconciliationsByAccount(periods);

    expect(groups).toHaveLength(2);
    expect(groups[0].latest.accountId).toBe('acct-1');
    expect(groups[0].latest.periodEnd).toBe('2026-04-30');
    expect(groups[0].history.map((p) => p.periodEnd)).toEqual([
      '2026-03-31',
      '2026-02-28',
    ]);
    expect(groups[1].latest.accountId).toBe('acct-2');
    expect(groups[1].latest.varianceAmount).toBe(-2);
    expect(groups[1].history).toHaveLength(1);
  });

  it('returns an empty list when no periods exist', () => {
    expect(groupReconciliationsByAccount([])).toEqual([]);
  });

  it('handles a single-period account without history', () => {
    const groups = groupReconciliationsByAccount([
      makePeriod('acct-only', '2026-04-30'),
    ]);
    expect(groups).toHaveLength(1);
    expect(groups[0].history).toEqual([]);
  });
});
