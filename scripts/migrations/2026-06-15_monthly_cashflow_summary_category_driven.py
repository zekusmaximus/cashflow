"""Schema migration: rebuild ``monthly_cashflow_summary`` on a category-driven
basis (2026-06-15).

Why
---
Per DL-2026-06-10-A the IonBank home-mortgage rows are classified
``primary_category='fixed_obligation'`` but carry ``direction='transfer'``, so
spend can no longer be computed from ``direction``. The old view bucketed by
``direction`` and therefore excluded the mortgage from ``outflow`` entirely and
counted transfer rows as neither inflow nor outflow — silently contradicting the
canonical ``compute_monthly_summary`` (PR #29) and the dashboard chart that reads
this view (``src/services/sqlite.ts``).

This recreates the view so its buckets are category-driven and reconcile exactly
to ``compute_monthly_summary``. Column names (``month``, ``inflow``, ``outflow``,
``net_cash_flow``) are unchanged — only the semantics change — so the frontend
and ``CashFlowMonth`` type need no change.

Safety
------
* Read-side only: drops and recreates a view. No ``transactions`` (or any other)
  data is touched. Dropping a view destroys no data.
* Idempotent: ``DROP VIEW IF EXISTS`` then recreate — safe to run repeatedly.
* ``CREATE VIEW IF NOT EXISTS`` would NOT replace the existing view, which is why
  the drop is required on an already-initialized DB.

Usage (from the repo root, venv activated):

    python scripts/migrations/2026-06-15_monthly_cashflow_summary_category_driven.py
"""

from __future__ import annotations

import sys
from pathlib import Path

_THIS = Path(__file__).resolve()
_SERVER_SRC = _THIS.parents[2] / "server" / "src"
if str(_SERVER_SRC) not in sys.path:
    sys.path.insert(0, str(_SERVER_SRC))

from liquidity_gate_mcp.config import load_settings  # noqa: E402
from liquidity_gate_mcp.database import DatabaseManager  # noqa: E402


# Keep this DDL identical to the definition in server/sql/schema.sql. The two are
# the single source of truth for the view; if you change one, change the other.
DROP_VIEW_SQL = "DROP VIEW IF EXISTS monthly_cashflow_summary;"

CREATE_VIEW_SQL = """CREATE VIEW monthly_cashflow_summary AS
SELECT
  substr(occurred_on, 1, 7) AS month,
  ROUND(SUM(CASE WHEN primary_category = 'income' AND direction = 'inflow'
                 THEN amount ELSE 0 END), 2) AS inflow,
  ROUND(SUM(CASE WHEN primary_category IN
                      ('fixed_obligation', 'variable_lifestyle', 'medical', 'abnormal')
                  AND direction != 'inflow'
                 THEN ABS(amount) ELSE 0 END), 2) AS outflow,
  ROUND(SUM(CASE WHEN primary_category = 'income' AND direction = 'inflow'
                 THEN amount ELSE 0 END)
        - SUM(CASE WHEN primary_category IN
                      ('fixed_obligation', 'variable_lifestyle', 'medical', 'abnormal')
                  AND direction != 'inflow'
                 THEN ABS(amount) ELSE 0 END), 2) AS net_cash_flow
FROM transactions
GROUP BY substr(occurred_on, 1, 7)
ORDER BY month;
"""


def _view_is_category_driven(create_sql: str | None) -> bool:
    if not create_sql:
        return False
    normalized = " ".join(create_sql.lower().split())
    # The category-driven definition references primary_category in its CASEs;
    # the old direction-based one does not.
    return "primary_category = 'income'" in normalized


def main() -> int:
    settings = load_settings()

    if not settings.database_path.exists():
        print(f"Database not found at {settings.database_path}. Nothing to do.")
        return 1

    database = DatabaseManager(settings.database_path, settings.schema_path)
    connection = database.connect()
    try:
        before = connection.execute(
            "SELECT sql FROM sqlite_master WHERE type='view' "
            "AND name='monthly_cashflow_summary'"
        ).fetchone()
        before_sql = before[0] if before else None

        if _view_is_category_driven(before_sql):
            print(
                "monthly_cashflow_summary is already category-driven. Nothing to do."
            )
            return 0

        connection.execute(DROP_VIEW_SQL)
        connection.executescript(CREATE_VIEW_SQL)
        connection.commit()

        after = connection.execute(
            "SELECT sql FROM sqlite_master WHERE type='view' "
            "AND name='monthly_cashflow_summary'"
        ).fetchone()
    finally:
        connection.close()

    print("Recreated monthly_cashflow_summary on a category-driven basis.")
    print("Category-driven definition present:", _view_is_category_driven(after[0] if after else None))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
