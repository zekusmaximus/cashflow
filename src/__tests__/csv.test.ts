import { describe, expect, it } from 'vitest';
import { parseCsv } from '../lib/csv';

describe('parseCsv', () => {
  it('parses a simple header + rows table', () => {
    const input = 'a,b,c\n1,2,3\n4,5,6\n';
    expect(parseCsv(input)).toEqual([
      { a: '1', b: '2', c: '3' },
      { a: '4', b: '5', c: '6' },
    ]);
  });

  it('handles quoted values containing commas', () => {
    const input = 'a,b\n"hello, world",x\n';
    expect(parseCsv(input)).toEqual([{ a: 'hello, world', b: 'x' }]);
  });

  it('handles escaped quotes inside quoted fields', () => {
    const input = 'a,b\n"she said ""hi""",y\n';
    expect(parseCsv(input)).toEqual([{ a: 'she said "hi"', b: 'y' }]);
  });

  it('handles CRLF line endings', () => {
    const input = 'a,b\r\n1,2\r\n3,4\r\n';
    expect(parseCsv(input)).toEqual([
      { a: '1', b: '2' },
      { a: '3', b: '4' },
    ]);
  });

  it('returns empty array on empty input', () => {
    expect(parseCsv('')).toEqual([]);
  });

  it('skips fully empty rows', () => {
    const input = 'a,b\n1,2\n\n3,4\n';
    expect(parseCsv(input)).toEqual([
      { a: '1', b: '2' },
      { a: '3', b: '4' },
    ]);
  });
});
