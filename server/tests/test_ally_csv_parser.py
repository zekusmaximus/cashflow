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
from liquidity_gate_mcp.parsers.ally_csv import parse_ally_csv


ALLY_CSV_HEADER = "Date,Time,Amount,Type,Description\n"

# Outbound Zelle to a third party — real spending, NOT an internal transfer.
ZELLE_WITHDRAWAL = (
    "5/8/2026,17:44:15,-200,Withdrawal,"
    "Zelle payment from Jeffrey Zyjeski (Ally Savings Account XXXXXX1081) to TAINARA GISELE ROSA DOS SANTOS\n"
)
# Inbound from Beacon — partner side of Beacon's ALLY BANK $TRANSFER.
TRANSFER_IN_JEFF = (
    "5/8/2026,3:21:27,1000,Deposit,"
    "Requested transfer from JEFFREY A ZYJESKI Ally Bank Transfer\n"
)
TRANSFER_IN_JEFF_2 = (
    "5/7/2026,3:51:04,925,Deposit,"
    "Requested transfer from JEFFREY A ZYJESKI Ally Bank Transfer\n"
)
# Generic-name variant — must still match the transfer pattern.
TRANSFER_IN_ASHLEY = (
    "5/3/2026,9:12:01,500,Deposit,"
    "Requested transfer from ASHLEY M CALABRESE Ally Bank Transfer\n"
)
# Hypothetical outbound transfer to checking — Ally side of a Beacon deposit.
TRANSFER_OUT = (
    "5/2/2026,8:00:00,-1500,Withdrawal,"
    "Requested transfer to JEFFREY A ZYJESKI Beacon Checking\n"
)
# Real income, not a transfer.
INTEREST_PAID = "5/5/2026,23:38:17,76.13,Deposit,Interest Paid\n"
# Two interest postings on the same day with same amount and description —
# only Time distinguishes them. Exercises the time-in-hash rule.
INTEREST_PAID_DUP_A = "4/5/2026,23:21:44,66.92,Deposit,Interest Paid\n"
INTEREST_PAID_DUP_B = "4/5/2026,23:21:45,66.92,Deposit,Interest Paid\n"
# Empty-date row should be reported as skipped, not silently dropped.
MISSING_DATE_ROW = ",12:00:00,10.00,Deposit,Phantom row with no date\n"


def _write_ally_csv(directory: Path, filename: str, body: str) -> Path:
    path = directory / filename
    path.write_text(ALLY_CSV_HEADER + body, encoding="utf-8")
    return path


def test_parses_withdrawal_and_deposit_with_correct_direction_and_sign(tmp_path: Path) -> None:
    csv_path = _write_ally_csv(
        tmp_path,
        "2026-05-13_Ally_HYSA.csv",
        ZELLE_WITHDRAWAL + INTEREST_PAID,
    )

    result = parse_ally_csv(csv_path)

    assert result.errors == []
    assert len(result.transactions) == 2
    zelle, interest = result.transactions

    # External Zelle is real spending, even though Type=Withdrawal.
    assert zelle.direction == "outflow"
    assert zelle.amount == Decimal("-200")
    assert zelle.occurred_on == date(2026, 5, 8)
    assert zelle.posted_on == date(2026, 5, 8)
    assert "TAINARA" in zelle.description_raw
    assert zelle.account.source_key == "acct-ally-hysa"
    assert zelle.account.institution == "Ally"
    assert zelle.account.account_type == "savings"
    assert zelle.account.account_name == "Ally HYSA"
    assert zelle.primary_category == "unclassified"
    assert zelle.merchant_normalized is None

    # Interest Paid is income, not a transfer.
    assert interest.direction == "inflow"
    assert interest.amount == Decimal("76.13")
    assert interest.description_raw == "Interest Paid"
    assert interest.metadata["ally_type"] == "Deposit"
    assert interest.metadata["occurred_at_time"] == "23:38:17"


def test_known_transfer_patterns_override_sign(tmp_path: Path) -> None:
    csv_path = _write_ally_csv(
        tmp_path,
        "ally.csv",
        TRANSFER_IN_JEFF + TRANSFER_IN_ASHLEY + TRANSFER_OUT,
    )

    result = parse_ally_csv(csv_path)

    assert result.errors == []
    assert [tx.direction for tx in result.transactions] == ["transfer", "transfer", "transfer"]
    # Original signs preserved even when reclassified as transfers.
    assert result.transactions[0].amount == Decimal("1000")
    assert result.transactions[2].amount == Decimal("-1500")


def test_interest_is_inflow_not_transfer(tmp_path: Path) -> None:
    csv_path = _write_ally_csv(tmp_path, "ally.csv", INTEREST_PAID)

    [tx] = parse_ally_csv(csv_path).transactions

    assert tx.direction == "inflow"


def test_statement_period_derived_from_filename(tmp_path: Path) -> None:
    csv_path = _write_ally_csv(
        tmp_path,
        "2026-05-13_Ally_HYSA.csv",
        INTEREST_PAID,
    )

    [tx] = parse_ally_csv(csv_path).transactions

    assert tx.statement_period == "2026-05"


def test_statement_period_none_when_filename_has_no_date(tmp_path: Path) -> None:
    csv_path = _write_ally_csv(tmp_path, "Ally_HYSA.csv", INTEREST_PAID)

    [tx] = parse_ally_csv(csv_path).transactions

    assert tx.statement_period is None


def test_source_record_key_is_stable_and_distinguishes_duplicate_rows(tmp_path: Path) -> None:
    csv_path = _write_ally_csv(
        tmp_path,
        "ally.csv",
        INTEREST_PAID + INTEREST_PAID_DUP_A + INTEREST_PAID_DUP_B,
    )

    first = parse_ally_csv(csv_path).transactions
    second = parse_ally_csv(csv_path).transactions

    # Same parse twice -> identical keys.
    assert [t.source_record_key for t in first] == [t.source_record_key for t in second]
    # Two same-day, same-amount, same-description Interest Paid rows must
    # get different keys so the idempotent upsert preserves both. Time is
    # the only axis that distinguishes them.
    assert first[1].source_record_key != first[2].source_record_key
    assert first[0].source_record_key != first[1].source_record_key


def test_missing_date_row_is_skipped(tmp_path: Path) -> None:
    csv_path = _write_ally_csv(
        tmp_path,
        "ally.csv",
        INTEREST_PAID + MISSING_DATE_ROW,
    )

    result = parse_ally_csv(csv_path)

    assert len(result.transactions) == 1
    assert len(result.skipped) == 1
    assert result.skipped[0].reason == "missing_date"
    assert result.errors == []


def test_missing_required_columns_yields_error(tmp_path: Path) -> None:
    path = tmp_path / "bad.csv"
    path.write_text("Foo,Bar\n1,2\n", encoding="utf-8")

    result = parse_ally_csv(path)

    assert result.transactions == []
    assert result.errors
    assert "missing required columns" in result.errors[0].lower()


@dataclass(frozen=True)
class _IngestHarness:
    settings: ServerSettings
    database: DatabaseManager
    csv_path: Path


@pytest.fixture()
def ally_ingest_harness(
    tmp_path: Path, database: DatabaseManager, schema_path: Path
) -> _IngestHarness:
    project_root = Path(__file__).resolve().parents[2]
    watch_root = tmp_path / "watch"
    watch_root.mkdir()
    csv_path = _write_ally_csv(
        watch_root,
        "2026-05-13_Ally_HYSA.csv",
        ZELLE_WITHDRAWAL
        + TRANSFER_IN_JEFF
        + TRANSFER_IN_JEFF_2
        + INTEREST_PAID
        + INTEREST_PAID_DUP_A
        + INTEREST_PAID_DUP_B,
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


def test_ingest_writes_ally_transactions_with_account_row(
    ally_ingest_harness: _IngestHarness,
) -> None:
    result = ingest_watch_root(ally_ingest_harness.settings, ally_ingest_harness.database)

    assert result.totals.files_seen == 1
    assert result.totals.files_ingested == 1
    assert result.totals.inserted == 6
    assert result.totals.updated == 0
    assert _count(ally_ingest_harness.database, "transactions") == 6
    assert _count(ally_ingest_harness.database, "accounts") == 1

    [summary] = result.files
    assert summary.doc_id == "doc-003"
    assert summary.parser == "ally-hysa"
    assert summary.status == "ingested"
    assert summary.inserted == 6

    connection = ally_ingest_harness.database.connect()
    try:
        account_row = connection.execute(
            "SELECT institution, account_name, account_type, owner FROM accounts"
        ).fetchone()
        assert account_row["institution"] == "Ally"
        assert account_row["account_name"] == "Ally HYSA"
        assert account_row["account_type"] == "savings"
        assert account_row["owner"] == "joint"

        directions = {
            (row["description_raw"], row["amount"]): row["direction"]
            for row in connection.execute(
                "SELECT description_raw, amount, direction FROM transactions"
            ).fetchall()
        }
        # Two Interest Paid rows differ only by amount in this fixture.
        assert directions[
            ("Requested transfer from JEFFREY A ZYJESKI Ally Bank Transfer", 1000.0)
        ] == "transfer"
        assert directions[("Interest Paid", 76.13)] == "inflow"
        assert directions[("Interest Paid", 66.92)] == "inflow"
        zelle_dir = next(
            d for (desc, _amount), d in directions.items() if desc.startswith("Zelle")
        )
        assert zelle_dir == "outflow"

        interest_metadata = json.loads(
            connection.execute(
                "SELECT metadata_json FROM transactions WHERE description_raw = 'Interest Paid' AND amount = 76.13"
            )
            .fetchone()["metadata_json"]
        )
        assert interest_metadata["ally_type"] == "Deposit"
        assert interest_metadata["occurred_at_time"] == "23:38:17"
    finally:
        connection.close()


def test_ingest_is_idempotent_on_rerun(ally_ingest_harness: _IngestHarness) -> None:
    ingest_watch_root(ally_ingest_harness.settings, ally_ingest_harness.database)
    second = ingest_watch_root(ally_ingest_harness.settings, ally_ingest_harness.database)

    assert second.totals.inserted == 0
    assert second.totals.updated == 6
    assert _count(ally_ingest_harness.database, "transactions") == 6
