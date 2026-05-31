from __future__ import annotations

import json
import sqlite3
from calendar import monthrange
from dataclasses import dataclass
from datetime import date
from uuid import uuid4

from .balances import AccountBalances, BalancesConfig
from .database import DatabaseManager
from .models import (
    ReconcilePeriodsRequest,
    ReconcilePeriodsResult,
    ReconciliationPeriodSummary,
)


@dataclass(frozen=True)
class _Account:
    id: str
    institution: str
    account_name: str
    account_type: str


@dataclass(frozen=True)
class _Breakdown:
    inflows: float
    outflows: float
    transfers_in: float
    transfers_out: float

    @property
    def net_signed(self) -> float:
        # Net change in the underlying ledger account, as it would post.
        # Inflows and transfers_in raise the balance; outflows and
        # transfers_out lower it. Credit-card sign handling is applied by
        # the caller so this helper stays direction-pure.
        return self.inflows + self.transfers_in - self.outflows - self.transfers_out


def reconcile_periods(
    database: DatabaseManager,
    balances: BalancesConfig,
    request: ReconcilePeriodsRequest,
) -> ReconcilePeriodsResult:
    """Compute and upsert one reconciliation_periods row per account-month.

    For each account, walks calendar months from request.period_start
    through request.period_end. Opening of month N is the prior period's
    statement closing when known (so a variance does not cascade), else
    the prior period's computed closing. The very first month seeds from
    ``balances.toml`` via ``balances.lookup(...)``.

    Statement closing is sourced in priority order:
      1. balances.toml ``statement_closings`` for that account/period_end
      2. ``metadata_json.running_balance`` on the last in-period transaction
         (Beacon/Webster only — Chase/Ally CSVs omit running balance)
      3. None → variance reports as null; UI surfaces "missing"

    Credit-card accounts (account_type == 'credit_card') treat the
    balance as amount-owed: computed_closing = opening − net_signed
    (charges raise the balance, payments lower it).

    Idempotent: re-running on unchanged data produces identical rows.
    Existing ``variance_explanation`` text is preserved across upserts so
    the human note survives recomputation.
    """
    connection = database.connect()
    try:
        accounts = _load_accounts(connection, request.account_ids)
        months = _months_in_range(request.period_start, request.period_end)

        summaries: list[ReconciliationPeriodSummary] = []
        for account in accounts:
            entry = balances.lookup(
                account_id=account.id, institution=account.institution
            )
            running_opening: float | None = entry.opening_balance
            for period_start, period_end in months:
                summary = _compute_period(
                    connection,
                    account=account,
                    period_start=period_start,
                    period_end=period_end,
                    opening=running_opening,
                    entry=entry,
                )
                _upsert(connection, summary)
                # Roll forward: prefer statement, fall back to computed.
                # Both can be None when a seed is missing — the next
                # month then also opens with None.
                running_opening = (
                    summary.statement_closing_balance
                    if summary.statement_closing_balance is not None
                    else summary.computed_closing_balance
                )
                summaries.append(summary)

        connection.commit()
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()

    return ReconcilePeriodsResult(
        balances_file_loaded=balances.loaded,
        balances_file_path=str(balances.source_path),
        period_start=request.period_start.isoformat(),
        period_end=request.period_end.isoformat(),
        accounts_processed=len(accounts),
        periods_written=len(summaries),
        summaries=summaries,
    )


def _load_accounts(
    connection: sqlite3.Connection, account_ids: list[str]
) -> list[_Account]:
    if account_ids:
        placeholders = ",".join(["?"] * len(account_ids))
        cursor = connection.execute(
            f"SELECT id, institution, account_name, account_type "
            f"FROM accounts WHERE id IN ({placeholders}) "
            f"ORDER BY institution, account_name",
            account_ids,
        )
    else:
        cursor = connection.execute(
            "SELECT id, institution, account_name, account_type "
            "FROM accounts ORDER BY institution, account_name"
        )
    return [
        _Account(
            id=row["id"],
            institution=row["institution"],
            account_name=row["account_name"],
            account_type=row["account_type"],
        )
        for row in cursor.fetchall()
    ]


def _months_in_range(start: date, end: date) -> list[tuple[date, date]]:
    if end < start:
        return []
    months: list[tuple[date, date]] = []
    cursor_year, cursor_month = start.year, start.month
    while True:
        last_day = monthrange(cursor_year, cursor_month)[1]
        period_start = date(cursor_year, cursor_month, 1)
        period_end = date(cursor_year, cursor_month, last_day)
        if period_start > end:
            break
        # Clip the trailing month so we don't over-report future periods.
        clipped_end = min(period_end, end)
        months.append((period_start, clipped_end))
        if cursor_month == 12:
            cursor_year += 1
            cursor_month = 1
        else:
            cursor_month += 1
    return months


def _compute_period(
    connection: sqlite3.Connection,
    *,
    account: _Account,
    period_start: date,
    period_end: date,
    opening: float | None,
    entry: AccountBalances,
) -> ReconciliationPeriodSummary:
    breakdown = _breakdown(connection, account.id, period_start, period_end)

    computed_closing: float | None
    if opening is None:
        computed_closing = None
    elif account.account_type == "credit_card":
        # Liability view: charges raise the balance owed, payments lower
        # it. The signed amounts already mean "money in/out of checking
        # if this were an asset", so for a card we invert the net.
        computed_closing = round(opening - breakdown.net_signed, 2)
    else:
        computed_closing = round(opening + breakdown.net_signed, 2)

    statement_closing, source = _resolve_statement_closing(
        connection,
        account=account,
        period_end=period_end,
        entry=entry,
    )

    variance: float | None = None
    if statement_closing is not None and computed_closing is not None:
        variance = round(statement_closing - computed_closing, 2)

    existing_explanation = _existing_explanation(
        connection, account.id, period_start, period_end
    )

    return ReconciliationPeriodSummary(
        account_id=account.id,
        account_label=f"{account.institution} · {account.account_name}",
        account_type=account.account_type,
        period_start=period_start.isoformat(),
        period_end=period_end.isoformat(),
        statement_opening_balance=opening,
        statement_closing_balance=statement_closing,
        closing_balance_source=source,
        computed_inflows=round(breakdown.inflows, 2),
        computed_outflows=round(breakdown.outflows, 2),
        computed_transfers_in=round(breakdown.transfers_in, 2),
        computed_transfers_out=round(breakdown.transfers_out, 2),
        computed_closing_balance=computed_closing,
        variance_amount=variance,
        variance_explanation=existing_explanation,
    )


def _breakdown(
    connection: sqlite3.Connection,
    account_id: str,
    period_start: date,
    period_end: date,
) -> _Breakdown:
    cursor = connection.execute(
        """
        SELECT direction, amount
          FROM transactions
         WHERE account_id = ?
           AND occurred_on >= ?
           AND occurred_on <= ?
        """,
        (account_id, period_start.isoformat(), period_end.isoformat()),
    )
    inflows = outflows = transfers_in = transfers_out = 0.0
    for row in cursor.fetchall():
        amount = float(row["amount"])
        direction = row["direction"]
        if direction == "inflow":
            inflows += amount
        elif direction == "outflow":
            outflows += abs(amount)
        elif direction == "transfer":
            if amount >= 0:
                transfers_in += amount
            else:
                transfers_out += abs(amount)
    return _Breakdown(
        inflows=inflows,
        outflows=outflows,
        transfers_in=transfers_in,
        transfers_out=transfers_out,
    )


def _resolve_statement_closing(
    connection: sqlite3.Connection,
    *,
    account: _Account,
    period_end: date,
    entry: AccountBalances,
) -> tuple[float | None, str | None]:
    # 1. Explicit override in balances.toml — always wins.
    explicit = entry.statement_closings.get(period_end)
    if explicit is not None:
        return explicit, "balances_toml"

    # 2. running_balance metadata on the last in-period date.
    # Find the latest date that has any transaction for this account.
    cursor = connection.execute(
        """
        SELECT MAX(occurred_on) AS last_date
          FROM transactions
         WHERE account_id = ?
           AND occurred_on <= ?
        """,
        (account.id, period_end.isoformat()),
    )
    row = cursor.fetchone()
    if row is None or row["last_date"] is None:
        return None, None

    last_date = row["last_date"]

    # Load every row on that date that carries a running_balance.
    cursor = connection.execute(
        """
        SELECT id, amount, metadata_json
          FROM transactions
         WHERE account_id = ?
           AND occurred_on = ?
        """,
        (account.id, last_date),
    )

    candidates: list[dict] = []
    for row in cursor.fetchall():
        try:
            metadata = json.loads(row["metadata_json"] or "{}")
        except json.JSONDecodeError:
            metadata = {}
        rb = metadata.get("running_balance")
        if rb is not None:
            try:
                candidates.append(
                    {
                        "id": row["id"],
                        "amount": float(row["amount"]),
                        "running_balance": float(rb),
                    }
                )
            except (TypeError, ValueError):
                pass

    if not candidates:
        return None, None

    if len(candidates) == 1:
        return candidates[0]["running_balance"], "metadata_running_balance"

    # Multiple rows share the last in-period date.  Reconstruct chronological
    # order from the running-balance chain: row b immediately follows row a
    # when round(a.running_balance + b.amount, 2) == round(b.running_balance, 2).
    # The terminal row is the one with no successor (no other row follows it).
    # id DESC is NOT used as a tiebreaker because ids are random UUIDs.
    predecessor_ids: set[str] = set()
    for a in candidates:
        for b in candidates:
            if a["id"] != b["id"]:
                if round(a["running_balance"] + b["amount"], 2) == round(
                    b["running_balance"], 2
                ):
                    predecessor_ids.add(a["id"])

    terminals = [c for c in candidates if c["id"] not in predecessor_ids]

    if len(terminals) == 1:
        return terminals[0]["running_balance"], "metadata_running_balance"

    # Chain is ambiguous (e.g. two rows with identical amounts and balances, or
    # data inconsistency).  Prefer the highest running_balance as the most
    # conservative pick and tag the source so the caller can spot the case.
    best = max(candidates, key=lambda c: c["running_balance"])
    return best["running_balance"], "metadata_running_balance_unresolved"


def _existing_explanation(
    connection: sqlite3.Connection,
    account_id: str,
    period_start: date,
    period_end: date,
) -> str:
    row = connection.execute(
        "SELECT variance_explanation FROM reconciliation_periods "
        "WHERE account_id = ? AND period_start = ? AND period_end = ?",
        (account_id, period_start.isoformat(), period_end.isoformat()),
    ).fetchone()
    return row["variance_explanation"] if row else ""


def _upsert(
    connection: sqlite3.Connection, summary: ReconciliationPeriodSummary
) -> None:
    connection.execute(
        """
        INSERT INTO reconciliation_periods (
          id,
          account_id,
          period_start,
          period_end,
          statement_opening_balance,
          statement_closing_balance,
          closing_balance_source,
          computed_inflows,
          computed_outflows,
          computed_transfers_in,
          computed_transfers_out,
          computed_closing_balance,
          variance_amount,
          variance_explanation
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(account_id, period_start, period_end) DO UPDATE SET
          statement_opening_balance = excluded.statement_opening_balance,
          statement_closing_balance = excluded.statement_closing_balance,
          closing_balance_source = excluded.closing_balance_source,
          computed_inflows = excluded.computed_inflows,
          computed_outflows = excluded.computed_outflows,
          computed_transfers_in = excluded.computed_transfers_in,
          computed_transfers_out = excluded.computed_transfers_out,
          computed_closing_balance = excluded.computed_closing_balance,
          variance_amount = excluded.variance_amount,
          computed_at = CURRENT_TIMESTAMP
        """,
        (
            str(uuid4()),
            summary.account_id,
            summary.period_start,
            summary.period_end,
            summary.statement_opening_balance,
            summary.statement_closing_balance,
            summary.closing_balance_source,
            summary.computed_inflows,
            summary.computed_outflows,
            summary.computed_transfers_in,
            summary.computed_transfers_out,
            summary.computed_closing_balance,
            summary.variance_amount,
            summary.variance_explanation,
        ),
    )
