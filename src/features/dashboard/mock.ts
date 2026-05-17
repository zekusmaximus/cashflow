import type { DashboardSnapshot } from './types';

export const dashboardMock: DashboardSnapshot = {
  months: [
    { label: 'Jan', inflow: 19200, outflow: 17350 },
    { label: 'Feb', inflow: 18600, outflow: 17120 },
    { label: 'Mar', inflow: 19340, outflow: 17710 },
    { label: 'Apr', inflow: 18880, outflow: 18140 },
    { label: 'May', inflow: 19550, outflow: 18360 },
  ],
  gates: [
    {
      gateKey: 'hysa-80000',
      label: 'HYSA gate before 2027 Roth re-engagement',
      currentAmount: 46200,
      targetAmount: 80000,
      targetDate: '2026-12-31',
    },
    {
      gateKey: 'tax-safe-harbor',
      label: 'Federal and state safe-harbor reserve',
      currentAmount: 11250,
      targetAmount: 18000,
      targetDate: '2026-09-15',
    },
  ],
  leakageCategories: [
    { name: 'Amazon convenience creep', monthlyBurn: 920, cap: 650 },
    { name: 'Pet services and boarding', monthlyBurn: 610, cap: 475 },
    { name: 'Dining and delivery spillover', monthlyBurn: 1240, cap: 900 },
  ],
  reconciliations: [
    {
      accountId: 'acct-beacon-demo',
      accountLabel: 'Beacon · Joint checking',
      accountType: 'checking',
      periodStart: '2026-04-01',
      periodEnd: '2026-04-30',
      statementOpeningBalance: 17200,
      statementClosingBalance: 18150,
      closingBalanceSource: 'metadata_running_balance',
      computedClosingBalance: 18150,
      varianceAmount: 0,
      varianceExplanation: '',
    },
    {
      accountId: 'acct-chase-demo',
      accountLabel: 'Chase · Credit Card',
      accountType: 'credit_card',
      periodStart: '2026-04-01',
      periodEnd: '2026-04-30',
      statementOpeningBalance: 3982,
      statementClosingBalance: 4435,
      closingBalanceSource: 'balances_toml',
      computedClosingBalance: 4437,
      varianceAmount: -2,
      varianceExplanation: '',
    },
  ],
};
