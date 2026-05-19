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
from liquidity_gate_mcp.parsers.webster_csv import parse_webster_csv


WEBSTER_CSV_HEADER = (
    '"Date","ReferenceNo.","Type","Description","Debit","Credit","CheckNumber","Balance"\n'
)
# Payroll deposit — real income, default inflow.
PAYROLL_DEPOSIT = (
    '"1/9/2026","","CREDIT","NOVARTIS SERVICE PAYROLL Ashley Calabrese NOV10046159",'
    '"","5809.73","","6933.12"\n'
)
# Chase EPAY — Ashley paying her Chase card. Must be tagged transfer to pair
# with the Chase-side Payment row.
CHASE_EPAY = (
    '"1/12/2026","","DEBIT","CHASE CREDIT CRD EPAY ASHLEY M CALABRESE 9034109140",'
    '"-5800.00","","","1133.12"\n'
)
# Fidelity stock-plan proceeds — large RSU-style inflow. Stays as inflow
# (mirrors Beacon's stance: leave FID MoneyLine for the classifier, not the
# transfer regex).
FID_MONEYLINE = (
    '"1/16/2026","","CREDIT","FID BKG SVC LLC  MONEYLINE ASHLEY CALABRESE X83894961 33S5N",'
    '"","25971.91","","27105.03"\n'
)
INTEREST_DEPOSIT = (
    '"1/16/2026","","CREDIT","INTEREST DEPOSIT","","0.05","","27105.08"\n'
)
# Webster CK TRANSFER outbound — Ashley pushing money to Beacon/HYSA. Must
# be tagged transfer per master index §3.
CK_TRANSFER_A = (
    '"1/21/2026","","DEBIT","ASHLEY M CALA CK TRANSFER ASHLEY M CALABRESE 16318299771",'
    '"-5000.00","","","22605.08"\n'
)
# Same-day same-amount sibling — exercises the balance-in-hash dedup rule.
CK_TRANSFER_B = (
    '"1/21/2026","","DEBIT","ASHLEY M CALA CK TRANSFER ASHLEY M CALABRESE 16318294247",'
    '"-5000.00","","","17605.08"\n'
)
# Venmo outflow — real spending, NOT auto-tagged transfer.
VENMO_OUTFLOW = (
    '"1/27/2026","","DEBIT","VENMO            PAYMENT ASHLEY CALABRESE 1047867744438",'
    '"-250.00","","","8164.81"\n'
)
# PayPal outflow — real spending.
PAYPAL_OUTFLOW = (
    '"2/17/2026","","DEBIT","PAYPAL           INST XFER ASHLEY CALABRESE 1UPNUTRITIO",'
    '"-130.76","","","17252.16"\n'
)
# Empty-date row should be surfaced as a missing_date skip, not dropped.
MISSING_DATE_ROW = (
    '"","","CREDIT","Phantom row with no date","","10.00","",""\n'
)


def _write_webster_csv(directory: Path, filename: str, body: str) -> Path:
    path = directory / filename
    path.write_text(WEBSTER_CSV_HEADER + body, encoding="utf-8")
    return path


def test_parses_credit_and_debit_with_correct_direction_and_sign(tmp_path: Path) -> None:
    csv_path = _write_webster_csv(
        tmp_path,
        "2026-05-17_Webster_Checking.csv",
        PAYROLL_DEPOSIT + VENMO_OUTFLOW,
    )

    result = parse_webster_csv(csv_path)

    assert result.errors == []
    assert len(result.transactions) == 2
    payroll, venmo = result.transactions

    # CREDIT with Credit column populated -> positive inflow.
    assert payroll.direction == "inflow"
    assert payroll.amount == Decimal("5809.73")
    assert payroll.occurred_on == date(2026, 1, 9)
    assert payroll.posted_on == date(2026, 1, 9)
    assert payroll.description_raw.startswith("NOVARTIS SERVICE PAYROLL")
    assert payroll.account.source_key == "acct-webster-checking"
    assert payroll.account.institution == "Webster"
    assert payroll.account.account_name == "Webster checking"
    assert payroll.account.account_type == "checking"
    assert payroll.account.owner == "ashley"
    assert payroll.household_role == "ashley"
    assert payroll.primary_category == "unclassified"

    # DEBIT with Debit column populated -> negative outflow.
    assert venmo.direction == "outflow"
    assert venmo.amount == Decimal("-250.00")
    assert venmo.metadata["webster_type"] == "DEBIT"
    assert venmo.metadata["running_balance"] == pytest.approx(8164.81)


def test_known_transfer_patterns_override_sign(tmp_path: Path) -> None:
    csv_path = _write_webster_csv(
        tmp_path,
        "webster.csv",
        CHASE_EPAY + CK_TRANSFER_A,
    )

    result = parse_webster_csv(csv_path)

    assert result.errors == []
    assert [tx.direction for tx in result.transactions] == ["transfer", "transfer"]
    # Amounts retain their original (negative) sign even when reclassified.
    assert result.transactions[0].amount == Decimal("-5800.00")
    assert result.transactions[1].amount == Decimal("-5000.00")


def test_fid_moneyline_inflow_is_not_auto_tagged_as_transfer(tmp_path: Path) -> None:
    # Large Fidelity MoneyLine inbounds on Webster are RSU/stock-plan
    # proceeds. They look like transfers but have no partner in the system
    # yet — leave them as inflow for the classifier to label later, matching
    # the Beacon parser's stance on FID MoneyLine.
    csv_path = _write_webster_csv(tmp_path, "webster.csv", FID_MONEYLINE)

    [tx] = parse_webster_csv(csv_path).transactions

    assert tx.direction == "inflow"
    assert tx.amount == Decimal("25971.91")


def test_interest_deposit_is_inflow(tmp_path: Path) -> None:
    csv_path = _write_webster_csv(tmp_path, "webster.csv", INTEREST_DEPOSIT)

    [tx] = parse_webster_csv(csv_path).transactions

    assert tx.direction == "inflow"
    assert tx.amount == Decimal("0.05")


def test_paypal_outflow_is_not_auto_tagged_as_transfer(tmp_path: Path) -> None:
    csv_path = _write_webster_csv(tmp_path, "webster.csv", PAYPAL_OUTFLOW)

    [tx] = parse_webster_csv(csv_path).transactions

    assert tx.direction == "outflow"


def test_statement_period_derived_from_filename(tmp_path: Path) -> None:
    csv_path = _write_webster_csv(
        tmp_path,
        "2026-05-17_Webster_Checking.csv",
        PAYROLL_DEPOSIT,
    )

    [tx] = parse_webster_csv(csv_path).transactions

    assert tx.statement_period == "2026-05"


def test_statement_period_none_when_filename_has_no_date(tmp_path: Path) -> None:
    csv_path = _write_webster_csv(tmp_path, "Webster_Checking.csv", PAYROLL_DEPOSIT)

    [tx] = parse_webster_csv(csv_path).transactions

    assert tx.statement_period is None


def test_source_record_key_is_stable_and_distinguishes_duplicate_rows(tmp_path: Path) -> None:
    csv_path = _write_webster_csv(
        tmp_path,
        "webster.csv",
        PAYROLL_DEPOSIT + CK_TRANSFER_A + CK_TRANSFER_B,
    )

    first = parse_webster_csv(csv_path).transactions
    second = parse_webster_csv(csv_path).transactions

    # Same parse twice -> identical keys.
    assert [t.source_record_key for t in first] == [t.source_record_key for t in second]
    # The two same-day same-amount CK TRANSFER rows must get different keys
    # so the idempotent upsert preserves both. Running balance is the only
    # axis that distinguishes them.
    assert first[1].source_record_key != first[2].source_record_key
    assert first[0].source_record_key != first[1].source_record_key


def test_missing_date_row_is_skipped(tmp_path: Path) -> None:
    csv_path = _write_webster_csv(
        tmp_path,
        "webster.csv",
        PAYROLL_DEPOSIT + MISSING_DATE_ROW,
    )

    result = parse_webster_csv(csv_path)

    assert len(result.transactions) == 1
    assert len(result.skipped) == 1
    assert result.skipped[0].reason == "missing_date"
    assert result.errors == []


def test_missing_required_columns_yields_error(tmp_path: Path) -> None:
    path = tmp_path / "bad.csv"
    path.write_text("Foo,Bar\n1,2\n", encoding="utf-8")

    result = parse_webster_csv(path)

    assert result.transactions == []
    assert result.errors
    assert "missing required columns" in result.errors[0].lower()


@dataclass(frozen=True)
class _IngestHarness:
    settings: ServerSettings
    database: DatabaseManager
    csv_path: Path


@pytest.fixture()
def webster_ingest_harness(
    tmp_path: Path, database: DatabaseManager, schema_path: Path
) -> _IngestHarness:
    project_root = Path(__file__).resolve().parents[2]
    watch_root = tmp_path / "watch"
    watch_root.mkdir()
    csv_path = _write_webster_csv(
        watch_root,
        "2026-05-17_Webster_Checking.csv",
        PAYROLL_DEPOSIT
        + CHASE_EPAY
        + FID_MONEYLINE
        + INTEREST_DEPOSIT
        + CK_TRANSFER_A
        + CK_TRANSFER_B
        + VENMO_OUTFLOW,
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


def test_ingest_writes_webster_transactions_with_account_row(
    webster_ingest_harness: _IngestHarness,
) -> None:
    result = ingest_watch_root(webster_ingest_harness.settings, webster_ingest_harness.database)

    assert result.totals.files_seen == 1
    assert result.totals.files_ingested == 1
    assert result.totals.inserted == 7
    assert result.totals.updated == 0
    assert _count(webster_ingest_harness.database, "transactions") == 7
    assert _count(webster_ingest_harness.database, "accounts") == 1

    [summary] = result.files
    assert summary.doc_id == "doc-004"
    assert summary.parser == "webster-checking"
    assert summary.status == "ingested"
    assert summary.inserted == 7

    connection = webster_ingest_harness.database.connect()
    try:
        account_row = connection.execute(
            "SELECT institution, account_name, account_type, owner FROM accounts"
        ).fetchone()
        assert account_row["institution"] == "Webster"
        assert account_row["account_name"] == "Webster checking"
        assert account_row["account_type"] == "checking"
        assert account_row["owner"] == "ashley"

        directions = {
            row["description_raw"]: row["direction"]
            for row in connection.execute(
                "SELECT description_raw, direction FROM transactions"
            ).fetchall()
        }
        chase_desc = next(d for d in directions if d.startswith("CHASE CREDIT CRD EPAY"))
        ck_desc = next(d for d in directions if "CK TRANSFER" in d)
        fid_desc = next(d for d in directions if d.startswith("FID BKG SVC LLC"))
        venmo_desc = next(d for d in directions if d.startswith("VENMO"))

        assert directions[chase_desc] == "transfer"
        assert directions[ck_desc] == "transfer"
        assert directions[fid_desc] == "inflow"
        assert directions[venmo_desc] == "outflow"
        assert directions["INTEREST DEPOSIT"] == "inflow"

        balance_row = connection.execute(
            "SELECT metadata_json FROM transactions WHERE description_raw = 'INTEREST DEPOSIT'"
        ).fetchone()
        metadata = json.loads(balance_row["metadata_json"])
        assert metadata["running_balance"] == pytest.approx(27105.08)
        assert metadata["webster_type"] == "CREDIT"
    finally:
        connection.close()


def test_ingest_is_idempotent_on_rerun(webster_ingest_harness: _IngestHarness) -> None:
    ingest_watch_root(webster_ingest_harness.settings, webster_ingest_harness.database)
    second = ingest_watch_root(webster_ingest_harness.settings, webster_ingest_harness.database)

    assert second.totals.inserted == 0
    assert second.totals.updated == 7
    assert _count(webster_ingest_harness.database, "transactions") == 7
