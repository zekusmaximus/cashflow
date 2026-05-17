export interface CashFlowMonth {
  label: string;
  inflow: number;
  outflow: number;
}

export interface LiquidityGate {
  gateKey: string;
  label: string;
  currentAmount: number;
  targetAmount: number;
  targetDate: string;
}

export interface LeakageCategory {
  name: string;
  monthlyBurn: number;
  cap: number;
}

export interface ReconciliationPeriod {
  accountId: string;
  accountLabel: string;
  accountType: string;
  periodStart: string;
  periodEnd: string;
  statementOpeningBalance: number | null;
  statementClosingBalance: number | null;
  closingBalanceSource: string | null;
  computedClosingBalance: number | null;
  varianceAmount: number | null;
  varianceExplanation: string;
}

export interface DashboardSnapshot {
  months: CashFlowMonth[];
  gates: LiquidityGate[];
  leakageCategories: LeakageCategory[];
  reconciliations: ReconciliationPeriod[];
}
