from __future__ import annotations

import json
import sqlite3
from datetime import date
from pathlib import Path

from liquidity_gate_mcp.balances import BalancesConfig, load_balances
from liquidity_gate_mcp.database import DatabaseManager
from liquidity_gate_mcp.models import ReconcilePeriodsRequest
from liquidity_gate_mcp.reconciliation import reconcile_periods


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


def _seed_batch(connection: sqlite3.Connection, batch_id: str = "batch-test") -> str:
    connection.execute(
        "INSERT OR IGNORE INTO import_batches (id, source_name, parser_version, "
        "imported_at, raw_payload) VALUES (?, ?, ?, ?, ?)",
        (batch_id, "seed", "test", "2026-05-13T00:00:00Z", "{}"),
    )
    return batch_id


def _insert_tx(
    connection: sqlite3.Connection,
    *,
    account_id: str,
    occurred_on: date,
    amount: float,
    direction: str,
    metadata: dict | None = None,
    tx_id: str | None = None,
) -> None:
    batch = _seed_batch(connection)
    connection.execute(
        """
        INSERT INTO transactions (
          id, account_id, import_batch_id, source_record_key,
          source_document_name, occurred_on, posted_on, description_raw,
          merchant_normalized, amount, direction, currency,
          primary_category, subcategory, household_role, lifecycle,
          transfer_group_key, statement_period, metadata_json
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, NULL, ?, ?, 'USD',
                  'unclassified', NULL, 'joint', 'recurring',
                  NULL, NULL, ?)
        """,
        (
            tx_id or f"tx-{occurred_on.isoformat()}-{amount}",
            account_id,
            batch,
            f"{account_id}-{occurred_on.isoformat()}-{amount}",
            "seed.csv",
            occurred_on.isoformat(),
            occurred_on.isoformat(),
            "row",
            amount,
            direction,
            json.dumps(metadata or {}),
        ),
    )


def _empty_balances(tmp_path: Path) -> BalancesConfig:
    # No file on disk → unloaded config sentinel.
    return load_balances(tmp_path)


def _balances_with(tmp_path: Path, body: str) -> BalancesConfig:
    (tmp_path / "balances.toml").write_text(body, encoding="utf-8")
    return load_balances(tmp_path)


def test_asset_account_computes_closing_from_opening_plus_net(
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
        # +$1,000 deposit, then +$50 interest -> opening 3113.44 + 1050 = 4163.44
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

    balances = _balances_with(
        tmp_path, '[opening_balances]\nally = 3113.44\n'
    )
    result = reconcile_periods(
        database,
        balances,
        ReconcilePeriodsRequest(
            period_start=date(2026, 1, 1), period_end=date(2026, 1, 31)
        ),
    )
    assert result.periods_written == 1
    summary = result.summaries[0]
    assert summary.statement_opening_balance == 3113.44
    assert summary.computed_inflows == 1050.0
    assert summary.computed_outflows == 0.0
    assert summary.computed_closing_balance == 4163.44


def test_credit_card_inverts_sign_for_closing_owed(
    database: DatabaseManager, tmp_path: Path
) -> None:
    connection = database.connect()
    try:
        _seed_account(
            connection,
            account_id="acct-chase-credit-card",
            institution="Chase",
            account_name="Credit Card",
            account_type="credit_card",
        )
        # Charge $100 (outflow, -100). Pay $200 (transfer, +200).
        # opening_owed = $1000.
        # closing_owed should be 1000 - (charges) + (payments) wait let me recompute.
        # signed_amount sum = -100 + 200 = 100
        # credit_card closing = opening - net_signed.
        # breakdown.net_signed = inflows + transfers_in - outflows - transfers_out
        #                     = 0 + 200 - 100 - 0 = 100
        # closing = 1000 - 100 = 900. Owed dropped by net (charges - payments = -100,
        # so debt went from 1000 -> 900). Correct.
        _insert_tx(
            connection,
            account_id="acct-chase-credit-card",
            occurred_on=date(2026, 1, 10),
            amount=-100.0,
            direction="outflow",
            tx_id="chase-charge",
        )
        _insert_tx(
            connection,
            account_id="acct-chase-credit-card",
            occurred_on=date(2026, 1, 20),
            amount=200.0,
            direction="transfer",
            tx_id="chase-payment",
        )
        connection.commit()
    finally:
        connection.close()

    balances = _balances_with(
        tmp_path, '[opening_balances]\nchase = 1000.00\n'
    )
    result = reconcile_periods(
        database,
        balances,
        ReconcilePeriodsRequest(
            period_start=date(2026, 1, 1), period_end=date(2026, 1, 31)
        ),
    )
    summary = result.summaries[0]
    assert summary.account_type == "credit_card"
    assert summary.computed_outflows == 100.0
    assert summary.computed_transfers_in == 200.0
    assert summary.computed_closing_balance == 900.0


def test_running_balance_metadata_drives_statement_closing(
    database: DatabaseManager, tmp_path: Path
) -> None:
    connection = database.connect()
    try:
        _seed_account(
            connection,
            account_id="acct-beacon-9999",
            institution="Beacon",
            account_name="Checking",
            account_type="checking",
        )
        _insert_tx(
            connection,
            account_id="acct-beacon-9999",
            occurred_on=date(2026, 1, 15),
            amount=500.0,
            direction="inflow",
            metadata={"running_balance": 17403.06},
            tx_id="beacon-mid",
        )
        # Last in-period row carries the period-end-ish running balance.
        _insert_tx(
            connection,
            account_id="acct-beacon-9999",
            occurred_on=date(2026, 1, 30),
            amount=-100.0,
            direction="outflow",
            metadata={"running_balance": 17303.06},
            tx_id="beacon-late",
        )
        connection.commit()
    finally:
        connection.close()

    balances = _balances_with(
        tmp_path, '[opening_balances]\nbeacon = 16903.06\n'
    )
    result = reconcile_periods(
        database,
        balances,
        ReconcilePeriodsRequest(
            period_start=date(2026, 1, 1), period_end=date(2026, 1, 31)
        ),
    )
    summary = result.summaries[0]
    assert summary.statement_closing_balance == 17303.06
    assert summary.closing_balance_source == "metadata_running_balance"
    # computed: 16903.06 + 500 - 100 = 17303.06 → variance is zero.
    assert summary.computed_closing_balance == 17303.06
    assert summary.variance_amount == 0.0


def test_explicit_balances_toml_wins_over_running_balance(
    database: DatabaseManager, tmp_path: Path
) -> None:
    connection = database.connect()
    try:
        _seed_account(
            connection,
            account_id="acct-beacon-9999",
            institution="Beacon",
            account_name="Checking",
            account_type="checking",
        )
        _insert_tx(
            connection,
            account_id="acct-beacon-9999",
            occurred_on=date(2026, 1, 30),
            amount=-100.0,
            direction="outflow",
            metadata={"running_balance": 17000.00},
        )
        connection.commit()
    finally:
        connection.close()

    body = (
        '[opening_balances]\nbeacon = 17100.00\n\n'
        '[statement_closings.beacon]\n"2026-01-31" = 16999.00\n'
    )
    balances = _balances_with(tmp_path, body)
    result = reconcile_periods(
        database,
        balances,
        ReconcilePeriodsRequest(
            period_start=date(2026, 1, 1), period_end=date(2026, 1, 31)
        ),
    )
    summary = result.summaries[0]
    assert summary.closing_balance_source == "balances_toml"
    assert summary.statement_closing_balance == 16999.00


def test_variance_chains_use_statement_closing_not_computed(
    database: DatabaseManager, tmp_path: Path
) -> None:
    # When month 1 has a $5 variance, month 2's opening must use the
    # STATEMENT closing — not computed — so the error doesn't cascade.
    connection = database.connect()
    try:
        _seed_account(
            connection,
            account_id="acct-beacon-9999",
            institution="Beacon",
            account_name="Checking",
            account_type="checking",
        )
        # Jan: +$1,000 inflow with running_balance saying 16000 (intentional $5 gap
        # vs computed 16903.06 + 1000 = 17903.06; statement says 17898.06 -> -$5 variance).
        _insert_tx(
            connection,
            account_id="acct-beacon-9999",
            occurred_on=date(2026, 1, 15),
            amount=1000.0,
            direction="inflow",
            metadata={"running_balance": 17898.06},
            tx_id="beacon-jan",
        )
        # Feb: +$2,000 inflow with running_balance 19898.06 (statement says 19898.06).
        # Computed Feb = opening (17898.06 from Jan statement) + 2000 = 19898.06.
        # If implementation rolled forward computed (17903.06) instead, Feb computed
        # would be 19903.06 and variance would be -$5, accumulating.
        _insert_tx(
            connection,
            account_id="acct-beacon-9999",
            occurred_on=date(2026, 2, 15),
            amount=2000.0,
            direction="inflow",
            metadata={"running_balance": 19898.06},
            tx_id="beacon-feb",
        )
        connection.commit()
    finally:
        connection.close()

    balances = _balances_with(tmp_path, '[opening_balances]\nbeacon = 16903.06\n')
    result = reconcile_periods(
        database,
        balances,
        ReconcilePeriodsRequest(
            period_start=date(2026, 1, 1), period_end=date(2026, 2, 28)
        ),
    )
    jan, feb = result.summaries
    assert jan.variance_amount == -5.0
    # Feb must open from Jan's statement (17898.06), not computed (17903.06).
    assert feb.statement_opening_balance == 17898.06
    assert feb.variance_amount == 0.0


def test_missing_opening_seed_emits_null_summary(
    database: DatabaseManager, tmp_path: Path
) -> None:
    connection = database.connect()
    try:
        _seed_account(
            connection,
            account_id="acct-orphan",
            institution="Mystery",
            account_name="Checking",
            account_type="checking",
        )
        _insert_tx(
            connection,
            account_id="acct-orphan",
            occurred_on=date(2026, 1, 10),
            amount=100.0,
            direction="inflow",
        )
        connection.commit()
    finally:
        connection.close()

    balances = _empty_balances(tmp_path)
    result = reconcile_periods(
        database,
        balances,
        ReconcilePeriodsRequest(
            period_start=date(2026, 1, 1), period_end=date(2026, 1, 31)
        ),
    )
    summary = result.summaries[0]
    assert summary.statement_opening_balance is None
    assert summary.computed_closing_balance is None
    assert summary.variance_amount is None
    # Breakdown still surfaces so Jeff can see activity exists.
    assert summary.computed_inflows == 100.0


def test_idempotent_rerun_preserves_variance_explanation(
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
            amount=100.0,
            direction="inflow",
        )
        connection.commit()
    finally:
        connection.close()

    balances = _balances_with(tmp_path, '[opening_balances]\nally = 1000.0\n')
    req = ReconcilePeriodsRequest(
        period_start=date(2026, 1, 1), period_end=date(2026, 1, 31)
    )
    reconcile_periods(database, balances, req)

    # Simulate Jeff writing an explanation directly into the table.
    connection = database.connect()
    try:
        connection.execute(
            "UPDATE reconciliation_periods SET variance_explanation = ? "
            "WHERE account_id = ?",
            ("Manual: $5 interest credit posted on 2/1", "acct-ally-hysa"),
        )
        connection.commit()
    finally:
        connection.close()

    # Re-run must not wipe the human note.
    second = reconcile_periods(database, balances, req)
    assert (
        second.summaries[0].variance_explanation
        == "Manual: $5 interest credit posted on 2/1"
    )

    # And the row count stays at one — UNIQUE constraint upserts.
    connection = database.connect(read_only=True)
    try:
        row = connection.execute(
            "SELECT COUNT(*) AS n FROM reconciliation_periods"
        ).fetchone()
        assert row["n"] == 1
    finally:
        connection.close()


def test_period_with_no_transactions_carries_opening_forward(
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
        # No transactions inserted for January.
        connection.commit()
    finally:
        connection.close()

    balances = _balances_with(tmp_path, '[opening_balances]\nally = 500.00\n')
    result = reconcile_periods(
        database,
        balances,
        ReconcilePeriodsRequest(
            period_start=date(2026, 1, 1), period_end=date(2026, 1, 31)
        ),
    )
    summary = result.summaries[0]
    assert summary.computed_inflows == 0
    assert summary.computed_closing_balance == 500.00
