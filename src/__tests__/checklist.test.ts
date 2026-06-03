import { describe, expect, it } from 'vitest';
import {
  classifyDisposition,
  deriveCategoryId,
  parseObtainedFlag,
} from '../features/document-intake/checklist';

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

describe('classifyDisposition (2026-06-03 coverage review)', () => {
  const disposition = (categoryId: string, document: string, obtained = false) =>
    classifyDisposition(categoryId, document, obtained);

  it('resolves the pet-records row handled via Venmo overrides', () => {
    expect(disposition('D', 'Dog grooming / daycare / boarding records')).toEqual({
      status: 'resolved',
      scope: 'transaction_enrichment',
    });
  });

  it('marks transaction-feed-covered sources as not_needed', () => {
    const expected = { status: 'not_needed', scope: 'transaction_enrichment' };
    expect(disposition('A', 'Venmo / PayPal / Zelle / Cash App history')).toEqual(expected);
    expect(disposition('A', 'Amazon order history export')).toEqual(expected);
    expect(disposition('A', 'Instacart / grocery delivery history')).toEqual(expected);
    // Every Subscriptions & Apps (H) row is already covered on Chase/Citi.
    expect(disposition('H', 'Apple subscriptions screenshot / export')).toEqual(expected);
    expect(disposition('H', 'Streaming subscriptions')).toEqual(expected);
    expect(disposition('H', 'Software / SaaS subscriptions')).toEqual(expected);
    // Utility / service bills identifiable from transaction descriptions.
    expect(disposition('D', 'Electric bills - all 2026 or CSV')).toEqual(expected);
    expect(disposition('D', 'Gas / oil / heating bills')).toEqual(expected);
    expect(disposition('D', 'Water / sewer bills')).toEqual(expected);
    expect(disposition('D', 'Internet bill')).toEqual(expected);
    expect(disposition('D', 'Cell phone bill')).toEqual(expected);
    expect(disposition('D', 'Security system')).toEqual(expected);
  });

  it('defers planning-reference documents that are never ingested', () => {
    const expected = { status: 'deferred_planning', scope: 'financial_planning_ref' };
    // Whole Insurance & Protection (E) and Tax Documents (I) categories.
    expect(disposition('E', 'Existing Jeff term life policy statement')).toEqual(expected);
    expect(disposition('E', 'Beneficiary designations - life, retirement')).toEqual(expected);
    expect(disposition('I', '2025 joint tax return')).toEqual(expected);
    expect(disposition('I', 'Form 8606 history (last 5 years, both spouses)')).toEqual(expected);
    // Rental Property (F) — everything except rent payment history.
    expect(disposition('F', 'Lease / rent amount / renewal terms')).toEqual(expected);
    expect(disposition('F', 'Prior-year Schedule E / depreciation schedule')).toEqual(expected);
    // Specific household / medical references.
    expect(disposition('D', 'Household service contracts')).toEqual(expected);
    expect(disposition('G', 'Fidelity HSA statements')).toEqual(expected);
    expect(disposition('G', 'YTD medical deductible and OOP-max tracker')).toEqual(expected);
  });

  it('keeps the remaining actionable transaction sources open', () => {
    const open = { status: 'open', scope: 'transaction_enrichment' };
    expect(disposition('F', 'Rent payment history - 2026')).toEqual(open);
    expect(disposition('G', 'HSA debit card transaction history')).toEqual(open);
    expect(disposition('D', 'Trash, lawn, snow, cleaning services')).toEqual(open);
    expect(disposition('G', 'Medical receipts folder / ledger')).toEqual(open);
    expect(disposition('G', 'Major medical / dental / vet bills')).toEqual(open);
  });

  it('treats an obtained, otherwise-unclassified row as resolved', () => {
    expect(disposition('A', 'Chase credit-card statements - all 2026 YTD', true)).toEqual({
      status: 'resolved',
      scope: 'transaction_enrichment',
    });
    expect(disposition('A', 'Chase credit-card statements - all 2026 YTD', false)).toEqual({
      status: 'open',
      scope: 'transaction_enrichment',
    });
  });
});
