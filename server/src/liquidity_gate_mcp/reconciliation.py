from __future__ import annotations

import json
import sqlite3
from calendar import monthrange
from dataclasses import dataclass
from datetime import date, timedelta
from uuid import uuid4

from .balances import AccountBalances, BalancesConfig
from .database import DatabaseManager
from .models import (
    ReconcilePeriodsRequest,
    ReconcilePeriodsResult,
    ReconciliationPeriodSummary,
)


# Float tolerance when linking running-balance rows into a chain. Balances are
# dollars-and-cents, so anything under half a cent is the same balance.
BALANCE_EPSILON = 0.005

# Effective date of the single undated ``[opening_balances]`` block in
# balances.toml. By definition and by the file's own comment that seed is the
# 12/31/2025 closing — i.e. the opening for the first 2026 period — so it
# anchors the 2026-01 period and MUST NOT be chained across from any stale
# prior-year stored period. If ``[opening_balances]`` later grows dated
# per-account entries, this becomes a per-entry lookup (most recent seed whose
# effective date is <= period_start); for now there is one block, effective
# here.
OPENING_SEED_EFFECTIVE_DATE = date(2025, 12, 31)

# Surfaced in variance_explanation when same-day rows cannot be linked into a
# single unbroken running-balance chain. Kept stable so the UI / a re-run can
# detect it and so it does not silently corrupt the next period's opening.
AMBIGUOUS_INTRADAY_NOTE = (
    "Ambiguous intraday ordering: same-day rows on {date} could not be linked "
    "into a single running-balance chain; closing balance derivation fell back "
    "to the highest running balance and may be wrong."
)

# Provenance value stored on reconciliation_periods.closing_balance_source when
# an explicit balance checkpoint (a dated [statement_closings.<account>] entry)
# governs the period. Wins over metadata_running_balance. The column is free
# text (no CHECK constraint), so adding this value needs no migration.
CHECKPOINT_SOURCE = "checkpoint"

# Single-line marker that prefixes the system-generated checkpoint note in
# variance_explanation. Used to strip a prior run's checkpoint note before
# regenerating it, so re-runs stay idempotent while preserving any human note.
CHECKPOINT_NOTE_PREFIX = "Checkpoint re-anchor:"

# A checkpoint is stale once its as-of date is more than this many calendar
# days before "today" (the server clock). See _checkpoint_stale.
CHECKPOINT_STALE_DAYS = 35


@dataclass(frozen=True)
class _Account:
    id: str
    institution: str
    account_name: str
    account_type: str


@dataclass(frozen=True)
class IntradayChain:
    """Result of reconstructing one day's transactions from running balances.

    ``closing`` is the terminal row's running balance (true end-of-day balance);
    ``opening`` is the chain head's predecessor (``running_balance - amount`` of
    the first row, i.e. the start-of-day balance). ``ambiguous`` is True when the
    rows cannot be linked into a single unbroken chain — a gap, a fork, a cycle,
    or a duplicate — in which case callers must NOT trust ``closing``/``opening``
    and should surface the ambiguity instead of silently picking a row.
    """

    closing: float | None
    opening: float | None
    ambiguous: bool


def reconstruct_intraday_chain(rows: list[dict]) -> IntradayChain:
    """Order same-day transactions by their running-balance chain.

    Each row must carry ``amount`` (signed) and ``running_balance`` (balance
    AFTER the transaction posts). Row ``a`` immediately precedes row ``b`` when
    ``a.running_balance == b.running_balance - b.amount`` (b's predecessor
    balance), compared within :data:`BALANCE_EPSILON`.

    * TERMINAL row (period closing) = the row whose running_balance is not any
      other row's predecessor balance — it has no successor.
    * CHAIN HEAD predecessor (period opening) = the predecessor balance that is
      not equal to any row's running_balance — the balance the day started at.

    Row/insertion order is never consulted; only the balances are. This is
    robust to forward- (Beacon) and reverse-chronological (Webster) CSVs alike.
    """
    n = len(rows)
    if n == 0:
        return IntradayChain(closing=None, opening=None, ambiguous=True)

    rbs = [float(r["running_balance"]) for r in rows]
    preds = [float(r["running_balance"]) - float(r["amount"]) for r in rows]

    if n == 1:
        # Fast path: a lone row is its own terminal and head.
        return IntradayChain(closing=rbs[0], opening=preds[0], ambiguous=False)

    # successor[i] = the unique j that follows i (rb[i] == pred[j]).
    successor: dict[int, int] = {}
    predecessor: dict[int, int] = {}
    ambiguous = False
    for i in range(n):
        succ = [
            j for j in range(n) if j != i and abs(rbs[i] - preds[j]) <= BALANCE_EPSILON
        ]
        if len(succ) > 1:
            ambiguous = True
        if succ:
            successor[i] = succ[0]
    for j in range(n):
        prev = [
            i for i in range(n) if i != j and abs(rbs[i] - preds[j]) <= BALANCE_EPSILON
        ]
        if len(prev) > 1:
            ambiguous = True
        if prev:
            predecessor[j] = prev[0]

    terminals = [i for i in range(n) if i not in successor]
    heads = [j for j in range(n) if j not in predecessor]

    if ambiguous or len(terminals) != 1 or len(heads) != 1:
        return IntradayChain(closing=None, opening=None, ambiguous=True)

    # Walk head → terminal and confirm we visit every row exactly once. This
    # rejects a chain split into two disjoint runs (which would also yield a
    # single head/terminal pair only by coincidence) and any cycle.
    head = heads[0]
    visited: set[int] = set()
    cur = head
    while True:
        if cur in visited:
            return IntradayChain(closing=None, opening=None, ambiguous=True)
        visited.add(cur)
        if cur in successor:
            cur = successor[cur]
        else:
            break

    if len(visited) != n or cur != terminals[0]:
        return IntradayChain(closing=None, opening=None, ambiguous=True)

    return IntradayChain(closing=rbs[terminals[0]], opening=preds[head], ambiguous=False)


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
        # Single clock for the whole run — the same source the SQL views use
        # (DATE('now','localtime')). Drives checkpoint staleness so there is
        # never a second, divergent notion of "today".
        today = _server_today(connection)

        summaries: list[ReconciliationPeriodSummary] = []
        for account in accounts:
            entry = balances.lookup(
                account_id=account.id, institution=account.institution
            )
            # Checkpoints apply only to cash accounts that carry no CSV running
            # balance (Ally HYSA today). Beacon/Webster derive their closings
            # from running_balance metadata and keep their existing behavior;
            # credit cards are out of scope entirely.
            has_running_balance = _account_has_running_balance(connection, account.id)
            # Seed the first requested month.
            #
            # The balances.toml opening seed is the authoritative anchor for the
            # period it represents — the first period on/after its effective
            # date (2026-01 for the current 12/31/2025 block). The seed-anchored
            # period MUST use the seed and MUST NOT chain from any prior stored
            # period: doing so would re-seed January from a stale prior-year
            # December row carrying the OLD, pre-correction seed.
            #
            # Only periods strictly *after* the seed-anchored period chain from
            # the prior stored period's closing (statement preferred, else
            # computed) — that is the single-month / mid-range rollforward, and
            # it must never re-seed every month from the balances.toml opening.
            first_period_start = months[0][0] if months else None
            running_opening: float | None = entry.opening_balance
            if first_period_start is not None:
                seed = entry.opening_balance
                anchor = _seed_anchor_period_start(OPENING_SEED_EFFECTIVE_DATE)
                if seed is not None and first_period_start <= anchor:
                    # The run starts at (or before) the period the seed anchors:
                    # the seed wins; never chain across the seed boundary.
                    running_opening = seed
                else:
                    # No seed for this account, or a period strictly after the
                    # anchor: chain from the prior stored period when one
                    # exists, otherwise fall back to the seed (or None).
                    prior_closing = _prior_period_closing(
                        connection, account.id, first_period_start
                    )
                    if prior_closing is not None:
                        running_opening = prior_closing
            for period_start, period_end in months:
                summary = _compute_period(
                    connection,
                    account=account,
                    period_start=period_start,
                    period_end=period_end,
                    opening=running_opening,
                    entry=entry,
                    has_running_balance=has_running_balance,
                    today=today,
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
    has_running_balance: bool,
    today: date,
) -> ReconciliationPeriodSummary:
    # Checkpoint path: a cash account with no running balance and an explicit
    # dated entry inside this period. The entry asserts a true end-of-day
    # balance at its date; it is consumed with mid-month semantics rather than
    # treated as a plain month-end statement closing.
    if not has_running_balance and account.account_type != "credit_card":
        checkpoint_dates = sorted(
            d for d in entry.statement_closings if period_start <= d <= period_end
        )
        if checkpoint_dates:
            return _compute_checkpoint_period(
                connection,
                account=account,
                period_start=period_start,
                period_end=period_end,
                opening=opening,
                entry=entry,
                checkpoint_dates=checkpoint_dates,
                today=today,
            )

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

    statement_closing, source, ambiguity_note = _resolve_statement_closing(
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

    # Surface an ambiguous intraday ordering so it cannot silently corrupt the
    # next period's opening. Prepend it to any human note already present, but
    # never duplicate it on idempotent re-runs.
    explanation = existing_explanation
    if ambiguity_note and ambiguity_note not in explanation:
        explanation = (
            ambiguity_note
            if not explanation
            else f"{ambiguity_note}\n{explanation}"
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
        variance_explanation=explanation,
    )


def _next_day(d: date) -> date:
    return d + timedelta(days=1)


def _server_today(connection: sqlite3.Connection) -> date:
    """Today's date from the same clock the SQL views use.

    ``v_computed_balance`` filters on ``DATE('now','localtime')``; reading the
    same expression here keeps checkpoint staleness on one clock rather than
    introducing a second (e.g. Python ``date.today()``) that could disagree
    across a timezone boundary.
    """
    row = connection.execute("SELECT DATE('now', 'localtime') AS d").fetchone()
    return date.fromisoformat(row["d"])


def _account_has_running_balance(
    connection: sqlite3.Connection, account_id: str
) -> bool:
    """True when any transaction for the account carries a running_balance.

    This is the gate for checkpoint behavior: Beacon/Webster CSVs embed a
    ``running_balance`` in metadata (so their closings are CSV-derived and
    checkpoints do not apply), while Ally/Chase/Citi do not. Today that selects
    Ally HYSA as the only cash account eligible for checkpoints — no per-account
    flag or column is introduced.
    """
    cursor = connection.execute(
        "SELECT metadata_json FROM transactions "
        "WHERE account_id = ? AND metadata_json LIKE '%running_balance%' LIMIT 5",
        (account_id,),
    )
    for row in cursor.fetchall():
        try:
            metadata = json.loads(row["metadata_json"] or "{}")
        except json.JSONDecodeError:
            continue
        if metadata.get("running_balance") is not None:
            return True
    return False


def _checkpoint_stale(
    connection: sqlite3.Connection,
    account_id: str,
    checkpoint_date: date,
    today: date,
) -> bool:
    """Whether the governing checkpoint can still be trusted as a fresh anchor.

    Stale when EITHER the checkpoint predates the newest ingested transaction
    for the account (activity has been imported past the last anchor, so the
    absolute balance is no longer verified), OR the checkpoint is more than
    :data:`CHECKPOINT_STALE_DAYS` calendar days old. A green reconcile only
    proves the net math is internally consistent since the anchor — this flag
    makes the "but the anchor itself may be old" distinction explicit.
    """
    row = connection.execute(
        "SELECT MAX(occurred_on) AS m FROM transactions WHERE account_id = ?",
        (account_id,),
    ).fetchone()
    last_tx = row["m"] if row else None
    if last_tx:
        try:
            if date.fromisoformat(str(last_tx)[:10]) > checkpoint_date:
                return True
        except ValueError:
            pass
    return (today - checkpoint_date).days > CHECKPOINT_STALE_DAYS


def _compute_checkpoint_period(
    connection: sqlite3.Connection,
    *,
    account: _Account,
    period_start: date,
    period_end: date,
    opening: float | None,
    entry: AccountBalances,
    checkpoint_dates: list[date],
    today: date,
) -> ReconciliationPeriodSummary:
    """Consume one or more in-period checkpoints with mid-month semantics.

    A checkpoint dated ``D`` with balance ``X`` asserts: the true end-of-day
    balance at ``D`` is ``X``; everything posted on/before ``D`` is already
    inside ``X``; everything after ``D`` chains forward from ``X``. So the
    figure carried into the next period's opening is ``X + net(D+1 .. period_end)``,
    NOT the unanchored chain.

    The latest checkpoint in the period governs the forward carry; earlier
    checkpoints are additional verification points. ``D == period_end`` collapses
    to the simple case (net after ``D`` is empty, so the re-anchored closing is
    just ``X``).

    ``net(range) = inflows - outflows + transfers_in - transfers_out`` over the
    range — identical to the existing chain math (``_Breakdown.net_signed``).
    Checkpoint accounts are never credit cards, so asset sign conventions apply.
    """
    breakdown = _breakdown(connection, account.id, period_start, period_end)

    governing_date = checkpoint_dates[-1]
    governing_balance = round(float(entry.statement_closings[governing_date]), 2)

    net_after = _breakdown(
        connection, account.id, _next_day(governing_date), period_end
    ).net_signed
    # Re-anchored end-of-period balance — the value future periods chain from.
    reanchored = round(governing_balance + net_after, 2)

    # Unanchored chain closing keeps its existing meaning (opening + full net).
    computed_closing = None if opening is None else round(opening + breakdown.net_signed, 2)

    # Verification delta. By the net identity this equals
    # reanchored - computed_closing == X - (opening + net(period_start .. D)),
    # i.e. how far the checkpoint diverges from the chain it should confirm.
    variance: float | None = None
    if computed_closing is not None:
        variance = round(reanchored - computed_closing, 2)
        if abs(variance) <= 0.01:
            variance = 0.0

    note = _build_checkpoint_note(
        connection,
        account=account,
        period_start=period_start,
        period_end=period_end,
        opening=opening,
        entry=entry,
        checkpoint_dates=checkpoint_dates,
        governing_date=governing_date,
        governing_balance=governing_balance,
        net_after=net_after,
        reanchored=reanchored,
        variance=variance,
    )

    # Preserve any human note; replace a prior run's checkpoint note (single
    # line, marker-prefixed) so re-runs stay idempotent.
    existing = _existing_explanation(connection, account.id, period_start, period_end)
    human_lines = [
        line
        for line in existing.split("\n")
        if line.strip() and not line.startswith(CHECKPOINT_NOTE_PREFIX)
    ]
    human = "\n".join(human_lines).strip()
    explanation = note if not human else f"{note}\n{human}"

    stale = _checkpoint_stale(connection, account.id, governing_date, today)

    return ReconciliationPeriodSummary(
        account_id=account.id,
        account_label=f"{account.institution} · {account.account_name}",
        account_type=account.account_type,
        period_start=period_start.isoformat(),
        period_end=period_end.isoformat(),
        statement_opening_balance=opening,
        statement_closing_balance=reanchored,
        closing_balance_source=CHECKPOINT_SOURCE,
        computed_inflows=round(breakdown.inflows, 2),
        computed_outflows=round(breakdown.outflows, 2),
        computed_transfers_in=round(breakdown.transfers_in, 2),
        computed_transfers_out=round(breakdown.transfers_out, 2),
        computed_closing_balance=computed_closing,
        variance_amount=variance,
        variance_explanation=explanation,
        checkpoint_stale=stale,
    )


def _build_checkpoint_note(
    connection: sqlite3.Connection,
    *,
    account: _Account,
    period_start: date,
    period_end: date,
    opening: float | None,
    entry: AccountBalances,
    checkpoint_dates: list[date],
    governing_date: date,
    governing_balance: float,
    net_after: float,
    reanchored: float,
    variance: float | None,
) -> str:
    """Single-line, human-auditable checkpoint explanation.

    Kept to one line (no embedded newlines) so a prior run's note can be
    stripped by line before regeneration. Includes the governing re-anchor and,
    when more than one checkpoint falls in the period, a chained verification
    delta for each earlier checkpoint (``net`` between consecutive checkpoints
    should close).
    """
    parts: list[str] = []
    if opening is None:
        parts.append(
            f"{CHECKPOINT_NOTE_PREFIX} {governing_date.isoformat()} balance "
            f"{governing_balance:.2f} taken as forward anchor (no prior opening to "
            f"verify); next period opens from {reanchored:.2f} "
            f"= {governing_balance:.2f} + net({_next_day(governing_date).isoformat()}.."
            f"{period_end.isoformat()}) {net_after:+.2f}."
        )
    else:
        net_to_governing = _breakdown(
            connection, account.id, period_start, governing_date
        ).net_signed
        parts.append(
            f"{CHECKPOINT_NOTE_PREFIX} {governing_date.isoformat()} balance "
            f"{governing_balance:.2f}; verification delta {variance:+.2f} vs chain "
            f"({opening:.2f} + net({period_start.isoformat()}.."
            f"{governing_date.isoformat()}) {net_to_governing:+.2f}); forward carry "
            f"re-anchored to {reanchored:.2f} (+ net after {governing_date.isoformat()} "
            f"{net_after:+.2f})."
        )

    # Earlier checkpoints: verify each against the prior anchor in the chain.
    if len(checkpoint_dates) > 1 and opening is not None:
        prev_balance = opening
        prev_boundary = period_start
        clauses: list[str] = []
        for d in checkpoint_dates:
            seg_net = _breakdown(connection, account.id, prev_boundary, d).net_signed
            x = round(float(entry.statement_closings[d]), 2)
            delta = round(x - (prev_balance + seg_net), 2)
            if abs(delta) <= 0.01:
                delta = 0.0
            if d != governing_date:
                clauses.append(f"{d.isoformat()}={x:.2f} delta {delta:+.2f}")
            prev_balance = x
            prev_boundary = _next_day(d)
        if clauses:
            parts.append(" Earlier checkpoints verified: " + "; ".join(clauses) + ".")

    return "".join(parts)


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
) -> tuple[float | None, str | None, str | None]:
    """Return (closing_balance, source, ambiguity_note).

    ``ambiguity_note`` is None unless same-day rows on the final active date
    could not be linked into a single running-balance chain, in which case it
    carries the message the caller should surface in variance_explanation.
    """
    # 1. Explicit override in balances.toml — always wins.
    explicit = entry.statement_closings.get(period_end)
    if explicit is not None:
        return explicit, "balances_toml", None

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
        return None, None, None

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
        return None, None, None

    if len(candidates) == 1:
        # Fast path: a single same-day row needs no chain reconstruction.
        return candidates[0]["running_balance"], "metadata_running_balance", None

    # Multiple rows share the last in-period date. Reconstruct their true
    # chronological order from the running-balance chain rather than trusting
    # row/insertion order (which is forward for Beacon, reverse for Webster).
    chain = reconstruct_intraday_chain(candidates)
    if not chain.ambiguous and chain.closing is not None:
        return chain.closing, "metadata_running_balance", None

    # Chain is ambiguous (gap, fork, cycle, or duplicate). Do NOT silently pick
    # a row: fall back to the highest running_balance but flag it so the note
    # surfaces instead of corrupting the next period's opening.
    best = max(candidates, key=lambda c: c["running_balance"])
    note = AMBIGUOUS_INTRADAY_NOTE.format(date=last_date)
    return best["running_balance"], "metadata_running_balance_unresolved", note


def _seed_anchor_period_start(effective: date) -> date:
    """First calendar-month ``period_start`` on/after a seed's effective date.

    A seed effective on the last day of a month (a statement close, e.g.
    2025-12-31) is the opening for the *next* month, so it anchors that next
    month's period (2026-01). A seed effective on the 1st anchors that same
    month. The seed-anchored period is the chain root: it uses the seed and
    does not chain from any earlier stored period.
    """
    if effective.day == 1:
        return date(effective.year, effective.month, 1)
    if effective.month == 12:
        return date(effective.year + 1, 1, 1)
    return date(effective.year, effective.month + 1, 1)


def _prior_period_closing(
    connection: sqlite3.Connection,
    account_id: str,
    period_start: date,
) -> float | None:
    """Closing balance of the period immediately preceding ``period_start``.

    Used to seed a single-month (or mid-range) reconcile so its opening chains
    from the prior stored period instead of the balances.toml seed. Prefers the
    statement closing when present (matching the full-run rollforward), else the
    computed closing. Returns None when no earlier period row exists, so the
    caller falls back to the balances.toml opening.
    """
    row = connection.execute(
        """
        SELECT statement_closing_balance, computed_closing_balance
          FROM reconciliation_periods
         WHERE account_id = ?
           AND period_end < ?
         ORDER BY period_end DESC
         LIMIT 1
        """,
        (account_id, period_start.isoformat()),
    ).fetchone()
    if row is None:
        return None
    if row["statement_closing_balance"] is not None:
        return float(row["statement_closing_balance"])
    if row["computed_closing_balance"] is not None:
        return float(row["computed_closing_balance"])
    return None


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
          -- excluded value already merges the prior human note (read in
          -- _compute_period) with any system-generated ambiguity note, so
          -- writing it back preserves the human note and persists the flag.
          variance_explanation = excluded.variance_explanation,
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
