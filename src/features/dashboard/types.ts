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

export interface DashboardSnapshot {
  months: CashFlowMonth[];
  gates: LiquidityGate[];
  leakageCategories: LeakageCategory[];
}
