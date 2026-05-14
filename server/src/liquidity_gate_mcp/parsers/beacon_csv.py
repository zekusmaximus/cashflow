from __future__ import annotations

import csv
import hashlib
import re
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path

from ..models import AccountReference, ParsedTransaction
from ._base import ParseResult, SkippedRow, derive_statement_period


PARSER_VERSION = "beacon-csv-v1"


BeaconParseResult = ParseResult


# Known cross-account transfer signatures observed in the joint-checking
# Raw Description column. Each pattern is matched case-insensitively against
# the Raw Description field. A match overrides the default sign-based
# direction so the row is recorded as ``transfer`` and does not double-count
# against spend already captured on another account.
#
# - chase\s+credit : payments made from checking to the Chase card.
# - ally\s+bank\s*\$?transfer : sweeps from checking to the Ally HYSA.
# - ally\s+bank\s+p2p : Ally-initiated P2P pulls from joint checking back
#   into the Ally HYSA. Same flow as $TRANSFER but Ally tags the originating
#   leg differently when it's initiated as a P2P request rather than a
#   standing transfer.
# - ionbank\s+online\s+xfr : internal IonBank-platform transfers between
#   household accounts.
#
# Other suspected transfer patterns (Webster P2P from Ashley, Fidelity
# MoneyLine sweeps, Venmo cash-out) are intentionally not in this list yet —
# they are flagged for human review rather than auto-classified.
TRANSFER_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"chase\s+credit", re.IGNORECASE),
    re.compile(r"ally\s+bank\s*\$?transfer", re.IGNORECASE),
    re.compile(r"ally\s+bank\s+p2p", re.IGNORECASE),
    re.compile(r"ionbank\s+online\s+xfr", re.IGNORECASE),
)


def parse_beacon_csv(file_path: Path) -> BeaconParseResult:
    """Parse a Beacon joint-checking CSV into ParsedTransaction records.

    Pure: reads the file, does not mutate it, does not touch the DB. Rows
    with an empty Posted Date are skipped and surfaced in ``skipped`` with
    reason ``"pending"``.
    """
    result = BeaconParseResult()
    document_name = file_path.name
    statement_period = derive_statement_period(file_path.stem)

    with file_path.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        if reader.fieldnames is None:
            result.errors.append("CSV has no header row.")
            return result

        normalized_fields = {_normalize_header(name): name for name in reader.fieldnames}
        required = {
            "posted date",
            "type",
            "description",
            "amount",
            "account",
            "balance",
            "raw description",
        }
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
    posted_date_raw = _get(raw, fields, "posted date")
    if not posted_date_raw:
        raise _RowSkipped("pending")

    occurred_on = _parse_date(posted_date_raw)
    if occurred_on is None:
        raise _RowError(f"unparseable posted date {posted_date_raw!r}")

    raw_description = _get(raw, fields, "raw description")
    if not raw_description:
        raise _RowError("raw description is empty")

    amount = _parse_amount(_get(raw, fields, "amount"))
    if amount is None:
        raise _RowError("amount is empty or unparseable")

    beacon_type = _get(raw, fields, "type")
    short_description = _get(raw, fields, "description")
    account_code = _get(raw, fields, "account")
    if not account_code:
        raise _RowError("account is empty")
    notes = _get(raw, fields, "notes") if "notes" in fields else ""

    running_balance = _parse_amount(_get(raw, fields, "balance"))
    if running_balance is None:
        raise _RowError("balance is empty or unparseable")

    direction = _direction_for(amount, raw_description)
    record_key = _source_record_key(
        occurred_on, amount, raw_description, running_balance
    )

    metadata = {
        "beacon_type": beacon_type,
        "short_description": short_description,
        "running_balance": float(running_balance),
        "notes": notes,
    }

    return ParsedTransaction(
        source_record_key=record_key,
        source_document_name=document_name,
        occurred_on=occurred_on,
        posted_on=occurred_on,
        description_raw=raw_description,
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
        account=_account_for(account_code),
    )


def _direction_for(amount: Decimal, raw_description: str) -> str:
    for pattern in TRANSFER_PATTERNS:
        if pattern.search(raw_description):
            return "transfer"
    if amount < 0:
        return "outflow"
    return "inflow"


def _account_for(account_code: str) -> AccountReference:
    return AccountReference(
        source_key=f"acct-beacon-{account_code.lower()}",
        institution="Beacon",
        account_name="Beacon checking",
        account_type="checking",
        owner="joint",
    )


def _source_record_key(
    occurred_on: date,
    amount: Decimal,
    raw_description: str,
    running_balance: Decimal,
) -> str:
    # The running balance is included because Beacon emits multiple rows per
    # day with otherwise identical (date, amount, raw_description) — most
    # notably back-to-back Fidelity MoneyLine $15 sweeps. The running balance
    # changes monotonically between rows in a statement and is stable across
    # re-imports of the same export, so it gives a deterministic per-row
    # tiebreaker that preserves idempotency.
    normalized_description = " ".join(raw_description.split())
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
    # Beacon exports ISO-8601 with a timezone offset (e.g.
    # "2026-05-12T04:00:00+00:00"). fromisoformat handles that on 3.11+,
    # including a trailing "Z".
    candidate = value.replace("Z", "+00:00")
    try:
        return datetime.fromisoformat(candidate).date()
    except ValueError:
        pass
    # Defensive fallback for bare-date exports.
    try:
        return datetime.strptime(value, "%Y-%m-%d").date()
    except ValueError:
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
