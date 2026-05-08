from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest
from pydantic import ValidationError

from liquidity_gate_mcp.database import DatabaseManager
from liquidity_gate_mcp.models import (
    AccountReference,
    ParsedTransaction,
    ReconcileTransactionsRequest,
)


def make_request(*, dry_run: bool = False) -> ReconcileTransactionsRequest:
    account = AccountReference(
        source_key="acct-1",
        institution="Chase",
        account_name="Checking",
        account_type="checking",
    )
    transactions = [
        ParsedTransaction(
            source_record_key="row-1",
            source_document_name="chase_jan_2026.csv",
            occurred_on=date(2026, 1, 5),
            description_raw="Grocery",
            amount=Decimal("42.00"),
            direction="outflow",
            primary_category="groceries",
            account=account,
        ),
        ParsedTransaction(
            source_record_key="row-2",
            source_document_name="chase_jan_2026.csv",
            occurred_on=date(2026, 1, 10),
            description_raw="Salary",
            amount=Decimal("4200.00"),
            direction="inflow",
            primary_category="payroll",
            account=account,
        ),
    ]
    return ReconcileTransactionsRequest(
        source_name="chase-jan",
        parser_version="test-1",
        dry_run=dry_run,
        transactions=transactions,
    )


def count(database: DatabaseManager, table: str) -> int:
    connection = database.connect()
    try:
        row = connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()
        return int(row[0])
    finally:
        connection.close()


def test_commit_inserts_transactions(database: DatabaseManager) -> None:
    request = make_request(dry_run=False)
    result = database.reconcile_transactions(request)
    assert result.inserted == 2
    assert result.updated == 0
    assert result.would_insert == 0
    assert result.would_update == 0
    assert result.dry_run is False
    assert count(database, "transactions") == 2
    assert count(database, "import_batches") == 1
    assert count(database, "accounts") == 1


def test_commit_updates_on_repeat(database: DatabaseManager) -> None:
    database.reconcile_transactions(make_request(dry_run=False))
    second = database.reconcile_transactions(make_request(dry_run=False))
    assert second.inserted == 0
    assert second.updated == 2
    assert count(database, "transactions") == 2


def test_dry_run_persists_no_state(database: DatabaseManager) -> None:
    result = database.reconcile_transactions(make_request(dry_run=True))
    assert result.dry_run is True
    assert result.inserted == 0
    assert result.updated == 0
    assert result.would_insert == 2
    assert result.would_update == 0
    # Critical: no rows of any kind written.
    assert count(database, "transactions") == 0
    assert count(database, "import_batches") == 0
    assert count(database, "accounts") == 0


def test_dry_run_after_commit_reports_would_update(database: DatabaseManager) -> None:
    database.reconcile_transactions(make_request(dry_run=False))
    pre_tx = count(database, "transactions")
    pre_batches = count(database, "import_batches")
    result = database.reconcile_transactions(make_request(dry_run=True))
    assert result.dry_run is True
    assert result.would_insert == 0
    assert result.would_update == 2
    # State unchanged after the dry-run.
    assert count(database, "transactions") == pre_tx
    assert count(database, "import_batches") == pre_batches


def test_request_rejects_extra_fields() -> None:
    payload = {
        "source_name": "x",
        "transactions": [],
        "extra_field": "nope",
    }
    with pytest.raises(ValidationError):
        ReconcileTransactionsRequest.model_validate(payload)


def test_request_round_trip_via_model_dump_json() -> None:
    request = make_request(dry_run=True)
    raw = request.model_dump_json()
    rehydrated = ReconcileTransactionsRequest.model_validate_json(raw)
    assert rehydrated.source_name == request.source_name
    assert rehydrated.dry_run is True
    assert len(rehydrated.transactions) == len(request.transactions)
    assert rehydrated.transactions[0].amount == request.transactions[0].amount
    assert rehydrated.transactions[0].direction == request.transactions[0].direction


def test_request_validation_requires_direction_literal() -> None:
    bad = {
        "source_name": "x",
        "transactions": [
            {
                "source_record_key": "k",
                "source_document_name": "d",
                "occurred_on": "2026-01-01",
                "description_raw": "x",
                "amount": "1.0",
                "direction": "weirdo",
                "primary_category": "x",
                "account": {
                    "source_key": "a",
                    "institution": "i",
                    "account_name": "n",
                    "account_type": "t",
                },
            }
        ],
    }
    with pytest.raises(ValidationError):
        ReconcileTransactionsRequest.model_validate(bad)
