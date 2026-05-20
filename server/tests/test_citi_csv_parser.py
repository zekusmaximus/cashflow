from __future__ import annotations

from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest

from liquidity_gate_mcp.parsers.citi_csv import (
    CITI_ACCOUNT,
    parse_citi_csv,
)


CITI_CSV_HEADER = "Status,Date,Description,Debit,Credit\n"
CHARGE_EZPASS = 'Cleared,04/01/2026,"E-ZPASS MA WALTHAM MA",20.00,\n'
PAYMENT_AUTOPAY = (
    'Cleared,04/22/2026,"AUTOPAY 231204084807155RAUTOPAY AUTO-PMT",,-200.00\n'
)
DONATION_ACTBLUE = (
    'Cleared,03/12/2026,"ACTBLUE* MAURA.HEALEY BOSTON MA",200.00,\n'
)
INTEREST_CHARGE = (
    'Cleared,01/26/2026,"INTEREST CHARGED TO ADV PR-12/17/25.",6.29,\n'
)
PENDING_ROW = 'Pending,04/30/2026,"AMZN MKTPLACE",15.99,\n'
NEITHER_FILLED = 'Cleared,02/15/2026,"GHOST ROW",,\n'
BOTH_FILLED = 'Cleared,02/16/2026,"AMBIGUOUS ROW",10.00,-10.00\n'


def _write_citi_csv(directory: Path, filename: str, body: str) -> Path:
    path = directory / filename
    path.write_text(CITI_CSV_HEADER + body, encoding="utf-8")
    return path


def test_parses_charge_as_outflow_with_negative_amount(tmp_path: Path) -> None:
    csv_path = _write_citi_csv(tmp_path, "citi.csv", CHARGE_EZPASS)

    [charge] = parse_citi_csv(csv_path).transactions

    assert charge.direction == "outflow"
    assert charge.amount == Decimal("-20.00")
    assert charge.description_raw == "E-ZPASS MA WALTHAM MA"
    assert charge.occurred_on == date(2026, 4, 1)
    assert charge.primary_category == "unclassified"
    assert charge.account == CITI_ACCOUNT


def test_parses_autopay_as_transfer_with_positive_amount(tmp_path: Path) -> None:
    csv_path = _write_citi_csv(tmp_path, "citi.csv", PAYMENT_AUTOPAY)

    [payment] = parse_citi_csv(csv_path).transactions

    # Citi exports the payment as Credit = -200.00. Repo convention stores
    # credit-card inflows as positive amount, and AUTOPAY is a transfer.
    assert payment.direction == "transfer"
    assert payment.amount == Decimal("200.00")
    assert payment.description_raw.startswith("AUTOPAY")


def test_donation_charge_is_outflow(tmp_path: Path) -> None:
    csv_path = _write_citi_csv(tmp_path, "citi.csv", DONATION_ACTBLUE)

    [donation] = parse_citi_csv(csv_path).transactions

    assert donation.direction == "outflow"
    assert donation.amount == Decimal("-200.00")
    assert donation.description_raw.startswith("ACTBLUE")


def test_interest_charge_is_outflow(tmp_path: Path) -> None:
    csv_path = _write_citi_csv(tmp_path, "citi.csv", INTEREST_CHARGE)

    [interest] = parse_citi_csv(csv_path).transactions

    assert interest.direction == "outflow"
    assert interest.amount == Decimal("-6.29")


def test_pending_row_is_skipped(tmp_path: Path) -> None:
    csv_path = _write_citi_csv(tmp_path, "citi.csv", CHARGE_EZPASS + PENDING_ROW)

    result = parse_citi_csv(csv_path)

    assert len(result.transactions) == 1
    assert len(result.skipped) == 1
    assert result.skipped[0].reason == "pending"


def test_neither_debit_nor_credit_is_error(tmp_path: Path) -> None:
    csv_path = _write_citi_csv(tmp_path, "citi.csv", NEITHER_FILLED)

    result = parse_citi_csv(csv_path)

    assert result.transactions == []
    assert len(result.errors) == 1
    assert "both debit and credit are empty" in result.errors[0]


def test_both_debit_and_credit_filled_is_error(tmp_path: Path) -> None:
    csv_path = _write_citi_csv(tmp_path, "citi.csv", BOTH_FILLED)

    result = parse_citi_csv(csv_path)

    assert result.transactions == []
    assert len(result.errors) == 1
    assert "ambiguous" in result.errors[0]


def test_missing_required_column_returns_error(tmp_path: Path) -> None:
    bad_path = tmp_path / "citi.csv"
    bad_path.write_text("Status,Date,Description,Amount\n", encoding="utf-8")

    result = parse_citi_csv(bad_path)

    assert result.transactions == []
    assert len(result.errors) == 1
    assert "credit" in result.errors[0]
    assert "debit" in result.errors[0]


def test_record_keys_are_stable_across_parses(tmp_path: Path) -> None:
    body = CHARGE_EZPASS + PAYMENT_AUTOPAY + DONATION_ACTBLUE
    csv_path = _write_citi_csv(tmp_path, "2026-05-20_Citi_Credit_Card.csv", body)

    first = {t.source_record_key for t in parse_citi_csv(csv_path).transactions}
    second = {t.source_record_key for t in parse_citi_csv(csv_path).transactions}

    assert first == second
    assert len(first) == 3


def test_duplicate_rows_get_distinct_record_keys(tmp_path: Path) -> None:
    # Two identical EZPass charges on the same day should both ingest.
    body = CHARGE_EZPASS + CHARGE_EZPASS
    csv_path = _write_citi_csv(tmp_path, "citi.csv", body)

    transactions = parse_citi_csv(csv_path).transactions
    keys = [t.source_record_key for t in transactions]

    assert len(transactions) == 2
    assert len(set(keys)) == 2


def test_statement_period_derived_from_filename(tmp_path: Path) -> None:
    csv_path = _write_citi_csv(
        tmp_path, "2026-05-20_Citi_Credit_Card.csv", CHARGE_EZPASS
    )

    [charge] = parse_citi_csv(csv_path).transactions

    assert charge.statement_period == "2026-05"
