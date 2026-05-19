import type {
  CashFlowMonth,
  HysaTrajectory,
  LeakageCategory,
} from './types';

export type FeasibilityStatus = 'green' | 'yellow' | 'red' | 'unknown';

export interface FeasibilityDriver {
  label: string;
  detail: string;
  status: FeasibilityStatus;
}

export interface FeasibilityAssessment {
  status: FeasibilityStatus;
  headline: string;
  drivers: FeasibilityDriver[];
}

const MS_PER_DAY = 86_400_000;
const STATUS_WEIGHT: Record<FeasibilityStatus, number> = {
  unknown: 0,
  green: 1,
  yellow: 2,
  red: 3,
};

function worst(a: FeasibilityStatus, b: FeasibilityStatus): FeasibilityStatus {
  return STATUS_WEIGHT[a] >= STATUS_WEIGHT[b] ? a : b;
}

function monthsBetween(fromIso: string, toIso: string): number {
  const fromTs = Date.parse(`${fromIso}T00:00:00Z`);
  const toTs = Date.parse(`${toIso}T00:00:00Z`);
  if (Number.isNaN(fromTs) || Number.isNaN(toTs)) return 0;
  return (toTs - fromTs) / MS_PER_DAY / 30.44;
}

function formatCurrency(value: number): string {
  return new Intl.NumberFormat('en-US', {
    style: 'currency',
    currency: 'USD',
    maximumFractionDigits: 0,
  }).format(value);
}

interface AssessOptions {
  /** Override the "now" date used to age the HYSA projection. */
  now?: string;
}

/**
 * v1 heuristic that combines available signals into a Green/Yellow/Red badge.
 *
 * This is intentionally simple — the master-index §10 capital-plan tests
 * (401(k) max absorption, HSA contributions, Roth timing, rental net) all
 * depend on roadmap items 2 (paystub extraction), 3 (normalization), and 4
 * (forward projection engine), none of which are shipped yet. Until those
 * land, the badge can only blend three coarse signals:
 *
 *   1. Average monthly net cash flow over the observed window.
 *   2. Projected $80K HYSA gate date (linear extrapolation from
 *      reconciliation_periods).
 *   3. Aggregate leakage overage across cap-tracked categories.
 *
 * The overall badge is the worst of the three drivers.
 */
export function assessCapitalPlanFeasibility(
  months: CashFlowMonth[],
  hysaTrajectory: HysaTrajectory,
  leakage: LeakageCategory[],
  options: AssessOptions = {},
): FeasibilityAssessment {
  const drivers: FeasibilityDriver[] = [];

  // 1. Net cash flow driver.
  if (months.length === 0) {
    drivers.push({
      label: 'Monthly net cash flow',
      detail: 'Need ingested transactions to evaluate.',
      status: 'unknown',
    });
  } else {
    const totalNet = months.reduce((sum, m) => sum + (m.inflow - m.outflow), 0);
    const avgNet = totalNet / months.length;
    let status: FeasibilityStatus;
    if (avgNet < 0) status = 'red';
    else if (avgNet < 1500) status = 'yellow';
    else status = 'green';
    drivers.push({
      label: 'Monthly net cash flow',
      detail: `${formatCurrency(avgNet)} avg over ${months.length} month${months.length === 1 ? '' : 's'}`,
      status,
    });
  }

  // 2. HYSA gate trajectory driver.
  if (!hysaTrajectory.projection) {
    drivers.push({
      label: `HYSA ${formatCurrency(hysaTrajectory.targetAmount)} gate`,
      detail:
        hysaTrajectory.points.length < 2
          ? 'Need at least two reconciled HYSA periods to project.'
          : 'Projection unavailable.',
      status: 'unknown',
    });
  } else if (hysaTrajectory.projection.gateDate === null) {
    drivers.push({
      label: `HYSA ${formatCurrency(hysaTrajectory.targetAmount)} gate`,
      detail: 'Trajectory is flat or shrinking — gate not reachable on trend.',
      status: 'red',
    });
  } else {
    const today = options.now ?? new Date().toISOString().slice(0, 10);
    const monthsOut = monthsBetween(today, hysaTrajectory.projection.gateDate);
    let status: FeasibilityStatus;
    let detail: string;
    if (monthsOut <= 0) {
      status = 'green';
      detail = `Gate reached on ${hysaTrajectory.projection.gateDate}.`;
    } else if (monthsOut <= 24) {
      status = 'green';
      detail = `Projected ${hysaTrajectory.projection.gateDate} (~${monthsOut.toFixed(0)} months out).`;
    } else if (monthsOut <= 48) {
      status = 'yellow';
      detail = `Projected ${hysaTrajectory.projection.gateDate} (~${monthsOut.toFixed(0)} months out — slower than the 2027 Roth target).`;
    } else {
      status = 'red';
      detail = `Projected ${hysaTrajectory.projection.gateDate} (~${monthsOut.toFixed(0)} months out — well past 2027 Roth target).`;
    }
    drivers.push({
      label: `HYSA ${formatCurrency(hysaTrajectory.targetAmount)} gate`,
      detail,
      status,
    });
  }

  // 3. Leakage overage driver.
  if (leakage.length === 0) {
    drivers.push({
      label: 'Leakage overage',
      detail: 'No cap-tracked categories configured.',
      status: 'unknown',
    });
  } else {
    const totalOverage = leakage.reduce(
      (sum, c) => sum + Math.max(0, c.monthlyBurn - c.cap),
      0,
    );
    let status: FeasibilityStatus;
    if (totalOverage > 1000) status = 'red';
    else if (totalOverage > 300) status = 'yellow';
    else status = 'green';
    const overCount = leakage.filter((c) => c.monthlyBurn > c.cap).length;
    drivers.push({
      label: 'Leakage overage',
      detail:
        totalOverage === 0
          ? 'All cap-tracked categories within cap.'
          : `${formatCurrency(totalOverage)}/mo over cap across ${overCount} categor${overCount === 1 ? 'y' : 'ies'}.`,
      status,
    });
  }

  // Combined badge is the worst of the drivers; if every driver is unknown
  // the overall status stays unknown.
  const overall = drivers.reduce<FeasibilityStatus>(
    (acc, d) => worst(acc, d.status),
    'unknown',
  );

  const headlines: Record<FeasibilityStatus, string> = {
    green:
      'Base capital-efficiency plan and $80K HYSA trajectory look supportable on current signals.',
    yellow:
      'Plan is supportable only with discretionary caps, bonus/RSU earmarking, or delayed Roth timing.',
    red:
      'Plan not supportable on current signals without reducing spending, slowing HYSA build, or shifting Roth timing.',
    unknown:
      'Not enough data yet — ingest transactions and run reconcile_periods to populate the inputs.',
  };

  return {
    status: overall,
    headline: headlines[overall],
    drivers,
  };
}
