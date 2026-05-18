"""Rule-based transaction classifier.

Loads classification rules from the `classification_rules` table (ordered by
priority ASC then created_at ASC), compiles each pattern as a case-insensitive
regex, and applies the first matching rule to each qualifying transaction.

Qualifying transactions are those where `primary_category = 'unclassified'`
unless `reclassify_all=True` is requested, in which case all transactions are
candidates except those that already carry a manual override
(`metadata_json.manual_override_applied = true`).

For each matched transaction the rule writes back up to five fields:
`primary_category`, `subcategory`, `merchant_normalized`, `household_role`,
and `lifecycle`. It also records `classifier_rule_id` and
`classifier_confidence` into `metadata_json` so the classification can be
audited or undone via `apply_classifier(reclassify_all=True)` later.
"""

from __future__ import annotations

import json
import re
import uuid
from datetime import datetime, timezone
from typing import Any

from .database import DatabaseManager
from .models import (
    ApplyClassifierRequest,
    ApplyClassifierResult,
    ClassificationRule,
    ListClassificationRulesResult,
    UpsertClassificationRuleRequest,
    UpsertClassificationRuleResult,
)

_CLASSIFICATION_FIELDS = (
    "primary_category",
    "subcategory",
    "merchant_normalized",
    "household_role",
    "lifecycle",
)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def apply_classifier(
    database: DatabaseManager,
    request: ApplyClassifierRequest,
) -> ApplyClassifierResult:
    """Run the rule engine over transactions and write classification fields back.

    By default only touches rows where `primary_category = 'unclassified'`.
    Pass `reclassify_all=True` to re-run on all rows (manual overrides are
    still protected).
    """
    with database.connect() as conn:
        rules = _load_rules(conn)
        if not rules:
            return ApplyClassifierResult(
                transactions_scanned=0,
                transactions_matched=0,
                transactions_unclassified=0,
                dry_run=request.dry_run,
                rules_applied=0,
            )

        compiled = [(rule, re.compile(rule.pattern, re.IGNORECASE)) for rule in rules]

        # Build fetch query
        conditions = []
        params: list[Any] = []
        if not request.reclassify_all:
            conditions.append("primary_category = 'unclassified'")
        if request.account_filter:
            conditions.append("account_id = ?")
            params.append(request.account_filter)

        where_clause = ("WHERE " + " AND ".join(conditions)) if conditions else ""
        rows = conn.execute(
            f"SELECT id, account_id, description_raw, direction, primary_category, "
            f"subcategory, merchant_normalized, household_role, lifecycle, metadata_json "
            f"FROM transactions {where_clause}",
            params,
        ).fetchall()

        matched = 0
        updates: list[dict[str, Any]] = []

        for row in rows:
            row_dict = dict(row)

            # Protect manual overrides when reclassify_all is active
            if request.reclassify_all:
                try:
                    meta = json.loads(row_dict.get("metadata_json") or "{}")
                    if meta.get("manual_override_applied"):
                        continue
                except (json.JSONDecodeError, TypeError):
                    pass

            update = _classify_row(row_dict, compiled)
            if update:
                updates.append({"id": row_dict["id"], **update})
                matched += 1

        if not request.dry_run and updates:
            for update in updates:
                set_clauses: list[str] = []
                set_params: list[Any] = []
                for field in _CLASSIFICATION_FIELDS:
                    if field in update:
                        set_clauses.append(f"{field} = ?")
                        set_params.append(update[field])
                if "metadata_json" in update:
                    set_clauses.append("metadata_json = ?")
                    set_params.append(update["metadata_json"])
                if set_clauses:
                    set_params.append(update["id"])
                    conn.execute(
                        f"UPDATE transactions SET {', '.join(set_clauses)} WHERE id = ?",
                        set_params,
                    )
            conn.commit()

        return ApplyClassifierResult(
            transactions_scanned=len(rows),
            transactions_matched=matched,
            transactions_unclassified=len(rows) - matched,
            dry_run=request.dry_run,
            rules_applied=len(rules),
        )


def upsert_classification_rule(
    database: DatabaseManager,
    request: UpsertClassificationRuleRequest,
) -> UpsertClassificationRuleResult:
    """Create or replace a classification rule by id.

    If `request.id` is None a new UUID is generated. If the id already exists
    all mutable fields are updated; `created_at` is preserved.
    """
    now = datetime.now(timezone.utc).isoformat()
    rule_id = request.id or str(uuid.uuid4())

    with database.connect() as conn:
        existing = conn.execute(
            "SELECT id FROM classification_rules WHERE id = ?", (rule_id,)
        ).fetchone()
        created = existing is None

        conn.execute(
            """
            INSERT INTO classification_rules (
              id, pattern, account_filter, direction_filter,
              primary_category, subcategory, merchant_normalized,
              household_role, lifecycle, confidence, priority, notes,
              created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET
              pattern             = excluded.pattern,
              account_filter      = excluded.account_filter,
              direction_filter    = excluded.direction_filter,
              primary_category    = excluded.primary_category,
              subcategory         = excluded.subcategory,
              merchant_normalized = excluded.merchant_normalized,
              household_role      = excluded.household_role,
              lifecycle           = excluded.lifecycle,
              confidence          = excluded.confidence,
              priority            = excluded.priority,
              notes               = excluded.notes,
              updated_at          = excluded.updated_at
            """,
            (
                rule_id,
                request.pattern,
                request.account_filter,
                request.direction_filter,
                request.primary_category,
                request.subcategory,
                request.merchant_normalized,
                request.household_role,
                request.lifecycle,
                request.confidence,
                request.priority,
                request.notes,
                now,   # created_at — only lands on INSERT; ON CONFLICT skips it
                now,   # updated_at — always refreshed
            ),
        )
        conn.commit()

        rule_row = conn.execute(
            "SELECT id, pattern, account_filter, direction_filter, primary_category, "
            "subcategory, merchant_normalized, household_role, lifecycle, confidence, "
            "priority, notes, created_at, updated_at "
            "FROM classification_rules WHERE id = ?",
            (rule_id,),
        ).fetchone()

    return UpsertClassificationRuleResult(
        id=rule_id,
        created=created,
        rule=ClassificationRule(**dict(rule_row)),
    )


def list_classification_rules(database: DatabaseManager) -> ListClassificationRulesResult:
    """Return all classification rules ordered by priority then created_at."""
    with database.connect(read_only=True) as conn:
        rows = conn.execute(
            "SELECT id, pattern, account_filter, direction_filter, primary_category, "
            "subcategory, merchant_normalized, household_role, lifecycle, confidence, "
            "priority, notes, created_at, updated_at "
            "FROM classification_rules ORDER BY priority ASC, created_at ASC"
        ).fetchall()
    rules = [ClassificationRule(**dict(row)) for row in rows]
    return ListClassificationRulesResult(rules=rules, total=len(rules))


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _load_rules(conn: Any) -> list[ClassificationRule]:
    rows = conn.execute(
        "SELECT id, pattern, account_filter, direction_filter, primary_category, "
        "subcategory, merchant_normalized, household_role, lifecycle, confidence, "
        "priority, notes, created_at, updated_at "
        "FROM classification_rules ORDER BY priority ASC, created_at ASC"
    ).fetchall()
    return [ClassificationRule(**dict(row)) for row in rows]


def _classify_row(
    row: dict[str, Any],
    compiled: list[tuple[ClassificationRule, re.Pattern[str]]],
) -> dict[str, Any] | None:
    """Return a dict of fields to update, or None if no rule matched."""
    description = row.get("description_raw", "")
    direction = row.get("direction", "")
    account_id = row.get("account_id", "")

    for rule, pattern in compiled:
        if rule.account_filter and rule.account_filter != account_id:
            continue
        if rule.direction_filter and rule.direction_filter != direction:
            continue
        if not pattern.search(description):
            continue

        # First matching rule wins — build the update payload
        update: dict[str, Any] = {}
        if rule.primary_category is not None:
            update["primary_category"] = rule.primary_category
        if rule.subcategory is not None:
            update["subcategory"] = rule.subcategory
        if rule.merchant_normalized is not None:
            update["merchant_normalized"] = rule.merchant_normalized
        if rule.household_role is not None:
            update["household_role"] = rule.household_role
        if rule.lifecycle is not None:
            update["lifecycle"] = rule.lifecycle

        if update:
            # Annotate metadata_json with classifier provenance
            try:
                meta = json.loads(row.get("metadata_json") or "{}")
            except (json.JSONDecodeError, TypeError):
                meta = {}
            meta["classifier_rule_id"] = rule.id
            meta["classifier_confidence"] = rule.confidence
            update["metadata_json"] = json.dumps(meta)
            return update

    return None
