import { describe, expect, it } from 'vitest';
import { splitSqlStatements } from '../services/sqlite';

describe('splitSqlStatements', () => {
  it('splits two simple statements', () => {
    const sql = 'CREATE TABLE a (id INT); CREATE TABLE b (id INT);';
    expect(splitSqlStatements(sql)).toEqual([
      'CREATE TABLE a (id INT)',
      'CREATE TABLE b (id INT)',
    ]);
  });

  it('preserves semicolons inside single-quoted strings', () => {
    const sql = "INSERT INTO t (msg) VALUES ('hi; there'); SELECT 1;";
    expect(splitSqlStatements(sql)).toEqual([
      "INSERT INTO t (msg) VALUES ('hi; there')",
      'SELECT 1',
    ]);
  });

  it('handles escaped single quotes', () => {
    const sql = "INSERT INTO t VALUES ('it''s ok; really'); SELECT 1;";
    const parts = splitSqlStatements(sql);
    expect(parts).toHaveLength(2);
    expect(parts[0]).toContain("it''s ok; really");
  });

  it('does not split on semicolons inside trigger BEGIN..END blocks', () => {
    const sql = `
      CREATE TRIGGER t_audit AFTER UPDATE ON x
      BEGIN
        INSERT INTO audit (v) VALUES (NEW.v);
        UPDATE meta SET counter = counter + 1;
      END;
      SELECT 1;
    `;
    const parts = splitSqlStatements(sql);
    expect(parts).toHaveLength(2);
    expect(parts[0]).toContain('BEGIN');
    expect(parts[0]).toContain('END');
    expect(parts[1]).toBe('SELECT 1');
  });

  it('skips line comments', () => {
    const sql = '-- a leading comment\nSELECT 1;\n-- trailing\n';
    expect(splitSqlStatements(sql)).toEqual(['-- a leading comment\nSELECT 1']);
  });

  it('handles block comments containing semicolons', () => {
    const sql = '/* one; two; three */ SELECT 1; SELECT 2;';
    expect(splitSqlStatements(sql)).toEqual([
      '/* one; two; three */ SELECT 1',
      'SELECT 2',
    ]);
  });

  it('returns empty array when input is only whitespace and comments', () => {
    expect(splitSqlStatements('-- nothing\n')).toEqual([]);
  });

  it('handles double-quoted identifiers containing semicolons', () => {
    const sql = 'SELECT "weird;name" FROM t; SELECT 2;';
    expect(splitSqlStatements(sql)).toEqual([
      'SELECT "weird;name" FROM t',
      'SELECT 2',
    ]);
  });
});
