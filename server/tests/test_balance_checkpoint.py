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

    The month SPLITS at the checkpoint (Defect 1): a verify period
    [01-01, 01-20] carries the checkpoint balance 13634.33 as its closing
    (verification 3113.44 + (20.89 + 13000 - 2500) = 13634.33 → variance 0.00),
    and a remainder [01-21, 01-31] re-anchors to 13634.33 and chains the +500
    after the checkpoint to a computed 14134.33 (NULL statement closing). The
    full month is never pinned to the mid-month observation. February opens from
    the re-anchored 14134.33.
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
    verify, remainder, feb = result.summaries
    # Verify period ends AT the checkpoint and carries the checkpoint balance.
    assert (verify.period_start, verify.period_end) == ("2026-01-01", "2026-01-20")
    assert verify.closing_balance_source == "checkpoint"
    assert verify.variance_amount == 0.0
    assert verify.statement_closing_balance == 13634.33
    # Remainder re-anchors at the checkpoint and chains the post-checkpoint +500
    # to a computed close; it is NOT pinned to the mid-month observation.
    assert (remainder.period_start, remainder.period_end) == ("2026-01-21", "2026-01-31")
    assert remainder.statement_opening_balance == 13634.33
    assert remainder.statement_closing_balance is None
    assert remainder.closing_balance_source is None
    assert remainder.computed_closing_balance == 14134.33
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
    verify, remainder, feb = result.summaries
    # The later checkpoint (01-15) governs the split: the verify period ends at
    # 01-15 and carries that balance; the remainder chains the +200 after it.
    assert (verify.period_start, verify.period_end) == ("2026-01-01", "2026-01-15")
    assert verify.statement_closing_balance == 4613.44
    assert (remainder.period_start, remainder.period_end) == ("2026-01-16", "2026-01-31")
    assert remainder.statement_opening_balance == 4613.44
    assert remainder.computed_closing_balance == 4813.44
    assert feb.statement_opening_balance == 4813.44
    # Earlier checkpoint surfaced in the explanation as a verification point.
    assert "Earlier checkpoints verified" in verify.variance_explanation
    assert "2026-01-10" in verify.variance_explanation


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
    verify, remainder, feb = result.summaries
    # Verify period ends at the checkpoint; X is taken as the forward anchor.
    assert (verify.period_start, verify.period_end) == ("2026-01-01", "2026-01-05")
    assert verify.variance_amount is None  # cannot verify without a prior anchor
    assert verify.statement_closing_balance == 9000.00
    # Remainder opens at X and chains the post-checkpoint +100 (the 01-20 inflow).
    assert (remainder.period_start, remainder.period_end) == ("2026-01-06", "2026-01-31")
    assert remainder.statement_opening_balance == 9000.00
    assert remainder.computed_closing_balance == 9100.00
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


# ---------------------------------------------------------------------------
# Regression — the three defects from the live 2026-06-02 run
# ---------------------------------------------------------------------------


def _rows_for(connection: sqlite3.Connection, account_id: str) -> list[sqlite3.Row]:
    return connection.execute(
        "SELECT period_start, period_end, statement_opening_balance, "
        "statement_closing_balance, closing_balance_source, "
        "computed_closing_balance, variance_amount "
        "FROM reconciliation_periods WHERE account_id = ? "
        "ORDER BY period_start, period_end",
        (account_id,),
    ).fetchall()


def test_defect1_midmonth_checkpoint_then_post_checkpoint_txns(
    database: DatabaseManager, tmp_path: Path
) -> None:
    """A mid-month checkpoint followed by post-checkpoint activity in the same
    month reconciles the month-end via the CHAIN, not the mid-month observation.

    This is the case the original 13 tests missed: once activity lands after the
    checkpoint, a full-month period pinned to the checkpoint value reports a
    spurious month-end variance. The fix splits the month so the verification
    delta lives on the [start, D] verify period and the month-end (remainder)
    re-anchors at X and chains forward with NO checkpoint-pinned closing.
    """
    connection = database.connect()
    try:
        _seed_ally(connection)
        # +1886.56 before the checkpoint (chain reaches 5000), -200 after it.
        _insert_tx(connection, account_id="acct-ally-hysa", occurred_on=date(2026, 6, 10), amount=1886.56, direction="inflow", tx_id="a1")
        _insert_tx(connection, account_id="acct-ally-hysa", occurred_on=date(2026, 6, 20), amount=-200.0, direction="outflow", tx_id="a2")
        connection.commit()
    finally:
        connection.close()

    # Bank shows 5050 on 06-15; the chain says 5000 → a +50 verification delta.
    balances = _balances_with(
        tmp_path,
        '[opening_balances]\nally = 3113.44\n\n'
        '[statement_closings.ally]\n"2026-06-15" = 5050.00\n',
    )
    reconcile_periods(
        database,
        balances,
        ReconcilePeriodsRequest(
            period_start=date(2026, 6, 1), period_end=date(2026, 7, 31)
        ),
    )

    connection = database.connect(read_only=True)
    try:
        rows = _rows_for(connection, "acct-ally-hysa")
    finally:
        connection.close()

    by_key = {(r["period_start"], r["period_end"]): r for r in rows}

    # No full-month row, and certainly none carrying a checkpoint-pinned closing.
    assert ("2026-06-01", "2026-06-30") not in by_key
    assert not any(
        r["period_end"] == "2026-06-30" and r["closing_balance_source"] == "checkpoint"
        for r in rows
    )

    # Exactly one checkpoint-sourced row: the verify period [06-01, 06-15].
    checkpoint_rows = [r for r in rows if r["closing_balance_source"] == "checkpoint"]
    assert len(checkpoint_rows) == 1
    verify = checkpoint_rows[0]
    assert (verify["period_start"], verify["period_end"]) == ("2026-06-01", "2026-06-15")
    assert verify["statement_closing_balance"] == 5050.00
    assert verify["computed_closing_balance"] == 5000.00
    # The verification delta is reported AT the checkpoint, not at month-end.
    assert verify["variance_amount"] == 50.00

    # The remainder re-anchors at X and chains the -200; month-end is NOT pinned.
    remainder = by_key[("2026-06-16", "2026-06-30")]
    assert remainder["statement_opening_balance"] == 5050.00
    assert remainder["statement_closing_balance"] is None
    assert remainder["closing_balance_source"] is None
    assert remainder["computed_closing_balance"] == 4850.00


def test_defect1_supersedes_stale_full_month_row(
    database: DatabaseManager, tmp_path: Path
) -> None:
    """Re-running over a pre-split full-month checkpoint row removes it.

    Simulates the live DB state: a prior (buggy) run wrote a full-month
    [06-01, 06-30] row stamped source='checkpoint'. Re-running must leave the
    split pair and no lingering full-month checkpoint row.
    """
    connection = database.connect()
    try:
        _seed_ally(connection)
        _insert_tx(connection, account_id="acct-ally-hysa", occurred_on=date(2026, 6, 10), amount=1886.56, direction="inflow", tx_id="a1")
        # Pre-existing stale full-month row from the buggy version.
        connection.execute(
            "INSERT INTO reconciliation_periods (id, account_id, period_start, "
            "period_end, statement_closing_balance, closing_balance_source) "
            "VALUES ('stale', 'acct-ally-hysa', '2026-06-01', '2026-06-30', "
            "5000.0, 'checkpoint')"
        )
        connection.commit()
    finally:
        connection.close()

    balances = _balances_with(
        tmp_path,
        '[opening_balances]\nally = 3113.44\n\n'
        '[statement_closings.ally]\n"2026-06-15" = 5000.00\n',
    )
    reconcile_periods(
        database,
        balances,
        ReconcilePeriodsRequest(
            period_start=date(2026, 6, 1), period_end=date(2026, 6, 30)
        ),
    )

    connection = database.connect(read_only=True)
    try:
        rows = _rows_for(connection, "acct-ally-hysa")
    finally:
        connection.close()
    keys = {(r["period_start"], r["period_end"]) for r in rows}
    assert ("2026-06-01", "2026-06-30") not in keys
    assert ("2026-06-01", "2026-06-15") in keys
    assert ("2026-06-16", "2026-06-30") in keys


def test_defect2_one_checkpoint_entry_one_sourced_period(
    database: DatabaseManager, tmp_path: Path
) -> None:
    """One checkpoint entry yields exactly one checkpoint-sourced period.

    The generic seed reader bootstraps an anchor at [06-01, 06-02] and the
    reconcile pass materialises its verify period at the same key; they must
    collapse to ONE row stamped 'checkpoint', never two overlapping rows with
    different sources (the seed's 'balances_toml' must not survive as a separate
    row for the same entry).
    """
    connection = database.connect()
    try:
        _seed_ally(connection)
        _insert_tx(connection, account_id="acct-ally-hysa", occurred_on=date(2026, 5, 20), amount=1000.0, direction="inflow", tx_id="a1")
        connection.commit()
    finally:
        connection.close()

    (tmp_path / "balances.toml").write_text(
        "[opening_balances]\nally = 3113.44\n\n"
        "[statement_closings.ally]\n",
        encoding="utf-8",
    )
    settings = _settings_for(tmp_path, database.database_path)
    upsert_balance_checkpoint(
        database,
        settings,
        UpsertBalanceCheckpointRequest(account="ally", date=date(2026, 6, 2), balance=35000),
    )

    connection = database.connect(read_only=True)
    try:
        rows = _rows_for(connection, "acct-ally-hysa")
    finally:
        connection.close()

    # Every row whose period covers the checkpoint date 06-02.
    covering = [
        r for r in rows
        if r["period_start"] <= "2026-06-02" <= r["period_end"]
    ]
    # Exactly one such row, and it is the checkpoint-sourced verify period.
    assert len(covering) == 1
    assert covering[0]["closing_balance_source"] == "checkpoint"
    assert (covering[0]["period_start"], covering[0]["period_end"]) == (
        "2026-06-01", "2026-06-02"
    )
    # No leftover balances_toml row for the same entry.
    assert not any(
        r["closing_balance_source"] == "balances_toml" and r["period_end"] == "2026-06-02"
        for r in rows
    )


def test_defect3_toml_entry_placed_under_header_not_orphaned(
    database: DatabaseManager, tmp_path: Path
) -> None:
    """A checkpoint written to a table followed by another section's comment
    block lands directly under its header, leaving the comments with the
    following section (Defect 3)."""
    connection = database.connect()
    try:
        _seed_ally(connection)
        connection.commit()
    finally:
        connection.close()

    original = (
        "# balances.toml — human-maintained anchors\n\n"
        "[opening_balances]\n"
        "ally = 3113.44  # Ally seed (12/31/2025)\n\n"
        "[statement_closings.ally]\n\n"
        "# Inputs for the monthly cashflow summary generator (the bridge\n"
        "# document for the wealth-tracker project).\n"
        "[wealth_bridge]\n"
        "hysa_target = 80000.0\n"
    )
    (tmp_path / "balances.toml").write_text(original, encoding="utf-8")

    settings = _settings_for(tmp_path, database.database_path)
    upsert_balance_checkpoint(
        database,
        settings,
        UpsertBalanceCheckpointRequest(
            account="ally", date=date(2026, 6, 2), balance=35000, note="Observed via Ally app"
        ),
    )

    lines = (tmp_path / "balances.toml").read_text(encoding="utf-8").splitlines()
    header_idx = lines.index("[statement_closings.ally]")
    entry_idx = next(i for i, ln in enumerate(lines) if ln.startswith('"2026-06-02"'))
    comment_idx = next(i for i, ln in enumerate(lines) if "monthly cashflow summary" in ln)
    bridge_idx = lines.index("[wealth_bridge]")

    # Entry sits immediately under its own header...
    assert entry_idx == header_idx + 1
    # ...above the wealth_bridge comment block...
    assert entry_idx < comment_idx
    # ...and that comment block stays directly above the [wealth_bridge] header.
    assert comment_idx < bridge_idx
    assert all(
        lines[i].lstrip().startswith("#") or lines[i].strip() == ""
        for i in range(entry_idx + 1, bridge_idx)
    )
    assert "[wealth_bridge]" == lines[bridge_idx]

    # Round-trip parses and every prior datum survives.
    reparsed = load_balances(tmp_path)
    assert reparsed.accounts["ally"].statement_closings[date(2026, 6, 2)] == 35000.0
    assert reparsed.accounts["ally"].opening_balance == 3113.44
    assert reparsed.wealth_bridge.hysa_target == 80000.0
