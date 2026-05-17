from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest

from liquidity_gate_mcp.config import ServerSettings
from liquidity_gate_mcp.database import DatabaseManager
from liquidity_gate_mcp.ingest import ingest_watch_root
from liquidity_gate_mcp.parsers.beacon_csv import parse_beacon_csv


BEACON_CSV_HEADER = (
    '"Posted Date","Type","Description","Amount","Account","Notes","Balance","Raw Description"\n'
)
WITHDRAWAL_PURCHASE = (
    '"2026-05-12T04:00:00+00:00","withdrawal","Eversource Energy",-425.88,'
    '"DP-876321032","",18678.06,"EVERSOURCE WEB_PAY 260512"\n'
)
DEPOSIT_PAYROLL = (
    '"2026-05-04T04:00:00+00:00","deposit","Mobile Check Deposit",8000.00,'
    '"DP-876321032","",32226.90,"MOBILE CHECK DEP"\n'
)
TRANSFER_CHASE_PAYMENT = (
    '"2026-05-07T04:00:00+00:00","withdrawal","Chase Credit Card",-5000.00,'
    '"DP-876321032","",19766.79,"CHASE CREDIT CRD EPAY 260507"\n'
)
TRANSFER_ALLY_SWEEP = (
    '"2026-05-07T04:00:00+00:00","withdrawal","Ally Bank Transfer",-925.00,'
    '"DP-876321032","",24766.79,"ALLY BANK $TRANSFER 260507"\n'
)
# Ally-initiated P2P pull (inbound to Beacon side as a deposit, but still a
# transfer from the household's perspective — pairs with an Ally outbound).
TRANSFER_ALLY_P2P = (
    '"2026-01-05T05:00:00+00:00","deposit","Ally Bank P2P",2500.00,'
    '"DP-876321032","",30000.00,"ALLY BANK P2P 260105 JEFFREY A ZYJES"\n'
)
TRANSFER_IONBANK = (
    '"2026-05-12T04:00:00+00:00","withdrawal","Ionbank Online Transfer",-1775.00,'
    '"DP-876321032","",16903.06,"IonBank ONLINE XFR 260512"\n'
)
# WEBSTR P2P inbound from Ashley's Webster checking. Master index §3
# classifies Ashley -> joint as a household-funding transfer; pairs with
# Webster's `CK TRANSFER … ASHLEY M CALABRESE` outbound.
TRANSFER_WEBSTR_ASHLEY = (
    '"2026-01-22T05:00:00+00:00","deposit","Webster P2P",5000.00,'
    '"DP-876321032","",30000.00,"WEBSTR CK WEBXFR P2P ASHLEY M CALABR 260122"\n'
)
# Two same-day, same-amount FID rows. They share (date, amount, description)
# and only the running balance differs — exercises the balance-in-hash rule.
DUPLICATE_FID_A = (
    '"2026-05-06T04:00:00+00:00","withdrawal","Fidelity Brokerage Services",-15.00,'
    '"DP-876321032","",26745.93,"FID BKG SVC LLC MONEYLINE 260506"\n'
)
DUPLICATE_FID_B = (
    '"2026-05-06T04:00:00+00:00","withdrawal","Fidelity Brokerage Services",-15.00,'
    '"DP-876321032","",26760.93,"FID BKG SVC LLC MONEYLINE 260506"\n'
)
PENDING_ROW = (
    '"","withdrawal","Pending Hold",-50.00,'
    '"DP-876321032","",17000.00,"PENDING AUTH HOLD"\n'
)


def _write_beacon_csv(directory: Path, filename: str, body: str) -> Path:
    path = directory / filename
    path.write_text(BEACON_CSV_HEADER + body, encoding="utf-8")
    return path


def test_parses_withdrawal_and_deposit_with_correct_direction_and_sign(tmp_path: Path) -> None:
    csv_path = _write_beacon_csv(
        tmp_path,
        "2026-05-13_Beacon_Checking.csv",
        WITHDRAWAL_PURCHASE + DEPOSIT_PAYROLL,
    )

    result = parse_beacon_csv(csv_path)

    assert len(result.transactions) == 2
    assert result.errors == []
    withdrawal, deposit = result.transactions

    assert withdrawal.direction == "outflow"
    assert withdrawal.amount == Decimal("-425.88")
    assert withdrawal.description_raw == "EVERSOURCE WEB_PAY 260512"
    assert withdrawal.occurred_on == date(2026, 5, 12)
    assert withdrawal.posted_on == date(2026, 5, 12)
    assert withdrawal.account.source_key == "acct-beacon-dp-876321032"
    assert withdrawal.account.institution == "Beacon"
    assert withdrawal.account.account_type == "checking"
    assert withdrawal.primary_category == "unclassified"
    assert withdrawal.merchant_normalized is None

    assert deposit.direction == "inflow"
    assert deposit.amount == Decimal("8000.00")
    assert deposit.description_raw == "MOBILE CHECK DEP"

    # Running balance is preserved as a float in metadata for downstream
    # statement-reconciliation work (not written to a column).
    assert withdrawal.metadata["running_balance"] == pytest.approx(18678.06)
    assert withdrawal.metadata["beacon_type"] == "withdrawal"
    assert withdrawal.metadata["short_description"] == "Eversource Energy"


def test_known_transfer_patterns_override_sign(tmp_path: Path) -> None:
    csv_path = _write_beacon_csv(
        tmp_path,
        "beacon.csv",
        TRANSFER_CHASE_PAYMENT
        + TRANSFER_ALLY_SWEEP
        + TRANSFER_ALLY_P2P
        + TRANSFER_IONBANK
        + TRANSFER_WEBSTR_ASHLEY,
    )

    result = parse_beacon_csv(csv_path)

    assert [tx.direction for tx in result.transactions] == [
        "transfer",
        "transfer",
        "transfer",
        "transfer",
        "transfer",
    ]
    # Amounts retain their original sign even when reclassified as transfers.
    assert result.transactions[0].amount == Decimal("-5000.00")
    # Ally P2P inbound keeps its positive sign — direction='transfer' does
    # not flip the amount.
    assert result.transactions[2].amount == Decimal("2500.00")
    assert result.transactions[2].description_raw.startswith("ALLY BANK P2P")
    assert result.transactions[3].description_raw == "IonBank ONLINE XFR 260512"
    # WEBSTR inbound from Ashley's Webster checking keeps positive sign.
    assert result.transactions[4].amount == Decimal("5000.00")
    assert result.transactions[4].description_raw.startswith(
        "WEBSTR CK WEBXFR P2P ASHLEY M CALABR"
    )


def test_statement_period_derived_from_filename(tmp_path: Path) -> None:
    csv_path = _write_beacon_csv(
        tmp_path,
        "2026-05-13_Beacon_Checking.csv",
        WITHDRAWAL_PURCHASE,
    )

    [tx] = parse_beacon_csv(csv_path).transactions

    assert tx.statement_period == "2026-05"


def test_statement_period_none_when_filename_has_no_date(tmp_path: Path) -> None:
    csv_path = _write_beacon_csv(tmp_path, "Beacon_Checking.csv", WITHDRAWAL_PURCHASE)

    [tx] = parse_beacon_csv(csv_path).transactions

    assert tx.statement_period is None


def test_source_record_key_is_stable_and_distinguishes_duplicate_rows(tmp_path: Path) -> None:
    csv_path = _write_beacon_csv(
        tmp_path,
        "beacon.csv",
        WITHDRAWAL_PURCHASE + DUPLICATE_FID_A + DUPLICATE_FID_B,
    )

    first = parse_beacon_csv(csv_path).transactions
    second = parse_beacon_csv(csv_path).transactions

    # Same parse twice -> identical keys.
    assert [t.source_record_key for t in first] == [t.source_record_key for t in second]
    # The two same-day same-amount FID rows must get different keys so the
    # idempotent upsert preserves both. The running balance is the only
    # axis that distinguishes them in source data.
    assert first[1].source_record_key != first[2].source_record_key
    assert first[0].source_record_key != first[1].source_record_key


def test_pending_row_is_skipped(tmp_path: Path) -> None:
    csv_path = _write_beacon_csv(
        tmp_path,
        "beacon.csv",
        WITHDRAWAL_PURCHASE + PENDING_ROW,
    )

    result = parse_beacon_csv(csv_path)

    assert len(result.transactions) == 1
    assert len(result.skipped) == 1
    assert result.skipped[0].reason == "pending"
    assert result.errors == []


def test_missing_required_columns_yields_error(tmp_path: Path) -> None:
    path = tmp_path / "bad.csv"
    path.write_text("Foo,Bar\n1,2\n", encoding="utf-8")

    result = parse_beacon_csv(path)

    assert result.transactions == []
    assert result.errors
    assert "missing required columns" in result.errors[0].lower()


@dataclass(frozen=True)
class _IngestHarness:
    settings: ServerSettings
    database: DatabaseManager
    csv_path: Path


@pytest.fixture()
def beacon_ingest_harness(
    tmp_path: Path, database: DatabaseManager, schema_path: Path
) -> _IngestHarness:
    project_root = Path(__file__).resolve().parents[2]
    watch_root = tmp_path / "watch"
    watch_root.mkdir()
    csv_path = _write_beacon_csv(
        watch_root,
        "2026-05-13_Beacon_Checking.csv",
        WITHDRAWAL_PURCHASE
        + DEPOSIT_PAYROLL
        + TRANSFER_CHASE_PAYMENT
        + TRANSFER_ALLY_SWEEP
        + TRANSFER_IONBANK
        + DUPLICATE_FID_A
        + DUPLICATE_FID_B,
    )
    settings = ServerSettings(
        project_root=project_root,
        docs_dir=project_root / "docs",
        tracker_csv_path=project_root / "docs" / "Spreadsheet_checklist_for_document_tracking.csv",
        master_index_path=project_root / "docs" / "00_CASH_FLOW_MASTER_INDEX.md",
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


def test_ingest_writes_beacon_transactions_with_account_row(
    beacon_ingest_harness: _IngestHarness,
) -> None:
    result = ingest_watch_root(beacon_ingest_harness.settings, beacon_ingest_harness.database)

    assert result.totals.files_seen == 1
    assert result.totals.files_ingested == 1
    assert result.totals.inserted == 7
    assert result.totals.updated == 0
    assert _count(beacon_ingest_harness.database, "transactions") == 7
    assert _count(beacon_ingest_harness.database, "accounts") == 1

    [summary] = result.files
    assert summary.doc_id == "doc-002"
    assert summary.parser == "beacon-checking"
    assert summary.status == "ingested"
    assert summary.inserted == 7

    connection = beacon_ingest_harness.database.connect()
    try:
        account_row = connection.execute(
            "SELECT institution, account_name, account_type, owner FROM accounts"
        ).fetchone()
        assert account_row["institution"] == "Beacon"
        assert account_row["account_name"] == "Beacon checking"
        assert account_row["account_type"] == "checking"
        assert account_row["owner"] == "joint"

        directions = {
            row["description_raw"]: row["direction"]
            for row in connection.execute(
                "SELECT description_raw, direction FROM transactions"
            ).fetchall()
        }
        assert directions["CHASE CREDIT CRD EPAY 260507"] == "transfer"
        assert directions["ALLY BANK $TRANSFER 260507"] == "transfer"
        assert directions["IonBank ONLINE XFR 260512"] == "transfer"
        assert directions["EVERSOURCE WEB_PAY 260512"] == "outflow"
        assert directions["MOBILE CHECK DEP"] == "inflow"

        balance_row = connection.execute(
            "SELECT metadata_json FROM transactions WHERE description_raw = 'EVERSOURCE WEB_PAY 260512'"
        ).fetchone()
        metadata = json.loads(balance_row["metadata_json"])
        assert metadata["running_balance"] == pytest.approx(18678.06)
        assert metadata["beacon_type"] == "withdrawal"
    finally:
        connection.close()


def test_ingest_is_idempotent_on_rerun(beacon_ingest_harness: _IngestHarness) -> None:
    ingest_watch_root(beacon_ingest_harness.settings, beacon_ingest_harness.database)
    second = ingest_watch_root(beacon_ingest_harness.settings, beacon_ingest_harness.database)

    assert second.totals.inserted == 0
    assert second.totals.updated == 7
    assert _count(beacon_ingest_harness.database, "transactions") == 7
