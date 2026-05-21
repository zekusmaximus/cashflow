from __future__ import annotations

import json
import sqlite3
from datetime import date
from pathlib import Path

from liquidity_gate_mcp.balances import BalancesConfig, load_balances
from liquidity_gate_mcp.computed_balance import (
    ALLY_HYSA_GATE_KEY,
    refresh_hysa_gate,
    seed_balance_anchors,
)
from liquidity_gate_mcp.database import DatabaseManager


def _seed_account(
    connection: sqlite3.Connection,
    *,
    account_id: str,
    institution: str,
    account_name: str,
    account_type: str,
) -> None:
    connection.execute(
        "INSERT OR REPLACE INTO accounts (id, institution, account_name, "
        "account_type, owner, currency) VALUES (?, ?, ?, ?, ?, ?)",
        (account_id, institution, account_name, account_type, "joint", "USD"),
    )


def _insert_tx(
    connection: sqlite3.Connection,
    *,
    account_id: str,
    occurred_on: date,
    amount: float,
    direction: str,
    tx_id: str,
) -> None:
    connection.execute(
        "INSERT OR IGNORE INTO import_batches (id, source_name, parser_version, "
        "imported_at, raw_payload) VALUES (?, ?, ?, ?, ?)",
        ("batch-test", "seed", "test", "2026-05-13T00:00:00Z", "{}"),
    )
    connection.execute(
        """
        INSERT INTO transactions (
          id, account_id, import_batch_id, source_record_key,
          source_document_name, occurred_on, posted_on, description_raw,
          merchant_normalized, amount, direction, currency,
          primary_category, subcategory, household_role, lifecycle,
          transfer_group_key, statement_period, metadata_json
        ) VALUES (?, ?, 'batch-test', ?, 'seed.csv', ?, ?, 'row',
                  NULL, ?, ?, 'USD', 'unclassified', NULL, 'joint',
                  'recurring', NULL, NULL, ?)
        """,
        (
            tx_id,
            account_id,
            tx_id,
            occurred_on.isoformat(),
            occurred_on.isoformat(),
            amount,
            direction,
            json.dumps({}),
        ),
    )


def _balances_with(tmp_path: Path, body: str) -> BalancesConfig:
    (tmp_path / "balances.toml").write_text(body, encoding="utf-8")
    return load_balances(tmp_path)


def _view_row(database: DatabaseManager, account_id: str) -> sqlite3.Row | None:
    connection = database.connect(read_only=True)
    try:
        return connection.execute(
            "SELECT * FROM v_computed_balance WHERE account_id = ?",
            (account_id,),
        ).fetchone()
    finally:
        connection.close()


def test_view_anchors_on_seeded_opening_balance(
    database: DatabaseManager, tmp_path: Path
) -> None:
    connection = database.connect()
    try:
        _seed_account(
            connection,
            account_id="acct-ally-hysa",
            institution="Ally",
            account_name="HYSA",
            account_type="savings",
        )
        # +$1,000 deposit and +$50 interest after the 12/31/2025 anchor.
        _insert_tx(
            connection,
            account_id="acct-ally-hysa",
            occurred_on=date(2026, 1, 5),
            amount=1000.0,
            direction="inflow",
            tx_id="ally-1",
        )
        _insert_tx(
            connection,
            account_id="acct-ally-hysa",
            occurred_on=date(2026, 1, 28),
            amount=50.0,
            direction="inflow",
            tx_id="ally-2",
        )
        connection.commit()
    finally:
        connection.close()

    balances = _balances_with(tmp_path, "[opening_balances]\nally = 3113.44\n")
    seed_balance_anchors(database, balances)

    row = _view_row(database, "acct-ally-hysa")
    assert row is not None
    assert row["anchor_date"] == "2025-12-31"
    assert row["anchor_balance"] == 3113.44
    assert row["net_since_anchor"] == 1050.0
    assert row["computed_balance"] == 4163.44


def test_view_includes_transfers_in_net(
    database: DatabaseManager, tmp_path: Path
) -> None:
    connection = database.connect()
    try:
        _seed_account(
            connection,
            account_id="acct-ally-hysa",
            institution="Ally",
            account_name="HYSA",
            account_type="savings",
        )
        _insert_tx(
            connection,
            account_id="acct-ally-hysa",
            occurred_on=date(2026, 1, 10),
            amount=200.0,
            direction="inflow",
            tx_id="ally-inflow",
        )
        # Transfers are real money movement on the account: a transfer in
        # raises the balance and a transfer out lowers it, by the stored
        # signed amount.
        _insert_tx(
            connection,
            account_id="acct-ally-hysa",
            occurred_on=date(2026, 1, 12),
            amount=5000.0,
            direction="transfer",
            tx_id="ally-transfer-in",
        )
        _insert_tx(
            connection,
            account_id="acct-ally-hysa",
            occurred_on=date(2026, 1, 20),
            amount=-1500.0,
            direction="transfer",
            tx_id="ally-transfer-out",
        )
        connection.commit()
    finally:
        connection.close()

    seed_balance_anchors(
        database, _balances_with(tmp_path, "[opening_balances]\nally = 3113.44\n")
    )

    row = _view_row(database, "acct-ally-hysa")
    # 200 inflow + 5000 transfer in - 1500 transfer out.
    assert row["net_since_anchor"] == 3700.0
    assert row["computed_balance"] == 6813.44


def test_account_without_opening_has_null_computed_balance(
    database: DatabaseManager, tmp_path: Path
) -> None:
    connection = database.connect()
    try:
        _seed_account(
            connection,
            account_id="acct-citi-credit-card",
            institution="Citi",
            account_name="Credit Card",
            account_type="credit_card",
        )
        _insert_tx(
            connection,
            account_id="acct-citi-credit-card",
            occurred_on=date(2026, 2, 1),
            amount=-75.0,
            direction="outflow",
            tx_id="citi-charge",
        )
        connection.commit()
    finally:
        connection.close()

    # balances.toml has no opening for Citi.
    seed_balance_anchors(
        database, _balances_with(tmp_path, "[opening_balances]\nally = 3113.44\n")
    )

    row = _view_row(database, "acct-citi-credit-card")
    assert row is not None
    assert row["anchor_date"] is None
    assert row["anchor_balance"] is None
    assert row["net_since_anchor"] is None
    assert row["computed_balance"] is None


def test_refresh_hysa_gate_updates_current_amount(
    database: DatabaseManager, tmp_path: Path
) -> None:
    connection = database.connect()
    try:
        _seed_account(
            connection,
            account_id="acct-ally-hysa",
            institution="Ally",
            account_name="HYSA",
            account_type="savings",
        )
        _insert_tx(
            connection,
            account_id="acct-ally-hysa",
            occurred_on=date(2026, 1, 15),
            amount=400.0,
            direction="inflow",
            tx_id="ally-dep",
        )
        connection.commit()
    finally:
        connection.close()

    # The gate row is seeded by schema.sql with a placeholder current_amount.
    connection = database.connect(read_only=True)
    try:
        before = connection.execute(
            "SELECT current_amount FROM liquidity_gates WHERE gate_key = ?",
            (ALLY_HYSA_GATE_KEY,),
        ).fetchone()
        assert before["current_amount"] == 0
    finally:
        connection.close()

    seed_balance_anchors(
        database, _balances_with(tmp_path, "[opening_balances]\nally = 3113.44\n")
    )
    result = refresh_hysa_gate(database)

    assert result.updated is True
    assert result.anchor_date == "2025-12-31"
    assert result.computed_balance == 3513.44

    connection = database.connect(read_only=True)
    try:
        after = connection.execute(
            "SELECT current_amount FROM liquidity_gates WHERE gate_key = ?",
            (ALLY_HYSA_GATE_KEY,),
        ).fetchone()
        assert after["current_amount"] == 3513.44
    finally:
        connection.close()


def test_statement_closing_shifts_anchor_forward(
    database: DatabaseManager, tmp_path: Path
) -> None:
    connection = database.connect()
    try:
        _seed_account(
            connection,
            account_id="acct-ally-hysa",
            institution="Ally",
            account_name="HYSA",
            account_type="savings",
        )
        # Pre-anchor January activity must be ignored once the 1/31 closing
        # becomes the anchor; only February activity should count.
        _insert_tx(
            connection,
            account_id="acct-ally-hysa",
            occurred_on=date(2026, 1, 20),
            amount=999.0,
            direction="inflow",
            tx_id="ally-jan",
        )
        _insert_tx(
            connection,
            account_id="acct-ally-hysa",
            occurred_on=date(2026, 2, 10),
            amount=300.0,
            direction="inflow",
            tx_id="ally-feb",
        )
        connection.commit()
    finally:
        connection.close()

    body = (
        "[opening_balances]\nally = 3113.44\n\n"
        '[statement_closings.ally]\n"2026-01-31" = 5000.00\n'
    )
    seed_balance_anchors(database, _balances_with(tmp_path, body))

    row = _view_row(database, "acct-ally-hysa")
    assert row["anchor_date"] == "2026-01-31"
    assert row["anchor_balance"] == 5000.0
    # January inflow is before the anchor; only the February $300 counts.
    assert row["net_since_anchor"] == 300.0
    assert row["computed_balance"] == 5300.0


def test_seed_fills_null_closing_on_existing_period(
    database: DatabaseManager, tmp_path: Path
) -> None:
    # reconcile_periods writes a skeleton Dec-2025 row with a NULL closing for
    # accounts with no running balance; seeding must fill it in place.
    connection = database.connect()
    try:
        _seed_account(
            connection,
            account_id="acct-ally-hysa",
            institution="Ally",
            account_name="HYSA",
            account_type="savings",
        )
        connection.execute(
            "INSERT INTO reconciliation_periods (id, account_id, period_start, "
            "period_end, statement_opening_balance, statement_closing_balance, "
            "closing_balance_source) VALUES ('recon-pre', 'acct-ally-hysa', "
            "'2025-12-01', '2025-12-31', 3113.44, NULL, NULL)"
        )
        connection.commit()
    finally:
        connection.close()

    filled = seed_balance_anchors(
        database, _balances_with(tmp_path, "[opening_balances]\nally = 3113.44\n")
    )
    assert filled == 1

    row = _view_row(database, "acct-ally-hysa")
    assert row["anchor_date"] == "2025-12-31"
    assert row["anchor_balance"] == 3113.44

    # Filled in place — not duplicated.
    connection = database.connect(read_only=True)
    try:
        count = connection.execute(
            "SELECT COUNT(*) AS n FROM reconciliation_periods "
            "WHERE account_id = 'acct-ally-hysa'"
        ).fetchone()
        assert count["n"] == 1
    finally:
        connection.close()


def test_seed_never_overwrites_a_real_closing(
    database: DatabaseManager, tmp_path: Path
) -> None:
    connection = database.connect()
    try:
        _seed_account(
            connection,
            account_id="acct-ally-hysa",
            institution="Ally",
            account_name="HYSA",
            account_type="savings",
        )
        # A real reconciled statement closing already on the books.
        connection.execute(
            "INSERT INTO reconciliation_periods (id, account_id, period_start, "
            "period_end, statement_opening_balance, statement_closing_balance, "
            "closing_balance_source) VALUES ('recon-real', 'acct-ally-hysa', "
            "'2025-12-01', '2025-12-31', NULL, 2500.00, 'balances_toml')"
        )
        connection.commit()
    finally:
        connection.close()

    seed_balance_anchors(
        database, _balances_with(tmp_path, "[opening_balances]\nally = 3113.44\n")
    )

    row = _view_row(database, "acct-ally-hysa")
    assert row["anchor_balance"] == 2500.00


def test_seed_balance_anchors_is_idempotent(
    database: DatabaseManager, tmp_path: Path
) -> None:
    connection = database.connect()
    try:
        _seed_account(
            connection,
            account_id="acct-ally-hysa",
            institution="Ally",
            account_name="HYSA",
            account_type="savings",
        )
        connection.commit()
    finally:
        connection.close()

    balances = _balances_with(tmp_path, "[opening_balances]\nally = 3113.44\n")
    first = seed_balance_anchors(database, balances)
    second = seed_balance_anchors(database, balances)

    assert first == 1
    assert second == 0

    connection = database.connect(read_only=True)
    try:
        count = connection.execute(
            "SELECT COUNT(*) AS n FROM reconciliation_periods "
            "WHERE account_id = 'acct-ally-hysa'"
        ).fetchone()
        assert count["n"] == 1
    finally:
        connection.close()
