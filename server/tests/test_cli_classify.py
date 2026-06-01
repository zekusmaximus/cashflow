"""Tests for the classifier CLI entry point (the Tauri sidecar)."""

from __future__ import annotations

import json

from liquidity_gate_mcp.cli_classify import main, run_classify
from liquidity_gate_mcp.database import DatabaseManager
from liquidity_gate_mcp.models import UpsertTransactionOverrideRequest


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _insert_transaction(
    database: DatabaseManager,
    *,
    tx_id: str = "tx-001",
    account_id: str = "acct-chase-credit",
    description_raw: str = "AMAZON.COM",
    direction: str = "outflow",
    amount: float = -50.0,
    primary_category: str = "unclassified",
) -> None:
    with database.connect() as conn:
        conn.execute(
            """
            INSERT OR IGNORE INTO accounts (id, institution, account_name, account_type, owner)
            VALUES (?, 'Chase', 'Chase Credit', 'credit_card', 'jeff')
            """,
            (account_id,),
        )
        conn.execute(
            """
            INSERT OR IGNORE INTO import_batches (id, source_name, parser_version, imported_at, raw_payload)
            VALUES ('batch-001', 'test.csv', 'test-v1', '2026-01-01T00:00:00Z', '{}')
            """,
        )
        conn.execute(
            """
            INSERT INTO transactions
              (id, account_id, import_batch_id, source_record_key, source_document_name,
               occurred_on, description_raw, amount, direction, primary_category, metadata_json)
            VALUES (?, ?, 'batch-001', ?, 'test.csv', '2026-01-15', ?, ?, ?, ?, '{}')
            """,
            (tx_id, account_id, tx_id, description_raw, amount, direction, primary_category),
        )
        conn.commit()


def _fetch_transaction(database: DatabaseManager, tx_id: str) -> dict:
    with database.connect(read_only=True) as conn:
        row = conn.execute(
            "SELECT * FROM transactions WHERE id = ?", (tx_id,)
        ).fetchone()
    return dict(row) if row else {}


# ---------------------------------------------------------------------------
# run_classify — rule upsert + classify
# ---------------------------------------------------------------------------


def test_run_classify_upserts_rule_and_classifies(database: DatabaseManager) -> None:
    _insert_transaction(
        database,
        description_raw="TRADER JOES #443",
        direction="outflow",
        primary_category="unclassified",
    )

    payload = run_classify(
        database,
        rule={
            "pattern": r"(?i)trader joe",
            "primary_category": "variable_lifestyle",
            "subcategory": "groceries",
            "merchant_normalized": "Trader Joe's",
            "household_role": "joint",
            "lifecycle": "recurring",
            "confidence": "high",
            "direction_filter": "outflow",
            "priority": 8,
        },
        reclassify_all=True,
    )

    assert payload["ok"] is True
    assert payload["rule_id"]
    assert payload["matched"] >= 1
    assert payload["dry_run"] is False

    tx = _fetch_transaction(database, "tx-001")
    assert tx["primary_category"] == "variable_lifestyle"
    assert tx["subcategory"] == "groceries"
    assert tx["merchant_normalized"] == "Trader Joe's"


def test_run_classify_reclassify_all_rebuckets_existing(database: DatabaseManager) -> None:
    # Already classified by a prior general guess — only reclassify_all reaches it.
    _insert_transaction(
        database,
        description_raw="TRADER JOES #443",
        direction="outflow",
        primary_category="variable_lifestyle",
    )

    payload = run_classify(
        database,
        rule={
            "pattern": r"(?i)trader joe",
            "subcategory": "groceries",
            "merchant_normalized": "Trader Joe's",
            "priority": 8,
        },
        reclassify_all=True,
    )

    assert payload["matched"] >= 1
    tx = _fetch_transaction(database, "tx-001")
    assert tx["subcategory"] == "groceries"


def test_run_classify_preserves_manual_override(database: DatabaseManager) -> None:
    # Override a row, then create a conflicting rule and apply — override stands.
    _insert_transaction(
        database,
        description_raw="AMAZON.COM*123ABC",
        direction="outflow",
        primary_category="unclassified",
    )
    database.upsert_transaction_override(
        UpsertTransactionOverrideRequest(
            transaction_id="tx-001",
            primary_category="business_expense",
            subcategory="software",
            note="kept for taxes",
        )
    )

    payload = run_classify(
        database,
        rule={
            "pattern": r"(?i)\bamazon\b",
            "primary_category": "variable_lifestyle",
            "subcategory": "amazon",
            "priority": 8,
        },
        reclassify_all=True,
    )

    assert payload["ok"] is True
    tx = _fetch_transaction(database, "tx-001")
    # The manual override wins over the conflicting rule.
    assert tx["primary_category"] == "business_expense"
    assert tx["subcategory"] == "software"


def test_run_classify_dry_run_does_not_write(database: DatabaseManager) -> None:
    _insert_transaction(database, description_raw="AMAZON PRIME")

    payload = run_classify(database, dry_run=True)

    assert payload["dry_run"] is True
    tx = _fetch_transaction(database, "tx-001")
    assert tx["primary_category"] == "unclassified"  # unchanged


def test_run_classify_unclassified_excludes_transfers(database: DatabaseManager) -> None:
    # A transfer-direction row is classified to 'transfer', not counted as unclassified;
    # and the unclassified count never includes transfer-direction rows.
    _insert_transaction(
        database,
        tx_id="tx-xfer",
        description_raw="ONLINE TRANSFER TO SAVINGS",
        direction="transfer",
        primary_category="unclassified",
    )
    _insert_transaction(
        database,
        tx_id="tx-mystery",
        description_raw="ZZQ UNKNOWN VENDOR",
        direction="outflow",
        primary_category="unclassified",
    )

    payload = run_classify(database, reclassify_all=True)

    # Only the outflow mystery row remains unclassified; the transfer row is excluded.
    assert payload["unclassified"] == 1


# ---------------------------------------------------------------------------
# main — JSON stdout contract
# ---------------------------------------------------------------------------


def test_main_emits_json_object(database: DatabaseManager, monkeypatch, capsys) -> None:
    _insert_transaction(database, description_raw="AMAZON PRIME")

    # Point the CLI's settings at the test database.
    from liquidity_gate_mcp import cli_classify

    class _Settings:
        database_path = database.database_path
        schema_path = database.schema_path

    monkeypatch.setattr(cli_classify, "load_settings", lambda: _Settings(), raising=False)
    import liquidity_gate_mcp.config as config_mod

    monkeypatch.setattr(config_mod, "load_settings", lambda: _Settings())

    exit_code = main(["--no-reclassify-all"])
    captured = capsys.readouterr()

    assert exit_code == 0
    payload = json.loads(captured.out.strip())
    assert payload["ok"] is True
    assert payload["matched"] == 1
    assert "unclassified" in payload


def test_main_reports_error_as_json(database: DatabaseManager, monkeypatch, capsys) -> None:
    from liquidity_gate_mcp import cli_classify

    class _Settings:
        database_path = database.database_path
        schema_path = database.schema_path

    import liquidity_gate_mcp.config as config_mod

    monkeypatch.setattr(config_mod, "load_settings", lambda: _Settings())

    # Invalid rule JSON → error object, non-zero exit.
    exit_code = main(["--rule", "{not valid json"])
    captured = capsys.readouterr()

    assert exit_code == 1
    payload = json.loads(captured.out.strip())
    assert payload["ok"] is False
    assert "error" in payload
