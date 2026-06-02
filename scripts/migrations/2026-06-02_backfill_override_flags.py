"""One-time data backfill: stamp ``manual_override_applied`` on every
override-governed transaction the original backfill missed (2026-06-02).

Background
----------
The rule-capture batch added ``metadata_json.manual_override_applied`` stamping
plus a bootstrap backfill so ``apply_classifier(reclassify_all=True)`` skips
manual overrides. A live audit found the original backfill matched on a
narrower/derived key (the SHA-256 ``match_key``, whose description-normalization
drifts), so 20 of 187 override-governed transactions were left unflagged — and a
direct MCP ``apply_classifier(reclassify_all=true)`` would have overwritten them.

The durable fix lives in Python: ``apply_classifier`` now stamps every
override-tuple-matching row before it reclassifies (engine-level
self-protection), and ``upsert_transaction_override`` stamps at write time. This
script closes the gap on the *existing* database in one set-based, tuple-matched
UPDATE — the exact inverse of the audit query — bringing it to 187/187.

What this does
--------------
1. Backs up the database file (``<db>.pre-override-flag-backfill.<ts>.bak``).
2. Runs ``stamp_override_flags`` (tuple match on
   ``account_id, occurred_on, amount, description_raw`` — never the match_key).
3. Verifies the definition-of-done invariants:
   * zero unprotected override-governed transactions, and
   * flagged transactions == transactions matching an override.

Idempotent: the stamp's trailing flag check makes re-runs no-ops, so this is
safe to run repeatedly and on an already-correct DB.

Usage (from the repo root, venv activated):

    python scripts/migrations/2026-06-02_backfill_override_flags.py
"""

from __future__ import annotations

import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path

# Make the in-tree package importable without an install.
_THIS = Path(__file__).resolve()
_SERVER_SRC = _THIS.parents[2] / "server" / "src"
if str(_SERVER_SRC) not in sys.path:
    sys.path.insert(0, str(_SERVER_SRC))

from liquidity_gate_mcp.config import load_settings  # noqa: E402
from liquidity_gate_mcp.database import DatabaseManager, stamp_override_flags  # noqa: E402


_UNPROTECTED_SQL = """
SELECT COUNT(*) FROM transactions t
WHERE EXISTS (
    SELECT 1 FROM transaction_overrides o
    WHERE o.account_id = t.account_id AND o.occurred_on = t.occurred_on
      AND o.amount = t.amount AND o.description_raw = t.description_raw
)
AND COALESCE(json_extract(t.metadata_json, '$.manual_override_applied'), 0)
    NOT IN (1, 'true')
"""

_FLAGGED_SQL = """
SELECT COUNT(*) FROM transactions
WHERE json_extract(metadata_json, '$.manual_override_applied') IN (1, 'true')
"""

_MATCHING_SQL = """
SELECT COUNT(DISTINCT t.id) FROM transactions t
JOIN transaction_overrides o
  ON o.account_id = t.account_id AND o.occurred_on = t.occurred_on
 AND o.amount = t.amount AND o.description_raw = t.description_raw
"""


def _backup_database(db_path: Path) -> Path:
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    backup_path = db_path.with_suffix(
        db_path.suffix + f".pre-override-flag-backfill.{timestamp}.bak"
    )
    shutil.copy2(db_path, backup_path)
    return backup_path


def main() -> int:
    settings = load_settings()
    database = DatabaseManager(settings.database_path, settings.schema_path)

    if not settings.database_path.exists():
        print(f"Database not found at {settings.database_path}. Nothing to do.")
        return 1

    backup = _backup_database(settings.database_path)
    print(f"Backed up database to {backup}")

    connection = database.connect()
    try:
        before = connection.execute(_UNPROTECTED_SQL).fetchone()[0]
        print(f"Unprotected override-governed transactions (before): {before}")

        stamped = stamp_override_flags(connection)
        connection.commit()
        print(f"Stamped manual_override_applied on {stamped} row(s).")
    finally:
        connection.close()

    # Verify the definition-of-done invariants (read-only).
    connection = database.connect(read_only=True)
    try:
        unprotected = connection.execute(_UNPROTECTED_SQL).fetchone()[0]
        flagged = connection.execute(_FLAGGED_SQL).fetchone()[0]
        matching = connection.execute(_MATCHING_SQL).fetchone()[0]
    finally:
        connection.close()

    print("\n--- Definition of done ---")
    print(f"  Unprotected override txns:   {unprotected}  (expect 0)")
    print(f"  Flagged transactions:        {flagged}")
    print(f"  Txns matching an override:   {matching}")
    print(f"  flagged == matching:         {flagged == matching}")

    ok = unprotected == 0 and flagged == matching
    print("\nRESULT:", "PASS" if ok else "CHECK VALUES ABOVE")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
