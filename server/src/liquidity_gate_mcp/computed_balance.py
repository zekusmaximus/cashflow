"""Anchor-based computed balances for the Liquidity Gate.

Ally and Chase CSV exports carry no running balance, so an account's absolute
position cannot be read directly — only relative flows. This module bridges
that gap: it materialises the ``balances.toml`` anchors into
``reconciliation_periods`` rows and exposes the ``refresh-hysa-gate`` command
that pushes the SQL view ``v_computed_balance`` onto the Ally HYSA liquidity
gate.

The view itself lives in ``server/sql/schema.sql``; this module only handles
the parts that need to read TOML (seeding) or write the gate (refresh).
"""

from __future__ import annotations

from dataclasses import dataclass
import sqlite3

from .balances import BalancesConfig
from .database import DatabaseManager


# The 12/31/2025 opening balances in balances.toml are the closing balances of
# December 2025 — we materialise them as a Dec-2025 reconciliation period so
# v_computed_balance can anchor on plain SQL without ever reading TOML.
OPENING_PERIOD_START = "2025-12-01"
OPENING_PERIOD_END = "2025-12-31"
OPENING_SOURCE = "balances_toml_opening"
CLOSING_SOURCE = "balances_toml"

ALLY_HYSA_ACCOUNT_ID = "acct-ally-hysa"
ALLY_HYSA_GATE_KEY = "ally_hysa"


@dataclass(frozen=True)
class RefreshHysaGateResult:
    anchor_date: str | None
    anchor_balance: float | None
    net_since_anchor: float | None
    computed_balance: float | None
    updated: bool

    def as_dict(self) -> dict:
        return {
            "anchor_date": self.anchor_date,
            "anchor_balance": self.anchor_balance,
            "net_since_anchor": self.net_since_anchor,
            "computed_balance": self.computed_balance,
            "updated": self.updated,
        }


def seed_balance_anchors(database: DatabaseManager, balances: BalancesConfig) -> int:
    """Materialise balances.toml into reconciliation_periods anchor rows.

    Two kinds of rows are seeded so ``v_computed_balance`` can anchor purely in
    SQL, without the view ever parsing TOML:

    * One Dec-2025 row per account carrying its 12/31/2025 opening balance as
      ``statement_closing_balance``. Accounts with no opening (e.g. Citi,
      untracked at 2025-12-31) still get a row, with a NULL closing, so the
      view treats them as unknown-anchor rather than ignoring them.
    * One row per ``[statement_closings.<account>]`` entry, keyed to that
      calendar month, so a freshly added statement closing immediately becomes
      the most-recent anchor.

    Non-destructive and idempotent. ``reconcile_periods`` walks every month
    from 2025-01 forward and writes skeleton rows whose ``statement_closing_
    balance`` is NULL whenever no statement or running balance is available
    (true for Ally and Chase, which have no running balance in their CSVs).
    Seeding therefore *fills a NULL closing* on an existing account/period row
    rather than skipping it — otherwise the 12/31/2025 anchor would never
    materialise. A row whose closing is already non-NULL (a real reconciled
    statement) is never touched. Returns the number of rows inserted or
    filled.
    """
    connection = database.connect()
    try:
        accounts = connection.execute(
            "SELECT id, institution FROM accounts"
        ).fetchall()
        seeded = 0
        for account in accounts:
            entry = balances.lookup(
                account_id=account["id"], institution=account["institution"]
            )
            seeded += _insert_anchor(
                connection,
                row_id=f"recon-opening-{account['id']}",
                account_id=account["id"],
                period_start=OPENING_PERIOD_START,
                period_end=OPENING_PERIOD_END,
                closing=entry.opening_balance,
                source=OPENING_SOURCE if entry.opening_balance is not None else None,
            )
            for period_end, closing in entry.statement_closings.items():
                period_start = period_end.replace(day=1)
                seeded += _insert_anchor(
                    connection,
                    row_id=f"recon-closing-{account['id']}-{period_end.isoformat()}",
                    account_id=account["id"],
                    period_start=period_start.isoformat(),
                    period_end=period_end.isoformat(),
                    closing=closing,
                    source=CLOSING_SOURCE,
                )
        connection.commit()
        return seeded
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()


def _insert_anchor(
    connection: sqlite3.Connection,
    *,
    row_id: str,
    account_id: str,
    period_start: str,
    period_end: str,
    closing: float | None,
    source: str | None,
) -> int:
    existing = connection.execute(
        "SELECT id, statement_closing_balance FROM reconciliation_periods "
        "WHERE account_id = ? AND period_start = ? AND period_end = ?",
        (account_id, period_start, period_end),
    ).fetchone()

    if existing is None:
        connection.execute(
            """
            INSERT INTO reconciliation_periods (
              id, account_id, period_start, period_end,
              statement_opening_balance, statement_closing_balance,
              closing_balance_source
            ) VALUES (?, ?, ?, ?, NULL, ?, ?)
            """,
            (row_id, account_id, period_start, period_end, closing, source),
        )
        return 1

    # A row exists. Never overwrite a real (non-NULL) closing — that would
    # clobber a reconciled statement. Only fill a NULL closing with the
    # balances.toml anchor value.
    if existing["statement_closing_balance"] is None and closing is not None:
        connection.execute(
            "UPDATE reconciliation_periods SET statement_closing_balance = ?, "
            "closing_balance_source = ? WHERE id = ?",
            (closing, source, existing["id"]),
        )
        return 1
    return 0


def refresh_hysa_gate(database: DatabaseManager) -> RefreshHysaGateResult:
    """Recompute the Ally HYSA gate's current_amount from v_computed_balance.

    Reads the anchor-based estimate for ``acct-ally-hysa`` and writes it onto
    ``liquidity_gates.current_amount`` for ``gate_key = 'ally_hysa'``. When no
    anchor exists (``computed_balance`` is NULL) the gate is left untouched and
    ``updated`` is False.
    """
    connection = database.connect()
    try:
        row = connection.execute(
            "SELECT anchor_date, anchor_balance, net_since_anchor, "
            "computed_balance FROM v_computed_balance WHERE account_id = ?",
            (ALLY_HYSA_ACCOUNT_ID,),
        ).fetchone()

        anchor_date = row["anchor_date"] if row else None
        anchor_balance = row["anchor_balance"] if row else None
        net_since_anchor = row["net_since_anchor"] if row else None
        computed_balance = row["computed_balance"] if row else None

        updated = False
        if computed_balance is not None:
            cursor = connection.execute(
                "UPDATE liquidity_gates SET current_amount = ? WHERE gate_key = ?",
                (computed_balance, ALLY_HYSA_GATE_KEY),
            )
            updated = cursor.rowcount > 0
        connection.commit()

        return RefreshHysaGateResult(
            anchor_date=anchor_date,
            anchor_balance=anchor_balance,
            net_since_anchor=net_since_anchor,
            computed_balance=computed_balance,
            updated=updated,
        )
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()


def main() -> None:
    """Console-script entry point for the ``refresh-hysa-gate`` command.

    Initialises the shared database, re-seeds the balances.toml anchors (so a
    newly added ``[statement_closings.ally]`` entry is picked up), then pushes
    the computed balance onto the Ally HYSA liquidity gate.
    """
    from .balances import load_balances
    from .config import load_settings

    settings = load_settings()
    database = DatabaseManager(settings.database_path, settings.schema_path)
    database.initialize()
    balances = load_balances(settings.watch_root)
    seed_balance_anchors(database, balances)
    result = refresh_hysa_gate(database)

    print(
        f"[refresh-hysa-gate] anchor={result.anchor_date} "
        f"anchor_balance={result.anchor_balance} "
        f"net={result.net_since_anchor} "
        f"computed={result.computed_balance}"
    )
    if not result.updated:
        print(
            "[refresh-hysa-gate] gate current_amount not updated — "
            "no anchor available or gate row missing."
        )


if __name__ == "__main__":
    main()
