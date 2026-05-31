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


# ---------------------------------------------------------------------------
# Running-balance chain: terminal-row selection on a busy final date
# ---------------------------------------------------------------------------
# Beacon CSV is newest-first; Webster is oldest-first.  In both layouts the
# terminal row (highest rb) is NOT necessarily the one with the
# lexicographically-last id.  The chain-reconstruction algorithm must
# identify it regardless of ID sort order.


def test_running_balance_chain_selects_terminal_newest_first(
    database: DatabaseManager, tmp_path: Path
) -> None:
    """Beacon-style (newest-first): terminal row has a lexicographically-lower id.

    Old id-DESC tiebreaker would pick the non-terminal row; chain logic must
    pick the terminal (higher running_balance) instead.

    Chain: tx-zz-webxfr (+5000, rb=23859.64) → tx-aa-deposit (+4694.21, rb=28553.85)
    Because 23859.64 + 4694.21 == 28553.85, tx-zz-webxfr is a predecessor.
    Terminal = tx-aa-deposit, rb=28553.85.
    """
    connection = database.connect()
    try:
        _seed_account(
            connection,
            account_id="acct-beacon-chain",
            institution="Beacon",
            account_name="Checking",
            account_type="checking",
        )
        # Mid-month row (single, no ambiguity)
        _insert_tx(
            connection,
            account_id="acct-beacon-chain",
            occurred_on=date(2026, 5, 15),
            amount=500.0,
            direction="inflow",
            metadata={"running_balance": 19164.43},
            tx_id="tx-mid",
        )
        # May-29: two rows.  tx-zz-webxfr sorts HIGHER by id → old code picks it.
        _insert_tx(
            connection,
            account_id="acct-beacon-chain",
            occurred_on=date(2026, 5, 29),
            amount=5000.0,
            direction="inflow",
            metadata={"running_balance": 23859.64},
            tx_id="tx-zz-webxfr",  # non-terminal, lexicographically higher id
        )
        _insert_tx(
            connection,
            account_id="acct-beacon-chain",
            occurred_on=date(2026, 5, 29),
            amount=4694.21,
            direction="inflow",
            metadata={"running_balance": 28553.85},
            tx_id="tx-aa-deposit",  # terminal, lexicographically lower id
        )
        connection.commit()
    finally:
        connection.close()

    # opening = 18664.43; +500 (mid) + 5000 + 4694.21 (May-29) = 28858.64 computed.
    # But statement close from chain = 28553.85 (terminal running_balance).
    balances = _balances_with(tmp_path, '[opening_balances]\nbeacon = 18664.43\n')
    result = reconcile_periods(
        database,
        balances,
        ReconcilePeriodsRequest(
            period_start=date(2026, 5, 1), period_end=date(2026, 5, 31)
        ),
    )
    summary = result.summaries[0]
    assert summary.statement_closing_balance == 28553.85
    assert summary.closing_balance_source == "metadata_running_balance"


def test_running_balance_chain_selects_terminal_oldest_first(
    database: DatabaseManager, tmp_path: Path
) -> None:
    """Webster-style (oldest-first): terminal row has a lexicographically-higher id.

    Both old and new code pick the terminal here, but we verify chain logic
    returns it for the right reason.

    Chain: tx-aa-webxfr (+5000, rb=23859.64) → tx-zz-deposit (+4694.21, rb=28553.85)
    Terminal = tx-zz-deposit, rb=28553.85.
    """
    connection = database.connect()
    try:
        _seed_account(
            connection,
            account_id="acct-beacon-chain2",
            institution="Beacon",
            account_name="Checking",
            account_type="checking",
        )
        _insert_tx(
            connection,
            account_id="acct-beacon-chain2",
            occurred_on=date(2026, 5, 29),
            amount=5000.0,
            direction="inflow",
            metadata={"running_balance": 23859.64},
            tx_id="tx-aa-webxfr",  # non-terminal, lexicographically lower id
        )
        _insert_tx(
            connection,
            account_id="acct-beacon-chain2",
            occurred_on=date(2026, 5, 29),
            amount=4694.21,
            direction="inflow",
            metadata={"running_balance": 28553.85},
            tx_id="tx-zz-deposit",  # terminal, lexicographically higher id
        )
        connection.commit()
    finally:
        connection.close()

    balances = _balances_with(tmp_path, '[opening_balances]\nbeacon = 18164.43\n')
    result = reconcile_periods(
        database,
        balances,
        ReconcilePeriodsRequest(
            period_start=date(2026, 5, 1), period_end=date(2026, 5, 31)
        ),
    )
    summary = result.summaries[0]
    assert summary.statement_closing_balance == 28553.85
    assert summary.closing_balance_source == "metadata_running_balance"


def test_running_balance_chain_selects_terminal_three_rows(
    database: DatabaseManager, tmp_path: Path
) -> None:
    """Three rows on the final date — chain A→B→C; C is the terminal."""
    connection = database.connect()
    try:
        _seed_account(
            connection,
            account_id="acct-beacon-chain3",
            institution="Beacon",
            account_name="Checking",
            account_type="checking",
        )
        # A (+1000, rb=11000) → B (+2000, rb=13000) → C (+3000, rb=16000)
        # IDs in reverse alphabetical order so id DESC picks A (non-terminal).
        _insert_tx(
            connection,
            account_id="acct-beacon-chain3",
            occurred_on=date(2026, 5, 31),
            amount=1000.0,
            direction="inflow",
            metadata={"running_balance": 11000.0},
            tx_id="tx-c-row-a",  # lexicographically highest → old code picks this
        )
        _insert_tx(
            connection,
            account_id="acct-beacon-chain3",
            occurred_on=date(2026, 5, 31),
            amount=2000.0,
            direction="inflow",
            metadata={"running_balance": 13000.0},
            tx_id="tx-b-row-b",
        )
        _insert_tx(
            connection,
            account_id="acct-beacon-chain3",
            occurred_on=date(2026, 5, 31),
            amount=3000.0,
            direction="inflow",
            metadata={"running_balance": 16000.0},
            tx_id="tx-a-row-c",  # lexicographically lowest → correct terminal
        )
        connection.commit()
    finally:
        connection.close()

    balances = _balances_with(tmp_path, '[opening_balances]\nbeacon = 10000.0\n')
    result = reconcile_periods(
        database,
        balances,
        ReconcilePeriodsRequest(
            period_start=date(2026, 5, 1), period_end=date(2026, 5, 31)
        ),
    )
    summary = result.summaries[0]
    assert summary.statement_closing_balance == 16000.0
    assert summary.closing_balance_source == "metadata_running_balance"
    # computed: 10000 + 1000 + 2000 + 3000 = 16000 → zero variance
    assert summary.variance_amount == 0.0


def test_running_balance_chain_no_running_balance_returns_none(
    database: DatabaseManager, tmp_path: Path
) -> None:
    """Chase/Ally/Citi rows (no running_balance) must still return (None, None)."""
    connection = database.connect()
    try:
        _seed_account(
            connection,
            account_id="acct-chase-checking",
            institution="Chase",
            account_name="Checking",
            account_type="checking",
        )
        _insert_tx(
            connection,
            account_id="acct-chase-checking",
            occurred_on=date(2026, 5, 29),
            amount=500.0,
            direction="inflow",
            # No running_balance key in metadata
            metadata={"source": "chase"},
            tx_id="tx-chase-1",
        )
        _insert_tx(
            connection,
            account_id="acct-chase-checking",
            occurred_on=date(2026, 5, 29),
            amount=250.0,
            direction="inflow",
            metadata={"source": "chase"},
            tx_id="tx-chase-2",
        )
        connection.commit()
    finally:
        connection.close()

    balances = _balances_with(tmp_path, '[opening_balances]\nchase = 5000.0\n')
    result = reconcile_periods(
        database,
        balances,
        ReconcilePeriodsRequest(
            period_start=date(2026, 5, 1), period_end=date(2026, 5, 31)
        ),
    )
    summary = result.summaries[0]
    assert summary.statement_closing_balance is None
    assert summary.closing_balance_source is None
    assert summary.variance_amount is None
