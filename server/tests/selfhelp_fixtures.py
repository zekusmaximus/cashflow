"""Self-Help FCU export fixtures shared by the parser and ingestion tests.

WHERE THESE NUMBERS COME FROM
-----------------------------
The live export
``~/Documents/Cashflow/SelfHelp_FCU_Rental_Transactions_LifeOfAccount.csv``
is not in the repo (it holds real account data and the watch root is outside
the tree). The 21 rows below are reconstructed from the verbatim sample rows
in the ingestion handoff plus its stated invariants, and the reconstruction is
fully determined by them:

* 21 rows, 2026-01-05 .. 2026-09-02; opening balance 5,660.69, closing 3,837.44
* SUM(amount) = -1,823.25; rows on/before 2026-08-03: 19 rows summing -3,118.88
* 9 rent rows at 1,295.00 (1/6, 2/6, 3/5, 4/1, 5/5, 6/4, 7/7, 8/3, 9/2)
* 5 dividends summing 3.44 (0.46, 1.04, 0.67, 0.64, 0.63)
* 4 ``ALLY BANK $TRANSFER`` sweeps summing -13,481.69, matching the Ally-side
  legs of 4,500.00 (1/5), 5,080.00 (4/8), 1,000.00 (5/8) and 2,901.69 (7/21)
* 3 ``ALLY BANK ACCTVERIFY`` rows on 7/15 netting 0.00 (+0.60, +0.46, -1.06)
* 8 rows with an empty ``Description``: all 5 ``Share Deposit`` rows and 3 of
  the 4 ``Share Deposit Transfer`` rows
* the running balance foots with zero breaks

Every published sample row in the handoff is reproduced here byte-for-byte,
and the chain closes on all of the above without slack. TWO VALUES ARE
INFERRED, because the handoff gives only the Ally side of those legs: the
Self-Help dates of the April and May sweeps, taken as same-day with the Ally
receipt (2026-04-08 and 2026-05-08) on the pattern of the 2026-01-05 leg,
which is same-day on both sides. The handoff singles out July (7/17 -> 7/21,
four days) as the only leg wider than the old three-day tolerance, which is
consistent with the other two being same-day or close. If the real file
differs on those two dates, only the pairing date-delta assertions move; the
totals, balances, classifications and record keys do not.

The old export (``2026_YTD_Jeff_SelfHelpFCU_Transactions.csv``, 19 rows) is a
strict prefix of the new one that differs ONLY in that its ACH ids are
unmasked. That difference is the whole point of the cross-export idempotency
test: a description-derived source_record_key would hash the two spellings
differently and double-book every masked row.
"""

from __future__ import annotations

from pathlib import Path


HEADER = "Date,Description,Ext,Draft,Amount,Balance\n"

_APYE_TERM = (
    "TERM:   90 DAYS  AVERAGE DAILY BALANCE:     {balance}   APYE:   .05"
)
_APYE_SHORT = "Annual Percentage Yield Earned:      .05"


def _sweep(masked: bool, tail: str) -> str:
    ident = "*********** **" + tail if masked else "16101559175 26" + tail
    return f"ALLY BANK $TRANSFER ID:{ident} S"


def _rows(masked: bool) -> list[tuple[str, str, str, str, str]]:
    """(date, description, ext, amount, balance) in file order."""
    return [
        ("1/5/2026", _sweep(masked, "0105"), "ACH Share Withdrawal", "-4500", "1160.69"),
        ("1/6/2026", "", "Share Deposit Transfer", "1295", "2455.69"),
        (
            "2/6/2026",
            "TRANSFER MADE BY SHARON DRAKE HUFF",
            "Share Deposit Transfer",
            "1295",
            "3750.69",
        ),
        ("3/5/2026", "", "Share Deposit Transfer", "1295", "5045.69"),
        (
            "3/31/2026",
            _APYE_TERM.format(balance="3,756.08"),
            "Dividend",
            ".46",
            "5046.15",
        ),
        ("4/1/2026", "", "Share Deposit Transfer", "1295", "6341.15"),
        ("4/8/2026", _sweep(masked, "0408"), "ACH Share Withdrawal", "-5080", "1261.15"),
        ("5/5/2026", "", "Share Deposit", "1295", "2556.15"),
        ("5/8/2026", _sweep(masked, "0508"), "ACH Share Withdrawal", "-1000", "1556.15"),
        (
            "5/31/2026",
            _APYE_TERM.format(balance="1,556.15"),
            "Dividend",
            "1.04",
            "1557.19",
        ),
        ("6/4/2026", "", "Share Deposit", "1295", "2852.19"),
        ("6/30/2026", _APYE_SHORT, "Dividend", ".67", "2852.86"),
        ("7/7/2026", "", "Share Deposit", "1295", "4147.86"),
        ("7/15/2026", "ALLY BANK ACCTVERIFY", "ACH Credit", ".6", "4148.46"),
        ("7/15/2026", "ALLY BANK ACCTVERIFY", "ACH Credit", ".46", "4148.92"),
        ("7/15/2026", "ALLY BANK ACCTVERIFY", "ACH Share Withdrawal", "-1.06", "4147.86"),
        (
            "7/17/2026",
            _sweep(masked, "0717"),
            "ACH Share Withdrawal",
            "-2901.69",
            "1246.17",
        ),
        ("7/31/2026", _APYE_SHORT, "Dividend", ".64", "1246.81"),
        ("8/3/2026", "", "Share Deposit", "1295", "2541.81"),
        ("8/31/2026", _APYE_SHORT, "Dividend", ".63", "2542.44"),
        ("9/2/2026", "", "Share Deposit", "1295", "3837.44"),
    ]


# The 19-row prefix ends at the 2026-08-03 rent deposit.
_PREFIX_ROW_COUNT = 19

# Opening balance the file's first row chains from: Balance[0] - Amount[0].
OPENING_BALANCE = 5660.69
CLOSING_BALANCE = 3837.44


def _render(rows: list[tuple[str, str, str, str, str]]) -> str:
    out = [HEADER]
    for occurred_on, description, ext, amount, balance in rows:
        out.append(
            f'{occurred_on},"{description}","{ext}",,{amount},{balance}\n'
        )
    return "".join(out)


def life_of_account_csv() -> str:
    """The current 21-row export, ACH ids masked."""
    return _render(_rows(masked=True))


def ytd_prefix_csv() -> str:
    """The superseded 19-row export, ACH ids unmasked. A strict prefix."""
    return _render(_rows(masked=False)[:_PREFIX_ROW_COUNT])


def write_life_of_account(directory: Path) -> Path:
    path = directory / "SelfHelp_FCU_Rental_Transactions_LifeOfAccount.csv"
    path.write_text(life_of_account_csv(), encoding="utf-8")
    return path


def write_ytd_prefix(directory: Path) -> Path:
    path = directory / "2026_YTD_Jeff_SelfHelpFCU_Transactions.csv"
    path.write_text(ytd_prefix_csv(), encoding="utf-8")
    return path
