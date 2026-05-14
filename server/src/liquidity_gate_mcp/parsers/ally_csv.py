from __future__ import annotations

import csv
import hashlib
import re
from datetime import date, datetime, time
from decimal import Decimal, InvalidOperation
from pathlib import Path

from ..models import AccountReference, ParsedTransaction
from ._base import ParseResult, SkippedRow, derive_statement_period


PARSER_VERSION = "ally-csv-v1"


AllyParseResult = ParseResult


# Ally HYSA exports are single-account, so the source_key is constant. The
# account number (XXXXXX1081) only appears inside Description text on
# transfer rows; we don't try to parse it out.
ALLY_HYSA_ACCOUNT = AccountReference(
    source_key="acct-ally-hysa",
    institution="Ally",
    account_name="Ally HYSA",
    account_type="savings",
    owner="joint",
)


# Cross-account transfer signatures observed in the Description column.
# A match overrides the default sign-based direction so the row is recorded
# as ``transfer`` and pairs with the partner row on the other account.
#
# - requested\s+transfer.*ally\s+bank\s+transfer : the partner side of
#   Beacon's ``ALLY BANK $TRANSFER`` outflows. Generic enough to match both
#   "JEFFREY A ZYJESKI" and "ASHLEY ..." variants.
# - requested\s+transfer\s+to : Ally-side outbound to a household checking
#   account. Mirrors the inbound pattern above.
#
# "Interest Paid" is intentionally NOT a transfer — it's real income from
# Ally that the master index §8 wants tracked as event income. Outbound
# Zelle to third parties is real spending and stays as outflow.
TRANSFER_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"requested\s+transfer.*ally\s+bank\s+transfer", re.IGNORECASE),
    re.compile(r"requested\s+transfer\s+to\b", re.IGNORECASE),
)


def parse_ally_csv(file_path: Path) -> AllyParseResult:
    """Parse an Ally HYSA CSV into ParsedTransaction records.

    Pure: reads the file, does not mutate it, does not touch the DB. Rows
    with an empty Date or unparseable Date are surfaced as errors, not
    silently dropped — Ally exports do not have a "pending" concept like
    Beacon does.
    """
    result = AllyParseResult()
    document_name = file_path.name
    statement_period = derive_statement_period(file_path.stem)

    with file_path.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        if reader.fieldnames is None:
            result.errors.append("CSV has no header row.")
            return result

        normalized_fields = {_normalize_header(name): name for name in reader.fieldnames}
        required = {"date", "time", "amount", "type", "description"}
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

    time_raw = _get(raw, fields, "time")
    occurred_at = _parse_time(time_raw)
    if occurred_at is None:
        raise _RowError(f"unparseable time {time_raw!r}")

    description = _get(raw, fields, "description")
    if not description:
        raise _RowError("description is empty")

    amount = _parse_amount(_get(raw, fields, "amount"))
    if amount is None:
        raise _RowError("amount is empty or unparseable")

    ally_type = _get(raw, fields, "type")

    direction = _direction_for(amount, description)
    record_key = _source_record_key(occurred_on, occurred_at, amount, description)

    metadata = {
        "ally_type": ally_type,
        "occurred_at_time": occurred_at.isoformat(timespec="seconds"),
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
        account=ALLY_HYSA_ACCOUNT,
    )


def _direction_for(amount: Decimal, description: str) -> str:
    for pattern in TRANSFER_PATTERNS:
        if pattern.search(description):
            return "transfer"
    if amount < 0:
        return "outflow"
    return "inflow"


def _source_record_key(
    occurred_on: date,
    occurred_at: time,
    amount: Decimal,
    description: str,
) -> str:
    # Ally has no running-balance column, so the per-second Time field is
    # the per-row tiebreaker that distinguishes otherwise-identical rows
    # on the same day (e.g. two interest postings, or repeated same-amount
    # transfers). Time is stable across re-exports of the same statement.
    normalized_description = " ".join(description.split())
    payload = (
        f"{occurred_on.isoformat()}|{occurred_at.isoformat(timespec='seconds')}"
        f"|{amount}|{normalized_description}"
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
    # Ally exports US-style M/D/YYYY without zero-padding (e.g. "5/8/2026").
    for fmt in ("%m/%d/%Y", "%Y-%m-%d"):
        try:
            return datetime.strptime(value, fmt).date()
        except ValueError:
            continue
    return None


def _parse_time(value: str) -> time | None:
    if not value:
        return None
    for fmt in ("%H:%M:%S", "%H:%M"):
        try:
            return datetime.strptime(value, fmt).time()
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
