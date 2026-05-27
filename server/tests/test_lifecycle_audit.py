"""Tests for the lifecycle-audit MCP tool."""

from __future__ import annotations

from liquidity_gate_mcp.database import DatabaseManager
from liquidity_gate_mcp.lifecycle_audit import list_lifecycle_audit_candidates
from liquidity_gate_mcp.models import LifecycleAuditRequest


def _seed_account(database: DatabaseManager) -> None:
    with database.connect() as conn:
        conn.execute(
            "INSERT OR IGNORE INTO accounts (id, institution, account_name, account_type, owner)"
            " VALUES ('acct-test', 'Test', 'Test Checking', 'checking', 'joint')"
        )
        conn.execute(
            "INSERT OR IGNORE INTO import_batches"
            " (id, source_name, parser_version, imported_at, raw_payload)"
            " VALUES ('batch-test', 'test.csv', 'test-v1', '2026-01-01T00:00:00Z', '{}')"
        )
        conn.commit()


def _insert(
    database: DatabaseManager,
    *,
    tx_id: str,
    occurred_on: str,
    merchant: str | None,
    amount: float,
    primary_category: str,
    subcategory: str | None,
    lifecycle: str,
    description_raw: str | None = None,
) -> None:
    with database.connect() as conn:
        conn.execute(
            """
            INSERT INTO transactions
              (id, account_id, import_batch_id, source_record_key, source_document_name,
               occurred_on, description_raw, merchant_normalized, amount, direction,
               primary_category, subcategory, lifecycle, metadata_json)
            VALUES (?, 'acct-test', 'batch-test', ?, 'test.csv', ?, ?, ?, ?, 'outflow',
                    ?, ?, ?, '{}')
            """,
            (
                tx_id,
                tx_id,
                occurred_on,
                description_raw or merchant or tx_id,
                merchant,
                amount,
                primary_category,
                subcategory,
                lifecycle,
            ),
        )
        conn.commit()


def test_one_time_with_recurring_merchant_surfaces(database: DatabaseManager) -> None:
    _seed_account(database)
    # 3 visits over the trailing-6mo window, all tagged one_time.
    for i, day in enumerate(("2026-03-05", "2026-04-05", "2026-05-05"), start=1):
        _insert(
            database,
            tx_id=f"tx-coffee-{i}",
            occurred_on=day,
            merchant="Brewery Coffee",
            amount=-150.00,
            primary_category="variable_lifestyle",
            subcategory="dining_out",
            lifecycle="one_time",
        )
    result = list_lifecycle_audit_candidates(database, LifecycleAuditRequest())
    assert result.total >= 3
    coffee_candidates = [c for c in result.candidates if c.merchant_normalized == "Brewery Coffee"]
    assert len(coffee_candidates) == 3
    assert all(
        c.suggested_review_reason == "one_time_but_recurring_merchant"
        for c in coffee_candidates
    )


def test_recurring_singleton_surfaces(database: DatabaseManager) -> None:
    _seed_account(database)
    _insert(
        database,
        tx_id="tx-oddball",
        occurred_on="2026-04-15",
        merchant="One-Off Vendor",
        amount=-450.00,
        primary_category="fixed_obligation",
        subcategory="other_fixed",
        lifecycle="recurring",
    )
    result = list_lifecycle_audit_candidates(database, LifecycleAuditRequest())
    reasons = {c.suggested_review_reason for c in result.candidates}
    assert "recurring_but_single_occurrence" in reasons


def test_pet_care_high_value_surfaces(database: DatabaseManager) -> None:
    _seed_account(database)
    _insert(
        database,
        tx_id="tx-pet-big",
        occurred_on="2026-04-01",
        merchant="Specialty Vet",
        amount=-1800.00,
        primary_category="medical",
        subcategory="pet_care",
        lifecycle="one_time",
    )
    result = list_lifecycle_audit_candidates(database, LifecycleAuditRequest())
    candidates = [c for c in result.candidates if c.transaction_id == "tx-pet-big"]
    assert candidates and candidates[0].suggested_review_reason == "pet_care_high_value"


def test_discretionary_outlier_surfaces(database: DatabaseManager) -> None:
    _seed_account(database)
    _insert(
        database,
        tx_id="tx-bigsplurge",
        occurred_on="2026-04-22",
        merchant="Spa Day",
        amount=-900.00,
        primary_category="variable_lifestyle",
        subcategory="personal_care",
        lifecycle="one_time",
    )
    result = list_lifecycle_audit_candidates(database, LifecycleAuditRequest())
    matching = [c for c in result.candidates if c.transaction_id == "tx-bigsplurge"]
    assert matching
    assert matching[0].suggested_review_reason == "discretionary_outlier_one_time"


def test_dedup_prefers_more_specific_reason(database: DatabaseManager) -> None:
    """A single row matching multiple criteria should only appear once,
    tagged with the most-specific reason."""
    _seed_account(database)
    # Pet_care + high value qualifies under pet_care_high_value (specific).
    # Without dedup it would also count under recurring_but_single_occurrence
    # (lifecycle=recurring, single merchant occurrence).
    _insert(
        database,
        tx_id="tx-pet-overlap",
        occurred_on="2026-04-01",
        merchant="Overlap Vet",
        amount=-700.00,
        primary_category="medical",
        subcategory="pet_care",
        lifecycle="recurring",
    )
    result = list_lifecycle_audit_candidates(database, LifecycleAuditRequest())
    rows = [c for c in result.candidates if c.transaction_id == "tx-pet-overlap"]
    assert len(rows) == 1
    assert rows[0].suggested_review_reason == "pet_care_high_value"


def test_results_ranked_by_absolute_amount(database: DatabaseManager) -> None:
    _seed_account(database)
    _insert(
        database,
        tx_id="tx-small-pet",
        occurred_on="2026-04-01",
        merchant="Small Vet",
        amount=-600.00,
        primary_category="medical",
        subcategory="pet_care",
        lifecycle="one_time",
    )
    _insert(
        database,
        tx_id="tx-large-pet",
        occurred_on="2026-04-02",
        merchant="Large Vet",
        amount=-1500.00,
        primary_category="medical",
        subcategory="pet_care",
        lifecycle="one_time",
    )
    result = list_lifecycle_audit_candidates(database, LifecycleAuditRequest())
    ids = [c.transaction_id for c in result.candidates if "pet" in c.transaction_id]
    assert ids[0] == "tx-large-pet"  # ranked first


def test_min_amount_filters_small_rows(database: DatabaseManager) -> None:
    _seed_account(database)
    for i in range(3):
        _insert(
            database,
            tx_id=f"tx-cheap-{i}",
            occurred_on=f"2026-04-0{i+1}",
            merchant="Cheap Coffee",
            amount=-5.00,
            primary_category="variable_lifestyle",
            subcategory="dining_out",
            lifecycle="one_time",
        )
    result = list_lifecycle_audit_candidates(
        database, LifecycleAuditRequest(min_amount=100.0)
    )
    assert all(abs(c.amount) >= 100.0 for c in result.candidates)
