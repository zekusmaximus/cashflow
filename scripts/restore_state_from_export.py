"""Restore overrides and rules from a verify_state export.

``verify_state {"write": true}`` writes ``<watch_root>/_state_exports/`` with
``transaction_overrides.csv`` and ``classification_rules.csv`` — every column,
header row, NULL written as ``\\N``. Those two tables are the only state that
exists nowhere but the SQLite file: transactions re-ingest from the bank CSVs and
reconciliation re-derives from ``balances.toml``, but manual overrides and rules
do not come back from anywhere. This script is what makes the export a
disaster-recovery copy rather than a dump.

What it does
------------
1. Reads both CSVs and checks their headers against the live table columns.
2. Plans the restore: ``classification_rules`` upserted by ``id``,
   ``transaction_overrides`` upserted by ``match_key`` — every column, including
   ``id``, ``created_at`` and ``updated_at``. Rows in the DB that are not in the
   export are reported and left untouched (it never deletes).
3. Dry run by default: prints the plan and writes nothing.
4. With ``--apply``: backs up the database (``<db>.pre-restore-state.<ts>.bak``,
   via the SQLite backup API), writes the plan in one transaction, then calls
   ``DatabaseManager.reapply_all_overrides()`` so every restored override lands
   on its transactions.

Foreign keys are not enforced during the restore: an override may reference an
account that is not in the DB yet. The intended order after a loss is
re-ingest first, then restore; restoring first is also safe — ingest applies the
stored overrides as rows arrive — but only rows already present are re-stamped
by this run.

Usage (from the repo root, venv activated)::

    python scripts/restore_state_from_export.py --db <path> --exports <dir>
    python scripts/restore_state_from_export.py --db <path> --exports <dir> --apply
"""

from __future__ import annotations

import argparse
import csv
import sqlite3
import sys
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

# Make the in-tree package importable without an install.
_THIS = Path(__file__).resolve()
_REPO_ROOT = _THIS.parents[1]
_SERVER_SRC = _REPO_ROOT / "server" / "src"
if str(_SERVER_SRC) not in sys.path:
    sys.path.insert(0, str(_SERVER_SRC))

from liquidity_gate_mcp.database import DatabaseManager  # noqa: E402
from liquidity_gate_mcp.verify_state import (  # noqa: E402
    EXPORT_NULL,
    OVERRIDES_EXPORT_FILENAME,
    RULES_EXPORT_FILENAME,
)

SCHEMA_PATH = _REPO_ROOT / "server" / "sql" / "schema.sql"

# (table, export file, upsert key)
TABLES: tuple[tuple[str, str, str], ...] = (
    ("classification_rules", RULES_EXPORT_FILENAME, "id"),
    ("transaction_overrides", OVERRIDES_EXPORT_FILENAME, "match_key"),
)


class RestoreError(RuntimeError):
    pass


@dataclass
class TablePlan:
    table: str
    key: str
    columns: list[str]
    inserts: list[dict[str, Any]] = field(default_factory=list)
    updates: list[tuple[dict[str, Any], list[str]]] = field(default_factory=list)
    unchanged: int = 0
    not_in_export: list[str] = field(default_factory=list)

    @property
    def writes(self) -> int:
        return len(self.inserts) + len(self.updates)


def _table_columns(connection: sqlite3.Connection, table: str) -> list[tuple[str, str]]:
    info = connection.execute(f"PRAGMA table_info({table})").fetchall()
    if not info:
        raise RestoreError(f"table {table!r} does not exist in the database")
    return [(row[1], (row[2] or "").upper()) for row in info]


def _convert(value: str, declared: str) -> Any:
    if value == EXPORT_NULL:
        return None
    if "INT" in declared:
        return int(value)
    if any(t in declared for t in ("REAL", "FLOA", "DOUB")):
        return float(value)
    return value


def load_export(path: Path, columns: list[tuple[str, str]]) -> list[dict[str, Any]]:
    """Rows of one export CSV, typed by the table's declared column types."""
    if not path.exists():
        raise RestoreError(f"export not found: {path}")
    names = [name for name, _ in columns]
    types = dict(columns)
    with path.open("r", encoding="utf-8", newline="") as handle:
        reader = csv.reader(handle)
        header = next(reader, None)
        if header != names:
            raise RestoreError(
                f"{path.name}: header {header} does not match the table columns {names}"
            )
        rows = []
        for line_number, record in enumerate(reader, start=2):
            if len(record) != len(names):
                raise RestoreError(f"{path.name}:{line_number}: expected {len(names)} fields")
            try:
                rows.append({n: _convert(v, types[n]) for n, v in zip(names, record)})
            except ValueError as exc:
                raise RestoreError(f"{path.name}:{line_number}: {exc}") from exc
    return rows


def plan_table(
    connection: sqlite3.Connection, table: str, key: str, rows: list[dict[str, Any]],
    columns: list[str],
) -> TablePlan:
    plan = TablePlan(table=table, key=key, columns=columns)
    existing = {
        row[key]: dict(row)
        for row in connection.execute(f"SELECT * FROM {table}").fetchall()
    }
    seen: set[Any] = set()
    for row in rows:
        if row[key] in seen:
            raise RestoreError(f"{table}: duplicate {key} {row[key]!r} in the export")
        seen.add(row[key])
        current = existing.get(row[key])
        if current is None:
            plan.inserts.append(row)
            continue
        changed = [c for c in columns if current.get(c) != row[c]]
        if changed:
            plan.updates.append((row, changed))
        else:
            plan.unchanged += 1
    plan.not_in_export = sorted(str(k) for k in existing if k not in seen)
    return plan


def build_plans(connection: sqlite3.Connection, exports: Path) -> list[TablePlan]:
    plans = []
    for table, filename, key in TABLES:
        columns = _table_columns(connection, table)
        rows = load_export(exports / filename, columns)
        plans.append(plan_table(connection, table, key, rows, [name for name, _ in columns]))
    return plans


def _upsert_sql(plan: TablePlan) -> str:
    cols = ", ".join(plan.columns)
    marks = ", ".join("?" * len(plan.columns))
    sets = ", ".join(f"{c} = excluded.{c}" for c in plan.columns if c != plan.key)
    return (
        f"INSERT INTO {plan.table} ({cols}) VALUES ({marks}) "
        f"ON CONFLICT({plan.key}) DO UPDATE SET {sets}"
    )


def apply_plans(connection: sqlite3.Connection, plans: list[TablePlan]) -> int:
    """Write every insert and update in one transaction. Returns rows written."""
    written = 0
    connection.execute("PRAGMA foreign_keys = OFF")
    try:
        for plan in plans:
            sql = _upsert_sql(plan)
            for row in plan.inserts + [row for row, _ in plan.updates]:
                connection.execute(sql, [row[c] for c in plan.columns])
                written += 1
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    return written


def backup_database(db_path: Path) -> Path:
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    target = db_path.with_name(f"{db_path.name}.pre-restore-state.{timestamp}.bak")
    source = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    try:
        destination = sqlite3.connect(target)
        try:
            source.backup(destination)
        finally:
            destination.close()
    finally:
        source.close()
    return target


def describe(plans: list[TablePlan]) -> list[str]:
    lines = []
    for plan in plans:
        total = plan.writes + plan.unchanged
        lines.append(
            f"{plan.table}: {total} rows in export - {len(plan.inserts)} insert, "
            f"{len(plan.updates)} update, {plan.unchanged} unchanged"
            + (
                f"; {len(plan.not_in_export)} in the DB but not in the export (left as is)"
                if plan.not_in_export
                else ""
            )
        )
        lines += [f"  insert {row[plan.key]}" for row in plan.inserts]
        lines += [f"  update {row[plan.key]}: {', '.join(changed)}" for row, changed in plan.updates]
        lines += [f"  not in export: {key}" for key in plan.not_in_export]
    return lines


def run(db_path: Path, exports: Path, *, apply: bool) -> list[str]:
    if not db_path.exists():
        raise RestoreError(
            f"no database at {db_path}; start the MCP server once (it creates the schema) first"
        )
    if not exports.is_dir():
        raise RestoreError(f"exports directory not found: {exports}")

    connection = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    try:
        plans = build_plans(connection, exports)
    finally:
        connection.close()

    lines = describe(plans)
    if not apply:
        lines.append("Dry run: nothing written. Re-run with --apply to write.")
        return lines

    backup = backup_database(db_path)
    lines.append(f"Backed up the database to {backup}")
    connection = sqlite3.connect(db_path)
    try:
        written = apply_plans(connection, plans)
    finally:
        connection.close()
    touched = DatabaseManager(db_path, SCHEMA_PATH).reapply_all_overrides()
    lines.append(
        f"Wrote {written} rows; reapply_all_overrides touched {touched} transactions."
    )
    return lines


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="restore_state_from_export",
        description="Restore transaction_overrides and classification_rules from a "
        "verify_state _state_exports/ folder.",
    )
    parser.add_argument("--db", required=True, type=Path, help="path to liquidity-gate.db")
    parser.add_argument("--exports", required=True, type=Path, help="the _state_exports directory")
    parser.add_argument("--apply", action="store_true", help="write (default: dry run)")
    args = parser.parse_args(argv)
    try:
        lines = run(args.db.expanduser().resolve(), args.exports.expanduser().resolve(), apply=args.apply)
    except (RestoreError, sqlite3.Error) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    print("\n".join(lines))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
