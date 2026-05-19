import type {
  HysaProjection,
  HysaTrajectoryPoint,
} from './types';

const MS_PER_DAY = 86_400_000;

function parseDateUtc(yyyyMmDd: string): number {
  // Treat the date as UTC midnight so subtraction returns whole-day counts
  // independent of the analyst's local timezone.
  return Date.parse(`${yyyyMmDd}T00:00:00Z`);
}

function formatDateUtc(timestamp: number): string {
  const iso = new Date(timestamp).toISOString();
  return iso.slice(0, 10);
}

/**
 * Linear-least-squares fit of balance vs. days-since-first-point.
 * Returns null if there are fewer than two distinct dates.
 */
export function projectHysaGate(
  points: HysaTrajectoryPoint[],
  targetAmount: number,
): HysaProjection | null {
  if (points.length < 2) return null;

  const firstTs = parseDateUtc(points[0].date);
  const xs: number[] = [];
  const ys: number[] = [];
  for (const p of points) {
    const ts = parseDateUtc(p.date);
    xs.push((ts - firstTs) / MS_PER_DAY);
    ys.push(p.balance);
  }

  // Reject the fit if every x is identical (e.g. duplicate-date rows).
  if (xs.every((x) => x === xs[0])) return null;

  const n = xs.length;
  const meanX = xs.reduce((a, b) => a + b, 0) / n;
  const meanY = ys.reduce((a, b) => a + b, 0) / n;
  let num = 0;
  let den = 0;
  for (let i = 0; i < n; i += 1) {
    const dx = xs[i] - meanX;
    num += dx * (ys[i] - meanY);
    den += dx * dx;
  }
  if (den === 0) return null;
  const slope = num / den;
  const intercept = meanY - slope * meanX;

  if (slope <= 0) {
    return { dailyRate: slope, gateDate: null, targetAmount };
  }

  // If the latest observed balance already meets or exceeds the target the
  // gate has effectively been crossed — surface the observation date instead
  // of an extrapolated past date.
  const latest = points[points.length - 1];
  if (latest.balance >= targetAmount) {
    return { dailyRate: slope, gateDate: latest.date, targetAmount };
  }

  const xAtTarget = (targetAmount - intercept) / slope;
  const projectedTs = firstTs + xAtTarget * MS_PER_DAY;
  return {
    dailyRate: slope,
    gateDate: formatDateUtc(projectedTs),
    targetAmount,
  };
}
