import { describe, expect, it } from 'vitest';
import { deriveCategoryId, parseObtainedFlag } from '../features/document-intake/checklist';

describe('parseObtainedFlag', () => {
  it('treats "yes" / "true" as obtained', () => {
    expect(parseObtainedFlag('yes')).toBe(true);
    expect(parseObtainedFlag('Yes')).toBe(true);
    expect(parseObtainedFlag('TRUE')).toBe(true);
  });

  it('treats checkmark glyphs as obtained', () => {
    expect(parseObtainedFlag('✓')).toBe(true);
    expect(parseObtainedFlag('☑ done')).toBe(true);
  });

  it('treats empty / no as not obtained', () => {
    expect(parseObtainedFlag('')).toBe(false);
    expect(parseObtainedFlag('no')).toBe(false);
    expect(parseObtainedFlag('pending')).toBe(false);
  });
});

describe('deriveCategoryId', () => {
  it('extracts the single-letter prefix as the id', () => {
    expect(deriveCategoryId('A. Core Transactions')).toBe('A');
    expect(deriveCategoryId('b. Income & Payroll')).toBe('B');
  });

  it('falls back to the trimmed label when there is no prefix', () => {
    expect(deriveCategoryId('Core Transactions')).toBe('Core Transactions');
    expect(deriveCategoryId('  Misc  ')).toBe('Misc');
  });
});
