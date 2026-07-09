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
import logging
import math
import re
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .balances import MortgageConfig, WealthBridgeConfig
from .computed_balance import ALLY_HYSA_ACCOUNT_ID
from .database import DatabaseManager
from .models import AnnualReferenceEntry
from .monthly_summary_flags import FlagInputs, detect_flags
from .monthly_summary_renderer import write_monthly_summary

# Discretionary scope is locked: spend in exactly these primary categories.
# Selection is category-driven, not direction-gated — a row counts as
# discretionary spend by its primary_category regardless of whether the parser
# tagged it 'outflow' or 'transfer' (direction stays authoritative only for
# reconciliation / pair_transfers, never here). Only inflow rows are held out,
# preserving today's refund handling (refunds are not netted into the bucket).
# The transfer/tax/fixed_obligation/investment/rental/business_expense/income
# *categories* are still excluded by definition. Do not let this drift.
DISCRETIONARY_CATEGORIES: tuple[str, ...] = ("variable_lifestyle", "medical", "abnormal")

# Consumption offsets (reimbursements / refunds) — the reporting layer nets
# these out of gross spend to expose true `net_consumption` WITHOUT touching any
# transaction row or the frozen `net_fcf` (DL-2026-06-15). Two sources:
#
#   * income-side offsets: `income` inflows whose subcategory is a reimbursement
#     or refund. These already sit in `inflows` (and therefore in `net_fcf` as
#     income), so `earned_income` splits them back out of the income line.
#   * spend-side offsets: inflow rows already correctly placed IN a spend
#     category (in-category refunds). The existing gross buckets hold inflows
#     out, so these never reached `net_fcf` at all.
#
# EXCLUDED from offsets by definition: payroll/paycheck/bonus/stock_sale/
# interest/check_deposit (real income) and tax_refund (belongs to a tax view,
# not a consumption offset — do not net return-of-overpaid-tax here).
INCOME_OFFSET_SUBCATEGORIES: tuple[str, ...] = (
    "insurance_reimbursement",
    "reimbursement",
    "refund",
)
SPEND_OFFSET_CATEGORIES: tuple[str, ...] = (
    "variable_lifestyle",
    "medical",
    "abnormal",
    "fixed_obligation",
)

# Scheduled-mortgage identification tolerance. The scheduled payment amount is
# matched within ±$50 of `scheduled_payment` so a future escrow re-analysis that
# nudges the payment does not silently drop the row (audit open question:
# "escrow drift"). Rows outside the tolerance are treated as non-scheduled
# IonBank debt paydown (the paid-off HELOC + irregular extra-principal
# prepayments — all historical Jan–Jun 2026).
_MORTGAGE_MATCH_TOLERANCE = 50.0

# Used only when the `[mortgage.ion]` config block is absent: still identify and
# carve out non-scheduled IonBank debt-paydown rows, but skip amortization (the
# scheduled principal portion is 0 without an anchor balance + rate).
_FALLBACK_MORTGAGE_MERCHANT = "IonBank Mortgage"
_FALLBACK_SCHEDULED_PAYMENT = 4044.66

logger = logging.getLogger("liquidity_gate_mcp")

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


def _income_inflows(connection: sqlite3.Connection, start: str, end: str) -> float:
    """Income inflows over [start, end) — the summary's top line.

    The ``primary_category = 'income'`` guard is deliberate: it pins this to
    genuine income and keeps non-income inflows (HYSA drawdowns, which are
    ``transfer`` anyway, etc.) out of the top line. Mirrored by the
    ``monthly_cashflow_summary`` view's ``inflow`` column — see
    ``server/sql/schema.sql``; the two must stay in sync.
    """
    return _scalar(
        connection,
        "SELECT COALESCE(SUM(amount), 0) AS v FROM transactions "
        "WHERE primary_category = 'income' AND direction = 'inflow' "
        "AND occurred_on >= ? AND occurred_on < ?",
        (start, end),
    )


def _fixed_obligation_sum(connection: sqlite3.Connection, start: str, end: str) -> float:
    """Fixed-obligation spend over [start, end) — category-driven.

    A ``direction='transfer'`` row whose ``primary_category`` is
    ``fixed_obligation`` (e.g. the IonBank mortgage, DL-2026-06-10-A) is real
    spending and must count. Only inflows are held out (refunds net as before).
    Mirrored by the ``fixed_obligation`` term of the ``monthly_cashflow_summary``
    view's ``outflow`` column — see ``server/sql/schema.sql``.
    """
    return _scalar(
        connection,
        "SELECT COALESCE(SUM(ABS(amount)), 0) AS v FROM transactions "
        "WHERE primary_category = 'fixed_obligation' AND direction != 'inflow' "
        "AND occurred_on >= ? AND occurred_on < ?",
        (start, end),
    )


def _discretionary_sum(connection: sqlite3.Connection, start: str, end: str) -> float:
    # Category-driven: include outflow and transfer rows in the discretionary
    # categories; hold out inflows so refunds net exactly as they did before.
    # Mirrored by the discretionary terms of the monthly_cashflow_summary view's
    # outflow column — see server/sql/schema.sql; keep the two in sync.
    placeholders = ",".join("?" * len(DISCRETIONARY_CATEGORIES))
    return _scalar(
        connection,
        f"SELECT COALESCE(SUM(ABS(amount)), 0) AS v FROM transactions "
        f"WHERE primary_category IN ({placeholders}) AND direction != 'inflow' "
        f"AND occurred_on >= ? AND occurred_on < ?",
        (*DISCRETIONARY_CATEGORIES, start, end),
    )


def _income_offset_sum(connection: sqlite3.Connection, start: str, end: str) -> float:
    """Income-side consumption offsets over [start, end).

    ``income`` inflows whose subcategory is a reimbursement/refund
    (``INCOME_OFFSET_SUBCATEGORIES``). These are a subset of ``_income_inflows``;
    ``_earned_income`` subtracts them to split the income line, and
    ``_reimbursements`` adds them to the total offset sum. The underlying rows
    are never modified — this is a reporting split only.
    """
    placeholders = ",".join("?" * len(INCOME_OFFSET_SUBCATEGORIES))
    return _scalar(
        connection,
        f"SELECT COALESCE(SUM(ABS(amount)), 0) AS v FROM transactions "
        f"WHERE primary_category = 'income' AND direction = 'inflow' "
        f"AND subcategory IN ({placeholders}) "
        f"AND occurred_on >= ? AND occurred_on < ?",
        (*INCOME_OFFSET_SUBCATEGORIES, start, end),
    )


def _spend_offset_sum(connection: sqlite3.Connection, start: str, end: str) -> float:
    """Spend-side consumption offsets over [start, end).

    Inflow rows already correctly placed IN a spend category
    (``SPEND_OFFSET_CATEGORIES``) — in-category refunds. The existing gross
    buckets hold inflows out, so these never entered ``net_fcf``; the reporting
    layer nets them against consumption via ``_reimbursements``.
    """
    placeholders = ",".join("?" * len(SPEND_OFFSET_CATEGORIES))
    return _scalar(
        connection,
        f"SELECT COALESCE(SUM(ABS(amount)), 0) AS v FROM transactions "
        f"WHERE primary_category IN ({placeholders}) AND direction = 'inflow' "
        f"AND occurred_on >= ? AND occurred_on < ?",
        (*SPEND_OFFSET_CATEGORIES, start, end),
    )


def _earned_income(connection: sqlite3.Connection, start: str, end: str) -> float:
    """``inflows`` (income-only) minus the income-side reimbursement subcategories.

    A reporting split of the existing income line — the true earned income once
    reimbursements/refunds booked as income are removed. The ``income`` rows are
    NOT modified; ``inflows`` and ``net_fcf`` are unchanged.
    """
    return round(
        _income_inflows(connection, start, end) - _income_offset_sum(connection, start, end),
        2,
    )


def _reimbursements(
    connection: sqlite3.Connection, start: str, end: str
) -> tuple[float, float, float]:
    """Total consumption offsets over [start, end) as ``(total, income, spend)``.

    ``total`` = income-side offsets (reimbursement/refund subcats under
    ``income``) + spend-side offsets (in-category inflow refunds). The split is
    returned so the identity cross-check can separate the reimbursements that
    were already inside ``net_fcf`` (income-side) from those that never were
    (spend-side).
    """
    income_side = _income_offset_sum(connection, start, end)
    spend_side = _spend_offset_sum(connection, start, end)
    return round(income_side + spend_side, 2), round(income_side, 2), round(spend_side, 2)


def _mortgage_principal(
    connection: sqlite3.Connection,
    year: int,
    month: int,
    start: str,
    end: str,
    mortgage: MortgageConfig | None,
) -> tuple[float, float, float]:
    """Debt-paydown carve-out for [start, end) as ``(total, scheduled, nonscheduled)``.

    ``scheduled`` is the amortized principal portion of the recurring IonBank
    mortgage payment, counted only when a scheduled row actually posted this
    month (a row matching ``merchant_match`` within ±$50 of ``scheduled_payment``).
    Escrow + interest of that payment stay in ``fixed_obligations`` — only
    principal is carved out. ``nonscheduled`` is the full amount of every other
    IonBank ``fixed_obligation`` row (the paid-off HELOC + extra-principal
    prepayments), treated as 100% debt paydown per the audit default.

    Without a config block the scheduled portion is 0 (no anchor to amortize
    from) but non-scheduled rows are still carved out using fallback
    identification constants.
    """
    merchant = mortgage.merchant_match if mortgage else _FALLBACK_MORTGAGE_MERCHANT
    scheduled_amount = mortgage.scheduled_payment if mortgage else _FALLBACK_SCHEDULED_PAYMENT

    rows = connection.execute(
        "SELECT amount FROM transactions "
        "WHERE merchant_normalized = ? AND primary_category = 'fixed_obligation' "
        "AND direction != 'inflow' AND occurred_on >= ? AND occurred_on < ?",
        (merchant, start, end),
    ).fetchall()

    scheduled_count = 0
    nonscheduled = 0.0
    for row in rows:
        amount = abs(float(row["amount"]))
        if abs(amount - scheduled_amount) <= _MORTGAGE_MATCH_TOLERANCE:
            scheduled_count += 1
        else:
            nonscheduled += amount

    scheduled_principal = 0.0
    if scheduled_count > 0 and mortgage is not None:
        scheduled_principal = round(
            mortgage.amortized_principal(year, month) * scheduled_count, 2
        )
    elif rows and scheduled_count == 0 and mortgage is not None:
        # IonBank activity this month but nothing matched the scheduled payment —
        # escrow drift may have moved the amount outside tolerance. Surface it so
        # the carve-out can be reconciled rather than silently under-counted.
        logger.warning(
            "[monthly-summary] %04d-%02d: IonBank fixed_obligation rows present "
            "but none matched the scheduled payment $%.2f (±$%.0f). Scheduled "
            "mortgage principal counted as $0 for the month; check for escrow "
            "drift in the payment amount.",
            year,
            month,
            scheduled_amount,
            _MORTGAGE_MATCH_TOLERANCE,
        )

    nonscheduled = round(nonscheduled, 2)
    return round(scheduled_principal + nonscheduled, 2), scheduled_principal, nonscheduled


def _merchant_totals(
    connection: sqlite3.Connection, start: str, end: str
) -> tuple[dict[str, float], dict[str, str]]:
    """Discretionary spend totals per merchant key over [start, end).

    Mirrors ``_discretionary_sum``'s category-driven selection so the per-merchant
    totals reconcile to the bucket: outflow + transfer rows in scope, inflows held
    out.
    """
    placeholders = ",".join("?" * len(DISCRETIONARY_CATEGORIES))
    rows = connection.execute(
        f"SELECT merchant_normalized, description_raw, amount FROM transactions "
        f"WHERE primary_category IN ({placeholders}) AND direction != 'inflow' "
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


def _annual_reference_derived(entry: AnnualReferenceEntry) -> dict[str, Any]:
    """Compute the ratios surfaced in the monthly summary's
    ``annual_reference`` section. Only called when ``entry.populated`` is
    True, so we can divide safely as long as gross is positive.
    """
    gross_total = entry.gross_w2_jeff + entry.gross_w2_ashley
    withholding_total = (
        entry.federal_withholding + entry.state_withholding + entry.fica_employee
    )
    pretax_deferrals = (
        entry.contribution_401k_jeff
        + entry.contribution_401k_ashley
        + entry.hsa_contribution
    )
    net_take_home = gross_total - withholding_total - pretax_deferrals
    effective_tax_rate_pct = (
        round(withholding_total / gross_total * 100.0, 2) if gross_total > 0 else None
    )
    gross_to_net_ratio = (
        round(net_take_home / gross_total, 4) if gross_total > 0 else None
    )
    pretax_savings_rate_pct = (
        round(pretax_deferrals / gross_total * 100.0, 2)
        if gross_total > 0
        else None
    )
    return {
        "year": entry.year,
        "gross_total": round(gross_total, 2),
        "withholding_total": round(withholding_total, 2),
        "pretax_deferrals_total": round(pretax_deferrals, 2),
        "net_take_home_estimate": round(net_take_home, 2),
        "effective_tax_rate_pct": effective_tax_rate_pct,
        "gross_to_net_ratio": gross_to_net_ratio,
        "pretax_savings_rate_pct": pretax_savings_rate_pct,
        "notes": entry.notes,
    }


def compute_monthly_summary(
    database: DatabaseManager,
    wealth_bridge: WealthBridgeConfig,
    year: int,
    month: int,
    annual_reference: AnnualReferenceEntry | None = None,
    mortgage: MortgageConfig | None = None,
) -> dict[str, Any]:
    """Compute the structured monthly summary dict. Pure — no file I/O.

    When ``annual_reference`` is supplied and ``.populated`` is True (i.e.
    the user has filled in at least one nonzero W-2/401(k)/HSA value), an
    additional ``annual_reference`` section is added to the output with
    effective tax rate, gross-to-net ratio, and pre-tax savings rate.
    Absent reference data simply omits the section rather than reporting
    zeros — those would be misleading.
    """
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
        # Income, fixed-obligation and discretionary selection all live in named
        # helpers so this path, the annual rollup, and the monthly_cashflow_summary
        # view reference one documented definition each.
        inflows = _income_inflows(connection, month_start, next_month_start)
        fixed_obligations = _fixed_obligation_sum(connection, month_start, next_month_start)
        discretionary_this = _discretionary_sum(connection, month_start, next_month_start)
        discretionary_prior = _discretionary_sum(connection, prior_start, prior_next)
        ytd_discretionary = _discretionary_sum(connection, year_start, next_month_start)

        # --- Section 2: net-consumption view (additive, DL-2026-06-15 safe) ---
        # Derived from existing rows by subcategory + a config-driven mortgage
        # amortization. None of inflows / fixed_obligations / discretionary /
        # net_fcf move; these are new reporting lines layered on top.
        earned_income = _earned_income(connection, month_start, next_month_start)
        reimb_total, reimb_income, reimb_spend = _reimbursements(
            connection, month_start, next_month_start
        )
        mort_total, mort_scheduled, mort_nonscheduled = _mortgage_principal(
            connection, year, month, month_start, next_month_start, mortgage
        )

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

    # --- Section 2 net-consumption derived ----------------------------------
    # True consumption = gross spend, less reimbursements, less the principal
    # portion of debt payments (balance-sheet-neutral saving, not spending).
    net_consumption = round(
        fixed_obligations + discretionary_this - reimb_total - mort_total, 2
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

    summary_dict: dict[str, Any] = {
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
        # New, strictly additive. `net_consumption` is the true-consumption
        # companion to `net_fcf`: gross spend net of reimbursements and the
        # carved-out mortgage principal. `earned_income` is the income line with
        # reimbursements split back out. See the identity note below.
        #
        # Identity (holds to the cent every month):
        #   earned_income - net_consumption
        #     == net_fcf + mortgage_principal + reimbursements_spend_side
        # i.e. income minus true consumption = cash saved (net_fcf) + equity
        # built via principal + in-category refunds that never touched the
        # frozen income line. The naive `net_fcf == earned_income -
        # net_consumption` only holds when the latter two terms are zero; the
        # reimbursements booked as income ARE just moved between the two sides of
        # the subtraction and cancel, which is why they do not appear here.
        "consumption": {
            "earned_income": earned_income,
            "reimbursements": reimb_total,
            "reimbursements_income_side": reimb_income,
            "reimbursements_spend_side": reimb_spend,
            "mortgage_principal": mort_total,
            "mortgage_principal_scheduled": mort_scheduled,
            "debt_paydown_nonscheduled": mort_nonscheduled,
            "net_consumption": net_consumption,
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
    if annual_reference is not None and annual_reference.populated:
        summary_dict["annual_reference"] = _annual_reference_derived(annual_reference)
    return summary_dict


def generate_monthly_summary(
    database: DatabaseManager,
    wealth_bridge: WealthBridgeConfig,
    year: int,
    month: int,
    output_root: Path,
    annual_reference: AnnualReferenceEntry | None = None,
    mortgage: MortgageConfig | None = None,
) -> dict[str, Any]:
    """Compute the summary, render + write the markdown, return the dict.

    Returns the same dict as ``compute_monthly_summary`` plus ``markdown_path``
    (the written file location) and an updated ``flags.manual_block_preserved``
    reflecting whether an existing file's manual notes were carried forward.

    Invoked by the Cowork scheduled task on the 1st of each month — see the
    module docstring for the scheduling contract.
    """
    summary = compute_monthly_summary(
        database,
        wealth_bridge,
        year,
        month,
        annual_reference=annual_reference,
        mortgage=mortgage,
    )
    markdown_path, manual_preserved = write_monthly_summary(
        summary, output_root, wealth_bridge.monthly_summary_output_dir
    )
    summary["flags"]["manual_block_preserved"] = manual_preserved
    summary["markdown_path"] = str(markdown_path)
    return summary
