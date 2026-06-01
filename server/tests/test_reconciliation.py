from __future__ import annotations

import json
import sqlite3
from datetime import date
from pathlib import Path

from liquidity_gate_mcp.balances import BalancesConfig, load_balances
from liquidity_gate_mcp.database import DatabaseManager
from liquidity_gate_mcp.models import ReconcilePeriodsRequest
from liquidity_gate_mcp.reconciliation import (
    reconcile_periods,
    reconstruct_intraday_chain,
)


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


# ---------------------------------------------------------------------------
# BUG 1 — intra-day chain reconstruction from real institution data
# ---------------------------------------------------------------------------
# Same-day rows must be ordered by reconstructing the running-balance chain
# (predecessor = running_balance - amount), never by row/insertion order. The
# fixtures below are the exact real Webster and Beacon same-day tables.


# Webster checking, 2026-02-25: three same-day transfers, reverse-chronological
# in the CSV.  running_balance is the balance AFTER the transaction.
WEBSTER_2026_02_25 = [
    {"id": "c2a9576c71d09dd1", "amount": -2000.00, "running_balance": 12602.33},
    {"id": "c4fb496f179e7540", "amount": -5000.00, "running_balance": 7602.33},
    {"id": "bcb425ada0f9fbdb", "amount": -5000.00, "running_balance": 2602.33},
]

# Beacon checking, 2026-01-05: four same-day rows, forward-chronological.
BEACON_2026_01_05 = [
    {"id": "8e403ceb058d16f8", "amount": 2500.00, "running_balance": 13184.54},
    {"id": "e026d2034bdcefa1", "amount": 5805.90, "running_balance": 18990.44},
    {"id": "0f0322f4ce2aef8c", "amount": -876.90, "running_balance": 18113.54},
    {"id": "5b6440e9a784d921", "amount": -4044.66, "running_balance": 14068.88},
]


def test_intraday_chain_webster_terminal_and_head() -> None:
    """DoD 1 — Webster 2026-02-25: closing 2602.33, opening 14602.33."""
    chain = reconstruct_intraday_chain(WEBSTER_2026_02_25)
    assert chain.ambiguous is False
    assert chain.closing == 2602.33
    assert chain.opening == 14602.33


def test_intraday_chain_beacon_no_regression() -> None:
    """DoD 2 — Beacon 2026-01-05: closing 14068.88, chain-head opening 10684.54."""
    chain = reconstruct_intraday_chain(BEACON_2026_01_05)
    assert chain.ambiguous is False
    assert chain.closing == 14068.88
    assert chain.opening == 10684.54


def test_intraday_chain_single_row() -> None:
    """DoD 3 — a lone same-day row: closing equals its running_balance."""
    chain = reconstruct_intraday_chain(
        [{"id": "solo", "amount": -100.0, "running_balance": 5400.0}]
    )
    assert chain.ambiguous is False
    assert chain.closing == 5400.0
    assert chain.opening == 5500.0


def test_intraday_chain_broken_flags_ambiguous() -> None:
    """DoD 4 — rows that cannot be linked into one chain flag ambiguity."""
    broken = [
        # 100 -> 110 is a valid link, but 480 -> 500 is a disjoint second run.
        {"id": "a", "amount": 10.0, "running_balance": 110.0},
        {"id": "b", "amount": 20.0, "running_balance": 500.0},
    ]
    chain = reconstruct_intraday_chain(broken)
    assert chain.ambiguous is True
    assert chain.closing is None
    assert chain.opening is None


def _insert_chain_rows(
    connection: sqlite3.Connection,
    account_id: str,
    occurred_on: date,
    rows: list[dict],
    direction: str = "transfer",
) -> None:
    # Insert directly so each row gets a distinct source_record_key — two of the
    # real Webster transfers share both date and amount (-5000.00), which would
    # collide under _insert_tx's date+amount key.
    batch = _seed_batch(connection)
    for row in rows:
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
                row["id"],
                account_id,
                batch,
                row["id"],
                "seed.csv",
                occurred_on.isoformat(),
                occurred_on.isoformat(),
                "row",
                row["amount"],
                direction,
                json.dumps({"running_balance": row["running_balance"]}),
            ),
        )


def test_webster_two_month_variance_is_zero(
    database: DatabaseManager, tmp_path: Path
) -> None:
    """DoD 5 — Feb+Mar Webster: both monthly variances are 0.00 (no ±10000 pair).

    Feb closes on the busy 2026-02-25 day (chain terminal 2602.33); March opens
    from Feb's statement closing and reconciles cleanly. Picking the wrong
    same-day row would inflate Feb by 10000 and depress March by 10000.
    """
    connection = database.connect()
    try:
        _seed_account(
            connection,
            account_id="acct-webster-checking",
            institution="Webster",
            account_name="Checking",
            account_type="checking",
        )
        # Feb: three same-day transfers; chain head 14602.33, terminal 2602.33.
        _insert_chain_rows(
            connection,
            "acct-webster-checking",
            date(2026, 2, 25),
            WEBSTER_2026_02_25,
        )
        # March: a single inflow that lands the statement on 3602.33.
        _insert_tx(
            connection,
            account_id="acct-webster-checking",
            occurred_on=date(2026, 3, 10),
            amount=1000.0,
            direction="inflow",
            metadata={"running_balance": 3602.33},
            tx_id="webster-mar",
        )
        connection.commit()
    finally:
        connection.close()

    # Feb-1 opening seed equals the reconstructed chain head.
    balances = _balances_with(tmp_path, '[opening_balances]\nwebster = 14602.33\n')
    result = reconcile_periods(
        database,
        balances,
        ReconcilePeriodsRequest(
            period_start=date(2026, 2, 1), period_end=date(2026, 3, 31)
        ),
    )
    feb, mar = result.summaries
    assert feb.statement_closing_balance == 2602.33
    assert feb.computed_closing_balance == 2602.33
    assert feb.variance_amount == 0.0
    # March opens from Feb's statement closing, not the Jan-1 seed.
    assert mar.statement_opening_balance == 2602.33
    assert mar.statement_closing_balance == 3602.33
    assert mar.variance_amount == 0.0


def test_broken_chain_flags_variance_explanation(
    database: DatabaseManager, tmp_path: Path
) -> None:
    """DoD 4 (integration) — an unlinkable final date writes the ambiguity note."""
    connection = database.connect()
    try:
        _seed_account(
            connection,
            account_id="acct-beacon-broken",
            institution="Beacon",
            account_name="Checking",
            account_type="checking",
        )
        # Two same-day rows whose running balances do not chain together.
        _insert_tx(
            connection,
            account_id="acct-beacon-broken",
            occurred_on=date(2026, 5, 31),
            amount=10.0,
            direction="inflow",
            metadata={"running_balance": 110.0},
            tx_id="broken-a",
        )
        _insert_tx(
            connection,
            account_id="acct-beacon-broken",
            occurred_on=date(2026, 5, 31),
            amount=20.0,
            direction="inflow",
            metadata={"running_balance": 500.0},
            tx_id="broken-b",
        )
        connection.commit()
    finally:
        connection.close()

    balances = _balances_with(tmp_path, '[opening_balances]\nbeacon = 100.0\n')
    result = reconcile_periods(
        database,
        balances,
        ReconcilePeriodsRequest(
            period_start=date(2026, 5, 1), period_end=date(2026, 5, 31)
        ),
    )
    summary = result.summaries[0]
    assert summary.closing_balance_source == "metadata_running_balance_unresolved"
    assert "Ambiguous intraday ordering" in summary.variance_explanation
    # Fell back to the highest running balance rather than returning nothing.
    assert summary.statement_closing_balance == 500.0


# ---------------------------------------------------------------------------
# BUG 2 — single-month reconcile chains from the prior stored period
# ---------------------------------------------------------------------------


def _insert_period_row(
    connection: sqlite3.Connection,
    *,
    account_id: str,
    period_start: date,
    period_end: date,
    statement_closing: float | None,
    computed_closing: float | None,
) -> None:
    connection.execute(
        """
        INSERT INTO reconciliation_periods (
          id, account_id, period_start, period_end,
          statement_opening_balance, statement_closing_balance,
          closing_balance_source, computed_closing_balance
        ) VALUES (?, ?, ?, ?, NULL, ?, ?, ?)
        """,
        (
            f"prior-{account_id}-{period_end.isoformat()}",
            account_id,
            period_start.isoformat(),
            period_end.isoformat(),
            statement_closing,
            "balances_toml" if statement_closing is not None else None,
            computed_closing,
        ),
    )


def test_single_month_seeds_from_prior_statement_closing(
    database: DatabaseManager, tmp_path: Path
) -> None:
    """DoD 6 — a May-only run opens from April's stored closing, not the seed."""
    connection = database.connect()
    try:
        _seed_account(
            connection,
            account_id="acct-beacon-9999",
            institution="Beacon",
            account_name="Checking",
            account_type="checking",
        )
        # April already reconciled and stored a closing of 26249.90.
        _insert_period_row(
            connection,
            account_id="acct-beacon-9999",
            period_start=date(2026, 4, 1),
            period_end=date(2026, 4, 30),
            statement_closing=26249.90,
            computed_closing=26249.90,
        )
        _insert_tx(
            connection,
            account_id="acct-beacon-9999",
            occurred_on=date(2026, 5, 5),
            amount=100.0,
            direction="inflow",
            tx_id="beacon-may",
        )
        connection.commit()
    finally:
        connection.close()

    # The Jan-1 seed (10684.54) must NOT be used for a May-only run.
    balances = _balances_with(tmp_path, '[opening_balances]\nbeacon = 10684.54\n')
    result = reconcile_periods(
        database,
        balances,
        ReconcilePeriodsRequest(
            period_start=date(2026, 5, 1), period_end=date(2026, 5, 31)
        ),
    )
    summary = result.summaries[0]
    assert summary.statement_opening_balance == 26249.90
    assert summary.computed_closing_balance == 26349.90


def test_single_month_prefers_computed_when_statement_null(
    database: DatabaseManager, tmp_path: Path
) -> None:
    """DoD 6 — when the prior statement closing is null, chain from computed."""
    connection = database.connect()
    try:
        _seed_account(
            connection,
            account_id="acct-ally-hysa",
            institution="Ally",
            account_name="HYSA",
            account_type="savings",
        )
        _insert_period_row(
            connection,
            account_id="acct-ally-hysa",
            period_start=date(2026, 4, 1),
            period_end=date(2026, 4, 30),
            statement_closing=None,
            computed_closing=27600.43,
        )
        connection.commit()
    finally:
        connection.close()

    balances = _balances_with(tmp_path, '[opening_balances]\nally = 3113.44\n')
    result = reconcile_periods(
        database,
        balances,
        ReconcilePeriodsRequest(
            period_start=date(2026, 5, 1), period_end=date(2026, 5, 31)
        ),
    )
    summary = result.summaries[0]
    assert summary.statement_opening_balance == 27600.43


def test_first_period_still_uses_balances_seed(
    database: DatabaseManager, tmp_path: Path
) -> None:
    """DoD 6 — with no prior period row, the first period uses the toml seed."""
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
            occurred_on=date(2026, 5, 5),
            amount=100.0,
            direction="inflow",
            tx_id="ally-only",
        )
        connection.commit()
    finally:
        connection.close()

    balances = _balances_with(tmp_path, '[opening_balances]\nally = 3113.44\n')
    result = reconcile_periods(
        database,
        balances,
        ReconcilePeriodsRequest(
            period_start=date(2026, 5, 1), period_end=date(2026, 5, 31)
        ),
    )
    summary = result.summaries[0]
    assert summary.statement_opening_balance == 3113.44


# ---------------------------------------------------------------------------
# BUG 3 — the balances.toml seed anchors its period; it must NOT chain from a
# stale prior-year stored period that predates the seed.
# ---------------------------------------------------------------------------
# The single [opening_balances] block is the 12/31/2025 closing = the opening
# for 2026-01. Leftover 2025 monthly periods from an earlier run carry the OLD
# pre-correction seed as a flat closing; the chaining logic must not seed
# January from December-2025's stale closing.


def test_seed_anchored_period_ignores_stale_prior_year_closing(
    database: DatabaseManager, tmp_path: Path
) -> None:
    """DoD 1 — 2026-01 opens from the balances.toml seed, not the stale Dec row.

    Beacon has leftover 2025 monthly periods carrying closing 16903.06 (the OLD
    pre-correction seed) and a corrected balances.toml seed of 10684.54. The
    seed-anchored period (2026-01) must use 10684.54 and must NOT chain from the
    16903.06 December-2025 row.
    """
    connection = database.connect()
    try:
        _seed_account(
            connection,
            account_id="acct-beacon-9999",
            institution="Beacon",
            account_name="Checking",
            account_type="checking",
        )
        # Leftover 2025 monthly periods, every one flat at the stale seed.
        for month in range(1, 13):
            last_day = 31 if month in (1, 3, 5, 7, 8, 10, 12) else 30
            if month == 2:
                last_day = 28
            _insert_period_row(
                connection,
                account_id="acct-beacon-9999",
                period_start=date(2025, month, 1),
                period_end=date(2025, month, last_day),
                statement_closing=16903.06,
                computed_closing=16903.06,
            )
        connection.commit()
    finally:
        connection.close()

    balances = _balances_with(tmp_path, '[opening_balances]\nbeacon = 10684.54\n')
    result = reconcile_periods(
        database,
        balances,
        ReconcilePeriodsRequest(
            period_start=date(2026, 1, 1), period_end=date(2026, 1, 31)
        ),
    )
    summary = result.summaries[0]
    # Seed wins — NOT the 16903.06 December-2025 stored closing.
    assert summary.statement_opening_balance == 10684.54
    # No transactions in January → closing carries the seed forward, variance 0.
    assert summary.computed_closing_balance == 10684.54


def test_post_anchor_period_still_chains_from_prior_stored_closing(
    database: DatabaseManager, tmp_path: Path
) -> None:
    """DoD 2 — 2026-02 chains from January's stored closing, not the seed.

    A period strictly after the seed-anchored period uses the prior stored
    period's closing (statement preferred over computed), exactly as the BUG 2
    rollforward does.
    """
    connection = database.connect()
    try:
        _seed_account(
            connection,
            account_id="acct-beacon-9999",
            institution="Beacon",
            account_name="Checking",
            account_type="checking",
        )
        # January already reconciled and stored: statement 11000.00 over a
        # different computed 12000.00 — statement must win for the chain.
        _insert_period_row(
            connection,
            account_id="acct-beacon-9999",
            period_start=date(2026, 1, 1),
            period_end=date(2026, 1, 31),
            statement_closing=11000.00,
            computed_closing=12000.00,
        )
        connection.commit()
    finally:
        connection.close()

    balances = _balances_with(tmp_path, '[opening_balances]\nbeacon = 10684.54\n')
    result = reconcile_periods(
        database,
        balances,
        ReconcilePeriodsRequest(
            period_start=date(2026, 2, 1), period_end=date(2026, 2, 28)
        ),
    )
    summary = result.summaries[0]
    # Chains from January's statement closing — not the 10684.54 seed.
    assert summary.statement_opening_balance == 11000.00


def test_seed_anchored_period_with_no_prior_periods_uses_seed(
    database: DatabaseManager, tmp_path: Path
) -> None:
    """DoD 3 — with no stale prior periods, 2026-01 still uses the seed."""
    connection = database.connect()
    try:
        _seed_account(
            connection,
            account_id="acct-beacon-9999",
            institution="Beacon",
            account_name="Checking",
            account_type="checking",
        )
        connection.commit()
    finally:
        connection.close()

    balances = _balances_with(tmp_path, '[opening_balances]\nbeacon = 10684.54\n')
    result = reconcile_periods(
        database,
        balances,
        ReconcilePeriodsRequest(
            period_start=date(2026, 1, 1), period_end=date(2026, 1, 31)
        ),
    )
    summary = result.summaries[0]
    assert summary.statement_opening_balance == 10684.54


def test_no_seed_no_prior_period_opens_null(
    database: DatabaseManager, tmp_path: Path
) -> None:
    """DoD 4 — an account with no seed and no prior period opens with None."""
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


def test_full_2026_run_january_seed_not_overwritten_by_stale_periods(
    database: DatabaseManager, tmp_path: Path
) -> None:
    """DoD 1+2 (integration) — full 2026-01→02 run over stale 2025 periods.

    January anchors from the seed (10684.54); February chains in-memory from
    January's closing. The stale 16903.06 December-2025 row never leaks in.
    """
    connection = database.connect()
    try:
        _seed_account(
            connection,
            account_id="acct-beacon-9999",
            institution="Beacon",
            account_name="Checking",
            account_type="checking",
        )
        _insert_period_row(
            connection,
            account_id="acct-beacon-9999",
            period_start=date(2025, 12, 1),
            period_end=date(2025, 12, 31),
            statement_closing=16903.06,
            computed_closing=16903.06,
        )
        # One January inflow with a running balance so January gets a statement
        # closing February can chain from.
        _insert_tx(
            connection,
            account_id="acct-beacon-9999",
            occurred_on=date(2026, 1, 15),
            amount=1000.0,
            direction="inflow",
            metadata={"running_balance": 11684.54},
            tx_id="beacon-jan-full",
        )
        connection.commit()
    finally:
        connection.close()

    balances = _balances_with(tmp_path, '[opening_balances]\nbeacon = 10684.54\n')
    result = reconcile_periods(
        database,
        balances,
        ReconcilePeriodsRequest(
            period_start=date(2026, 1, 1), period_end=date(2026, 2, 28)
        ),
    )
    jan, feb = result.summaries
    assert jan.statement_opening_balance == 10684.54
    # 10684.54 + 1000 = 11684.54 computed, statement 11684.54 → zero variance.
    assert jan.computed_closing_balance == 11684.54
    assert jan.variance_amount == 0.0
    # February opens from January's statement closing, not the stale December row.
    assert feb.statement_opening_balance == 11684.54
