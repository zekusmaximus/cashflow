import { describe, expect, it } from 'vitest';
import { projectHysaGate } from '../features/dashboard/hysa-projection';
import type { HysaTrajectoryPoint } from '../features/dashboard/types';

describe('projectHysaGate', () => {
  it('returns null when fewer than two points are available', () => {
    expect(projectHysaGate([], 80000)).toBeNull();
    expect(projectHysaGate([{ date: '2026-01-31', balance: 40000 }], 80000)).toBeNull();
  });

  it('extrapolates a positive trend to the target balance', () => {
    // ~$1000 per month linear climb starting at $40k → should cross $80k
    // roughly 40 months out, near 2029-05-31.
    const points: HysaTrajectoryPoint[] = [
      { date: '2026-01-31', balance: 40000 },
      { date: '2026-02-28', balance: 41000 },
      { date: '2026-03-31', balance: 42000 },
      { date: '2026-04-30', balance: 43000 },
    ];
    const projection = projectHysaGate(points, 80000);
    expect(projection).not.toBeNull();
    expect(projection!.gateDate).not.toBeNull();
    // Daily rate is ~$33/day → ~$1k/month
    expect(projection!.dailyRate).toBeGreaterThan(30);
    expect(projection!.dailyRate).toBeLessThan(40);
    // Gate should land somewhere in 2029.
    expect(projection!.gateDate!.startsWith('2029')).toBe(true);
  });

  it('returns a null gateDate when the trend is flat or shrinking', () => {
    const flat: HysaTrajectoryPoint[] = [
      { date: '2026-01-31', balance: 40000 },
      { date: '2026-02-28', balance: 40000 },
      { date: '2026-03-31', balance: 40000 },
    ];
    expect(projectHysaGate(flat, 80000)?.gateDate).toBeNull();

    const shrinking: HysaTrajectoryPoint[] = [
      { date: '2026-01-31', balance: 50000 },
      { date: '2026-02-28', balance: 48000 },
      { date: '2026-03-31', balance: 46000 },
    ];
    const projection = projectHysaGate(shrinking, 80000);
    expect(projection?.gateDate).toBeNull();
    expect(projection?.dailyRate).toBeLessThan(0);
  });

  it('reports the latest observation date when the target is already reached', () => {
    const points: HysaTrajectoryPoint[] = [
      { date: '2026-01-31', balance: 78000 },
      { date: '2026-02-28', balance: 79000 },
      { date: '2026-03-31', balance: 81000 },
    ];
    const projection = projectHysaGate(points, 80000);
    expect(projection?.gateDate).toBe('2026-03-31');
  });
});
