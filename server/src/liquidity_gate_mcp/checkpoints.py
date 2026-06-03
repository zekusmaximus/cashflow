"""``upsert_balance_checkpoint`` — durable, dated balance assertions.

For cash accounts whose CSV exports carry a ``running_balance`` (Beacon,
Webster), reconcile self-anchors every month. Ally HYSA has no running balance
in its CSV, so its computed balance only chains forward from the 2025-12-31
seed and is never verified against an actual bank balance.

This tool writes a non-destructive, human-auditable balance assertion
("Ally HYSA = $35,000 as of 2026-06-02") into the ``[statement_closings.<account>]``
section of ``balances.toml`` — the file's own comment already states that
explicit entries there win over CSV-derived running balances. The entry
survives re-ingest and reclassify because it lives in TOML, not the
``transactions`` table, and becomes the opening anchor future periods chain
from (see ``reconciliation._compute_checkpoint_period``).

The tool is a write+refresh convenience wrapper: it round-trips the TOML
(preserving every other entry and comment), re-seeds the balance anchors,
refreshes the Ally HYSA gate, re-runs the reconcile pass for the affected
account, and returns the written entry plus the post-reconcile state.

It is account-generic even though Ally is the only account that needs it
today; checkpoints are rejected for credit-card accounts.
"""

from __future__ import annotations

from calendar import monthrange
from datetime import date
from pathlib import Path
import sqlite3

from .balances import load_balances
from .computed_balance import (
    ALLY_HYSA_ACCOUNT_ID,
    refresh_hysa_gate,
    seed_balance_anchors,
)
from .config import ServerSettings
from .database import DatabaseManager
from .models import (
    ReconcilePeriodsRequest,
    ReconciliationPeriodSummary,
    UpsertBalanceCheckpointRequest,
    UpsertBalanceCheckpointResult,
)
from .reconciliation import CHECKPOINT_SOURCE, reconcile_periods


# First reconciliation period the 12/31/2025 opening seed anchors. Checkpoint
# re-runs start here so the re-anchored chain replaces the unanchored one from
# the very first 2026 period forward.
_RUN_START = date(2026, 1, 1)


class CheckpointError(ValueError):
    """Raised when a checkpoint cannot be written (bad account, credit card)."""


def upsert_balance_checkpoint(
    database: DatabaseManager,
    settings: ServerSettings,
    request: UpsertBalanceCheckpointRequest,
) -> UpsertBalanceCheckpointResult:
    connection = database.connect()
    try:
        account = _resolve_account(connection, request.account)
        section_key = _section_key(connection, account)
    finally:
        connection.close()

    if account["account_type"] == "credit_card":
        raise CheckpointError(
            f"'{request.account}' resolves to credit-card account "
            f"{account['id']}; checkpoints are cash-account only (a credit-card "
            f"balance is amount-owed, not a verified cash position)."
        )

    # 1. Write/overwrite the entry, round-tripping the TOML.
    balances_path = _write_checkpoint_entry(
        settings.watch_root,
        section_key=section_key,
        as_of=request.date,
        balance=request.balance,
        note=request.note,
    )

    # 2. Re-seed anchors (materialises the new checkpoint into
    #    reconciliation_periods so v_computed_balance can see it), then refresh
    #    the Ally HYSA gate when this is the Ally account.
    balances = load_balances(settings.watch_root)
    seed_balance_anchors(database, balances)

    gate_updated = False
    gate_anchor_date: str | None = None
    gate_computed_balance: float | None = None
    if account["id"] == ALLY_HYSA_ACCOUNT_ID:
        gate = refresh_hysa_gate(database)
        gate_updated = gate.updated
        gate_anchor_date = gate.anchor_date
        gate_computed_balance = gate.computed_balance

    # 3. Re-run reconcile for the affected account over a range that contains
    #    the checkpoint month and the month after it (so the re-anchored
    #    forward carry lands on a "next period" the caller can read back).
    run_end = _run_end(connection_today(database), request.date)
    run_start = min(_RUN_START, request.date.replace(day=1))
    recon = reconcile_periods(
        database,
        balances,
        ReconcilePeriodsRequest(
            period_start=run_start,
            period_end=run_end,
            account_ids=[account["id"]],
        ),
    )

    checkpoint_period, next_period = _locate_periods(recon.summaries, request.date)

    return UpsertBalanceCheckpointResult(
        account_id=account["id"],
        account_section=section_key,
        account_type=account["account_type"],
        balances_file_path=str(balances_path),
        written_date=request.date.isoformat(),
        written_balance=round(float(request.balance), 2),
        written_note=request.note,
        gate_updated=gate_updated,
        gate_anchor_date=gate_anchor_date,
        gate_computed_balance=gate_computed_balance,
        checkpoint_period_start=(
            checkpoint_period.period_start if checkpoint_period else None
        ),
        checkpoint_period_end=(
            checkpoint_period.period_end if checkpoint_period else None
        ),
        statement_closing_balance=(
            checkpoint_period.statement_closing_balance if checkpoint_period else None
        ),
        computed_closing_balance=(
            checkpoint_period.computed_closing_balance if checkpoint_period else None
        ),
        variance_amount=(
            checkpoint_period.variance_amount if checkpoint_period else None
        ),
        next_period_opening_balance=(
            next_period.statement_opening_balance if next_period else None
        ),
        checkpoint_stale=(
            checkpoint_period.checkpoint_stale if checkpoint_period else None
        ),
    )


def connection_today(database: DatabaseManager) -> date:
    """Today's date from the same clock the SQL views use."""
    connection = database.connect(read_only=True)
    try:
        row = connection.execute("SELECT DATE('now', 'localtime') AS d").fetchone()
        return date.fromisoformat(row["d"])
    finally:
        connection.close()


def _resolve_account(
    connection: sqlite3.Connection, account_input: str
) -> sqlite3.Row:
    """Resolve to an accounts row using balances.toml's order: id, then alias.

    Exact ``accounts.id`` wins; otherwise a case-insensitive institution alias
    (ally, chase, beacon, webster) must match exactly one account.
    """
    row = connection.execute(
        "SELECT id, institution, account_type FROM accounts WHERE id = ?",
        (account_input,),
    ).fetchone()
    if row is not None:
        return row

    alias = account_input.strip().lower()
    rows = connection.execute(
        "SELECT id, institution, account_type FROM accounts "
        "WHERE LOWER(institution) = ?",
        (alias,),
    ).fetchall()
    if len(rows) == 1:
        return rows[0]
    if len(rows) > 1:
        raise CheckpointError(
            f"Institution alias '{account_input}' is ambiguous (matches "
            f"{len(rows)} accounts); pass an exact accounts.id instead."
        )
    raise CheckpointError(
        f"'{account_input}' did not resolve to any accounts.id or single "
        f"institution alias."
    )


def _section_key(connection: sqlite3.Connection, account: sqlite3.Row) -> str:
    """TOML section key for the account.

    Prefer the lowercased institution alias (the existing balances.toml
    convention, e.g. ``ally``) when that institution maps to exactly one
    account — so the entry merges with the alias-keyed ``[opening_balances]``
    that the loader resolves. Fall back to the exact account id when an
    institution has multiple accounts (the alias would be ambiguous).
    """
    institution = account["institution"]
    count = connection.execute(
        "SELECT COUNT(*) AS n FROM accounts WHERE LOWER(institution) = ?",
        (institution.strip().lower(),),
    ).fetchone()["n"]
    return institution.strip().lower() if count == 1 else account["id"]


def _write_checkpoint_entry(
    watch_root: Path,
    *,
    section_key: str,
    as_of: date,
    balance: float,
    note: str,
) -> Path:
    """Write ``"<date>" = <balance> # <note>`` under [statement_closings.<key>].

    Round-trips the existing TOML so every other entry and the file's extensive
    human comments survive untouched. Balances are written with two decimals
    (e.g. ``35000.00``) to match the existing entries.
    """
    # Imported lazily so a missing optional dependency degrades to "this one
    # tool errors" instead of bricking the whole MCP server at startup.
    try:
        import tomlkit
        from tomlkit.items import KeyType, SingleKey
    except ModuleNotFoundError as exc:  # pragma: no cover - environment guard
        raise CheckpointError(
            "upsert_balance_checkpoint requires the 'tomlkit' package "
            "(comment-preserving TOML writes). Install it into the server's "
            "Python environment, e.g. `pip install -e .` from the server/ "
            "directory, then restart the server."
        ) from exc

    path = watch_root / "balances.toml"
    if path.exists():
        doc = tomlkit.parse(path.read_text(encoding="utf-8"))
    else:
        watch_root.mkdir(parents=True, exist_ok=True)
        doc = tomlkit.document()
        doc.add(
            tomlkit.comment(
                "balances.toml — human-maintained balance anchors."
            )
        )
        doc.add(
            tomlkit.comment(
                "Explicit entries here always win over CSV-derived running_balance."
            )
        )

    closings = doc.get("statement_closings")
    if closings is None:
        closings = tomlkit.table(is_super_table=True)
        doc["statement_closings"] = closings

    section = closings.get(section_key)
    if section is None:
        section = tomlkit.table()
        closings[section_key] = section

    # Two-decimal float, preserved verbatim by tomlkit (it keeps the literal
    # token rather than re-rendering the Python float).
    value = tomlkit.parse(f"x = {float(balance):.2f}")["x"]
    clean_note = note.lstrip("#").strip().replace("\n", " ").replace("\r", " ")
    if clean_note:
        value.comment(clean_note)
    # Quoted/basic-string key (e.g. "2026-06-02"), matching existing entries.
    key = SingleKey(as_of.isoformat(), t=KeyType.Basic)
    _insert_in_order(section, key=key, value=value, as_of=as_of, note=clean_note)

    path.write_text(tomlkit.dumps(doc), encoding="utf-8")
    return path


def _insert_in_order(section, *, key, value, as_of: date, note: str) -> None:
    """Place ``"<date>" = <balance>`` in the table at a human-auditable spot.

    ``tomlkit`` attributes a trailing comment block to the *preceding* (often
    empty) table, so a plain ``section[key] = value`` appends the new entry
    *below* those comments — orphaning it just above the next section's header
    and making the next section's descriptive comments read as if they belong
    to this entry (Defect 3). Instead:

    * Overwrite in place when the date already exists (preserve its position).
    * Otherwise insert among existing dated entries in date order, before any
      trailing comments tomlkit has mis-attributed to this table.
    * For an empty table, drop the entry directly under the header (body index
      0) and keep a blank line before any trailing comment block so that block
      stays visually attached to the following section.
    """
    from tomlkit.items import Whitespace

    key_str = as_of.isoformat()
    if key_str in section:
        # Same date already present — overwrite value + comment, keep position.
        section[key] = value
        if note:
            section[key].comment(note)
        return

    container = section.value
    body = container.body
    keyed = [(idx, k) for idx, (k, _item) in enumerate(body) if k is not None]
    # A normal one-line entry terminates its own line; tomlkit does not add the
    # newline for a body-level insert, so set it explicitly.
    value.trivia.trail = "\n"

    if keyed:
        before_idx = None
        for idx, k in keyed:
            try:
                existing_date = date.fromisoformat(k.key)
            except ValueError:
                continue
            if as_of < existing_date:
                before_idx = idx
                break
        if before_idx is not None:
            container._insert_at(before_idx, key, value)
        else:
            # Later than every existing entry: append right after the last dated
            # entry (before any trailing comment that belongs to a later table).
            container._insert_after(keyed[-1][1], key, value)
        return

    if not body:
        # Truly empty table: a plain add lands directly under the header.
        section[key] = value
        return

    # Empty table whose body is only trailing trivia (whitespace/comments that
    # actually document the *following* section). Put the entry at the top and
    # keep a blank line before that trivia.
    if not isinstance(body[0][1], Whitespace):
        value.trivia.trail = "\n\n"
    container._insert_at(0, key, value)


def _run_end(today: date, checkpoint_date: date) -> date:
    """Last day of the month AFTER max(today, checkpoint month).

    Guarantees the reconcile range includes both the checkpoint month (in full)
    and a following "next period" the re-anchored forward carry lands on.
    """
    base = max(today, checkpoint_date)
    year, month = base.year, base.month
    if month == 12:
        year, month = year + 1, 1
    else:
        month += 1
    return date(year, month, monthrange(year, month)[1])


def _locate_periods(
    summaries: list[ReconciliationPeriodSummary], checkpoint_date: date
) -> tuple[ReconciliationPeriodSummary | None, ReconciliationPeriodSummary | None]:
    """Return (checkpoint period, next period) from the reconcile summaries."""
    checkpoint_period: ReconciliationPeriodSummary | None = None
    for summary in summaries:
        start = date.fromisoformat(summary.period_start)
        end = date.fromisoformat(summary.period_end)
        if start <= checkpoint_date <= end and summary.closing_balance_source == (
            CHECKPOINT_SOURCE
        ):
            checkpoint_period = summary
            break

    next_period: ReconciliationPeriodSummary | None = None
    if checkpoint_period is not None:
        cp_end = date.fromisoformat(checkpoint_period.period_end)
        candidates = [
            s for s in summaries if date.fromisoformat(s.period_start) > cp_end
        ]
        if candidates:
            next_period = min(
                candidates, key=lambda s: date.fromisoformat(s.period_start)
            )
    return checkpoint_period, next_period
