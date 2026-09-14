"""Self-Help FCU savings parser.

The format's defining trap is that ``Description`` is empty on most rent
deposits and the transaction type lives in ``Ext``. Most of these tests exist
to pin that behaviour; the rest pin the two other landmines (bare-leading
decimals, and a source_record_key that must not be derived from description
text because ACH ids are masked in newer exports).
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from pathlib import Path

from . import selfhelp_fixtures as fx
from liquidity_gate_mcp.parsers.selfhelp_csv import (
    SELFHELP_SAVINGS_ACCOUNT,
    build_description,
    parse_selfhelp_csv,
)


def _write(directory: Path, body: str, name: str = "selfhelp.csv") -> Path:
    path = directory / name
    path.write_text(fx.HEADER + body, encoding="utf-8")
    return path


# --------------------------------------------------------------------------
# The Ext column
# --------------------------------------------------------------------------


def test_empty_description_falls_back_to_ext(tmp_path: Path) -> None:
    csv_path = _write(tmp_path, '1/6/2026,"","Share Deposit Transfer",,1295,2455.69\n')

    [rent] = parse_selfhelp_csv(csv_path).transactions

    assert rent.description_raw == "Share Deposit Transfer"
    assert rent.metadata["ext"] == "Share Deposit Transfer"


def test_description_and_ext_are_joined(tmp_path: Path) -> None:
    csv_path = _write(
        tmp_path,
        '2/6/2026,"TRANSFER MADE BY SHARON DRAKE HUFF",'
        '"Share Deposit Transfer",,1295,3750.69\n',
    )

    [rent] = parse_selfhelp_csv(csv_path).transactions

    assert rent.description_raw == (
        "TRANSFER MADE BY SHARON DRAKE HUFF | Share Deposit Transfer"
    )
    assert rent.metadata["ext"] == "Share Deposit Transfer"


def test_build_description_never_leaves_a_bare_separator() -> None:
    assert build_description("", "Share Deposit") == "Share Deposit"
    assert build_description("PAID", "") == "PAID"
    assert build_description("PAID", "Share Deposit") == "PAID | Share Deposit"
    assert build_description("", "") == ""


def test_row_with_no_description_and_no_ext_is_an_error(tmp_path: Path) -> None:
    csv_path = _write(tmp_path, '4/1/2026,"","",,1295,6341.15\n')

    result = parse_selfhelp_csv(csv_path)

    assert result.transactions == []
    assert "neither a Description nor an Ext" in result.errors[0]


# --------------------------------------------------------------------------
# Format handling
# --------------------------------------------------------------------------


def test_parses_unpadded_us_dates(tmp_path: Path) -> None:
    csv_path = _write(tmp_path, '1/6/2026,"","Share Deposit",,1295,2455.69\n')

    [row] = parse_selfhelp_csv(csv_path).transactions

    assert row.occurred_on == date(2026, 1, 6)
    assert row.posted_on == date(2026, 1, 6)


def test_parses_bare_leading_decimal_amounts(tmp_path: Path) -> None:
    csv_path = _write(
        tmp_path,
        '7/15/2026,"ALLY BANK ACCTVERIFY","ACH Credit",,.6,4148.46\n'
        '3/31/2026,"TERM: 90 DAYS APYE: .05","Dividend",,.46,5046.15\n',
    )

    verify, dividend = parse_selfhelp_csv(csv_path).transactions

    # Decimal, not int(): ".6" must not truncate to 0 and must not pick up
    # binary-float noise.
    assert verify.amount == Decimal("0.6")
    assert dividend.amount == Decimal("0.46")


def test_empty_draft_column_is_not_required(tmp_path: Path) -> None:
    csv_path = _write(tmp_path, '8/3/2026,"","Share Deposit",,1295,2541.81\n')

    [row] = parse_selfhelp_csv(csv_path).transactions

    assert row.metadata["draft"] == ""


def test_running_balance_is_preserved_in_metadata(tmp_path: Path) -> None:
    csv_path = _write(tmp_path, '8/3/2026,"","Share Deposit",,1295,2541.81\n')

    [row] = parse_selfhelp_csv(csv_path).transactions

    assert row.metadata["running_balance"] == 2541.81


def test_missing_required_column_is_reported(tmp_path: Path) -> None:
    path = tmp_path / "bad.csv"
    path.write_text(
        "Date,Description,Draft,Amount,Balance\n"
        '1/6/2026,"",,1295,2455.69\n',
        encoding="utf-8",
    )

    result = parse_selfhelp_csv(path)

    assert result.transactions == []
    assert "ext" in result.errors[0]


# --------------------------------------------------------------------------
# Direction and account
# --------------------------------------------------------------------------


def test_sign_convention_is_used_as_exported(tmp_path: Path) -> None:
    csv_path = _write(
        tmp_path,
        '1/6/2026,"","Share Deposit Transfer",,1295,2455.69\n'
        '7/15/2026,"SOME FEE","ACH Share Withdrawal",,-1.06,4147.86\n',
    )

    deposit, withdrawal = parse_selfhelp_csv(csv_path).transactions

    assert (deposit.direction, deposit.amount) == ("inflow", Decimal("1295"))
    assert (withdrawal.direction, withdrawal.amount) == ("outflow", Decimal("-1.06"))


def test_ally_sweep_is_a_transfer_masked_or_not(tmp_path: Path) -> None:
    csv_path = _write(
        tmp_path,
        '1/5/2026,"ALLY BANK $TRANSFER ID:*********** **0105 S",'
        '"ACH Share Withdrawal",,-4500,1160.69\n'
        '4/8/2026,"ALLY BANK $TRANSFER ID:16101559175 260408 S",'
        '"ACH Share Withdrawal",,-5080,1261.15\n',
    )

    masked, unmasked = parse_selfhelp_csv(csv_path).transactions

    assert masked.direction == "transfer"
    assert unmasked.direction == "transfer"


def test_acctverify_micro_rows_are_transfers(tmp_path: Path) -> None:
    csv_path = _write(
        tmp_path,
        '7/15/2026,"ALLY BANK ACCTVERIFY","ACH Credit",,.6,4148.46\n'
        '7/15/2026,"ALLY BANK ACCTVERIFY","ACH Credit",,.46,4148.92\n'
        '7/15/2026,"ALLY BANK ACCTVERIFY","ACH Share Withdrawal",,-1.06,4147.86\n',
    )

    rows = parse_selfhelp_csv(csv_path).transactions

    assert [row.direction for row in rows] == ["transfer"] * 3
    assert sum(row.amount for row in rows) == Decimal("0.00")


def test_rows_belong_to_the_selfhelp_account_and_to_jeff(tmp_path: Path) -> None:
    csv_path = _write(tmp_path, '9/2/2026,"","Share Deposit",,1295,3837.44\n')

    [row] = parse_selfhelp_csv(csv_path).transactions

    assert row.account == SELFHELP_SAVINGS_ACCOUNT
    assert row.account.source_key == "acct-selfhelp-savings"
    assert row.household_role == "jeff"
    assert row.primary_category == "unclassified"


# --------------------------------------------------------------------------
# source_record_key
# --------------------------------------------------------------------------


def test_record_key_ignores_description_so_masking_cannot_double_book(
    tmp_path: Path,
) -> None:
    masked = _write(
        tmp_path,
        '1/5/2026,"ALLY BANK $TRANSFER ID:*********** **0105 S",'
        '"ACH Share Withdrawal",,-4500,1160.69\n',
        name="masked.csv",
    )
    unmasked = _write(
        tmp_path,
        '1/5/2026,"ALLY BANK $TRANSFER ID:16101559175 260105 S",'
        '"ACH Share Withdrawal",,-4500,1160.69\n',
        name="unmasked.csv",
    )

    [from_masked] = parse_selfhelp_csv(masked).transactions
    [from_unmasked] = parse_selfhelp_csv(unmasked).transactions

    assert from_masked.description_raw != from_unmasked.description_raw
    assert from_masked.source_record_key == from_unmasked.source_record_key


def test_same_day_rows_get_distinct_record_keys(tmp_path: Path) -> None:
    csv_path = _write(
        tmp_path,
        '7/15/2026,"ALLY BANK ACCTVERIFY","ACH Credit",,.6,4148.46\n'
        '7/15/2026,"ALLY BANK ACCTVERIFY","ACH Credit",,.46,4148.92\n'
        '7/15/2026,"ALLY BANK ACCTVERIFY","ACH Share Withdrawal",,-1.06,4147.86\n',
    )

    rows = parse_selfhelp_csv(csv_path).transactions

    assert len({row.source_record_key for row in rows}) == 3


def test_record_keys_are_identical_across_the_two_exports(tmp_path: Path) -> None:
    new_export = fx.write_life_of_account(tmp_path)
    old_export = fx.write_ytd_prefix(tmp_path)

    new_rows = parse_selfhelp_csv(new_export).transactions
    old_rows = parse_selfhelp_csv(old_export).transactions

    old_keys = {row.source_record_key for row in old_rows}
    new_keys = {row.source_record_key for row in new_rows}
    assert old_keys < new_keys  # strict subset: the old file is a prefix
    assert len(new_keys) == 21
    assert len(old_keys) == 19


# --------------------------------------------------------------------------
# The whole file
# --------------------------------------------------------------------------


def test_life_of_account_file_parses_to_the_expected_ledger(tmp_path: Path) -> None:
    result = parse_selfhelp_csv(fx.write_life_of_account(tmp_path))
    rows = result.transactions

    assert result.errors == []
    assert result.skipped == []
    assert len(rows) == 21
    assert min(row.occurred_on for row in rows) == date(2026, 1, 5)
    assert max(row.occurred_on for row in rows) == date(2026, 9, 2)

    # DoD #3 — nothing blank, and 8 of the source rows had no Description.
    assert all(row.description_raw.strip() for row in rows)
    assert sum(1 for row in rows if row.description_raw == row.metadata["ext"]) == 8

    # DoD #4 — the ledger closes on the reported balances.
    assert sum(row.amount for row in rows) == Decimal("-1823.25")
    closing = Decimal(str(fx.OPENING_BALANCE)) + sum(row.amount for row in rows)
    assert closing == Decimal(str(fx.CLOSING_BALANCE))


def test_running_balance_foots_with_zero_breaks(tmp_path: Path) -> None:
    """DoD #5 — Balance[i] == Balance[i-1] + Amount[i], and the file opens at
    5,660.69. A break here means the parser mis-signed or dropped a row."""
    rows = parse_selfhelp_csv(fx.write_life_of_account(tmp_path)).transactions

    previous = fx.OPENING_BALANCE
    breaks = []
    for row in rows:
        expected = previous + float(row.amount)
        actual = row.metadata["running_balance"]
        if abs(actual - expected) > 0.005:
            breaks.append((row.occurred_on.isoformat(), expected, actual))
        previous = actual

    assert breaks == []
    assert abs(previous - fx.CLOSING_BALANCE) < 0.005


def test_prefix_regression_guard(tmp_path: Path) -> None:
    """DoD #6 — the old 19-row export must be a strict prefix of the new one:
    19 rows on/before 2026-08-03 summing -3,118.88."""
    rows = parse_selfhelp_csv(fx.write_life_of_account(tmp_path)).transactions

    through_august_3 = [row for row in rows if row.occurred_on <= date(2026, 8, 3)]
    assert len(through_august_3) == 19
    assert sum(row.amount for row in through_august_3) == Decimal("-3118.88")


def test_two_digit_year_is_an_error_not_a_guess(tmp_path: Path) -> None:
    """No year inference on this account: the export carries no pre-2026 rows
    and no two-digit years, so a malformed date must surface rather than be
    silently assigned a century."""
    csv_path = _write(tmp_path, '1/6/26,"","Share Deposit",,1295,2455.69\n')

    result = parse_selfhelp_csv(csv_path)

    assert result.transactions == []
    assert "unparseable date" in result.errors[0]
