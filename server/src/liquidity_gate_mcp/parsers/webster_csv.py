from __future__ import annotations

import csv
import hashlib
import re
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path

from ..models import AccountReference, ParsedTransaction
from ._base import ParseResult, SkippedRow, derive_statement_period


PARSER_VERSION = "webster-csv-v1"


WebsterParseResult = ParseResult


# Webster exports a single account, so the source_key is constant. The account
# number does not appear anywhere in the CSV.
WEBSTER_CHECKING_ACCOUNT = AccountReference(
    source_key="acct-webster-checking",
    institution="Webster",
    account_name="Webster checking",
    account_type="checking",
    owner="ashley",
)


# Cross-account transfer signatures observed in the Description column. A
# match overrides the default sign-based direction so the row is recorded as
# ``transfer`` and pairs with the partner row on the other account.
#
# - chase\s+credit\s+crd\s+epay : Ashley paying her Chase card from Webster.
#   Pairs with Chase Type=Payment rows already tagged transfer by chase_csv.
# - ck\s+transfer.*ashley\s*m\s*calabr : Webster's check-transfer P2P feature
#   moving cash from Ashley's account into the joint Beacon (master index §3
#   classifies Ashley -> joint checking as a household-funding transfer, not
#   income). Will pair once the Beacon parser learns the WEBSTR inbound side.
#
# FID BKG SVC LLC MONEYLINE inbounds (large RSU/stock-plan proceeds) and
# VENMO/PAYPAL outbounds are intentionally not auto-tagged — they need the
# classifier, not the transfer pair detector, to handle correctly. This
# mirrors the Beacon parser's stance.
TRANSFER_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"chase\s+credit\s+crd\s+epay", re.IGNORECASE),
    re.compile(r"ck\s+transfer.*ashley\s*m\s*calabr", re.IGNORECASE),
)


def parse_webster_csv(file_path: Path) -> WebsterParseResult:
    """Parse a Webster checking CSV into ParsedTransaction records.

    Pure: reads the file, does not mutate it, does not touch the DB. Rows
    with an empty Date are surfaced as ``missing_date`` skips; Webster does
    not have a "pending" concept in its export.
    """
    result = WebsterParseResult()
    document_name = file_path.name
    statement_period = derive_statement_period(file_path.stem)

    with file_path.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        if reader.fieldnames is None:
            result.errors.append("CSV has no header row.")
            return result

        normalized_fields = {_normalize_header(name): name for name in reader.fieldnames}
        required = {"date", "type", "description", "debit", "credit", "balance"}
        missing = required - normalized_fields.keys()
        if missing:
            result.errors.append(
                f"CSV missing required columns: {', '.join(sorted(missing))}."
            )
            return result

        for row_index, raw in enumerate(reader, start=2):  # header is line 1
            try:
                parsed = _parse_row(
                    raw,
                    normalized_fields,
                    document_name=document_name,
                    statement_period=statement_period,
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
) -> ParsedTransaction:
    date_raw = _get(raw, fields, "date")
    if not date_raw:
        raise _RowSkipped("missing_date")

    occurred_on = _parse_date(date_raw)
    if occurred_on is None:
        raise _RowError(f"unparseable date {date_raw!r}")

    description = _get(raw, fields, "description")
    if not description:
        raise _RowError("description is empty")

    debit_raw = _get(raw, fields, "debit")
    credit_raw = _get(raw, fields, "credit")
    amount = _amount_from_columns(debit_raw, credit_raw)
    if amount is None:
        raise _RowError("debit and credit are both empty or unparseable")

    webster_type = _get(raw, fields, "type")
    reference_no = _get(raw, fields, "referenceno.") if "referenceno." in fields else ""
    check_number = _get(raw, fields, "checknumber") if "checknumber" in fields else ""

    running_balance = _parse_amount(_get(raw, fields, "balance"))
    if running_balance is None:
        raise _RowError("balance is empty or unparseable")

    direction = _direction_for(amount, description)
    record_key = _source_record_key(occurred_on, amount, description, running_balance)

    metadata = {
        "webster_type": webster_type,
        "reference_no": reference_no,
        "check_number": check_number,
        "running_balance": float(running_balance),
    }

    return ParsedTransaction(
        source_record_key=record_key,
        source_document_name=document_name,
        occurred_on=occurred_on,
        posted_on=occurred_on,
        description_raw=description,
        merchant_normalized=None,
        amount=amount,
        direction=direction,
        primary_category="unclassified",
        subcategory=None,
        household_role="ashley",
        lifecycle="recurring",
        transfer_group_key=None,
        statement_period=statement_period,
        metadata=metadata,
        account=WEBSTER_CHECKING_ACCOUNT,
    )


def _amount_from_columns(debit_raw: str, credit_raw: str) -> Decimal | None:
    # Webster exports debits as already-signed negative numbers in the Debit
    # column and credits as positive numbers in the Credit column. Exactly
    # one of the two is populated per row.
    debit = _parse_amount(debit_raw)
    credit = _parse_amount(credit_raw)
    if debit is not None and credit is not None:
        # Defensive: both columns set is malformed. Prefer the non-zero one.
        if debit != 0 and credit == 0:
            return debit
        if credit != 0 and debit == 0:
            return credit
        return None
    if debit is not None:
        return debit
    if credit is not None:
        return credit
    return None


def _direction_for(amount: Decimal, description: str) -> str:
    for pattern in TRANSFER_PATTERNS:
        if pattern.search(description):
            return "transfer"
    if amount < 0:
        return "outflow"
    return "inflow"


def _source_record_key(
    occurred_on: date,
    amount: Decimal,
    description: str,
    running_balance: Decimal,
) -> str:
    # Webster exports a running balance column. Same rationale as Beacon: it
    # disambiguates same-day same-amount rows (e.g. five $5K CK TRANSFER rows
    # on 1/21/2026) and is stable across re-exports.
    normalized_description = " ".join(description.split())
    payload = (
        f"{occurred_on.isoformat()}|{amount}|{normalized_description}|{running_balance}"
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]


def _normalize_header(name: str) -> str:
    return name.strip().lower()


def _get(row: dict[str, str], fields: dict[str, str], key: str) -> str:
    column = fields.get(key)
    if column is None:
        return ""
    value = row.get(column, "")
    return value.strip() if value else ""


def _parse_date(value: str) -> date | None:
    if not value:
        return None
    # Webster exports US-style M/D/YYYY without zero-padding (e.g. "1/9/2026").
    for fmt in ("%m/%d/%Y", "%Y-%m-%d"):
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
