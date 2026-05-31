"""One-time remediation: remove the superseded Chase YTD import batch that
was duplicated when the account was migrated to per-month CSVs (2026-05-31).

Background
----------
The Chase YTD export (``2026-05-12_Chase_Credit_Card.csv``, 936 rows, batch
``c248bebe-...``) was replaced by five per-month files. Because dedup was
filename-scoped at the time, ingesting the monthly files inserted a second
copy of every Chase transaction (936 -> 1972). The five monthly batches
fully cover the YTD batch by content (verified two ways, on both
``occurred_on`` and ``posted_on`` keys), so the YTD batch can be deleted
without losing any transaction.

What this does
--------------
1. Backs up the database file (``<db>.pre-chase-remediation.<ts>.bak``).
2. Deletes the 936 ``transactions`` rows for the YTD batch.
3. Deletes the YTD ``import_batches`` row (no other table FK-references it;
   if a future schema adds such an FK this step is skipped and the batch row
   is left in place — its transactions are still gone).
4. Resets ``transfer_group_key`` on any transfer group left unbalanced by the
   deletion (singletons) or that involved Chase, so re-pairing forms clean
   pairs from scratch. Intact groups (e.g. Webster<->Beacon) are untouched.
5. Re-runs ``pair_transfers`` then ``apply_classifier(reclassify_all=True)``.
   Manual overrides are preserved (the classifier skips override-stamped rows
   and the override match_key is account+date+amount+description, which the
   surviving monthly rows still satisfy).

Idempotent: once the YTD batch is gone, re-running deletes nothing, finds no
unbalanced groups, and pair/classify converge with no further changes.

NOTE — also physically move the archived YTD CSV out of the watch root
(e.g. to ``C:\\Users\\Jeff\\Projects\\cashflow\\_superseded_inputs\\``). Until
the B1 scan-scope fix is deployed an archived file under the watch root would
be re-ingested. (After the fix, ``archive/`` is skipped — but moving it out
entirely is the durable choice.)

Usage (from the repo root, venv activated):

    python scripts/migrations/2026-05-31_remediate_chase_ytd_duplicates.py
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

from liquidity_gate_mcp.classifier import apply_classifier  # noqa: E402
from liquidity_gate_mcp.config import load_settings  # noqa: E402
from liquidity_gate_mcp.database import DatabaseManager  # noqa: E402
from liquidity_gate_mcp.models import (  # noqa: E402
    ApplyClassifierRequest,
    PairTransfersRequest,
)
from liquidity_gate_mcp.transfers import pair_transfers  # noqa: E402

YTD_BATCH_ID = "c248bebe-5849-4736-8f51-801bf91c048c"
CHASE_ACCOUNT_ID = "acct-chase-credit-card"


def _backup_database(db_path: Path) -> Path:
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    backup_path = db_path.with_suffix(
        db_path.suffix + f".pre-chase-remediation.{timestamp}.bak"
    )
    shutil.copy2(db_path, backup_path)
    return backup_path


def main() -> int:
    settings = load_settings()
    database = DatabaseManager(settings.database_path, settings.schema_path)

    if not settings.database_path.exists():
        print(f"Database not found at {settings.database_path}. Nothing to do.")
        return 1

    connection = database.connect()
    try:
        ytd_count = connection.execute(
            "SELECT COUNT(*) FROM transactions WHERE import_batch_id = ?",
            (YTD_BATCH_ID,),
        ).fetchone()[0]

        if ytd_count == 0:
            print(
                f"YTD batch {YTD_BATCH_ID} has no transactions — already "
                "remediated. Re-running pair/classify to converge."
            )
        else:
            backup = _backup_database(settings.database_path)
            print(f"Backed up database to {backup}")
            print(f"Deleting {ytd_count} transactions from YTD batch {YTD_BATCH_ID} ...")
            connection.execute(
                "DELETE FROM transactions WHERE import_batch_id = ?", (YTD_BATCH_ID,)
            )

            # Drop the batch row too, unless something still references it.
            still_referenced = connection.execute(
                "SELECT COUNT(*) FROM transactions WHERE import_batch_id = ?",
                (YTD_BATCH_ID,),
            ).fetchone()[0]
            if still_referenced == 0:
                connection.execute(
                    "DELETE FROM import_batches WHERE id = ?", (YTD_BATCH_ID,)
                )
                print("Deleted YTD import_batches row.")

            # Reset transfer groups broken by the deletion so they re-pair.
            connection.execute(
                """
                UPDATE transactions
                SET transfer_group_key = NULL
                WHERE transfer_group_key IN (
                    SELECT transfer_group_key
                    FROM transactions
                    WHERE transfer_group_key IS NOT NULL
                    GROUP BY transfer_group_key
                    HAVING COUNT(*) < 2
                       OR SUM(CASE WHEN account_id = ? THEN 1 ELSE 0 END) > 0
                )
                """,
                (CHASE_ACCOUNT_ID,),
            )
            connection.commit()
    finally:
        connection.close()

    print("Re-pairing transfers ...")
    pair_result = pair_transfers(database, PairTransfersRequest(dry_run=False))
    print(f"  pairs_created={pair_result.pairs_created} unpaired={len(pair_result.unpaired)}")

    print("Re-running classifier (reclassify_all=True) ...")
    classify_result = apply_classifier(
        database, ApplyClassifierRequest(dry_run=False, reclassify_all=True)
    )
    print(
        f"  scanned={classify_result.transactions_scanned} "
        f"matched={classify_result.transactions_matched}"
    )

    # Verify the definition-of-done invariants.
    connection = database.connect(read_only=True)
    try:
        chase_total = connection.execute(
            "SELECT COUNT(*) FROM transactions WHERE account_id = ?",
            (CHASE_ACCOUNT_ID,),
        ).fetchone()[0]
        distinct_keys = connection.execute(
            "SELECT COUNT(DISTINCT source_record_key) FROM transactions WHERE account_id = ?",
            (CHASE_ACCOUNT_ID,),
        ).fetchone()[0]
        dup_probe = connection.execute(
            """
            SELECT COUNT(*) FROM (
                SELECT 1 FROM transactions
                WHERE account_id = ?
                GROUP BY occurred_on, amount, description_raw, source_record_key
                HAVING COUNT(*) > 1
            )
            """,
            (CHASE_ACCOUNT_ID,),
        ).fetchone()[0]
        db_total = connection.execute("SELECT COUNT(*) FROM transactions").fetchone()[0]
        span = connection.execute(
            "SELECT MIN(occurred_on), MAX(occurred_on) FROM transactions WHERE account_id = ?",
            (CHASE_ACCOUNT_ID,),
        ).fetchone()
    finally:
        connection.close()

    print("\n--- Definition of done (Part A) ---")
    print(f"  Chase transactions:          {chase_total}  (expect 1036)")
    print(f"  Chase DISTINCT record keys:  {distinct_keys}  (expect 1036)")
    print(f"  Chase duplicate probe rows:  {dup_probe}  (expect 0)")
    print(f"  DB total transactions:       {db_total}  (expect 1153)")
    print(f"  Chase coverage span:         {span[0]} -> {span[1]}  (expect 2025-12-30 -> 2026-05-28)")

    ok = (
        chase_total == 1036
        and distinct_keys == 1036
        and dup_probe == 0
        and db_total == 1153
    )
    print("\nRESULT:", "PASS" if ok else "CHECK VALUES ABOVE")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
