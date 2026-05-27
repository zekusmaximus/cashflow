"""Check-register and mobile-deposit-ledger ingestion.

These two MCP tools let the analyst attach payee + category information to
outbound paper checks and incoming mobile check deposits — neither of which
carries useful description data in the raw CSV exports. Both tools read a
CSV from the watch root, match each row against an existing transaction,
and write a `transaction_overrides` row via the same upsert path the UI
uses, so overrides survive re-imports.

The match strategies are intentionally different:

* **Check register** — outbound checks. Each transaction's `description_raw`
  is literally ``CHECK# <n>``; the register supplies the check number, so we
  match by ``(account_id, ABS(amount), regex on description)``. A fallback
  match by ``(account_id, ABS(amount), posted_on ± 10 days)`` handles
  formatting drift; ambiguous fallbacks are surfaced for manual triage.
* **Deposit ledger** — incoming mobile check deposits. All raw rows read
  ``MOBILE CHECK DEP``, so there's no per-check identifier to lean on.
  We match by ``(account_id, amount, occurred_on ± 3 days)``; ambiguity is
  resolved by user-supplied date precision rather than description content.

Both tools call ``upsert_transaction_override`` for each match, so the row
fanout (multiple transactions sharing one match_key all update) is
preserved automatically. We do **not** trigger ``apply_classifier`` after
the import — the upsert already materializes the new columns on the matched
rows and stamps ``metadata_json.manual_override_applied=true``, which
shields them from a future ``reclassify_all`` pass.
"""

from __future__ import annotations

import csv
import re
from datetime import date, timedelta
from pathlib import Path
from typing import Iterable

from .database import DatabaseManager
from .models import (
    CheckDepositDuplicate,
    CheckDepositUnmatched,
    CheckRegisterDuplicate,
    CheckRegisterRowError,
    CheckRegisterUnmatched,
    IngestCheckDepositLedgerRequest,
    IngestCheckDepositLedgerResult,
    IngestCheckRegisterRequest,
    IngestCheckRegisterResult,
    UpsertTransactionOverrideRequest,
)


CHECK_REGISTER_REQUIRED_COLUMNS = (
    "account",
    "check_number",
    "date_written",
    "amount",
    "payee",
)
CHECK_REGISTER_OPTIONAL_COLUMNS = (
    "primary_category",
    "subcategory",
    "lifecycle",
    "household_role",
    "notes",
)

DEPOSIT_LEDGER_REQUIRED_COLUMNS = (
    "account",
    "date_deposited",
    "amount",
    "source",
)
DEPOSIT_LEDGER_OPTIONAL_COLUMNS = (
    "primary_category",
    "subcategory",
    "lifecycle",
    "household_role",
    "notes",
)

DEFAULT_CHECK_REGISTER_FILENAME = "check_register.csv"
DEFAULT_DEPOSIT_LEDGER_FILENAME = "check_deposits.csv"

# `description_raw` for paper checks looks like "CHECK# 209" across all
# observed accounts (Beacon, Webster). Leading zeros on the number are
# tolerated. The trailing-end anchor keeps us from accidentally matching
# memo lines that contain the literal "CHECK# 209 to ACME".
_CHECK_DESC_TEMPLATE = r"^\s*CHECK#\s*0*{n}\s*$"

# Mobile check deposit descriptions vary slightly across feeds; this
# catches the canonical Beacon form plus minor whitespace/punctuation drift.
_MOBILE_DEPOSIT_DESCRIPTION_RE = re.compile(
    r"^\s*MOBILE\s+CHECK\s+DEP(?:OSIT)?\s*$", re.IGNORECASE
)

CHECK_FALLBACK_DAYS = 10
DEPOSIT_DATE_TOLERANCE_DAYS = 3


def _resolve_path(supplied: str | None, watch_root: Path, default_name: str) -> Path:
    if supplied:
        return Path(supplied).expanduser()
    return watch_root / default_name


def _read_csv_rows(path: Path) -> list[dict[str, str]]:
    """Read a CSV, dropping comment lines that start with ``#``.

    Comment-only lines may appear before or interleaved with data so the
    template can ship example rows commented out. ``csv.DictReader`` handles
    the header on the first non-comment line.
    """
    with path.open("r", encoding="utf-8", newline="") as handle:
        non_comment_lines = [line for line in handle if not line.lstrip().startswith("#")]
    reader = csv.DictReader(non_comment_lines)
    return [dict(row) for row in reader]


def _validate_columns(
    actual: Iterable[str], required: tuple[str, ...]
) -> list[str]:
    actual_set = {col.strip().lower() for col in actual}
    return [col for col in required if col not in actual_set]


def _normalize_row(row: dict[str, str]) -> dict[str, str]:
    return {(k or "").strip().lower(): (v or "").strip() for k, v in row.items()}


def _resolve_account(
    database: DatabaseManager, account_label: str
) -> tuple[str | None, list[tuple[str, str]]]:
    """Resolve a free-form account label to an ``accounts.id``.

    Tries exact id match first, then case-insensitive ``account_name`` match.
    Returns ``(account_id, all_known_accounts)``; the all-known list is used
    to build a helpful error message when nothing matches.
    """
    with database.connect(read_only=True) as conn:
        rows = conn.execute(
            "SELECT id, account_name FROM accounts ORDER BY account_name"
        ).fetchall()
    all_known = [(r["id"], r["account_name"]) for r in rows]
    label = account_label.strip()
    label_lower = label.lower()
    for acct_id, name in all_known:
        if acct_id == label or name.strip().lower() == label_lower:
            return acct_id, all_known
    return None, all_known


def _parse_amount(raw: str) -> float:
    cleaned = raw.replace("$", "").replace(",", "").strip()
    return float(cleaned)


def _parse_date(raw: str) -> date:
    return date.fromisoformat(raw.strip())


def _already_user_authoritative(
    database: DatabaseManager, account_id: str, occurred_on: str, amount: float
) -> bool:
    """Detect whether a transaction at this triple already carries an override
    from a non-register source (typically the classifier). Used only to count
    superseded overrides for the result summary; we still apply the new one.
    """
    from .database import transaction_override_match_key

    with database.connect(read_only=True) as conn:
        row = conn.execute(
            "SELECT description_raw FROM transactions "
            "WHERE account_id = ? AND occurred_on = ? AND amount = ? LIMIT 1",
            (account_id, occurred_on, amount),
        ).fetchone()
        if row is None:
            return False
        key = transaction_override_match_key(
            account_id, occurred_on, amount, row["description_raw"]
        )
        existing = conn.execute(
            "SELECT 1 FROM transaction_overrides WHERE match_key = ?",
            (key,),
        ).fetchone()
    return existing is not None


# ---------------------------------------------------------------------------
# Check register
# ---------------------------------------------------------------------------


def ingest_check_register(
    database: DatabaseManager,
    watch_root: Path,
    request: IngestCheckRegisterRequest,
) -> IngestCheckRegisterResult:
    """Apply check-register entries as durable overrides on outbound checks."""
    register_path = _resolve_path(
        request.register_path, watch_root, DEFAULT_CHECK_REGISTER_FILENAME
    )
    result = IngestCheckRegisterResult(
        register_path=register_path.as_posix(),
        register_exists=register_path.exists(),
        dry_run=request.dry_run,
    )
    if not register_path.exists():
        return result

    rows = _read_csv_rows(register_path)
    result.rows_read = len(rows)
    if not rows:
        return result

    missing = _validate_columns(rows[0].keys(), CHECK_REGISTER_REQUIRED_COLUMNS)
    if missing:
        result.errors.append(
            CheckRegisterRowError(
                row_number=0,
                reason="missing_columns",
                detail=f"Missing required columns: {', '.join(missing)}",
            )
        )
        return result

    # Detect intra-file duplicates (same account + check number).
    by_check: dict[tuple[str, str], list[int]] = {}
    for index, raw in enumerate(rows, start=2):  # data starts on file row 2
        row = _normalize_row(raw)
        key = (row.get("account", "").lower(), row.get("check_number", ""))
        by_check.setdefault(key, []).append(index)
    duplicate_keys = {k for k, v in by_check.items() if len(v) > 1}
    for (acct, chk), row_numbers in by_check.items():
        if len(row_numbers) > 1:
            result.duplicates_in_register.append(
                CheckRegisterDuplicate(
                    check_number=chk,
                    account=acct,
                    row_numbers=row_numbers,
                )
            )

    for index, raw in enumerate(rows, start=2):
        row = _normalize_row(raw)
        try:
            _process_check_register_row(
                database, request, row, index, duplicate_keys, result
            )
        except Exception as exc:  # row-local failure, never aborts the file
            result.errors.append(
                CheckRegisterRowError(
                    row_number=index,
                    reason="row_error",
                    detail=f"{type(exc).__name__}: {exc}",
                )
            )

    return result


def _process_check_register_row(
    database: DatabaseManager,
    request: IngestCheckRegisterRequest,
    row: dict[str, str],
    row_number: int,
    duplicate_keys: set[tuple[str, str]],
    result: IngestCheckRegisterResult,
) -> None:
    account_label = row.get("account", "")
    check_number = row.get("check_number", "")
    date_written_raw = row.get("date_written", "")
    amount_raw = row.get("amount", "")
    payee = row.get("payee", "")

    if not all([account_label, check_number, date_written_raw, amount_raw, payee]):
        result.errors.append(
            CheckRegisterRowError(
                row_number=row_number,
                reason="missing_value",
                detail="account, check_number, date_written, amount, payee are all required",
            )
        )
        return

    if (account_label.lower(), check_number) in duplicate_keys:
        return  # already reported in duplicates_in_register; skip silently

    account_id, all_known = _resolve_account(database, account_label)
    if account_id is None:
        result.errors.append(
            CheckRegisterRowError(
                row_number=row_number,
                reason="unknown_account",
                detail=(
                    f"Account {account_label!r} not found. Known: "
                    + ", ".join(f"{aid} ({name})" for aid, name in all_known)
                ),
            )
        )
        return

    try:
        amount = _parse_amount(amount_raw)
        date_written = _parse_date(date_written_raw)
    except (ValueError, TypeError) as exc:
        result.errors.append(
            CheckRegisterRowError(
                row_number=row_number,
                reason="parse_error",
                detail=str(exc),
            )
        )
        return

    candidate = _find_check_transaction(
        database, account_id, check_number, amount, date_written
    )
    if candidate is None:
        result.unmatched.append(
            CheckRegisterUnmatched(
                row_number=row_number,
                check_number=check_number,
                account=account_label,
                amount=amount,
                date_written=date_written.isoformat(),
                reason="no_matching_transaction",
            )
        )
        return
    if isinstance(candidate, list):
        result.unmatched.append(
            CheckRegisterUnmatched(
                row_number=row_number,
                check_number=check_number,
                account=account_label,
                amount=amount,
                date_written=date_written.isoformat(),
                reason="ambiguous_fallback",
                detail=f"{len(candidate)} candidates match the fallback window",
            )
        )
        return

    result.matched += 1

    superseded = _already_user_authoritative(
        database, account_id, candidate["occurred_on"], candidate["amount"]
    )

    notes_extra = row.get("notes", "")
    note_text = f"Check #{check_number} to {payee}"
    if notes_extra:
        note_text = f"{note_text} — {notes_extra}"
    note_text = f"{note_text} [source=check_register.csv:{row_number}]"

    if request.dry_run:
        if superseded:
            result.overrides_superseded_prior += 1
        return

    override_request = UpsertTransactionOverrideRequest(
        transaction_id=candidate["id"],
        merchant_normalized=payee,
        primary_category=row.get("primary_category") or None,
        subcategory=row.get("subcategory") or None,
        lifecycle=row.get("lifecycle") or None,
        household_role=row.get("household_role") or None,
        note=note_text,
    )
    database.upsert_transaction_override(override_request)
    result.overrides_written += 1
    if superseded:
        result.overrides_superseded_prior += 1


def _find_check_transaction(
    database: DatabaseManager,
    account_id: str,
    check_number: str,
    amount: float,
    date_written: date,
) -> dict | list[dict] | None:
    """Return the unique matching transaction, a list of fallback candidates
    when ambiguous, or ``None`` when nothing matches."""
    abs_amount = abs(amount)
    pattern = re.compile(_CHECK_DESC_TEMPLATE.format(n=re.escape(check_number)), re.IGNORECASE)

    with database.connect(read_only=True) as conn:
        # Strict pass: pull every check-like row on this account at this
        # amount, then regex-filter in Python (sqlite has no PCRE).
        strict_rows = conn.execute(
            "SELECT id, occurred_on, posted_on, description_raw, amount "
            "FROM transactions "
            "WHERE account_id = ? AND ABS(amount) = ? "
            "  AND description_raw LIKE 'CHECK#%'",
            (account_id, abs_amount),
        ).fetchall()
        strict_matches = [
            dict(r) for r in strict_rows if pattern.match(r["description_raw"])
        ]
        if len(strict_matches) == 1:
            return strict_matches[0]
        if len(strict_matches) > 1:
            # Two strict matches at the same amount + check number on the
            # same account is a real-world impossibility — surface it as
            # ambiguous rather than silently picking one.
            return strict_matches

        # Fallback: same account + amount + any CHECK# description within
        # ±CHECK_FALLBACK_DAYS of date_written on posted_on (or occurred_on
        # when posted_on is missing).
        window_start = (date_written - timedelta(days=CHECK_FALLBACK_DAYS)).isoformat()
        window_end = (date_written + timedelta(days=CHECK_FALLBACK_DAYS)).isoformat()
        fallback_rows = conn.execute(
            "SELECT id, occurred_on, posted_on, description_raw, amount "
            "FROM transactions "
            "WHERE account_id = ? AND ABS(amount) = ? "
            "  AND description_raw LIKE 'CHECK#%' "
            "  AND COALESCE(posted_on, occurred_on) BETWEEN ? AND ?",
            (account_id, abs_amount, window_start, window_end),
        ).fetchall()
    fallback_matches = [dict(r) for r in fallback_rows]
    if len(fallback_matches) == 1:
        return fallback_matches[0]
    if len(fallback_matches) > 1:
        return fallback_matches
    return None


# ---------------------------------------------------------------------------
# Mobile check deposit ledger
# ---------------------------------------------------------------------------


def ingest_check_deposit_ledger(
    database: DatabaseManager,
    watch_root: Path,
    request: IngestCheckDepositLedgerRequest,
) -> IngestCheckDepositLedgerResult:
    """Apply deposit-ledger entries as durable overrides on mobile deposits."""
    ledger_path = _resolve_path(
        request.ledger_path, watch_root, DEFAULT_DEPOSIT_LEDGER_FILENAME
    )
    result = IngestCheckDepositLedgerResult(
        ledger_path=ledger_path.as_posix(),
        ledger_exists=ledger_path.exists(),
        dry_run=request.dry_run,
    )
    if not ledger_path.exists():
        return result

    rows = _read_csv_rows(ledger_path)
    result.rows_read = len(rows)
    if not rows:
        return result

    missing = _validate_columns(rows[0].keys(), DEPOSIT_LEDGER_REQUIRED_COLUMNS)
    if missing:
        result.errors.append(
            CheckRegisterRowError(
                row_number=0,
                reason="missing_columns",
                detail=f"Missing required columns: {', '.join(missing)}",
            )
        )
        return result

    by_triple: dict[tuple[str, str, str], list[int]] = {}
    for index, raw in enumerate(rows, start=2):
        row = _normalize_row(raw)
        key = (
            row.get("account", "").lower(),
            row.get("date_deposited", ""),
            row.get("amount", ""),
        )
        by_triple.setdefault(key, []).append(index)
    duplicate_keys = {k for k, v in by_triple.items() if len(v) > 1}
    for (acct, ddate, amt), row_numbers in by_triple.items():
        if len(row_numbers) > 1:
            try:
                amt_float = _parse_amount(amt)
            except (ValueError, TypeError):
                amt_float = 0.0
            result.duplicates_in_ledger.append(
                CheckDepositDuplicate(
                    account=acct,
                    date_deposited=ddate,
                    amount=amt_float,
                    row_numbers=row_numbers,
                )
            )

    for index, raw in enumerate(rows, start=2):
        row = _normalize_row(raw)
        try:
            _process_deposit_ledger_row(
                database, request, row, index, duplicate_keys, result
            )
        except Exception as exc:
            result.errors.append(
                CheckRegisterRowError(
                    row_number=index,
                    reason="row_error",
                    detail=f"{type(exc).__name__}: {exc}",
                )
            )

    return result


def _process_deposit_ledger_row(
    database: DatabaseManager,
    request: IngestCheckDepositLedgerRequest,
    row: dict[str, str],
    row_number: int,
    duplicate_keys: set[tuple[str, str, str]],
    result: IngestCheckDepositLedgerResult,
) -> None:
    account_label = row.get("account", "")
    date_deposited_raw = row.get("date_deposited", "")
    amount_raw = row.get("amount", "")
    source_label = row.get("source", "")

    if not all([account_label, date_deposited_raw, amount_raw, source_label]):
        result.errors.append(
            CheckRegisterRowError(
                row_number=row_number,
                reason="missing_value",
                detail="account, date_deposited, amount, source are all required",
            )
        )
        return

    triple_key = (account_label.lower(), date_deposited_raw, amount_raw)
    if triple_key in duplicate_keys:
        return

    account_id, all_known = _resolve_account(database, account_label)
    if account_id is None:
        result.errors.append(
            CheckRegisterRowError(
                row_number=row_number,
                reason="unknown_account",
                detail=(
                    f"Account {account_label!r} not found. Known: "
                    + ", ".join(f"{aid} ({name})" for aid, name in all_known)
                ),
            )
        )
        return

    try:
        amount = _parse_amount(amount_raw)
        date_deposited = _parse_date(date_deposited_raw)
    except (ValueError, TypeError) as exc:
        result.errors.append(
            CheckRegisterRowError(
                row_number=row_number,
                reason="parse_error",
                detail=str(exc),
            )
        )
        return

    candidate = _find_deposit_transaction(database, account_id, amount, date_deposited)
    if candidate is None:
        result.unmatched.append(
            CheckDepositUnmatched(
                row_number=row_number,
                account=account_label,
                amount=amount,
                date_deposited=date_deposited.isoformat(),
                source=source_label,
                reason="no_matching_transaction",
            )
        )
        return
    if isinstance(candidate, list):
        result.unmatched.append(
            CheckDepositUnmatched(
                row_number=row_number,
                account=account_label,
                amount=amount,
                date_deposited=date_deposited.isoformat(),
                source=source_label,
                reason="ambiguous_match",
                detail=f"{len(candidate)} candidates within ±{DEPOSIT_DATE_TOLERANCE_DAYS} days",
            )
        )
        return

    result.matched += 1
    superseded = _already_user_authoritative(
        database, account_id, candidate["occurred_on"], candidate["amount"]
    )

    notes_extra = row.get("notes", "")
    note_text = f"Mobile check deposit from {source_label}"
    if notes_extra:
        note_text = f"{note_text} — {notes_extra}"
    note_text = f"{note_text} [source=check_deposits.csv:{row_number}]"

    if request.dry_run:
        if superseded:
            result.overrides_superseded_prior += 1
        return

    # Default to income/check_deposit when the ledger row left the
    # categorization columns blank. The ledger author can override either
    # default (e.g. primary_category=rental, subcategory=rental_income).
    primary_category = row.get("primary_category") or "income"
    subcategory = row.get("subcategory") or None

    override_request = UpsertTransactionOverrideRequest(
        transaction_id=candidate["id"],
        merchant_normalized=source_label,
        primary_category=primary_category,
        subcategory=subcategory,
        lifecycle=row.get("lifecycle") or None,
        household_role=row.get("household_role") or None,
        note=note_text,
    )
    database.upsert_transaction_override(override_request)
    result.overrides_written += 1
    if superseded:
        result.overrides_superseded_prior += 1


def _find_deposit_transaction(
    database: DatabaseManager,
    account_id: str,
    amount: float,
    date_deposited: date,
) -> dict | list[dict] | None:
    abs_amount = abs(amount)
    window_start = (date_deposited - timedelta(days=DEPOSIT_DATE_TOLERANCE_DAYS)).isoformat()
    window_end = (date_deposited + timedelta(days=DEPOSIT_DATE_TOLERANCE_DAYS)).isoformat()

    with database.connect(read_only=True) as conn:
        rows = conn.execute(
            "SELECT id, occurred_on, posted_on, description_raw, amount "
            "FROM transactions "
            "WHERE account_id = ? AND ABS(amount) = ? "
            "  AND occurred_on BETWEEN ? AND ?",
            (account_id, abs_amount, window_start, window_end),
        ).fetchall()

    matches = [
        dict(r)
        for r in rows
        if _MOBILE_DEPOSIT_DESCRIPTION_RE.match(r["description_raw"] or "")
    ]
    if not matches:
        return None
    if len(matches) == 1:
        return matches[0]
    return matches
