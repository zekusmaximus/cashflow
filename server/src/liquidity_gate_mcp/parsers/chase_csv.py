from __future__ import annotations

import csv
import hashlib
import re
from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path

from ..models import AccountReference, ParsedTransaction


PARSER_VERSION = "chase-csv-v1"

CHASE_ACCOUNT = AccountReference(
    source_key="acct-chase-credit-card",
    institution="Chase",
    account_name="Chase Credit Card",
    account_type="credit_card",
    owner="joint",
)

_DATE_FORMATS = ("%m/%d/%Y", "%m/%d/%y")
_STATEMENT_PERIOD_RE = re.compile(r"(20\d{2})[-_](\d{1,2})")
_PAYMENT_TYPE = "payment"


@dataclass(frozen=True)
class SkippedRow:
    row_index: int
    reason: str
    raw: dict[str, str]


@dataclass
class ChaseParseResult:
    transactions: list[ParsedTransaction] = field(default_factory=list)
    skipped: list[SkippedRow] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)


def parse_chase_csv(file_path: Path) -> ChaseParseResult:
    """Parse a Chase credit-card CSV into ParsedTransaction records.

    Pure: reads the file, does not mutate it, does not touch the DB.
    Pending rows (empty Transaction Date) are silently skipped and surfaced
    in the result's ``skipped`` list with reason ``"pending"``.
    """
    result = ChaseParseResult()
    document_name = file_path.name
    statement_period = _derive_statement_period(file_path.stem)

    with file_path.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        if reader.fieldnames is None:
            result.errors.append("CSV has no header row.")
            return result

        normalized_fields = {_normalize_header(name): name for name in reader.fieldnames}
        required = {"transaction date", "post date", "description", "amount", "type"}
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
    transaction_date_raw = _get(raw, fields, "transaction date")
    if not transaction_date_raw:
        raise _RowSkipped("pending")

    occurred_on = _parse_date(transaction_date_raw)
    if occurred_on is None:
        raise _RowError(f"unparseable transaction date {transaction_date_raw!r}")

    post_date_raw = _get(raw, fields, "post date")
    posted_on = _parse_date(post_date_raw) if post_date_raw else None

    description = _get(raw, fields, "description")
    if not description:
        raise _RowError("description is empty")

    amount = _parse_amount(_get(raw, fields, "amount"))
    if amount is None:
        raise _RowError("amount is empty or unparseable")

    chase_type = _get(raw, fields, "type")
    chase_category = _get(raw, fields, "category") if "category" in fields else ""
    memo = _get(raw, fields, "memo") if "memo" in fields else ""

    direction = _direction_for(amount, chase_type)
    record_key = _source_record_key(occurred_on, amount, description)

    metadata = {
        "chase_category": chase_category,
        "chase_type": chase_type,
        "memo": memo,
    }

    return ParsedTransaction(
        source_record_key=record_key,
        source_document_name=document_name,
        occurred_on=occurred_on,
        posted_on=posted_on,
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
        account=CHASE_ACCOUNT,
    )


def _direction_for(amount: Decimal, chase_type: str) -> str:
    if chase_type.strip().lower() == _PAYMENT_TYPE:
        return "transfer"
    if amount < 0:
        return "outflow"
    return "inflow"


def _source_record_key(occurred_on: date, amount: Decimal, description: str) -> str:
    # Stable across re-imports so the UNIQUE (source_document_name,
    # source_record_key) constraint catches duplicates. Normalize the
    # description to collapse incidental whitespace differences between
    # Chase's CSV export and any later edits.
    normalized_description = " ".join(description.split())
    payload = f"{occurred_on.isoformat()}|{amount}|{normalized_description}"
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]


def _derive_statement_period(stem: str) -> str | None:
    match = _STATEMENT_PERIOD_RE.search(stem)
    if not match:
        return None
    year = match.group(1)
    month = int(match.group(2))
    if not 1 <= month <= 12:
        return None
    return f"{year}-{month:02d}"


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
