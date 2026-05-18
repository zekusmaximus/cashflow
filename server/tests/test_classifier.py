"""Tests for the rule-based classifier module."""

from __future__ import annotations

from pathlib import Path

import pytest

from liquidity_gate_mcp.classifier import (
    apply_classifier,
    list_classification_rules,
    upsert_classification_rule,
)
from liquidity_gate_mcp.database import DatabaseManager
from liquidity_gate_mcp.models import (
    ApplyClassifierRequest,
    UpsertClassificationRuleRequest,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _insert_transaction(
    database: DatabaseManager,
    *,
    tx_id: str = "tx-001",
    account_id: str = "acct-chase-credit",
    description_raw: str = "AMAZON.COM",
    direction: str = "outflow",
    amount: float = -50.0,
    primary_category: str = "unclassified",
) -> None:
    with database.connect() as conn:
        conn.execute(
            """
            INSERT OR IGNORE INTO accounts (id, institution, account_name, account_type, owner)
            VALUES (?, 'Chase', 'Chase Credit', 'credit_card', 'jeff')
            """,
            (account_id,),
        )
        conn.execute(
            """
            INSERT INTO import_batches (id, source_name, parser_version, imported_at, raw_payload)
            VALUES ('batch-001', 'test.csv', 'test-v1', '2026-01-01T00:00:00Z', '{}')
            """,
        )
        conn.execute(
            """
            INSERT OR IGNORE INTO transactions
              (id, account_id, import_batch_id, source_record_key, source_document_name,
               occurred_on, description_raw, amount, direction, primary_category, metadata_json)
            VALUES (?, ?, 'batch-001', ?, 'test.csv', '2026-01-15', ?, ?, ?, ?, '{}')
            """,
            (tx_id, account_id, tx_id, description_raw, amount, direction, primary_category),
        )
        conn.commit()


def _fetch_transaction(database: DatabaseManager, tx_id: str) -> dict:
    with database.connect(read_only=True) as conn:
        row = conn.execute(
            "SELECT * FROM transactions WHERE id = ?", (tx_id,)
        ).fetchone()
    return dict(row) if row else {}


# ---------------------------------------------------------------------------
# list_classification_rules
# ---------------------------------------------------------------------------


def test_seed_rules_are_present(database: DatabaseManager) -> None:
    result = list_classification_rules(database)
    rule_ids = {r.id for r in result.rules}

    assert "rule-transfer-catchall" in rule_ids
    assert "rule-interest-paid" in rule_ids
    assert "rule-payroll-novartis" in rule_ids
    assert "rule-amazon" in rule_ids
    assert result.total == len(result.rules)


def test_seed_rules_ordered_by_priority(database: DatabaseManager) -> None:
    result = list_classification_rules(database)
    priorities = [r.priority for r in result.rules]
    assert priorities == sorted(priorities)


# ---------------------------------------------------------------------------
# upsert_classification_rule
# ---------------------------------------------------------------------------


def test_upsert_creates_new_rule(database: DatabaseManager) -> None:
    request = UpsertClassificationRuleRequest(
        pattern=r"(?i)spotify",
        primary_category="fixed_obligation",
        subcategory="subscriptions",
        merchant_normalized="Spotify",
        lifecycle="recurring",
        confidence="high",
        priority=20,
        notes="Spotify monthly subscription",
    )
    result = upsert_classification_rule(database, request)

    assert result.created is True
    assert result.rule.pattern == r"(?i)spotify"
    assert result.rule.primary_category == "fixed_obligation"
    assert result.rule.merchant_normalized == "Spotify"


def test_upsert_updates_existing_rule(database: DatabaseManager) -> None:
    request = UpsertClassificationRuleRequest(
        id="rule-amazon",
        pattern=r"(?i)\bamazon\b|\bamzn\b",
        primary_category="variable_lifestyle",
        subcategory="amazon",
        merchant_normalized="Amazon",
        confidence="high",
        priority=20,
        notes="Updated notes",
    )
    result = upsert_classification_rule(database, request)

    assert result.created is False
    assert result.rule.notes == "Updated notes"


def test_upsert_requires_at_least_one_output_field(database: DatabaseManager) -> None:
    with pytest.raises(Exception):
        UpsertClassificationRuleRequest(pattern=r"(?i)test")


def test_upsert_uses_stable_id_when_provided(database: DatabaseManager) -> None:
    request = UpsertClassificationRuleRequest(
        id="my-custom-rule",
        pattern=r"(?i)netflix",
        primary_category="fixed_obligation",
        subcategory="subscriptions",
        merchant_normalized="Netflix",
    )
    result = upsert_classification_rule(database, request)

    assert result.id == "my-custom-rule"
    assert result.created is True


# ---------------------------------------------------------------------------
# apply_classifier — happy path
# ---------------------------------------------------------------------------


def test_classifies_amazon_transaction(database: DatabaseManager) -> None:
    _insert_transaction(database, description_raw="AMAZON.COM*123ABC", direction="outflow")

    result = apply_classifier(database, ApplyClassifierRequest())

    assert result.transactions_matched == 1
    assert result.transactions_unclassified == 0
    tx = _fetch_transaction(database, "tx-001")
    assert tx["primary_category"] == "variable_lifestyle"
    assert tx["subcategory"] == "amazon"
    assert tx["merchant_normalized"] == "Amazon"


def test_classifies_transfer_direction_rows(database: DatabaseManager) -> None:
    _insert_transaction(
        database,
        tx_id="tx-transfer",
        description_raw="CHASE CREDIT CRD EPAY",
        direction="transfer",
    )

    result = apply_classifier(database, ApplyClassifierRequest())

    assert result.transactions_matched == 1
    tx = _fetch_transaction(database, "tx-transfer")
    assert tx["primary_category"] == "transfer"


def test_classifies_interest_paid(database: DatabaseManager) -> None:
    _insert_transaction(
        database,
        tx_id="tx-interest",
        account_id="acct-chase-credit",
        description_raw="Interest Paid",
        direction="inflow",
        amount=76.13,
    )

    apply_classifier(database, ApplyClassifierRequest())

    tx = _fetch_transaction(database, "tx-interest")
    assert tx["primary_category"] == "income"
    assert tx["subcategory"] == "interest"


def test_classifies_novartis_payroll(database: DatabaseManager) -> None:
    _insert_transaction(
        database,
        tx_id="tx-payroll",
        account_id="acct-chase-credit",
        description_raw="NOVARTIS SERVICE PAYROLL DIR DEP",
        direction="inflow",
        amount=3500.0,
    )

    apply_classifier(database, ApplyClassifierRequest())

    tx = _fetch_transaction(database, "tx-payroll")
    assert tx["primary_category"] == "income"
    assert tx["subcategory"] == "payroll"
    assert tx["household_role"] == "ashley"
    assert tx["merchant_normalized"] == "Novartis Payroll"


def test_whole_foods_classified_as_groceries(database: DatabaseManager) -> None:
    _insert_transaction(
        database,
        tx_id="tx-wf",
        account_id="acct-chase-credit",
        description_raw="WHOLEFDS MKT #123",
        direction="outflow",
        amount=-87.42,
    )

    apply_classifier(database, ApplyClassifierRequest())

    tx = _fetch_transaction(database, "tx-wf")
    assert tx["primary_category"] == "variable_lifestyle"
    assert tx["subcategory"] == "groceries"


# ---------------------------------------------------------------------------
# apply_classifier — dry_run
# ---------------------------------------------------------------------------


def test_dry_run_does_not_write(database: DatabaseManager) -> None:
    _insert_transaction(database, description_raw="AMAZON PRIME")

    result = apply_classifier(database, ApplyClassifierRequest(dry_run=True))

    assert result.dry_run is True
    assert result.transactions_matched == 1
    tx = _fetch_transaction(database, "tx-001")
    assert tx["primary_category"] == "unclassified"  # unchanged


# ---------------------------------------------------------------------------
# apply_classifier — reclassify_all
# ---------------------------------------------------------------------------


def test_reclassify_all_updates_previously_classified(database: DatabaseManager) -> None:
    _insert_transaction(
        database,
        description_raw="AMAZON MARKETPLACE",
        primary_category="unknown_old_value",
    )

    result = apply_classifier(database, ApplyClassifierRequest(reclassify_all=True))

    assert result.transactions_matched == 1
    tx = _fetch_transaction(database, "tx-001")
    assert tx["primary_category"] == "variable_lifestyle"


def test_reclassify_all_skips_manual_overrides(database: DatabaseManager) -> None:
    import json

    _insert_transaction(
        database,
        description_raw="AMAZON MARKETPLACE",
        primary_category="manual_category",
    )
    # Mark as manually overridden
    with database.connect() as conn:
        conn.execute(
            "UPDATE transactions SET metadata_json = ? WHERE id = 'tx-001'",
            (json.dumps({"manual_override_applied": True}),),
        )
        conn.commit()

    apply_classifier(database, ApplyClassifierRequest(reclassify_all=True))

    tx = _fetch_transaction(database, "tx-001")
    assert tx["primary_category"] == "manual_category"  # untouched


# ---------------------------------------------------------------------------
# apply_classifier — account_filter
# ---------------------------------------------------------------------------


def test_account_filter_only_classifies_matching_account(database: DatabaseManager) -> None:
    _insert_transaction(
        database,
        tx_id="tx-chase",
        account_id="acct-chase-credit",
        description_raw="AMAZON.COM",
        direction="outflow",
    )
    with database.connect() as conn:
        conn.execute(
            """
            INSERT OR IGNORE INTO accounts (id, institution, account_name, account_type, owner)
            VALUES ('acct-beacon', 'Beacon', 'Beacon Checking', 'checking', 'jeff')
            """
        )
        conn.execute(
            """
            INSERT OR IGNORE INTO transactions
              (id, account_id, import_batch_id, source_record_key, source_document_name,
               occurred_on, description_raw, amount, direction, primary_category, metadata_json)
            VALUES ('tx-beacon', 'acct-beacon', 'batch-001', 'tx-beacon', 'test.csv',
                    '2026-01-15', 'AMAZON.COM', -30.0, 'outflow', 'unclassified', '{}')
            """
        )
        conn.commit()

    apply_classifier(
        database, ApplyClassifierRequest(account_filter="acct-chase-credit")
    )

    chase_tx = _fetch_transaction(database, "tx-chase")
    beacon_tx = _fetch_transaction(database, "tx-beacon")
    assert chase_tx["primary_category"] == "variable_lifestyle"
    assert beacon_tx["primary_category"] == "unclassified"  # not touched


# ---------------------------------------------------------------------------
# apply_classifier — metadata provenance
# ---------------------------------------------------------------------------


def test_classifier_provenance_stored_in_metadata(database: DatabaseManager) -> None:
    import json

    _insert_transaction(database, description_raw="AMAZON PRIME")

    apply_classifier(database, ApplyClassifierRequest())

    tx = _fetch_transaction(database, "tx-001")
    meta = json.loads(tx["metadata_json"])
    assert meta["classifier_rule_id"] == "rule-amazon"
    assert meta["classifier_confidence"] == "high"


# ---------------------------------------------------------------------------
# apply_classifier — no rules case
# ---------------------------------------------------------------------------


def test_returns_zero_when_no_rules(database: DatabaseManager) -> None:
    _insert_transaction(database, description_raw="RANDOM MERCHANT")

    # Delete all rules
    with database.connect() as conn:
        conn.execute("DELETE FROM classification_rules")
        conn.commit()

    result = apply_classifier(database, ApplyClassifierRequest())

    assert result.rules_applied == 0
    assert result.transactions_scanned == 0
    assert result.transactions_matched == 0


# ---------------------------------------------------------------------------
# apply_classifier — already-classified rows are skipped by default
# ---------------------------------------------------------------------------


def test_already_classified_rows_skipped_by_default(database: DatabaseManager) -> None:
    _insert_transaction(
        database,
        description_raw="AMAZON.COM",
        primary_category="fixed_obligation",  # already set
    )

    result = apply_classifier(database, ApplyClassifierRequest())

    # reclassify_all=False means only 'unclassified' rows are touched
    assert result.transactions_scanned == 0
    assert result.transactions_matched == 0
