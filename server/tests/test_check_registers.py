"""Tests for the check register + mobile-deposit ledger ingestion tools."""

from __future__ import annotations

from pathlib import Path
from textwrap import dedent

from liquidity_gate_mcp.check_registers import (
    ingest_check_deposit_ledger,
    ingest_check_register,
)
from liquidity_gate_mcp.database import DatabaseManager
from liquidity_gate_mcp.models import (
    IngestCheckDepositLedgerRequest,
    IngestCheckRegisterRequest,
)


# ---------------------------------------------------------------------------
# Helpers — mirror test_classifier.py conventions
# ---------------------------------------------------------------------------


def _seed_account(database: DatabaseManager, account_id: str, name: str) -> None:
    with database.connect() as conn:
        conn.execute(
            "INSERT OR IGNORE INTO accounts (id, institution, account_name, account_type, owner)"
            " VALUES (?, 'Beacon', ?, 'checking', 'joint')",
            (account_id, name),
        )
        conn.execute(
            "INSERT OR IGNORE INTO import_batches"
            " (id, source_name, parser_version, imported_at, raw_payload)"
            " VALUES ('batch-test', 'test.csv', 'test-v1',"
            "         '2026-01-01T00:00:00Z', '{}')"
        )
        conn.commit()


def _insert_tx(
    database: DatabaseManager,
    *,
    tx_id: str,
    account_id: str,
    occurred_on: str,
    description_raw: str,
    amount: float,
    direction: str = "outflow",
    posted_on: str | None = None,
    primary_category: str = "unclassified",
) -> None:
    with database.connect() as conn:
        conn.execute(
            """
            INSERT INTO transactions
              (id, account_id, import_batch_id, source_record_key, source_document_name,
               occurred_on, posted_on, description_raw, amount, direction,
               primary_category, metadata_json)
            VALUES (?, ?, 'batch-test', ?, 'test.csv', ?, ?, ?, ?, ?, ?, '{}')
            """,
            (
                tx_id,
                account_id,
                tx_id,
                occurred_on,
                posted_on,
                description_raw,
                amount,
                direction,
                primary_category,
            ),
        )
        conn.commit()


def _fetch_tx(database: DatabaseManager, tx_id: str) -> dict:
    with database.connect(read_only=True) as conn:
        row = conn.execute(
            "SELECT * FROM transactions WHERE id = ?", (tx_id,)
        ).fetchone()
    return dict(row) if row else {}


# ---------------------------------------------------------------------------
# Check register
# ---------------------------------------------------------------------------


def test_ingest_check_register_strict_match_writes_override(
    database: DatabaseManager, tmp_path: Path
) -> None:
    _seed_account(database, "acct-beacon", "Beacon Checking")
    _insert_tx(
        database,
        tx_id="tx-check-209",
        account_id="acct-beacon",
        occurred_on="2026-01-12",
        description_raw="CHECK# 209",
        amount=-425.00,
    )

    register_csv = tmp_path / "check_register.csv"
    register_csv.write_text(
        dedent(
            """\
            account,check_number,date_written,amount,payee,primary_category,subcategory,lifecycle,household_role,notes
            Beacon Checking,209,2026-01-12,425.00,ACME Landscaping,fixed_obligation,landscaping,recurring,joint,Monthly mowing
            """
        ),
        encoding="utf-8",
    )

    result = ingest_check_register(
        database,
        tmp_path,
        IngestCheckRegisterRequest(register_path=str(register_csv)),
    )

    assert result.matched == 1
    assert result.overrides_written == 1
    assert result.unmatched == []
    assert result.duplicates_in_register == []
    assert result.errors == []

    row = _fetch_tx(database, "tx-check-209")
    assert row["merchant_normalized"] == "ACME Landscaping"
    assert row["primary_category"] == "fixed_obligation"
    assert row["subcategory"] == "landscaping"
    assert row["lifecycle"] == "recurring"


def test_ingest_check_register_dry_run_writes_nothing(
    database: DatabaseManager, tmp_path: Path
) -> None:
    _seed_account(database, "acct-beacon", "Beacon Checking")
    _insert_tx(
        database,
        tx_id="tx-check-210",
        account_id="acct-beacon",
        occurred_on="2026-02-04",
        description_raw="CHECK# 210",
        amount=-1850.00,
    )

    register_csv = tmp_path / "check_register.csv"
    register_csv.write_text(
        "account,check_number,date_written,amount,payee\n"
        "Beacon Checking,210,2026-02-04,1850.00,Smith Roofing\n",
        encoding="utf-8",
    )
    result = ingest_check_register(
        database,
        tmp_path,
        IngestCheckRegisterRequest(register_path=str(register_csv), dry_run=True),
    )
    assert result.matched == 1
    assert result.overrides_written == 0
    row = _fetch_tx(database, "tx-check-210")
    # Dry run leaves the original (None / unclassified) values alone.
    assert row["merchant_normalized"] is None
    assert row["primary_category"] == "unclassified"


def test_ingest_check_register_leading_zeros_and_whitespace(
    database: DatabaseManager, tmp_path: Path
) -> None:
    _seed_account(database, "acct-beacon", "Beacon Checking")
    # Description with extra whitespace and the register supplying leading
    # zeros should still match.
    _insert_tx(
        database,
        tx_id="tx-check-179",
        account_id="acct-beacon",
        occurred_on="2026-01-20",
        description_raw="CHECK#  179",
        amount=-100.00,
    )
    csv_path = tmp_path / "reg.csv"
    csv_path.write_text(
        "account,check_number,date_written,amount,payee\n"
        "Beacon Checking,0179,2026-01-20,100.00,Town Clerk\n",
        encoding="utf-8",
    )
    result = ingest_check_register(
        database, tmp_path, IngestCheckRegisterRequest(register_path=str(csv_path))
    )
    assert result.matched == 1
    assert result.overrides_written == 1


def test_ingest_check_register_no_match_marked_unmatched(
    database: DatabaseManager, tmp_path: Path
) -> None:
    _seed_account(database, "acct-beacon", "Beacon Checking")
    csv_path = tmp_path / "reg.csv"
    csv_path.write_text(
        "account,check_number,date_written,amount,payee\n"
        "Beacon Checking,999,2026-04-01,50.00,Mystery Co\n",
        encoding="utf-8",
    )
    result = ingest_check_register(
        database, tmp_path, IngestCheckRegisterRequest(register_path=str(csv_path))
    )
    assert result.matched == 0
    assert len(result.unmatched) == 1
    assert result.unmatched[0].reason == "no_matching_transaction"
    assert result.unmatched[0].check_number == "999"


def test_ingest_check_register_unknown_account_errors(
    database: DatabaseManager, tmp_path: Path
) -> None:
    _seed_account(database, "acct-beacon", "Beacon Checking")
    csv_path = tmp_path / "reg.csv"
    csv_path.write_text(
        "account,check_number,date_written,amount,payee\n"
        "Some Other Bank,209,2026-01-12,425.00,ACME\n",
        encoding="utf-8",
    )
    result = ingest_check_register(
        database, tmp_path, IngestCheckRegisterRequest(register_path=str(csv_path))
    )
    assert result.matched == 0
    assert len(result.errors) == 1
    assert result.errors[0].reason == "unknown_account"
    # Helpful error message includes known accounts
    assert "Beacon Checking" in result.errors[0].detail


def test_ingest_check_register_duplicate_check_numbers_skipped(
    database: DatabaseManager, tmp_path: Path
) -> None:
    _seed_account(database, "acct-beacon", "Beacon Checking")
    _insert_tx(
        database,
        tx_id="tx-check-209",
        account_id="acct-beacon",
        occurred_on="2026-01-12",
        description_raw="CHECK# 209",
        amount=-425.00,
    )
    csv_path = tmp_path / "reg.csv"
    csv_path.write_text(
        "account,check_number,date_written,amount,payee\n"
        "Beacon Checking,209,2026-01-12,425.00,Vendor A\n"
        "Beacon Checking,209,2026-01-13,425.00,Vendor B\n",
        encoding="utf-8",
    )
    result = ingest_check_register(
        database, tmp_path, IngestCheckRegisterRequest(register_path=str(csv_path))
    )
    assert result.matched == 0
    assert result.overrides_written == 0
    assert len(result.duplicates_in_register) == 1
    assert result.duplicates_in_register[0].check_number == "209"
    assert sorted(result.duplicates_in_register[0].row_numbers) == [2, 3]
    # Neither row should have been applied
    row = _fetch_tx(database, "tx-check-209")
    assert row["merchant_normalized"] is None


def test_ingest_check_register_missing_required_columns(
    database: DatabaseManager, tmp_path: Path
) -> None:
    csv_path = tmp_path / "reg.csv"
    csv_path.write_text(
        "account,check_number,amount,payee\n"
        "Beacon Checking,209,425.00,ACME\n",
        encoding="utf-8",
    )
    result = ingest_check_register(
        database, tmp_path, IngestCheckRegisterRequest(register_path=str(csv_path))
    )
    assert len(result.errors) == 1
    assert result.errors[0].reason == "missing_columns"
    assert "date_written" in result.errors[0].detail


def test_ingest_check_register_skips_comment_lines(
    database: DatabaseManager, tmp_path: Path
) -> None:
    _seed_account(database, "acct-beacon", "Beacon Checking")
    _insert_tx(
        database,
        tx_id="tx-check-209",
        account_id="acct-beacon",
        occurred_on="2026-01-12",
        description_raw="CHECK# 209",
        amount=-425.00,
    )
    csv_path = tmp_path / "reg.csv"
    csv_path.write_text(
        "# This is the check_register.csv template\n"
        "# Comment lines like this are skipped.\n"
        "account,check_number,date_written,amount,payee\n"
        "# Commented-out example: ignore this line\n"
        "Beacon Checking,209,2026-01-12,425.00,ACME\n",
        encoding="utf-8",
    )
    result = ingest_check_register(
        database, tmp_path, IngestCheckRegisterRequest(register_path=str(csv_path))
    )
    assert result.matched == 1


def test_ingest_check_register_missing_file_returns_empty_result(
    database: DatabaseManager, tmp_path: Path
) -> None:
    result = ingest_check_register(
        database, tmp_path, IngestCheckRegisterRequest()
    )
    assert result.register_exists is False
    assert result.matched == 0
    assert result.errors == []


# ---------------------------------------------------------------------------
# Mobile check deposit ledger
# ---------------------------------------------------------------------------


def test_ingest_deposit_ledger_within_date_window(
    database: DatabaseManager, tmp_path: Path
) -> None:
    _seed_account(database, "acct-beacon", "Beacon Checking")
    _insert_tx(
        database,
        tx_id="tx-dep-1",
        account_id="acct-beacon",
        occurred_on="2026-02-03",
        description_raw="MOBILE CHECK DEP",
        amount=5235.87,
        direction="inflow",
        primary_category="income",
    )
    csv_path = tmp_path / "deposits.csv"
    csv_path.write_text(
        "account,date_deposited,amount,source,primary_category,subcategory,lifecycle\n"
        "Beacon Checking,2026-02-02,5235.87,Hartwell Tenant,rental,rental_income,recurring\n",
        encoding="utf-8",
    )
    result = ingest_check_deposit_ledger(
        database,
        tmp_path,
        IngestCheckDepositLedgerRequest(ledger_path=str(csv_path)),
    )
    assert result.matched == 1
    assert result.overrides_written == 1

    row = _fetch_tx(database, "tx-dep-1")
    assert row["merchant_normalized"] == "Hartwell Tenant"
    assert row["primary_category"] == "rental"
    assert row["subcategory"] == "rental_income"
    assert row["lifecycle"] == "recurring"


def test_ingest_deposit_ledger_outside_date_window_unmatched(
    database: DatabaseManager, tmp_path: Path
) -> None:
    _seed_account(database, "acct-beacon", "Beacon Checking")
    _insert_tx(
        database,
        tx_id="tx-dep-far",
        account_id="acct-beacon",
        occurred_on="2026-02-03",
        description_raw="MOBILE CHECK DEP",
        amount=1000.00,
        direction="inflow",
        primary_category="income",
    )
    csv_path = tmp_path / "deposits.csv"
    csv_path.write_text(
        "account,date_deposited,amount,source\n"
        "Beacon Checking,2026-03-01,1000.00,Far Source\n",
        encoding="utf-8",
    )
    result = ingest_check_deposit_ledger(
        database,
        tmp_path,
        IngestCheckDepositLedgerRequest(ledger_path=str(csv_path)),
    )
    assert result.matched == 0
    assert len(result.unmatched) == 1
    assert result.unmatched[0].reason == "no_matching_transaction"


def test_ingest_deposit_ledger_defaults_to_income_when_blank(
    database: DatabaseManager, tmp_path: Path
) -> None:
    _seed_account(database, "acct-beacon", "Beacon Checking")
    _insert_tx(
        database,
        tx_id="tx-dep-blank",
        account_id="acct-beacon",
        occurred_on="2026-04-15",
        description_raw="MOBILE CHECK DEP",
        amount=250.00,
        direction="inflow",
        primary_category="income",
    )
    csv_path = tmp_path / "deposits.csv"
    csv_path.write_text(
        "account,date_deposited,amount,source\n"
        "Beacon Checking,2026-04-15,250.00,Birthday Gift\n",
        encoding="utf-8",
    )
    result = ingest_check_deposit_ledger(
        database,
        tmp_path,
        IngestCheckDepositLedgerRequest(ledger_path=str(csv_path)),
    )
    assert result.matched == 1
    row = _fetch_tx(database, "tx-dep-blank")
    assert row["primary_category"] == "income"
    assert row["merchant_normalized"] == "Birthday Gift"


def test_ingest_deposit_ledger_transfer_passthrough(
    database: DatabaseManager, tmp_path: Path
) -> None:
    _seed_account(database, "acct-beacon", "Beacon Checking")
    _insert_tx(
        database,
        tx_id="tx-dep-transfer",
        account_id="acct-beacon",
        occurred_on="2026-05-01",
        description_raw="MOBILE CHECK DEP",
        amount=2000.00,
        direction="inflow",
        primary_category="income",
    )
    csv_path = tmp_path / "deposits.csv"
    csv_path.write_text(
        "account,date_deposited,amount,source,primary_category\n"
        "Beacon Checking,2026-05-01,2000.00,My Credit Union Acct,transfer\n",
        encoding="utf-8",
    )
    result = ingest_check_deposit_ledger(
        database,
        tmp_path,
        IngestCheckDepositLedgerRequest(ledger_path=str(csv_path)),
    )
    assert result.matched == 1
    row = _fetch_tx(database, "tx-dep-transfer")
    assert row["primary_category"] == "transfer"
