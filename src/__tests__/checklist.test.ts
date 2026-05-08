import { describe, expect, it } from 'vitest';
import { parseObtainedFlag } from '../features/document-intake/checklist';

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
