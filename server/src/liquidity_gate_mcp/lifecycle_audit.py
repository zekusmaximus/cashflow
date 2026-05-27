"""Surface transactions whose lifecycle / category looks suspicious.

Read-only tool. Does not reclassify; the analyst inspects the candidates
and decides whether to apply ``upsert_transaction_override``. Four
heuristics are run and union'd into one ranked list:

1. ``one_time`` rows whose merchant appears 3+ times in the trailing 6 months
   (likely actually recurring).
2. ``recurring`` rows whose merchant only appears once (likely actually
   one-time).
3. Discretionary outliers — ``variable_lifestyle`` outflows > $500 tagged
   ``one_time`` (may belong under ``abnormal``).
4. High-value pet_care rows (> $500) — the line item is anomalously large
   for a recurring service category and merits per-row review.

Results are deduplicated by transaction_id (first-reason-wins ranking) and
sorted by absolute amount desc.
"""

from __future__ import annotations

from datetime import date, timedelta
from typing import Any

from .database import DatabaseManager
from .models import (
    LifecycleAuditCandidate,
    LifecycleAuditRequest,
    LifecycleAuditResult,
)


# Reasons are ordered most-specific-first so the first match wins when a
# row qualifies under multiple heuristics.
_REASON_ORDER = (
    "pet_care_high_value",
    "discretionary_outlier_one_time",
    "one_time_but_recurring_merchant",
    "recurring_but_single_occurrence",
)


def list_lifecycle_audit_candidates(
    database: DatabaseManager,
    request: LifecycleAuditRequest,
) -> LifecycleAuditResult:
    today = date.today()
    trailing_start = (today - timedelta(days=183)).isoformat()  # ~6 months
    min_amt = float(request.min_amount)

    # The merchant-frequency join uses COALESCE(merchant_normalized,
    # description_raw) so unclassified rows still group sensibly.
    merchant_expr = "COALESCE(NULLIF(merchant_normalized, ''), description_raw)"
    filter_clauses: list[str] = []
    params: list[Any] = []
    if request.primary_category:
        filter_clauses.append("t.primary_category = ?")
        params.append(request.primary_category)
    extra_filter = (" AND " + " AND ".join(filter_clauses)) if filter_clauses else ""

    with database.connect(read_only=True) as conn:
        # Criterion 1: one_time, merchant 3+ times in the trailing 6mo window
        one_time_recurring_rows = conn.execute(
            f"""
            WITH merchant_counts AS (
              SELECT {merchant_expr} AS merchant_key, COUNT(*) AS occurrences
                FROM transactions
               WHERE occurred_on >= ?
               GROUP BY {merchant_expr}
            )
            SELECT t.id, t.occurred_on, t.merchant_normalized, t.description_raw,
                   t.amount, t.primary_category, t.subcategory, t.lifecycle
              FROM transactions t
              JOIN merchant_counts mc ON mc.merchant_key = {merchant_expr}
             WHERE t.lifecycle = 'one_time'
               AND mc.occurrences >= 3
               AND ABS(t.amount) >= ?
               {extra_filter}
             ORDER BY ABS(t.amount) DESC
            """,
            [trailing_start, min_amt, *params],
        ).fetchall()

        # Criterion 2: recurring, merchant has exactly one occurrence (any time)
        recurring_singletons = conn.execute(
            f"""
            WITH merchant_counts AS (
              SELECT {merchant_expr} AS merchant_key, COUNT(*) AS occurrences
                FROM transactions
               GROUP BY {merchant_expr}
            )
            SELECT t.id, t.occurred_on, t.merchant_normalized, t.description_raw,
                   t.amount, t.primary_category, t.subcategory, t.lifecycle
              FROM transactions t
              JOIN merchant_counts mc ON mc.merchant_key = {merchant_expr}
             WHERE t.lifecycle = 'recurring'
               AND mc.occurrences = 1
               AND ABS(t.amount) >= ?
               {extra_filter}
             ORDER BY ABS(t.amount) DESC
            """,
            [min_amt, *params],
        ).fetchall()

        # Criterion 3: variable_lifestyle outliers tagged one_time
        discretionary_outliers = conn.execute(
            f"""
            SELECT t.id, t.occurred_on, t.merchant_normalized, t.description_raw,
                   t.amount, t.primary_category, t.subcategory, t.lifecycle
              FROM transactions t
             WHERE t.primary_category = 'variable_lifestyle'
               AND t.lifecycle = 'one_time'
               AND ABS(t.amount) > 500
               {extra_filter}
             ORDER BY ABS(t.amount) DESC
            """,
            params,
        ).fetchall()

        # Criterion 4: pet_care > $500 — independent of lifecycle
        pet_care_high_value = conn.execute(
            f"""
            SELECT t.id, t.occurred_on, t.merchant_normalized, t.description_raw,
                   t.amount, t.primary_category, t.subcategory, t.lifecycle
              FROM transactions t
             WHERE t.subcategory = 'pet_care'
               AND ABS(t.amount) > 500
               {extra_filter}
             ORDER BY ABS(t.amount) DESC
            """,
            params,
        ).fetchall()

    by_reason: dict[str, list[dict]] = {
        "one_time_but_recurring_merchant": [dict(r) for r in one_time_recurring_rows],
        "recurring_but_single_occurrence": [dict(r) for r in recurring_singletons],
        "discretionary_outlier_one_time": [dict(r) for r in discretionary_outliers],
        "pet_care_high_value": [dict(r) for r in pet_care_high_value],
    }

    # Dedup by transaction_id, keeping the first-listed reason (most specific).
    seen: dict[str, LifecycleAuditCandidate] = {}
    for reason in _REASON_ORDER:
        for row in by_reason[reason]:
            tx_id = row["id"]
            if tx_id in seen:
                continue
            seen[tx_id] = LifecycleAuditCandidate(
                transaction_id=tx_id,
                occurred_on=row["occurred_on"],
                merchant_normalized=row["merchant_normalized"],
                description_raw=row["description_raw"],
                amount=float(row["amount"]),
                primary_category=row["primary_category"],
                subcategory=row["subcategory"],
                lifecycle=row["lifecycle"],
                suggested_review_reason=reason,
            )

    candidates = sorted(seen.values(), key=lambda c: abs(c.amount), reverse=True)
    limit = max(1, int(request.limit))
    return LifecycleAuditResult(
        candidates=candidates[:limit],
        total=len(candidates),
    )
