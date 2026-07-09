"""Markdown rendering for the annual cashflow spend-summary.

Kept separate from the compute pass so layout edits never touch the numbers.
Reuses the monthly renderer's manual-notes preservation machinery (markers +
``extract_manual_block``) so hand-written review notes survive regeneration,
exactly as they do for the monthly summary.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from .monthly_summary_renderer import (
    _AUTO_CLOSE,
    _AUTO_OPEN,
    _DEFAULT_MANUAL_BODY,
    _MANUAL_CLOSE,
    _MANUAL_OPEN,
    _money,
    _signed_money,
    extract_manual_block,
)


def _render_month_rows(months: list[dict[str, Any]]) -> str:
    if not months:
        return "| _(no transactions this year)_ | | | | |"
    return "\n".join(
        f"| {row['month']} | {_money(row['income'])} | "
        f"{_money(row['fixed_obligations'])} | {_money(row['discretionary'])} | "
        f"{_signed_money(row['net_fcf'])} |"
        for row in months
    )


def _render_consumption_rows(months: list[dict[str, Any]]) -> str:
    if not months:
        return "| _(no transactions this year)_ | | | | |"
    return "\n".join(
        f"| {row['month']} | {_money(row['consumption']['earned_income'])} | "
        f"{_money(row['consumption']['reimbursements'])} | "
        f"{_money(row['consumption']['mortgage_principal'])} | "
        f"{_money(row['consumption']['net_consumption'])} |"
        for row in months
    )


def _render_breakdown_rows(breakdown: list[dict[str, Any]]) -> str:
    if not breakdown:
        return "| _(no transactions this year)_ | | |"
    return "\n".join(
        f"| {row['primary_category']} | {row['txn_count']} | {_money(row['total_usd'])} |"
        for row in breakdown
    )


def render_annual_markdown(summary: dict[str, Any], manual_body: str) -> str:
    """Render the full annual summary document. ``manual_body`` re-emitted as-is."""
    totals = summary["totals"]
    consumption_totals = summary["consumption_totals"]
    methodology = summary["methodology"]

    return f"""# {summary['year']} Annual Cashflow Summary

_Generated {summary['generated_at']}. Bridge document for the wealth-tracker
project. Every monthly row is the canonical monthly summary for that month — the
annual view is the monthly view summed, never a separate definition._

## 1. Monthly Spend Bridge

| Month | Income | Fixed obligations | Discretionary | Net FCF |
| --- | ---: | ---: | ---: | ---: |
{_render_month_rows(summary['months'])}
| **Total** | **{_money(totals['income'])}** | **{_money(totals['fixed_obligations'])}** | **{_money(totals['discretionary'])}** | **{_signed_money(totals['net_fcf'])}** |

Net FCF per month = income − fixed_obligations − discretionary.

## 2. Net Consumption Bridge (additive)

Strictly additive over Section 1 — the frozen spend bridge is unchanged. These
lines net reimbursements/refunds out of spend and carve the mortgage principal
(debt paydown, not consumption) out of it, exposing true `net_consumption`.

| Month | Earned income | Reimbursements | Mortgage principal | Net consumption |
| --- | ---: | ---: | ---: | ---: |
{_render_consumption_rows(summary['months'])}
| **Total** | **{_money(consumption_totals['earned_income'])}** | **{_money(consumption_totals['reimbursements'])}** | **{_money(consumption_totals['mortgage_principal'])}** | **{_money(consumption_totals['net_consumption'])}** |

Net consumption = fixed_obligations + discretionary − reimbursements −
mortgage_principal. Escrow and interest stay in consumption; only loan principal
is carved out.

## 3. Category Breakdown (informational)

Raw `GROUP BY primary_category` over the year using `ABS(amount)`. This spans
**all** categories — including transfer/income/tax — so it is a reference table,
not the spend bridge.

| Category | Transactions | Total |
| --- | ---: | ---: |
{_render_breakdown_rows(summary['category_breakdown'])}

## 4. Notes for Review

{_AUTO_OPEN}
No auto-flags are computed at the annual level — see the monthly summaries for
flag detail.
{_AUTO_CLOSE}

{_MANUAL_OPEN}{manual_body}{_MANUAL_CLOSE}

---

### Methodology

- **Spend definition:** {methodology['spend_definition']}. Spend is
  category-driven, never direction-driven: a `direction='transfer'` row whose
  `primary_category` is `fixed_obligation` (the IonBank mortgage) is spending.
- **Net consumption:** {methodology['net_consumption_definition']}. Additive
  reporting layer — no transaction row is reclassified and `net_fcf` is frozen.
- **Discretionary scope:** `primary_category` ∈
  {methodology['discretionary_categories']}.
- **Excluded categories:** {methodology['excluded_categories']} are outside the
  spend bridge by definition.
- **Reconciliation:** each monthly row equals
  `compute_monthly_summary(...).fcf_transactions` for that month; `totals` is the
  column-wise sum.
"""


def write_annual_summary(
    summary: dict[str, Any],
    output_root: Path,
    output_dir_name: str,
) -> tuple[Path, bool]:
    """Render and write the annual summary markdown under ``output_root``.

    Returns ``(path, manual_block_preserved)``. The output directory is created
    if missing. An existing file's manual-notes block is lifted out and
    re-emitted so hand-written notes survive the re-run.
    """
    output_dir = Path(output_root) / output_dir_name
    output_dir.mkdir(parents=True, exist_ok=True)

    path = output_dir / f"{summary['year']}_Annual_Cashflow_Summary.md"

    existing_manual = extract_manual_block(path)
    manual_preserved = existing_manual is not None
    manual_body = existing_manual if manual_preserved else _DEFAULT_MANUAL_BODY

    path.write_text(render_annual_markdown(summary, manual_body), encoding="utf-8")
    return path, manual_preserved
