export interface CashFlowMonth {
  label: string;
  /** YYYY-MM string from the monthly_cashflow_summary view; optional for mock/test data. */
  month?: string;
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
  /** Primary-category key used in the transactions table; optional for mock/test data. */
  categoryKey?: string;
  monthlyBurn: number;
  cap: number;
}

export interface HysaTrajectoryPoint {
  /** Period-end date in YYYY-MM-DD form. */
  date: string;
  /** Closing balance at the end of that period, in dollars. */
  balance: number;
}

export interface HysaProjection {
  /** Slope of the linear fit in dollars per day. */
  dailyRate: number;
  /** Projected gate-crossing date in YYYY-MM-DD form. `null` if the slope is non-positive. */
  gateDate: string | null;
  /** Target balance the projection is solved against. */
  targetAmount: number;
}

export interface HysaTrajectory {
  points: HysaTrajectoryPoint[];
  /** Master-index §9 target — the $80K gate before 2027 Roth re-engagement. */
  targetAmount: number;
  projection: HysaProjection | null;
  accountLabel: string | null;
}

export interface SubscriptionAuditEntry {
  merchant: string;
  subcategory: string | null;
  householdRole: string | null;
  chargeCount: number;
  monthCount: number;
  avgAmount: number;
  monthlyBurn: number;
  lastChargedOn: string;
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
  subscriptionAudit: SubscriptionAuditEntry[];
  hysaTrajectory: HysaTrajectory;
}

export interface Transaction {
  id: string;
  occurredOn: string;
  accountId: string;
  accountLabel: string;
  descriptionRaw: string;
  merchantNormalized: string | null;
  primaryCategory: string;
  subcategory: string | null;
  householdRole: string;
  lifecycle: string;
  amount: number;
  direction: 'inflow' | 'outflow' | 'transfer';
}

export interface TransactionPage {
  rows: Transaction[];
  total: number;
  page: number;
  pageSize: number;
}

export type TransactionDirectionFilter = 'all' | 'inflow' | 'outflow';

/**
 * Filter shape consumed by `getTransactionPage` / `useTransactionRegister`.
 * `primaryCategory` and `accountId` use the sentinel `'all'` to mean "no filter";
 * `primaryCategory` also accepts `'unclassified'` (a state) plus any taxonomy value.
 */
export interface TransactionRegisterFilter {
  page: number;
  direction: TransactionDirectionFilter;
  /** Taxonomy value, `'unclassified'`, or `'all'`. */
  primaryCategory?: string;
  /** `accounts.id` or `'all'`. */
  accountId?: string;
  /** Free-text merchant/description search. */
  search?: string;
}

export interface AccountOption {
  id: string;
  label: string;
}

export type DashboardRange = 'ytd' | '12mo' | 'all' | 'custom';

export interface LeakageTransaction {
  id: string;
  vendor: string;
  amount: number;
  date: string;
  rawDescription: string;
  matchScore?: number;
}
