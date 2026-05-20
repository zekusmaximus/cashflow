from __future__ import annotations

import csv
import hashlib
import re
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path

from ..models import AccountReference, ParsedTransaction
from ._base import ParseResult, SkippedRow, derive_statement_period


PARSER_VERSION = "citi-csv-v1"

CITI_ACCOUNT = AccountReference(
    source_key="acct-citi-credit-card",
    institution="Citi",
    account_name="Citi Credit Card",
    account_type="credit_card",
    owner="joint",
)

_DATE_FORMATS = ("%m/%d/%Y", "%m/%d/%y", "%Y-%m-%d")

# Citi's AUTOPAY rows correspond to the household-funded credit-card
# payment from Beacon checking. Treat them as transfers so they do not
# count as inflows on the card and instead pair with Beacon outbounds.
_AUTOPAY_PATTERN = re.compile(r"\bautopay\b|\bauto[-\s]?pmt\b", re.IGNORECASE)

# Rows that have not yet cleared. Citi marks them "Pending" or "Posted".
# Anything that is not explicitly cleared/posted is skipped.
_CLEARED_STATUSES = frozenset({"cleared", "posted"})


CitiParseResult = ParseResult


def parse_citi_csv(file_path: Path) -> CitiParseResult:
    """Parse a Citi credit-card CSV into ParsedTransaction records.

    Pure: reads the file, does not mutate it, does not touch the DB.

    Citi exports two signed columns - ``Debit`` (positive = card charge,
    i.e. an outflow that increases the balance owed) and ``Credit``
    (negative = payment/refund credited to the card, i.e. an inflow that
    decreases the balance owed). The parser converts that into the
    standard repo convention: outflows stored as a negative ``amount``,
    inflows/payments stored as a positive ``amount``. AUTOPAY rows are
    flagged ``direction='transfer'`` so they pair with the Beacon-side
    "CITI AUTOPAY PAYMENT" outbounds and are excluded from spending.
    """
    result = CitiParseResult()
    document_name = file_path.name
    statement_period = derive_statement_period(file_path.stem)

    with file_path.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        if reader.fieldnames is None:
            result.errors.append("CSV has no header row.")
            return result

        normalized_fields = {_normalize_header(name): name for name in reader.fieldnames}
        required = {"status", "date", "description", "debit", "credit"}
        missing = required - normalized_fields.keys()
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
    status = _get(raw, fields, "status").lower()
    if not status:
        raise _RowSkipped("pending")
    if status not in _CLEARED_STATUSES:
        raise _RowSkipped("pending")

    date_raw = _get(raw, fields, "date")
    if not date_raw:
        raise _RowSkipped("pending")

    occurred_on = _parse_date(date_raw)
    if occurred_on is None:
        raise _RowError(f"unparseable date {date_raw!r}")

    description = _get(raw, fields, "description")
    if not description:
        raise _RowError("description is empty")

    debit = _parse_amount(_get(raw, fields, "debit"))
    credit = _parse_amount(_get(raw, fields, "credit"))

    if debit is None and credit is None:
        raise _RowError("both debit and credit are empty or unparseable")
    if debit is not None and credit is not None:
        raise _RowError("both debit and credit are populated; ambiguous row")

    amount, direction = _amount_and_direction(debit, credit, description)
    base_payload = _base_payload(occurred_on, amount, description)
    duplicate_index = duplicate_counts.get(base_payload, 0)
    duplicate_counts[base_payload] = duplicate_index + 1
    record_key = _source_record_key(base_payload, duplicate_index)

    metadata = {
        "citi_status": _get(raw, fields, "status"),
        "citi_debit_raw": _get(raw, fields, "debit"),
        "citi_credit_raw": _get(raw, fields, "credit"),
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
        household_role="joint",
        lifecycle="recurring",
        transfer_group_key=None,
        statement_period=statement_period,
        metadata=metadata,
        account=CITI_ACCOUNT,
    )


def _amount_and_direction(
    debit: Decimal | None, credit: Decimal | None, description: str
) -> tuple[Decimal, str]:
    if debit is not None:
        # A charge on the card: balance increases, household outflow.
        # Repo convention stores card outflows as negative amount.
        signed = debit if debit < 0 else -debit
        return signed, "outflow"

    assert credit is not None  # narrowed by caller
    # A payment / refund credited to the card: balance decreases.
    # Citi exports these as negative numbers; the repo stores card
    # inflows as a positive amount. Flip the sign so the magnitude is
    # the dollars credited.
    signed = credit if credit > 0 else -credit
    if _AUTOPAY_PATTERN.search(description):
        return signed, "transfer"
    return signed, "inflow"


def _base_payload(occurred_on: date, amount: Decimal, description: str) -> str:
    normalized_description = " ".join(description.split())
    return f"{occurred_on.isoformat()}|{amount}|{normalized_description}"


def _source_record_key(base_payload: str, duplicate_index: int) -> str:
    # Mirrors the Chase parser's idempotent-with-tiebreaker scheme. The
    # first occurrence of any (date, amount, description) tuple keeps the
    # plain hash so re-imports continue to dedupe; subsequent matches in
    # the same file get a |seqN suffix to disambiguate genuine duplicates
    # (e.g. two same-day micro-charges from a single merchant).
    payload = base_payload if duplicate_index == 0 else f"{base_payload}|seq{duplicate_index}"
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
