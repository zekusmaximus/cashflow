from __future__ import annotations

import csv
import hashlib
import re
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path

from ..models import AccountReference, ParsedTransaction
from ._base import ParseResult, SkippedRow, derive_statement_period


PARSER_VERSION = "selfhelp-csv-v1"


SelfHelpParseResult = ParseResult


# Self-Help FCU savings account that receives the 140 Kane D5 rent and is
# swept to Ally. Single-account export, so the source_key is constant.
SELFHELP_SAVINGS_ACCOUNT = AccountReference(
    source_key="acct-selfhelp-savings",
    institution="Self-Help",
    account_name="Self-Help FCU (rental)",
    account_type="savings",
    owner="jeff",
)


# Columns: Date,Description,Ext,Draft,Amount,Balance
_REQUIRED_COLUMNS = frozenset({"date", "description", "ext", "amount", "balance"})

# Four-digit years only. This export has no pre-2026 rows and no two-digit
# years, and a "%m/%d/%y" fallback would quietly *infer* a century for a
# malformed date rather than surfacing it — exactly the year-guessing this
# account must not do. An unparseable date becomes a row error instead.
_DATE_FORMATS = ("%m/%d/%Y", "%Y-%m-%d")


# Cross-account transfer signatures. Both live in the Description column on
# this export.
#
# - ``ALLY BANK $TRANSFER`` : the sweep legs that pair with Ally's
#   "Requested transfer from ..." inbounds. The ACH id that trails the text
#   is masked in newer exports ("ID:*********** **0105 S") and unmasked in
#   older ones ("ID:16101559175 260105 S"), so the pattern deliberately stops
#   at the "$TRANSFER" token and never looks at the id.
# - ``ALLY BANK ACCTVERIFY`` : Ally's micro-deposit account-verification
#   probes (+0.60/+0.46 in, -1.06 back out). They net to zero and are not
#   household income, so they are transfers, not inflows.
#
# Dividends and rent deposits are deliberately NOT transfers — they are real
# income on this account (see the seeded selfhelp classification rules).
_TRANSFER_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"ally\s+bank\s+\$?\s*transfer", re.IGNORECASE),
    re.compile(r"ally\s+bank\s+acctverify", re.IGNORECASE),
)


def parse_selfhelp_csv(file_path: Path) -> SelfHelpParseResult:
    """Parse a Self-Help FCU savings CSV into ParsedTransaction records.

    Pure: reads the file, does not mutate it, does not touch the DB.

    THE FORMAT'S ONE TRAP: ``Description`` is empty on most rent-deposit
    rows — the transaction type lives in the ``Ext`` column
    ("Share Deposit", "Share Deposit Transfer", "ACH Share Withdrawal",
    "Dividend", "ACH Credit"). In the life-of-account export 8 of 21 rows
    have a blank ``Description``, and all nine $1,295 rent deposits are
    identified by ``Ext`` alone. A Description-only parser therefore lands
    eight blank-description rows that match no classification rule and
    violate ``transactions.description_raw NOT NULL`` in spirit if not in
    letter — the failure that looks like success.

    So ``description_raw`` is built as ``"<Description> | <Ext>"`` with
    ``Ext`` always present, and the raw ``Ext`` value is preserved in
    metadata as ``{"ext": "..."}`` so rules can filter on it exactly rather
    than by substring-matching the joined text.
    """
    result = SelfHelpParseResult()
    document_name = file_path.name
    statement_period = derive_statement_period(file_path.stem)

    with file_path.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        if reader.fieldnames is None:
            result.errors.append("CSV has no header row.")
            return result

        normalized_fields = {_normalize_header(name): name for name in reader.fieldnames}
        missing = _REQUIRED_COLUMNS - normalized_fields.keys()
        if missing:
            result.errors.append(
                f"CSV missing required columns: {', '.join(sorted(missing))}."
            )
            return result

        duplicate_counts: dict[str, int] = {}
        for row_index, raw in enumerate(reader, start=2):  # header is line 1
            try:
                parsed = _parse_row(
                    raw,
                    normalized_fields,
                    document_name=document_name,
                    statement_period=statement_period,
                    duplicate_counts=duplicate_counts,
                )
            except _RowSkipped as skipped:
                result.skipped.append(
                    SkippedRow(row_index=row_index, reason=skipped.reason, raw=dict(raw))
                )
                continue
            except _RowError as err:
                result.errors.append(f"Row {row_index}: {err.message}")
                continue

            result.transactions.append(parsed)

    return result


def _parse_row(
    raw: dict[str, str],
    fields: dict[str, str],
    *,
    document_name: str,
    statement_period: str | None,
    duplicate_counts: dict[str, int],
) -> ParsedTransaction:
    date_raw = _get(raw, fields, "date")
    if not date_raw:
        raise _RowSkipped("missing_date")

    occurred_on = _parse_date(date_raw)
    if occurred_on is None:
        raise _RowError(f"unparseable date {date_raw!r}")

    description = _get(raw, fields, "description")
    ext = _get(raw, fields, "ext")
    description_raw = build_description(description, ext)
    if not description_raw:
        # Neither column carried text. Surfacing this as an error (rather
        # than writing an empty string) keeps description_raw meaningful:
        # a blank row cannot be classified and must be looked at by hand.
        raise _RowError("row has neither a Description nor an Ext value")

    amount = _parse_amount(_get(raw, fields, "amount"))
    if amount is None:
        raise _RowError("amount is empty or unparseable")

    running_balance = _parse_amount(_get(raw, fields, "balance"))
    if running_balance is None:
        raise _RowError("balance is empty or unparseable")

    # Self-Help's Draft column carries the share-draft (check) number. It is
    # empty on every row of the rental account's export; map it through when
    # present rather than requiring it.
    draft = _get(raw, fields, "draft") if "draft" in fields else ""

    direction = _direction_for(amount, description_raw)
    base_payload = _base_payload(occurred_on, amount, running_balance)
    duplicate_index = duplicate_counts.get(base_payload, 0)
    duplicate_counts[base_payload] = duplicate_index + 1
    record_key = _source_record_key(base_payload, duplicate_index)

    metadata = {
        "ext": ext,
        "draft": draft,
        "running_balance": float(running_balance),
    }

    return ParsedTransaction(
        source_record_key=record_key,
        source_document_name=document_name,
        occurred_on=occurred_on,
        posted_on=occurred_on,
        description_raw=description_raw,
        merchant_normalized=None,
        amount=amount,
        direction=direction,
        primary_category="unclassified",
        subcategory=None,
        # The rental property and this account are Jeff's, not joint.
        household_role="jeff",
        lifecycle="recurring",
        transfer_group_key=None,
        statement_period=statement_period,
        metadata=metadata,
        account=SELFHELP_SAVINGS_ACCOUNT,
    )


def build_description(description: str, ext: str) -> str:
    """Join ``Description`` and ``Ext`` so ``Ext`` is never lost.

    ``"" + "Share Deposit Transfer"`` -> ``"Share Deposit Transfer"``
    ``"TRANSFER MADE BY SHARON DRAKE HUFF" + "Share Deposit Transfer"``
    -> ``"TRANSFER MADE BY SHARON DRAKE HUFF | Share Deposit Transfer"``

    Exported to keep the rule-writing contract in one place: every
    classification rule for this account matches against exactly this text.
    """
    return f"{description} | {ext}".strip(" |")


def _direction_for(amount: Decimal, description_raw: str) -> str:
    for pattern in _TRANSFER_PATTERNS:
        if pattern.search(description_raw):
            return "transfer"
    # Self-Help's sign convention already matches the repo's: withdrawals
    # are exported negative, deposits positive. No flipping needed.
    if amount < 0:
        return "outflow"
    return "inflow"


def _base_payload(
    occurred_on: date, amount: Decimal, running_balance: Decimal
) -> str:
    # DELIBERATELY DESCRIPTION-FREE. The same transactions are described
    # differently across exports — the 2026-08 export carries unmasked ACH
    # ids ("ALLY BANK $TRANSFER ID:16101559175 260105 S") while the current
    # one masks them ("ID:*********** **0105 S"). A description-derived key
    # would hash to different values for the same row, defeating the
    # UNIQUE (account_id, source_record_key) dedupe and double-booking every
    # masked row on re-export.
    #
    # Date + amount + running balance is stable across both exports and is
    # unique on its own: a running balance advances by exactly `amount` on
    # every row, so two rows on the same day cannot share both.
    return f"{occurred_on.isoformat()}|{amount}|{running_balance}"


def _source_record_key(base_payload: str, duplicate_index: int) -> str:
    # Belt-and-braces ordinal tiebreaker, mirroring the Chase/Citi scheme:
    # the first occurrence of a payload keeps the plain hash so re-imports
    # continue to dedupe, and an exact repeat (only reachable via a $0 row)
    # gets a |seqN suffix instead of colliding.
    payload = (
        base_payload if duplicate_index == 0 else f"{base_payload}|seq{duplicate_index}"
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]


def _normalize_header(name: str) -> str:
    return name.strip().lower().lstrip("﻿")


def _get(row: dict[str, str], fields: dict[str, str], key: str) -> str:
    column = fields.get(key)
    if column is None:
        return ""
    value = row.get(column, "")
    return value.strip() if value else ""


def _parse_date(value: str) -> date | None:
    if not value:
        return None
    # Self-Help exports US-style M/D/YYYY with no zero padding ("1/5/2026").
    for fmt in _DATE_FORMATS:
        try:
            return datetime.strptime(value, fmt).date()
        except ValueError:
            continue
    return None


def _parse_amount(value: str) -> Decimal | None:
    if not value:
        return None
    cleaned = value.replace(",", "").replace("$", "").strip()
    if not cleaned:
        return None
    # Bare-leading-decimal values are normal here (".46", ".6", "-.05").
    # Decimal handles them directly; int()/float() rounding is never used.
    try:
        return Decimal(cleaned)
    except InvalidOperation:
        return None


class _RowSkipped(Exception):
    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


class _RowError(Exception):
    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message
