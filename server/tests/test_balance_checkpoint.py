from __future__ import annotations

import json
import sqlite3
from datetime import date
from pathlib import Path

import pytest

from liquidity_gate_mcp.balances import load_balances
from liquidity_gate_mcp.checkpoints import (
    CheckpointError,
    upsert_balance_checkpoint,
)
from liquidity_gate_mcp.computed_balance import ALLY_HYSA_GATE_KEY
from liquidity_gate_mcp.config import ServerSettings
from liquidity_gate_mcp.database import DatabaseManager
from liquidity_gate_mcp.models import (
    ReconcilePeriodsRequest,
    UpsertBalanceCheckpointRequest,
)
from liquidity_gate_mcp.reconciliation import (
    _account_has_running_balance,
    _checkpoint_stale,
    reconcile_periods,
)


# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------


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
    metadata: dict | None = None,
    tx_id: str | None = None,
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
            tx_id or f"tx-{occurred_on.isoformat()}-{amount}",
            account_id,
            f"{account_id}-{occurred_on.isoformat()}-{amount}",
            occurred_on.isoformat(),
            occurred_on.isoformat(),
            amount,
            direction,
            json.dumps(metadata or {}),
        ),
    )


def _seed_ally(connection: sqlite3.Connection) -> None:
    _seed_account(
        connection,
        account_id="acct-ally-hysa",
        institution="Ally",
        account_name="HYSA",
        account_type="savings",
    )


def _settings_for(tmp_path: Path, db_path: Path) -> ServerSettings:
    schema = Path(__file__).resolve().parents[2] / "server" / "sql" / "schema.sql"
    return ServerSettings(
        project_root=tmp_path,
        docs_dir=tmp_path,
        tracker_csv_path=tmp_path / "tracker.csv",
        master_index_path=tmp_path / "index.md",
        database_path=db_path,
        schema_path=schema,
        watch_root=tmp_path,
    )


# ---------------------------------------------------------------------------
# Deliverable 2 — reconcile re-anchor logic (unit, via reconcile_periods)
# ---------------------------------------------------------------------------


def _balances_with(tmp_path: Path, body: str):
    (tmp_path / "balances.toml").write_text(body, encoding="utf-8")
    return load_balances(tmp_path)


def test_mid_month_checkpoint_verifies_and_reanchors_forward(
    database: DatabaseManager, tmp_path: Path
) -> None:
    """A mid-month checkpoint verifies the chain and re-anchors the next period.

    Ally opens Jan at the 3113.44 seed. A checkpoint asserts 13634.33 on
    2026-01-20. Activity: +20.89 interest (01-10), +13000 transfer-in (01-15),
    -2500 transfer-out (01-18) all on/before the checkpoint, then +500 inflow
    on 01-25 AFTER the checkpoint.

    Verification: 3113.44 + (20.89 + 13000 - 2500) = 13634.33 → variance 0.00.
    Forward carry: 13634.33 + net(01-21..01-31) = 13634.33 + 500 = 14134.33,
    which February must open from.
    """
    connection = database.connect()
    try:
        _seed_ally(connection)
        _insert_tx(connection, account_id="acct-ally-hysa", occurred_on=date(2026, 1, 10), amount=20.89, direction="inflow", tx_id="a1")
        _insert_tx(connection, account_id="acct-ally-hysa", occurred_on=date(2026, 1, 15), amount=13000.0, direction="transfer", tx_id="a2")
        _insert_tx(connection, account_id="acct-ally-hysa", occurred_on=date(2026, 1, 18), amount=-2500.0, direction="transfer", tx_id="a3")
        _insert_tx(connection, account_id="acct-ally-hysa", occurred_on=date(2026, 1, 25), amount=500.0, direction="inflow", tx_id="a4")
        connection.commit()
    finally:
        connection.close()

    balances = _balances_with(
        tmp_path,
        '[opening_balances]\nally = 3113.44\n\n'
        '[statement_closings.ally]\n"2026-01-20" = 13634.33\n',
    )
    result = reconcile_periods(
        database,
        balances,
        ReconcilePeriodsRequest(
            period_start=date(2026, 1, 1), period_end=date(2026, 2, 28)
        ),
    )
    jan, feb = result.summaries
    assert jan.closing_balance_source == "checkpoint"
    assert jan.variance_amount == 0.0
    # Re-anchored closing = checkpoint + net after the checkpoint date.
    assert jan.statement_closing_balance == 14134.33
    # February opens from the re-anchored figure, NOT the unanchored chain.
    assert feb.statement_opening_balance == 14134.33


def test_month_end_checkpoint_sets_closing_directly_no_spurious_variance(
    database: DatabaseManager, tmp_path: Path
) -> None:
    """D == period_end collapses to the simple case: closing == X, variance 0."""
    connection = database.connect()
    try:
        _seed_ally(connection)
        _insert_tx(connection, account_id="acct-ally-hysa", occurred_on=date(2026, 1, 15), amount=886.56, direction="inflow", tx_id="a1")
        connection.commit()
    finally:
        connection.close()

    # 3113.44 + 886.56 = 4000.00 → checkpoint matches the chain exactly.
    balances = _balances_with(
        tmp_path,
        '[opening_balances]\nally = 3113.44\n\n'
        '[statement_closings.ally]\n"2026-01-31" = 4000.00\n',
    )
    result = reconcile_periods(
        database,
        balances,
        ReconcilePeriodsRequest(
            period_start=date(2026, 1, 1), period_end=date(2026, 1, 31)
        ),
    )
    jan = result.summaries[0]
    assert jan.closing_balance_source == "checkpoint"
    assert jan.statement_closing_balance == 4000.00
    assert jan.variance_amount == 0.0


def test_checkpoint_verification_variance_is_reported(
    database: DatabaseManager, tmp_path: Path
) -> None:
    """A checkpoint that disagrees with the chain reports a non-zero delta."""
    connection = database.connect()
    try:
        _seed_ally(connection)
        _insert_tx(connection, account_id="acct-ally-hysa", occurred_on=date(2026, 1, 10), amount=100.0, direction="inflow", tx_id="a1")
        connection.commit()
    finally:
        connection.close()

    # Chain says 3113.44 + 100 = 3213.44, but the bank shows 3250.00 on 01-31.
    balances = _balances_with(
        tmp_path,
        '[opening_balances]\nally = 3113.44\n\n'
        '[statement_closings.ally]\n"2026-01-31" = 3250.00\n',
    )
    result = reconcile_periods(
        database,
        balances,
        ReconcilePeriodsRequest(
            period_start=date(2026, 1, 1), period_end=date(2026, 1, 31)
        ),
    )
    jan = result.summaries[0]
    assert jan.statement_closing_balance == 3250.00
    assert jan.computed_closing_balance == 3213.44
    assert jan.variance_amount == 36.56
    assert "Checkpoint re-anchor:" in jan.variance_explanation


def test_two_checkpoints_later_governs_earlier_verified(
    database: DatabaseManager, tmp_path: Path
) -> None:
    """Two checkpoints in one month: the later governs; the earlier is verified."""
    connection = database.connect()
    try:
        _seed_ally(connection)
        _insert_tx(connection, account_id="acct-ally-hysa", occurred_on=date(2026, 1, 5), amount=1000.0, direction="inflow", tx_id="a1")   # before cp1
        _insert_tx(connection, account_id="acct-ally-hysa", occurred_on=date(2026, 1, 12), amount=500.0, direction="inflow", tx_id="a2")    # between cp1 and cp2
        _insert_tx(connection, account_id="acct-ally-hysa", occurred_on=date(2026, 1, 25), amount=200.0, direction="inflow", tx_id="a3")    # after cp2
        connection.commit()
    finally:
        connection.close()

    # cp1 (01-10) = 4113.44 (= 3113.44 + 1000). cp2 (01-15) = 4613.44 (= 4113.44 + 500).
    balances = _balances_with(
        tmp_path,
        '[opening_balances]\nally = 3113.44\n\n'
        '[statement_closings.ally]\n'
        '"2026-01-10" = 4113.44\n'
        '"2026-01-15" = 4613.44\n',
    )
    result = reconcile_periods(
        database,
        balances,
        ReconcilePeriodsRequest(
            period_start=date(2026, 1, 1), period_end=date(2026, 2, 28)
        ),
    )
    jan, feb = result.summaries
    # Later checkpoint (01-15) governs: re-anchored = 4613.44 + net(01-16..01-31) = 4613.44 + 200.
    assert jan.statement_closing_balance == 4813.44
    assert feb.statement_opening_balance == 4813.44
    # Earlier checkpoint surfaced in the explanation as a verification point.
    assert "Earlier checkpoints verified" in jan.variance_explanation
    assert "2026-01-10" in jan.variance_explanation


def test_checkpoint_before_first_transaction_no_prior_anchor(
    database: DatabaseManager, tmp_path: Path
) -> None:
    """No seed and a checkpoint before the first tx: X is the forward anchor."""
    connection = database.connect()
    try:
        _seed_ally(connection)
        # First ingested transaction is AFTER the checkpoint date.
        _insert_tx(connection, account_id="acct-ally-hysa", occurred_on=date(2026, 1, 20), amount=100.0, direction="inflow", tx_id="a1")
        connection.commit()
    finally:
        connection.close()

    # No [opening_balances] → no prior anchor; checkpoint on 01-05 = 9000.00.
    balances = _balances_with(
        tmp_path,
        '[statement_closings.ally]\n"2026-01-05" = 9000.00\n',
    )
    result = reconcile_periods(
        database,
        balances,
        ReconcilePeriodsRequest(
            period_start=date(2026, 1, 1), period_end=date(2026, 2, 28)
        ),
    )
    jan, feb = result.summaries
    assert jan.variance_amount is None  # cannot verify without a prior anchor
    # X carried forward + net after the checkpoint (the 01-20 inflow).
    assert jan.statement_closing_balance == 9100.00
    assert feb.statement_opening_balance == 9100.00


def test_running_balance_account_unaffected_by_statement_closing(
    database: DatabaseManager, tmp_path: Path
) -> None:
    """Beacon (has running_balance) keeps balances_toml behavior, not checkpoint."""
    connection = database.connect()
    try:
        _seed_account(connection, account_id="acct-beacon-9999", institution="Beacon", account_name="Checking", account_type="checking")
        _insert_tx(connection, account_id="acct-beacon-9999", occurred_on=date(2026, 1, 30), amount=-100.0, direction="outflow", metadata={"running_balance": 17000.00}, tx_id="b1")
        connection.commit()
    finally:
        connection.close()

    balances = _balances_with(
        tmp_path,
        '[opening_balances]\nbeacon = 17100.00\n\n'
        '[statement_closings.beacon]\n"2026-01-31" = 16999.00\n',
    )
    result = reconcile_periods(
        database,
        balances,
        ReconcilePeriodsRequest(
            period_start=date(2026, 1, 1), period_end=date(2026, 1, 31)
        ),
    )
    summary = result.summaries[0]
    assert summary.closing_balance_source == "balances_toml"
    assert summary.checkpoint_stale is None


def test_idempotent_checkpoint_explanation(
    database: DatabaseManager, tmp_path: Path
) -> None:
    """Re-running does not duplicate the checkpoint note, and keeps human notes."""
    connection = database.connect()
    try:
        _seed_ally(connection)
        _insert_tx(connection, account_id="acct-ally-hysa", occurred_on=date(2026, 1, 10), amount=100.0, direction="inflow", tx_id="a1")
        connection.commit()
    finally:
        connection.close()

    balances = _balances_with(
        tmp_path,
        '[opening_balances]\nally = 3113.44\n\n'
        '[statement_closings.ally]\n"2026-01-31" = 3250.00\n',
    )
    req = ReconcilePeriodsRequest(period_start=date(2026, 1, 1), period_end=date(2026, 1, 31))
    reconcile_periods(database, balances, req)

    # Add a human note alongside the system checkpoint note.
    connection = database.connect()
    try:
        row = connection.execute(
            "SELECT variance_explanation FROM reconciliation_periods WHERE account_id = ?",
            ("acct-ally-hysa",),
        ).fetchone()
        connection.execute(
            "UPDATE reconciliation_periods SET variance_explanation = ? WHERE account_id = ?",
            (row["variance_explanation"] + "\nHuman: opened a new sub-account", "acct-ally-hysa"),
        )
        connection.commit()
    finally:
        connection.close()

    second = reconcile_periods(database, balances, req)
    explanation = second.summaries[0].variance_explanation
    assert explanation.count("Checkpoint re-anchor:") == 1
    assert "Human: opened a new sub-account" in explanation


# ---------------------------------------------------------------------------
# Deliverable 3 — staleness
# ---------------------------------------------------------------------------


def test_staleness_flag(database: DatabaseManager) -> None:
    connection = database.connect()
    try:
        _seed_ally(connection)
        _insert_tx(connection, account_id="acct-ally-hysa", occurred_on=date(2026, 6, 10), amount=10.0, direction="inflow", tx_id="a1")
        connection.commit()

        today = date(2026, 6, 15)
        # Checkpoint predates the newest ingested transaction (06-10) → stale.
        assert _checkpoint_stale(connection, "acct-ally-hysa", date(2026, 6, 5), today) is True
        # Checkpoint after the newest tx and within 35 days of today → fresh.
        assert _checkpoint_stale(connection, "acct-ally-hysa", date(2026, 6, 12), today) is False
        # Checkpoint more than 35 days old → stale, even with no later tx.
        assert _checkpoint_stale(connection, "acct-ally-hysa", date(2026, 4, 1), date(2026, 6, 12)) is True
    finally:
        connection.close()


def test_account_has_running_balance(database: DatabaseManager) -> None:
    connection = database.connect()
    try:
        _seed_ally(connection)
        _seed_account(connection, account_id="acct-beacon-9999", institution="Beacon", account_name="Checking", account_type="checking")
        _insert_tx(connection, account_id="acct-ally-hysa", occurred_on=date(2026, 1, 10), amount=100.0, direction="inflow", tx_id="a1")
        _insert_tx(connection, account_id="acct-beacon-9999", occurred_on=date(2026, 1, 10), amount=100.0, direction="inflow", metadata={"running_balance": 200.0}, tx_id="b1")
        connection.commit()
        assert _account_has_running_balance(connection, "acct-ally-hysa") is False
        assert _account_has_running_balance(connection, "acct-beacon-9999") is True
    finally:
        connection.close()


# ---------------------------------------------------------------------------
# Deliverable 1 — the MCP tool
# ---------------------------------------------------------------------------


def test_tool_writes_entry_preserves_comments_and_reanchors(
    database: DatabaseManager, tmp_path: Path
) -> None:
    """DoD acceptance: writes the entry, refreshes the gate, re-anchors."""
    connection = database.connect()
    try:
        _seed_ally(connection)
        # Activity through the checkpoint reaches 35000; nothing after 06-02.
        _insert_tx(connection, account_id="acct-ally-hysa", occurred_on=date(2026, 5, 20), amount=1000.0, direction="inflow", tx_id="a1")
        connection.commit()
    finally:
        connection.close()

    original = (
        "# balances.toml — human-maintained anchors\n"
        "# Explicit entries here always win over CSV-derived running_balance.\n\n"
        "[opening_balances]\n"
        "ally = 3113.44  # Ally seed (12/31/2025)\n\n"
        "[statement_closings.ally]\n\n"
        "[wealth_bridge]\n"
        "hysa_target = 80000.0\n"
    )
    (tmp_path / "balances.toml").write_text(original, encoding="utf-8")

    settings = _settings_for(tmp_path, database.database_path)
    result = upsert_balance_checkpoint(
        database,
        settings,
        UpsertBalanceCheckpointRequest(
            account="ally", date=date(2026, 6, 2), balance=35000, note="verified via Ally app"
        ),
    )

    # Entry written under the ally section, two-decimal, comment preserved.
    text = (tmp_path / "balances.toml").read_text(encoding="utf-8")
    assert '"2026-06-02" = 35000.00' in text
    assert "verified via Ally app" in text
    # All prior content survives.
    assert "# Explicit entries here always win" in text
    assert "Ally seed (12/31/2025)" in text
    assert "hysa_target = 80000.0" in text

    # Result reports the re-anchor + gate refresh.
    assert result.account_section == "ally"
    assert result.account_id == "acct-ally-hysa"
    assert result.statement_closing_balance == 35000.00
    assert result.next_period_opening_balance == 35000.00
    assert result.gate_updated is True
    assert result.gate_computed_balance == 35000.00
    assert isinstance(result.checkpoint_stale, bool)

    # The gate row reflects the checkpoint-anchored balance.
    connection = database.connect(read_only=True)
    try:
        gate = connection.execute(
            "SELECT current_amount FROM liquidity_gates WHERE gate_key = ?",
            (ALLY_HYSA_GATE_KEY,),
        ).fetchone()
        assert gate["current_amount"] == 35000.00
    finally:
        connection.close()


def test_tool_does_not_touch_transactions(
    database: DatabaseManager, tmp_path: Path
) -> None:
    connection = database.connect()
    try:
        _seed_ally(connection)
        _insert_tx(connection, account_id="acct-ally-hysa", occurred_on=date(2026, 5, 20), amount=1000.0, direction="inflow", tx_id="a1")
        connection.commit()
        before = connection.execute("SELECT COUNT(*) AS n FROM transactions").fetchone()["n"]
    finally:
        connection.close()

    settings = _settings_for(tmp_path, database.database_path)
    upsert_balance_checkpoint(
        database,
        settings,
        UpsertBalanceCheckpointRequest(account="ally", date=date(2026, 6, 2), balance=35000),
    )

    connection = database.connect(read_only=True)
    try:
        after = connection.execute("SELECT COUNT(*) AS n FROM transactions").fetchone()["n"]
        assert after == before
    finally:
        connection.close()


def test_tool_rejects_credit_card(database: DatabaseManager, tmp_path: Path) -> None:
    connection = database.connect()
    try:
        _seed_account(connection, account_id="acct-chase-credit-card", institution="Chase", account_name="Credit Card", account_type="credit_card")
        connection.commit()
    finally:
        connection.close()

    settings = _settings_for(tmp_path, database.database_path)
    with pytest.raises(CheckpointError):
        upsert_balance_checkpoint(
            database,
            settings,
            UpsertBalanceCheckpointRequest(account="chase", date=date(2026, 6, 2), balance=4000),
        )
    # Nothing was written to the file.
    assert not (tmp_path / "balances.toml").exists()


def test_tool_resolves_exact_account_id(database: DatabaseManager, tmp_path: Path) -> None:
    connection = database.connect()
    try:
        _seed_ally(connection)
        connection.commit()
    finally:
        connection.close()

    settings = _settings_for(tmp_path, database.database_path)
    result = upsert_balance_checkpoint(
        database,
        settings,
        UpsertBalanceCheckpointRequest(account="acct-ally-hysa", date=date(2026, 6, 2), balance=35000),
    )
    # Single Ally account → section keyed by the institution alias for round-trip
    # with the existing [opening_balances] convention.
    assert result.account_section == "ally"
    assert result.account_id == "acct-ally-hysa"
