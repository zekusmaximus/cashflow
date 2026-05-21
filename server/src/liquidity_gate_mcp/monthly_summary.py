"""Monthly cashflow summary — compute + orchestration.

Builds the structured bridge document consumed by the separate wealth-tracker
project. Two entry points, one shared compute pass so the JSON and the markdown
can never diverge:

* ``compute_monthly_summary`` — pure computation. Returns the structured dict
  described in the build spec. No file I/O; fully unit-testable.
* ``generate_monthly_summary`` — calls compute, renders markdown via
  ``monthly_summary_renderer``, writes the ``.md`` file, returns the same dict
  plus a ``markdown_path`` field.

Scheduling: ``generate_monthly_summary`` is invoked by a Cowork scheduled task
on the 1st of each month — shifted to the second business day when the 1st
falls on a weekend or US federal holiday — and always targets the *prior*
calendar month. The scheduler passes the correct ``(year, month)``; no
scheduler lives in this server.
"""

from __future__ import annotations

import calendar
import math
import re
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .balances import WealthBridgeConfig
from .computed_balance import ALLY_HYSA_ACCOUNT_ID
from .database import DatabaseManager
from .monthly_summary_flags import FlagInputs, detect_flags
from .monthly_summary_renderer import write_monthly_summary

# Discretionary scope is locked: outflows in exactly these primary categories.
# Transfers, taxes, fixed_obligation, investment, rental, business_expense and
# income are excluded by definition. Do not let this drift — the spec pins it.
DISCRETIONARY_CATEGORIES: tuple[str, ...] = ("variable_lifestyle", "medical", "abnormal")

# Top-mover filter thresholds (Section 3). Existing merchant: prior-month total
# must be >= $100 AND abs(delta) >= $200. New merchant (no prior spend): this
# month >= $300. Suppresses small-merchant churn.
_MOVER_MIN_PRIOR_BASE = 100.0
_MOVER_MIN_ABS_DELTA = 200.0
_MOVER_MIN_NEW_SPEND = 300.0

MERCHANT_NORMALIZATION_RULE = (
    "merchant_normalized column when populated; otherwise description_raw "
    "case-folded with internal whitespace collapsed, a trailing 2-letter "
    "US-state location code stripped, and trailing transaction/reference IDs "
    "(4+ digit runs, optional #/ref/trace prefix) stripped repeatedly."
)

# Trailing transaction / reference id: a run of 4+ digits at end of string,
# optionally prefixed by '#', 'ref', or 'trace'.
_TXN_ID_SUFFIX = re.compile(r"(?:#|ref#?|trace#?)?\s*\d{4,}\s*$", re.IGNORECASE)

_US_STATES = frozenset(
    "al ak az ar ca co ct de fl ga hi id il in ia ks ky la me md ma mi mn ms "
    "mo mt ne nv nh nj nm ny nc nd oh ok or pa ri sc sd tn tx ut vt va wa wv "
    "wi wy dc".split()
)


# ---------------------------------------------------------------------------
# Pure helpers
# ---------------------------------------------------------------------------


def normalize_merchant(description: str) -> str:
    """Collapse a raw description to a stable merchant aggregation key.

    Deterministic and date-independent so the same merchant on different days
    aggregates to one key (Section 3 of the build spec):

    1. case-fold to lowercase, collapse internal whitespace runs
    2. strip a trailing 2-letter US-state location code
    3. strip trailing transaction/reference IDs (4+ digit runs, optional
       #/ref/trace prefix), repeatedly — so "amazon #1234" and "amazon #9987"
       both collapse to "amazon"
    4. trim trailing punctuation

    The rule lives here as a documented constant so it stays stable across
    months; see ``MERCHANT_NORMALIZATION_RULE``.
    """
    text = " ".join(description.strip().lower().split())

    tokens = text.split()
    if len(tokens) > 1 and tokens[-1] in _US_STATES:
        text = " ".join(tokens[:-1])

    previous = None
    while previous != text:
        previous = text
        text = _TXN_ID_SUFFIX.sub("", text).strip()

    text = text.rstrip(" #-*.").strip()
    # Fall back to the case-folded original if stripping emptied the string.
    return text or " ".join(description.strip().lower().split())


def _merchant_key_and_name(
    merchant_normalized: str | None, description_raw: str
) -> tuple[str, str]:
    """Aggregation key + human display name for one transaction row."""
    if merchant_normalized and merchant_normalized.strip():
        name = merchant_normalized.strip()
        return name.lower(), name
    normalized = normalize_merchant(description_raw)
    return normalized, normalized.title()


def _add_months(year: int, month: int, delta: int) -> tuple[int, int]:
    """Return (year, month) shifted by ``delta`` calendar months."""
    index = year * 12 + (month - 1) + delta
    return index // 12, index % 12 + 1


def _month_window(year: int, month: int) -> tuple[str, str]:
    """ISO [start, next_start) bounds for a calendar month, for date strings."""
    start = f"{year:04d}-{month:02d}-01"
    next_year, next_month = _add_months(year, month, 1)
    return start, f"{next_year:04d}-{next_month:02d}-01"


def _month_end_iso(year: int, month: int) -> str:
    last_day = calendar.monthrange(year, month)[1]
    return f"{year:04d}-{month:02d}-{last_day:02d}"


def project_hysa_hit_date(
    balance: float,
    target: float,
    trailing_avg_delta: float,
    target_year: int,
    target_month: int,
) -> tuple[str, int | None]:
    """Extrapolate the HYSA target hit date from the trailing 3-month delta.

    Returns ``(projected_hit_date, months_remaining)``:

    * trailing average ≤ $0 → ``("indefinite", None)`` — unreachable on trend
    * balance already at/over target → ``(end of target month, 0)``
    * otherwise → ``(end of the projected month, months_remaining)`` where
      ``months_remaining = ceil(gap / trailing_avg_delta)``
    """
    gap = round(target - balance, 2)
    if gap <= 0:
        return _month_end_iso(target_year, target_month), 0
    if trailing_avg_delta <= 0:
        return "indefinite", None
    months_remaining = math.ceil(gap / trailing_avg_delta)
    hit_year, hit_month = _add_months(target_year, target_month, months_remaining)
    return _month_end_iso(hit_year, hit_month), months_remaining


def select_top_movers(
    this_month: dict[str, float],
    prior_month: dict[str, float],
    display_names: dict[str, str],
    limit: int = 3,
) -> list[dict[str, Any]]:
    """Pick the top discretionary merchant-level movers.

    Existing merchant qualifies when prior-month total ≥ $100 AND
    abs(delta) ≥ $200. New merchant (no prior-month spend) qualifies when this
    month's spend ≥ $300. Qualifying movers are ranked by abs(delta) desc.
    """
    movers: list[dict[str, Any]] = []
    for key in set(this_month) | set(prior_month):
        this_amount = round(this_month.get(key, 0.0), 2)
        prior_amount = round(prior_month.get(key, 0.0), 2)
        delta = round(this_amount - prior_amount, 2)
        is_new = prior_amount <= 0.0

        if is_new:
            if this_amount < _MOVER_MIN_NEW_SPEND:
                continue
        elif prior_amount < _MOVER_MIN_PRIOR_BASE or abs(delta) < _MOVER_MIN_ABS_DELTA:
            continue

        movers.append(
            {
                "merchant": display_names.get(key, key.title()),
                "this_month": this_amount,
                "prior_month": prior_amount,
                "delta": delta,
                "is_new": is_new,
            }
        )

    movers.sort(key=lambda m: (abs(m["delta"]), m["this_month"]), reverse=True)
    return movers[:limit]


def placeholder_401k_warning(wealth_bridge: WealthBridgeConfig) -> str | None:
    """Return a warning string when either 401(k) figure is still ``0``.

    The server logs this at init; the dry-run script prints it. ``None`` when
    both figures are populated.
    """
    if not wealth_bridge.has_placeholder_401k:
        return None
    missing = []
    if wealth_bridge.jeff_401k_monthly == 0:
        missing.append("jeff_401k_monthly")
    if wealth_bridge.ashley_401k_monthly == 0:
        missing.append("ashley_401k_monthly")
    return (
        "[monthly-summary] 401(k) config still at placeholder 0: "
        + ", ".join(missing)
        + ". Populate from the most recent Novartis paystub before trusting "
        "the theoretical savings-rate view. Generation is not blocked."
    )


# ---------------------------------------------------------------------------
# SQL helpers
# ---------------------------------------------------------------------------


def _scalar(connection: sqlite3.Connection, sql: str, params: tuple) -> float:
    row = connection.execute(sql, params).fetchone()
    return round(float(row["v"]), 2)


def _hysa_delta(connection: sqlite3.Connection, start: str, end: str) -> float:
    """Account-scoped net balance change for the HYSA over [start, end).

    All categories included: account scope IS the filter. Inbound transfers
    count positively (the savings mechanism), outbound drawdowns negatively,
    interest counts. ``amount`` is stored signed, so a plain SUM is the delta.
    """
    return _scalar(
        connection,
        "SELECT COALESCE(SUM(amount), 0) AS v FROM transactions "
        "WHERE account_id = ? AND occurred_on >= ? AND occurred_on < ?",
        (ALLY_HYSA_ACCOUNT_ID, start, end),
    )


def _discretionary_sum(connection: sqlite3.Connection, start: str, end: str) -> float:
    placeholders = ",".join("?" * len(DISCRETIONARY_CATEGORIES))
    return _scalar(
        connection,
        f"SELECT COALESCE(SUM(ABS(amount)), 0) AS v FROM transactions "
        f"WHERE primary_category IN ({placeholders}) AND direction = 'outflow' "
        f"AND occurred_on >= ? AND occurred_on < ?",
        (*DISCRETIONARY_CATEGORIES, start, end),
    )


def _merchant_totals(
    connection: sqlite3.Connection, start: str, end: str
) -> tuple[dict[str, float], dict[str, str]]:
    """Discretionary outflow totals per merchant key over [start, end)."""
    placeholders = ",".join("?" * len(DISCRETIONARY_CATEGORIES))
    rows = connection.execute(
        f"SELECT merchant_normalized, description_raw, amount FROM transactions "
        f"WHERE primary_category IN ({placeholders}) AND direction = 'outflow' "
        f"AND occurred_on >= ? AND occurred_on < ?",
        (*DISCRETIONARY_CATEGORIES, start, end),
    ).fetchall()

    totals: dict[str, float] = {}
    names: dict[str, str] = {}
    for row in rows:
        key, name = _merchant_key_and_name(row["merchant_normalized"], row["description_raw"])
        totals[key] = totals.get(key, 0.0) + abs(float(row["amount"]))
        names.setdefault(key, name)
    return totals, names


# ---------------------------------------------------------------------------
# Compute
# ---------------------------------------------------------------------------


def compute_monthly_summary(
    database: DatabaseManager,
    wealth_bridge: WealthBridgeConfig,
    year: int,
    month: int,
) -> dict[str, Any]:
    """Compute the structured monthly summary dict. Pure — no file I/O."""
    month_start, next_month_start = _month_window(year, month)
    prior_year, prior_month = _add_months(year, month, -1)
    prior_start, prior_next = _month_window(prior_year, prior_month)
    year_start = f"{year:04d}-01-01"

    connection = database.connect(read_only=True)
    try:
        # --- Section 1: HYSA -------------------------------------------------
        balance_row = connection.execute(
            "SELECT computed_balance FROM v_computed_balance WHERE account_id = ?",
            (ALLY_HYSA_ACCOUNT_ID,),
        ).fetchone()
        hysa_balance = (
            round(float(balance_row["computed_balance"]), 2)
            if balance_row and balance_row["computed_balance"] is not None
            else 0.0
        )

        monthly_delta = _hysa_delta(connection, month_start, next_month_start)
        trailing_deltas = [monthly_delta]
        for offset in (-1, -2):
            ty, tm = _add_months(year, month, offset)
            trailing_deltas.append(_hysa_delta(connection, *_month_window(ty, tm)))
        trailing_3mo_avg = round(sum(trailing_deltas) / 3.0, 2)

        # --- Section 2: transactions view -----------------------------------
        inflows = _scalar(
            connection,
            "SELECT COALESCE(SUM(amount), 0) AS v FROM transactions "
            "WHERE direction = 'inflow' AND occurred_on >= ? AND occurred_on < ?",
            (month_start, next_month_start),
        )
        fixed_obligations = _scalar(
            connection,
            "SELECT COALESCE(SUM(ABS(amount)), 0) AS v FROM transactions "
            "WHERE primary_category = 'fixed_obligation' AND direction = 'outflow' "
            "AND occurred_on >= ? AND occurred_on < ?",
            (month_start, next_month_start),
        )
        discretionary_this = _discretionary_sum(connection, month_start, next_month_start)
        discretionary_prior = _discretionary_sum(connection, prior_start, prior_next)
        ytd_discretionary = _discretionary_sum(connection, year_start, next_month_start)

        # --- Section 2: theoretical view ------------------------------------
        payroll_inflows = _scalar(
            connection,
            "SELECT COALESCE(SUM(amount), 0) AS v FROM transactions "
            "WHERE direction = 'inflow' AND subcategory = 'payroll' "
            "AND occurred_on >= ? AND occurred_on < ?",
            (month_start, next_month_start),
        )
        investment_outflows = _scalar(
            connection,
            "SELECT COALESCE(SUM(ABS(amount)), 0) AS v FROM transactions "
            "WHERE primary_category = 'investment' AND direction = 'outflow' "
            "AND occurred_on >= ? AND occurred_on < ?",
            (month_start, next_month_start),
        )

        # --- Section 3: merchant movers -------------------------------------
        this_totals, this_names = _merchant_totals(connection, month_start, next_month_start)
        prior_totals, prior_names = _merchant_totals(connection, prior_start, prior_next)

        # --- Section 4: abnormal transactions (threshold applied downstream) -
        abnormal_rows = connection.execute(
            "SELECT merchant_normalized, description_raw, amount, occurred_on "
            "FROM transactions WHERE primary_category = 'abnormal' "
            "AND occurred_on >= ? AND occurred_on < ?",
            (month_start, next_month_start),
        ).fetchall()
    finally:
        connection.close()

    # --- Section 1 derived --------------------------------------------------
    target = wealth_bridge.hysa_target
    gap = round(target - hysa_balance, 2)
    projected_hit_date, months_remaining = project_hysa_hit_date(
        hysa_balance, target, trailing_3mo_avg, year, month
    )

    # --- Section 2 transactions derived -------------------------------------
    net_fcf = round(inflows - fixed_obligations - discretionary_this, 2)
    savings_rate_txn = (
        round(monthly_delta / inflows * 100.0, 2) if inflows > 0 else 0.0
    )

    # --- Section 2 theoretical derived --------------------------------------
    gross_monthly = wealth_bridge.gross_household_income_monthly
    tax_advantaged = round(wealth_bridge.tax_advantaged_monthly, 2)
    implied_raw = gross_monthly - tax_advantaged - payroll_inflows
    implied_withholding_floored = implied_raw < 0
    implied_withholding = round(max(implied_raw, 0.0), 2)
    theoretical_savings = round(tax_advantaged + monthly_delta + investment_outflows, 2)
    savings_rate_theo = (
        round(theoretical_savings / gross_monthly * 100.0, 2) if gross_monthly > 0 else 0.0
    )
    delta_vs_target = round(savings_rate_theo - wealth_bridge.savings_rate_target_pct, 2)

    # --- Section 3 derived --------------------------------------------------
    mom_delta = round(discretionary_this - discretionary_prior, 2)
    mom_delta_pct = (
        round(mom_delta / discretionary_prior * 100.0, 2)
        if discretionary_prior > 0
        else 0.0
    )
    ytd_annualized = round(ytd_discretionary * 12.0 / month, 2)
    display_names = {**prior_names, **this_names}
    top_movers = select_top_movers(this_totals, prior_totals, display_names)

    # --- Section 4: flags ---------------------------------------------------
    abnormal_txns = [
        {
            "merchant": (
                row["merchant_normalized"].strip()
                if row["merchant_normalized"] and row["merchant_normalized"].strip()
                else normalize_merchant(row["description_raw"]).title()
            ),
            "amount": round(abs(float(row["amount"])), 2),
            "date": row["occurred_on"],
        }
        for row in abnormal_rows
    ]
    auto_flags = detect_flags(
        FlagInputs(
            hysa_monthly_delta=monthly_delta,
            savings_rate_transactions_pct=savings_rate_txn,
            discretionary_this_month=discretionary_this,
            abnormal_txns=abnormal_txns,
            hysa_floor=wealth_bridge.hysa_floor_monthly_delta,
            savings_rate_floor=wealth_bridge.savings_rate_floor_pct,
            discretionary_ceiling=wealth_bridge.discretionary_ceiling_monthly,
            abnormal_threshold=wealth_bridge.abnormal_flag_threshold,
        )
    )
    if implied_withholding_floored:
        # Internal review flag — implied withholding would have been negative,
        # meaning gross config understates actual payroll deposits.
        auto_flags.append(
            {
                "code": "implied_withholding_negative",
                "message": (
                    f"Implied withholding computed negative (gross "
                    f"${gross_monthly:,.0f} − tax-advantaged ${tax_advantaged:,.0f} "
                    f"− payroll deposits ${payroll_inflows:,.0f}); floored to $0. "
                    f"Reconcile the gross config against actual payroll deposits."
                ),
                "severity": "warn",
            }
        )

    return {
        "period": {"year": year, "month": month, "label": f"{year:04d}-{month:02d}"},
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "hysa": {
            "balance": hysa_balance,
            "target": round(target, 2),
            "gap": gap,
            "monthly_delta": monthly_delta,
            "trailing_3mo_avg_delta": trailing_3mo_avg,
            "projected_hit_date": projected_hit_date,
            "months_remaining": months_remaining,
        },
        "fcf_transactions": {
            "inflows": inflows,
            "fixed_obligations": fixed_obligations,
            "discretionary": discretionary_this,
            "net_fcf": net_fcf,
            "savings_rate_pct": savings_rate_txn,
        },
        "fcf_theoretical": {
            "gross_monthly": round(gross_monthly, 2),
            "tax_advantaged_contributions": tax_advantaged,
            "implied_withholding": implied_withholding,
            "theoretical_savings": theoretical_savings,
            "savings_rate_pct": savings_rate_theo,
            "target_pct": round(wealth_bridge.savings_rate_target_pct, 2),
            "delta_vs_target_pct": delta_vs_target,
            "config_sourced": True,
            # True while the 401(k) placeholders are still 0 — the theoretical
            # rate then understates real savings by the unconfigured deferral.
            # Presentational guard only; the transactions-view flag is
            # unaffected (it does not depend on 401(k) config).
            "config_incomplete": wealth_bridge.has_placeholder_401k,
        },
        "spend": {
            "this_month_discretionary": discretionary_this,
            "prior_month_discretionary": discretionary_prior,
            "mom_delta": mom_delta,
            "mom_delta_pct": mom_delta_pct,
            "ytd_discretionary": ytd_discretionary,
            "ytd_annualized": ytd_annualized,
            "top_movers": top_movers,
        },
        "flags": {
            "auto": auto_flags,
            # compute() cannot know whether an existing file's manual block was
            # preserved — that is file I/O. generate() flips this to True after
            # the renderer lifts an existing manual block.
            "manual_block_preserved": False,
        },
        "methodology": {
            "discretionary_categories": list(DISCRETIONARY_CATEGORIES),
            "hysa_projection_basis": "3mo_trailing_net_delta",
            "month_boundary": "calendar_txn_date",
            "merchant_normalization": MERCHANT_NORMALIZATION_RULE,
        },
    }


def generate_monthly_summary(
    database: DatabaseManager,
    wealth_bridge: WealthBridgeConfig,
    year: int,
    month: int,
    output_root: Path,
) -> dict[str, Any]:
    """Compute the summary, render + write the markdown, return the dict.

    Returns the same dict as ``compute_monthly_summary`` plus ``markdown_path``
    (the written file location) and an updated ``flags.manual_block_preserved``
    reflecting whether an existing file's manual notes were carried forward.

    Invoked by the Cowork scheduled task on the 1st of each month — see the
    module docstring for the scheduling contract.
    """
    summary = compute_monthly_summary(database, wealth_bridge, year, month)
    markdown_path, manual_preserved = write_monthly_summary(
        summary, output_root, wealth_bridge.monthly_summary_output_dir
    )
    summary["flags"]["manual_block_preserved"] = manual_preserved
    summary["markdown_path"] = str(markdown_path)
    return summary
