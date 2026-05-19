import { describe, expect, it } from 'vitest';
import { assessCapitalPlanFeasibility } from '../features/dashboard/feasibility';
import type {
  CashFlowMonth,
  HysaTrajectory,
  LeakageCategory,
} from '../features/dashboard/types';

const NOW = '2026-05-19';

function months(...rows: Array<[number, number]>): CashFlowMonth[] {
  return rows.map(([inflow, outflow], i) => ({
    label: `M${i + 1}`,
    inflow,
    outflow,
  }));
}

function trajectory(opts: {
  gateDate: string | null;
  points?: number;
}): HysaTrajectory {
  const points = Array.from({ length: opts.points ?? 4 }, (_, i) => ({
    date: `2026-0${i + 1}-28`,
    balance: 40000 + i * 1000,
  }));
  return {
    points,
    targetAmount: 80000,
    projection: { dailyRate: 33, gateDate: opts.gateDate, targetAmount: 80000 },
    accountLabel: 'Ally · Ally HYSA',
  };
}

const NO_LEAKAGE: LeakageCategory[] = [];

describe('assessCapitalPlanFeasibility', () => {
  it('returns Green when every driver is healthy', () => {
    const result = assessCapitalPlanFeasibility(
      months([20000, 16000], [20500, 16500], [21000, 17000]),
      trajectory({ gateDate: '2027-12-31' }),
      [{ name: 'Dining', monthlyBurn: 800, cap: 900 }],
      { now: NOW },
    );
    expect(result.status).toBe('green');
    expect(result.drivers.map((d) => d.status)).toEqual(['green', 'green', 'green']);
  });

  it('escalates to Yellow when HYSA gate is far out', () => {
    const result = assessCapitalPlanFeasibility(
      months([20000, 17000], [20500, 17500], [21000, 18000]),
      trajectory({ gateDate: '2029-08-31' }),
      NO_LEAKAGE,
      { now: NOW },
    );
    expect(result.status).toBe('yellow');
    expect(
      result.drivers.find((d) => d.label.includes('HYSA'))?.status,
    ).toBe('yellow');
  });

  it('escalates to Red when net cash flow is negative', () => {
    const result = assessCapitalPlanFeasibility(
      months([15000, 18000], [16000, 17500], [15500, 18200]),
      trajectory({ gateDate: '2027-12-31' }),
      NO_LEAKAGE,
      { now: NOW },
    );
    expect(result.status).toBe('red');
    expect(result.drivers[0].status).toBe('red');
  });

  it('returns Red when the HYSA projection is flat or shrinking', () => {
    const result = assessCapitalPlanFeasibility(
      months([20000, 17000], [20000, 17000]),
      trajectory({ gateDate: null }),
      NO_LEAKAGE,
      { now: NOW },
    );
    expect(result.status).toBe('red');
  });

  it('returns "unknown" when there is nothing to evaluate', () => {
    const empty: HysaTrajectory = {
      points: [],
      targetAmount: 80000,
      projection: null,
      accountLabel: null,
    };
    const result = assessCapitalPlanFeasibility([], empty, NO_LEAKAGE, {
      now: NOW,
    });
    expect(result.status).toBe('unknown');
    expect(result.drivers.every((d) => d.status === 'unknown')).toBe(true);
  });

  it('flags heavy leakage as Red regardless of other healthy drivers', () => {
    const result = assessCapitalPlanFeasibility(
      months([20000, 16000], [20500, 16500]),
      trajectory({ gateDate: '2027-06-30' }),
      [
        { name: 'Dining', monthlyBurn: 1800, cap: 900 },
        { name: 'Amazon', monthlyBurn: 1500, cap: 650 },
      ],
      { now: NOW },
    );
    expect(result.status).toBe('red');
    expect(result.drivers.find((d) => d.label === 'Leakage overage')?.status).toBe(
      'red',
    );
  });
});
