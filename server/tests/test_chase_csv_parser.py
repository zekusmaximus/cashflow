from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest

from liquidity_gate_mcp.config import ServerSettings
from liquidity_gate_mcp.database import DatabaseManager
from liquidity_gate_mcp.ingest import ingest_watch_root
from liquidity_gate_mcp.parsers.chase_csv import (
    CHASE_ACCOUNT,
    parse_chase_csv,
)


CHASE_CSV_HEADER = "Transaction Date,Post Date,Description,Category,Type,Amount,Memo\n"
POSTED_PURCHASE = '5/01/2026,5/02/2026,"TARGET STORE #123",Shopping,Sale,-42.10,\n'
POSTED_PAYMENT = '5/03/2026,5/04/2026,"AUTOMATIC PAYMENT - THANK YOU",,Payment,850.00,\n'
PENDING_PURCHASE = ',,"PENDING WHOLE FOODS",Groceries,Sale,-18.55,\n'
# Three identical (date, amount, description) rows — the case that caused
# Chase same-day dedup collapse before the |seqN suffix was added.
DUPLICATE_MICRO_A = '5/05/2026,5/06/2026,"AMAZON DIGITAL",Shopping,Sale,-0.99,\n'
DUPLICATE_MICRO_B = '5/05/2026,5/06/2026,"AMAZON DIGITAL",Shopping,Sale,-0.99,\n'
DUPLICATE_MICRO_C = '5/05/2026,5/06/2026,"AMAZON DIGITAL",Shopping,Sale,-0.99,\n'


def _write_chase_csv(directory: Path, filename: str, body: str) -> Path:
    path = directory / filename
    path.write_text(CHASE_CSV_HEADER + body, encoding="utf-8")
    return path


def test_parses_posted_rows_and_skips_pending(tmp_path: Path) -> None:
    csv_path = _write_chase_csv(
        tmp_path,
        "2026-05-12_Chase_Credit_Card.csv",
        POSTED_PURCHASE + POSTED_PAYMENT + PENDING_PURCHASE,
    )

    result = parse_chase_csv(csv_path)

    assert len(result.transactions) == 2
    assert len(result.skipped) == 1
    assert result.skipped[0].reason == "pending"
    assert result.errors == []


def test_purchase_row_has_outflow_direction_and_signed_amount(tmp_path: Path) -> None:
    csv_path = _write_chase_csv(tmp_path, "chase.csv", POSTED_PURCHASE)

    [purchase] = parse_chase_csv(csv_path).transactions

    assert purchase.direction == "outflow"
    assert purchase.amount == Decimal("-42.10")
    assert purchase.description_raw == "TARGET STORE #123"
    assert purchase.occurred_on == date(2026, 5, 1)
    assert purchase.posted_on == date(2026, 5, 2)
    assert purchase.metadata["chase_category"] == "Shopping"
    assert purchase.metadata["chase_type"] == "Sale"
    assert purchase.primary_category == "unclassified"
    assert purchase.merchant_normalized is None
    assert purchase.household_role == "joint"
    assert purchase.account == CHASE_ACCOUNT


def test_payment_row_is_transfer_regardless_of_positive_amount(tmp_path: Path) -> None:
    csv_path = _write_chase_csv(tmp_path, "chase.csv", POSTED_PAYMENT)

    [payment] = parse_chase_csv(csv_path).transactions

    assert payment.direction == "transfer"
    assert payment.amount == Decimal("850.00")


def test_source_record_key_is_stable_across_parses(tmp_path: Path) -> None:
    csv_path = _write_chase_csv(tmp_path, "chase.csv", POSTED_PURCHASE + POSTED_PAYMENT)

    first = parse_chase_csv(csv_path).transactions
    second = parse_chase_csv(csv_path).transactions

    assert [t.source_record_key for t in first] == [t.source_record_key for t in second]
    # Distinct rows produce distinct keys.
    assert first[0].source_record_key != first[1].source_record_key


def test_duplicate_same_day_rows_get_distinct_keys(tmp_path: Path) -> None:
    csv_path = _write_chase_csv(
        tmp_path,
        "chase.csv",
        DUPLICATE_MICRO_A + DUPLICATE_MICRO_B + DUPLICATE_MICRO_C,
    )

    transactions = parse_chase_csv(csv_path).transactions

    assert len(transactions) == 3
    keys = [t.source_record_key for t in transactions]
    assert len(set(keys)) == 3  # all three are distinct after the seq suffix


def test_first_occurrence_key_matches_legacy_singleton_key(tmp_path: Path) -> None:
    # Backwards-compat invariant: the first occurrence of a (date, amount,
    # description) tuple must hash identically to the legacy single-row
    # case, so existing DBs don't see 924 inserts on re-import. A file with
    # the row once and a file with it three times must agree on the first
    # row's key.
    a_dir = tmp_path / "a"
    a_dir.mkdir()
    b_dir = tmp_path / "b"
    b_dir.mkdir()
    singleton = _write_chase_csv(a_dir, "chase.csv", DUPLICATE_MICRO_A)
    triplet = _write_chase_csv(b_dir, "chase.csv", DUPLICATE_MICRO_A + DUPLICATE_MICRO_B + DUPLICATE_MICRO_C)

    [singleton_tx] = parse_chase_csv(singleton).transactions
    triplet_txs = parse_chase_csv(triplet).transactions

    assert triplet_txs[0].source_record_key == singleton_tx.source_record_key


def test_duplicate_dedup_is_idempotent_across_reparses(tmp_path: Path) -> None:
    csv_path = _write_chase_csv(
        tmp_path,
        "chase.csv",
        DUPLICATE_MICRO_A + DUPLICATE_MICRO_B + DUPLICATE_MICRO_C,
    )

    first = parse_chase_csv(csv_path).transactions
    second = parse_chase_csv(csv_path).transactions

    assert [t.source_record_key for t in first] == [t.source_record_key for t in second]


def test_statement_period_derived_from_filename(tmp_path: Path) -> None:
    csv_path = _write_chase_csv(
        tmp_path,
        "2026-05-12_Chase_Credit_Card.csv",
        POSTED_PURCHASE,
    )

    [tx] = parse_chase_csv(csv_path).transactions

    assert tx.statement_period == "2026-05"


def test_statement_period_none_when_filename_has_no_date(tmp_path: Path) -> None:
    csv_path = _write_chase_csv(tmp_path, "Chase_Credit_Card.csv", POSTED_PURCHASE)

    [tx] = parse_chase_csv(csv_path).transactions

    assert tx.statement_period is None


def test_missing_required_columns_yields_error(tmp_path: Path) -> None:
    path = tmp_path / "bad.csv"
    path.write_text("Foo,Bar\n1,2\n", encoding="utf-8")

    result = parse_chase_csv(path)

    assert result.transactions == []
    assert result.errors
    assert "missing required columns" in result.errors[0].lower()


@dataclass(frozen=True)
class _IngestHarness:
    settings: ServerSettings
    database: DatabaseManager
    csv_path: Path


@pytest.fixture()
def ingest_harness(tmp_path: Path, database: DatabaseManager, schema_path: Path) -> _IngestHarness:
    project_root = Path(__file__).resolve().parents[2]
    watch_root = tmp_path / "watch"
    watch_root.mkdir()
    csv_path = _write_chase_csv(
        watch_root,
        "2026-05-12_Chase_Credit_Card.csv",
        POSTED_PURCHASE + POSTED_PAYMENT + PENDING_PURCHASE,
    )
    settings = ServerSettings(
        project_root=project_root,
        docs_dir=project_root / "docs",
        tracker_csv_path=project_root / "docs" / "Spreadsheet_checklist_for_document_tracking.csv",
        master_index_path=project_root / "docs" / "00_CASH_FLOW_MASTER_INDEX.md",
        project_status_path=project_root / "docs" / "PROJECT_STATUS.md",
        database_path=database.database_path,
        schema_path=schema_path,
        watch_root=watch_root,
    )
    return _IngestHarness(settings=settings, database=database, csv_path=csv_path)


def _count(database: DatabaseManager, table: str) -> int:
    connection = database.connect()
    try:
        return int(connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])
    finally:
        connection.close()


def test_ingest_writes_transactions_and_reports_summary(ingest_harness: _IngestHarness) -> None:
    result = ingest_watch_root(ingest_harness.settings, ingest_harness.database)

    assert result.totals.files_seen == 1
    assert result.totals.files_ingested == 1
    assert result.totals.inserted == 2
    assert result.totals.updated == 0
    assert result.totals.skipped_pending == 1
    assert _count(ingest_harness.database, "transactions") == 2
    assert _count(ingest_harness.database, "accounts") == 1

    [summary] = result.files
    assert summary.doc_id == "doc-001"
    assert summary.parser == "chase-credit-card"
    assert summary.status == "ingested"
    assert summary.inserted == 2
    assert summary.skipped_pending == 1


def test_ingest_is_idempotent_on_rerun(ingest_harness: _IngestHarness) -> None:
    ingest_watch_root(ingest_harness.settings, ingest_harness.database)
    second = ingest_watch_root(ingest_harness.settings, ingest_harness.database)

    assert second.totals.inserted == 0
    assert second.totals.updated == 2
    assert _count(ingest_harness.database, "transactions") == 2
