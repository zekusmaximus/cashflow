"""Annual cashflow spend-summary — compute + orchestration.

The annual view is the monthly view *summed*, never a separate definition: each
``months[]`` row is exactly ``compute_monthly_summary``'s ``fcf_transactions``
fields for that month, and ``totals`` is the column-wise sum. This guarantees
the annual rollup reconciles to the canonical monthly summary (and, transitively,
to the ``monthly_cashflow_summary`` SQL view) — there is one spend definition in
this project, encoded in ``monthly_summary._income_inflows`` /
``_fixed_obligation_sum`` / ``_discretionary_sum`` and mirrored in
``server/sql/schema.sql``.

Two entry points mirror the monthly module:

* ``compute_annual_summary`` — pure computation, no file I/O.
* ``generate_annual_summary`` — computes, renders markdown, writes the ``.md``
  file, returns the same dict plus ``markdown_path``.
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .balances import MortgageConfig, WealthBridgeConfig
from .database import DatabaseManager
from .models import AnnualReferenceEntry
from .monthly_summary import DISCRETIONARY_CATEGORIES, compute_monthly_summary

# New net-consumption fields rolled up alongside the frozen spend bridge. Kept in
# a separate `consumption` per-month block + `consumption_totals` so the original
# `totals` dict (income/fixed_obligations/discretionary/net_fcf) stays exactly as
# it was — the DL-2026-06-15 reconciliation and its exact-shape tests are
# untouched. Each field is the column-wise sum of the monthly `consumption`
# section, so the annual view reconciles field-for-field to the monthly path.
CONSUMPTION_FIELDS: tuple[str, ...] = (
    "earned_income",
    "reimbursements",
    "reimbursements_income_side",
    "reimbursements_spend_side",
    "mortgage_principal",
    "mortgage_principal_scheduled",
    "debt_paydown_nonscheduled",
    "net_consumption",
)

# Categories intentionally outside the spend bridge (kept in lockstep with the
# monthly path and the monthly_cashflow_summary view). The category_breakdown
# section still reports them — it is an informational reference table, not the
# spend bridge.
EXCLUDED_CATEGORIES: tuple[str, ...] = (
    "transfer",
    "income",
    "tax",
    "investment",
    "rental",
    "business_expense",
)


def compute_annual_summary(
    database: DatabaseManager,
    wealth_bridge: WealthBridgeConfig,
    year: int,
    annual_reference: AnnualReferenceEntry | None = None,
    mortgage: MortgageConfig | None = None,
) -> dict[str, Any]:
    """Compute the year's spend bridge as a per-month series plus annual totals.

    Each month present in the DB is run through ``compute_monthly_summary`` and
    its ``fcf_transactions`` fields are lifted verbatim, so every monthly row
    reconciles field-for-field to the canonical monthly summary. ``totals`` is
    the column-wise sum. ``category_breakdown`` is a raw ``GROUP BY
    primary_category`` over the year (all categories, informational). Pure — no
    file I/O.
    """
    year_start = f"{year:04d}-01-01"
    next_year_start = f"{year + 1:04d}-01-01"

    connection = database.connect(read_only=True)
    try:
        present_labels = [
            row["m"]
            for row in connection.execute(
                "SELECT DISTINCT substr(occurred_on, 1, 7) AS m FROM transactions "
                "WHERE occurred_on >= ? AND occurred_on < ? ORDER BY m",
                (year_start, next_year_start),
            ).fetchall()
        ]
        # Informational reference table: every primary_category present in the
        # year, descending by total_usd, ties broken by primary_category asc.
        category_breakdown = [
            {
                "primary_category": row["primary_category"],
                "txn_count": int(row["c"]),
                "total_usd": round(float(row["t"]), 2),
            }
            for row in connection.execute(
                "SELECT primary_category, COUNT(*) AS c, "
                "COALESCE(SUM(ABS(amount)), 0) AS t FROM transactions "
                "WHERE occurred_on >= ? AND occurred_on < ? "
                "GROUP BY primary_category ORDER BY t DESC, primary_category ASC",
                (year_start, next_year_start),
            ).fetchall()
        ]
    finally:
        connection.close()

    months: list[dict[str, Any]] = []
    totals = {"income": 0.0, "fixed_obligations": 0.0, "discretionary": 0.0, "net_fcf": 0.0}
    consumption_totals = {key: 0.0 for key in CONSUMPTION_FIELDS}
    for label in present_labels:
        month = int(label[5:7])
        monthly = compute_monthly_summary(
            database,
            wealth_bridge,
            year,
            month,
            annual_reference=annual_reference,
            mortgage=mortgage,
        )
        fcf = monthly["fcf_transactions"]
        consumption = monthly["consumption"]
        row = {
            "month": label,
            "income": fcf["inflows"],
            "fixed_obligations": fcf["fixed_obligations"],
            "discretionary": fcf["discretionary"],
            "net_fcf": fcf["net_fcf"],
            "consumption": {key: consumption[key] for key in CONSUMPTION_FIELDS},
        }
        months.append(row)
        for key in totals:
            totals[key] += row[key]
        for key in CONSUMPTION_FIELDS:
            consumption_totals[key] += consumption[key]

    totals = {key: round(value, 2) for key, value in totals.items()}
    consumption_totals = {key: round(value, 2) for key, value in consumption_totals.items()}

    return {
        "year": year,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "months": months,
        "totals": totals,
        "consumption_totals": consumption_totals,
        "category_breakdown": category_breakdown,
        "methodology": {
            "discretionary_categories": list(DISCRETIONARY_CATEGORIES),
            "spend_definition": "fixed_obligation + discretionary, direction != 'inflow'",
            "net_consumption_definition": (
                "fixed_obligation + discretionary − reimbursements − "
                "mortgage_principal (additive; net_fcf and gross lines unchanged)"
            ),
            "excluded_categories": list(EXCLUDED_CATEGORIES),
        },
    }


def generate_annual_summary(
    database: DatabaseManager,
    wealth_bridge: WealthBridgeConfig,
    year: int,
    output_root: Path,
    annual_reference: AnnualReferenceEntry | None = None,
    mortgage: MortgageConfig | None = None,
) -> dict[str, Any]:
    """Compute the annual summary, render + write the markdown, return the dict.

    Returns the same dict as ``compute_annual_summary`` plus ``markdown_path``
    (the written file location) and ``flags.manual_block_preserved`` reflecting
    whether an existing file's manual notes were carried forward. Written to
    ``<output_root>/<output_dir>/<year>_Annual_Cashflow_Summary.md``.
    """
    # Imported here to avoid a module-import cycle through the renderer.
    from .annual_summary_renderer import write_annual_summary

    summary = compute_annual_summary(
        database,
        wealth_bridge,
        year,
        annual_reference=annual_reference,
        mortgage=mortgage,
    )
    markdown_path, manual_preserved = write_annual_summary(
        summary, output_root, wealth_bridge.monthly_summary_output_dir
    )
    summary["markdown_path"] = str(markdown_path)
    summary["manual_block_preserved"] = manual_preserved
    return summary
