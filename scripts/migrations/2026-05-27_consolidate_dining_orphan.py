"""One-time migration: consolidate the single pre-dropdown ``subcategory =
'dining'`` row into the canonical ``dining_out`` value.

Pure SQL can't write the migration: ``transaction_overrides.match_key`` is
a SHA256 of (account_id|occurred_on|amount|normalized_description), so the
override row has to be created via the same Python helper that the upsert
path uses. Running this script through ``upsert_transaction_override``
also stamps ``metadata_json.manual_override_applied`` so a future
``apply_classifier(reclassify_all=True)`` will not undo the change.

Usage (from the repo root, venv activated):

    python -m scripts.migrations.2026_05_27_consolidate_dining_orphan

or:

    python scripts/migrations/2026-05-27_consolidate_dining_orphan.py

Idempotent: re-running the script after the first pass finds zero rows
left with ``subcategory = 'dining'`` and exits cleanly.
"""

from __future__ import annotations

import sys
from pathlib import Path

# Make the in-tree package importable without an install when run as a
# bare script. Skipped when invoked via `python -m`.
_THIS = Path(__file__).resolve()
_SERVER_SRC = _THIS.parents[2] / "server" / "src"
if str(_SERVER_SRC) not in sys.path:
    sys.path.insert(0, str(_SERVER_SRC))

from liquidity_gate_mcp.config import load_settings  # noqa: E402
from liquidity_gate_mcp.database import DatabaseManager  # noqa: E402
from liquidity_gate_mcp.models import UpsertTransactionOverrideRequest  # noqa: E402

MIGRATION_TAG = "migration:2026-05-27_consolidate_dining_orphan"


def main() -> int:
    settings = load_settings()
    database = DatabaseManager(settings.database_path, settings.schema_path)

    with database.connect(read_only=True) as conn:
        orphan_rows = conn.execute(
            "SELECT id, account_id, occurred_on, amount, description_raw, merchant_normalized "
            "FROM transactions WHERE subcategory = 'dining'"
        ).fetchall()

    if not orphan_rows:
        print("No transactions with subcategory='dining' found. Nothing to do.")
        return 0

    print(f"Found {len(orphan_rows)} transaction(s) with subcategory='dining':")
    for row in orphan_rows:
        print(
            f"  id={row['id']} occurred_on={row['occurred_on']} "
            f"amount={row['amount']} desc={row['description_raw']!r}"
        )

    written = 0
    for row in orphan_rows:
        note = (
            f"Consolidate pre-dropdown dining label into dining_out [{MIGRATION_TAG}]"
        )
        request = UpsertTransactionOverrideRequest(
            transaction_id=row["id"],
            subcategory="dining_out",
            note=note,
        )
        result = database.upsert_transaction_override(request)
        print(
            f"  -> override match_key={result.match_key} "
            f"updated_transactions={result.updated_transactions}"
        )
        written += 1

    with database.connect(read_only=True) as conn:
        remaining = conn.execute(
            "SELECT COUNT(*) AS n FROM transactions WHERE subcategory = 'dining'"
        ).fetchone()["n"]
    print(f"\nWrote {written} override(s). Remaining 'dining' rows: {remaining}.")
    return 0 if remaining == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
