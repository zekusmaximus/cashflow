"""Schema migration: swap the ``transactions`` uniqueness constraint from
``(source_document_name, source_record_key)`` to
``(account_id, source_record_key)`` (2026-05-31).

Why
---
Dedup is now account-scoped, not filename-scoped (see database.py
``_find_existing_transaction_id``). The DB-level invariant must match so a
future code path or manual insert cannot reintroduce the cross-file
duplication that doubled Chase. SQLite cannot ``ALTER`` a table constraint,
so this rebuilds the table (create new -> copy -> drop -> rename) inside one
transaction. Views that read ``transactions`` (``monthly_cashflow_summary``,
``v_computed_balance``) resolve by name at query time and keep working; no
other table FK-references ``transactions``.

Safety
------
* Backs up the DB file first.
* Refuses to run if any ``(account_id, source_record_key)`` collision exists
  (would violate the new constraint) — run the Chase remediation script
  first so the duplicates are gone.
* Idempotent: detects when the constraint is already account-scoped and exits.

Run the data remediation (2026-05-31_remediate_chase_ytd_duplicates.py)
BEFORE this script.

Usage (from the repo root, venv activated):

    python scripts/migrations/2026-05-31_transactions_account_scoped_unique.py
"""

from __future__ import annotations

import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path

_THIS = Path(__file__).resolve()
_SERVER_SRC = _THIS.parents[2] / "server" / "src"
if str(_SERVER_SRC) not in sys.path:
    sys.path.insert(0, str(_SERVER_SRC))

from liquidity_gate_mcp.config import load_settings  # noqa: E402
from liquidity_gate_mcp.database import DatabaseManager  # noqa: E402


NEW_TRANSACTIONS_DDL = """CREATE TABLE transactions_new (
  id TEXT PRIMARY KEY,
  account_id TEXT NOT NULL,
  import_batch_id TEXT NOT NULL,
  source_record_key TEXT NOT NULL,
  source_document_name TEXT NOT NULL,
  occurred_on TEXT NOT NULL,
  posted_on TEXT,
  description_raw TEXT NOT NULL,
  merchant_normalized TEXT,
  amount REAL NOT NULL,
  direction TEXT NOT NULL CHECK (direction IN ('inflow', 'outflow', 'transfer')),
  currency TEXT NOT NULL DEFAULT 'USD',
  primary_category TEXT NOT NULL,
  subcategory TEXT,
  household_role TEXT NOT NULL DEFAULT 'joint',
  lifecycle TEXT NOT NULL DEFAULT 'recurring',
  transfer_group_key TEXT,
  is_reconciled INTEGER NOT NULL DEFAULT 0 CHECK (is_reconciled IN (0, 1)),
  statement_period TEXT,
  metadata_json TEXT NOT NULL DEFAULT '{}',
  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  UNIQUE (account_id, source_record_key),
  FOREIGN KEY (account_id) REFERENCES accounts(id),
  FOREIGN KEY (import_batch_id) REFERENCES import_batches(id)
);
"""

TRANSACTIONS_COLUMNS = (
    "id, account_id, import_batch_id, source_record_key, source_document_name, "
    "occurred_on, posted_on, description_raw, merchant_normalized, amount, "
    "direction, currency, primary_category, subcategory, household_role, "
    "lifecycle, transfer_group_key, is_reconciled, statement_period, "
    "metadata_json, created_at"
)


def _backup_database(db_path: Path) -> Path:
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    backup_path = db_path.with_suffix(
        db_path.suffix + f".pre-unique-migration.{timestamp}.bak"
    )
    shutil.copy2(db_path, backup_path)
    return backup_path


def _constraint_is_account_scoped(create_sql: str) -> bool:
    normalized = " ".join(create_sql.lower().split())
    return "unique (account_id, source_record_key)" in normalized


def main() -> int:
    settings = load_settings()
    database = DatabaseManager(settings.database_path, settings.schema_path)

    if not settings.database_path.exists():
        print(f"Database not found at {settings.database_path}. Nothing to do.")
        return 1

    connection = database.connect()
    try:
        create_sql = connection.execute(
            "SELECT sql FROM sqlite_master WHERE type='table' AND name='transactions'"
        ).fetchone()[0]

        if _constraint_is_account_scoped(create_sql):
            print("transactions already uses UNIQUE(account_id, source_record_key). Nothing to do.")
            return 0

        collisions = connection.execute(
            """
            SELECT account_id, source_record_key, COUNT(*) c
            FROM transactions
            GROUP BY account_id, source_record_key
            HAVING COUNT(*) > 1
            """
        ).fetchall()
        if collisions:
            print(
                f"ABORT: {len(collisions)} (account_id, source_record_key) collision(s) "
                "exist. Run the Chase YTD remediation first, then re-run this migration."
            )
            for row in collisions[:10]:
                print(f"  account={row[0]} key={row[1]} count={row[2]}")
            return 1

        backup = _backup_database(settings.database_path)
        print(f"Backed up database to {backup}")

        # legacy_alter_table lets us RENAME the rebuilt table without SQLite
        # trying to re-validate the views that reference ``transactions`` mid
        # rebuild (they resolve fine once the rename completes). foreign_keys
        # is held OFF so the table swap doesn't trip referential checks.
        connection.execute("PRAGMA foreign_keys = OFF")
        connection.execute("PRAGMA legacy_alter_table = ON")
        connection.execute("DROP TABLE IF EXISTS transactions_new")
        connection.execute(NEW_TRANSACTIONS_DDL)
        connection.execute(
            f"INSERT INTO transactions_new ({TRANSACTIONS_COLUMNS}) "
            f"SELECT {TRANSACTIONS_COLUMNS} FROM transactions"
        )
        connection.execute("DROP TABLE transactions")
        connection.execute("ALTER TABLE transactions_new RENAME TO transactions")
        connection.commit()

        # Verify FK integrity survived the rebuild.
        connection.execute("PRAGMA legacy_alter_table = OFF")
        violations = connection.execute("PRAGMA foreign_key_check").fetchall()
        connection.execute("PRAGMA foreign_keys = ON")
        if violations:
            print(f"WARNING: {len(violations)} foreign-key violation(s) after rebuild:")
            for v in violations[:10]:
                print(f"  {tuple(v)}")
            return 1

        new_create_sql = connection.execute(
            "SELECT sql FROM sqlite_master WHERE type='table' AND name='transactions'"
        ).fetchone()[0]
    finally:
        connection.close()

    print("Rebuilt transactions table.")
    print("Account-scoped UNIQUE constraint present:", _constraint_is_account_scoped(new_create_sql))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
