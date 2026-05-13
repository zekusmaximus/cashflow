from __future__ import annotations

import sqlite3
from datetime import date

from liquidity_gate_mcp.database import DatabaseManager
from liquidity_gate_mcp.models import PairTransfersRequest
from liquidity_gate_mcp.transfers import pair_transfers


def _seed_account(connection: sqlite3.Connection, account_id: str) -> None:
    connection.execute(
        "INSERT OR IGNORE INTO accounts (id, institution, account_name, "
        "account_type, owner, currency) VALUES (?, ?, ?, ?, ?, ?)",
        (account_id, "Test", f"acct {account_id}", "checking", "joint", "USD"),
    )


def _seed_batch(connection: sqlite3.Connection, batch_id: str) -> None:
    connection.execute(
        "INSERT OR IGNORE INTO import_batches (id, source_name, parser_version, "
        "imported_at, raw_payload) VALUES (?, ?, ?, ?, ?)",
        (batch_id, "seed", "test", "2026-05-13T00:00:00Z", "{}"),
    )


def _insert_transaction(
    database: DatabaseManager,
    *,
    transaction_id: str,
    account_id: str,
    occurred_on: date,
    amount: float,
    direction: str,
    description: str = "",
) -> None:
    connection = database.connect()
    try:
        _seed_account(connection, account_id)
        batch_id = "batch-test"
        _seed_batch(connection, batch_id)
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
                      NULL, NULL, '{}')
            """,
            (
                transaction_id,
                account_id,
                batch_id,
                transaction_id,  # source_record_key — unique per id is fine
                f"seed-{transaction_id}.csv",
                occurred_on.isoformat(),
                occurred_on.isoformat(),
                description or f"row {transaction_id}",
                amount,
                direction,
            ),
        )
        connection.commit()
    finally:
        connection.close()


def _fetch_keys(database: DatabaseManager) -> dict[str, str | None]:
    connection = database.connect(read_only=True)
    try:
        rows = connection.execute(
            "SELECT id, transfer_group_key FROM transactions"
        ).fetchall()
    finally:
        connection.close()
    return {row["id"]: row["transfer_group_key"] for row in rows}


def test_pairs_chase_payment_with_beacon_epay(database: DatabaseManager) -> None:
    _insert_transaction(
        database,
        transaction_id="tx-chase",
        account_id="acct-chase",
        occurred_on=date(2026, 4, 15),
        amount=5000.0,
        direction="transfer",
        description="Payment Thank You",
    )
    _insert_transaction(
        database,
        transaction_id="tx-beacon",
        account_id="acct-beacon",
        occurred_on=date(2026, 4, 15),
        amount=-5000.0,
        direction="transfer",
        description="CHASE CREDIT CRD EPAY",
    )

    result = pair_transfers(database, PairTransfersRequest())

    assert result.pairs_created == 1
    assert result.candidates_examined == 2
    assert result.unpaired == []
    assert result.ambiguous == []
    keys = _fetch_keys(database)
    assert keys["tx-chase"] is not None
    assert keys["tx-chase"] == keys["tx-beacon"]


def test_pairs_within_date_tolerance(database: DatabaseManager) -> None:
    _insert_transaction(
        database,
        transaction_id="tx-a",
        account_id="acct-a",
        occurred_on=date(2026, 4, 15),
        amount=200.0,
        direction="transfer",
    )
    _insert_transaction(
        database,
        transaction_id="tx-b",
        account_id="acct-b",
        occurred_on=date(2026, 4, 17),  # 2-day delta
        amount=-200.0,
        direction="transfer",
    )

    result = pair_transfers(database, PairTransfersRequest(date_tolerance_days=3))
    assert result.pairs_created == 1
    keys = _fetch_keys(database)
    assert keys["tx-a"] == keys["tx-b"] is not None


def test_does_not_pair_outside_date_tolerance(database: DatabaseManager) -> None:
    _insert_transaction(
        database,
        transaction_id="tx-a",
        account_id="acct-a",
        occurred_on=date(2026, 4, 1),
        amount=200.0,
        direction="transfer",
    )
    _insert_transaction(
        database,
        transaction_id="tx-b",
        account_id="acct-b",
        occurred_on=date(2026, 4, 10),  # 9-day delta vs 3-day tolerance
        amount=-200.0,
        direction="transfer",
    )

    result = pair_transfers(database, PairTransfersRequest(date_tolerance_days=3))
    assert result.pairs_created == 0
    assert {u.transaction_id for u in result.unpaired} == {"tx-a", "tx-b"}
    assert all(u.likely_reason for u in result.unpaired)
    keys = _fetch_keys(database)
    assert keys["tx-a"] is None and keys["tx-b"] is None


def test_amount_mismatch_no_pair(database: DatabaseManager) -> None:
    _insert_transaction(
        database,
        transaction_id="tx-a",
        account_id="acct-a",
        occurred_on=date(2026, 4, 15),
        amount=200.0,
        direction="transfer",
    )
    _insert_transaction(
        database,
        transaction_id="tx-b",
        account_id="acct-b",
        occurred_on=date(2026, 4, 15),
        amount=-201.0,
        direction="transfer",
    )

    result = pair_transfers(database, PairTransfersRequest())
    assert result.pairs_created == 0
    assert len(result.unpaired) == 2


def test_same_account_collision_is_ambiguous_not_paired(
    database: DatabaseManager,
) -> None:
    _insert_transaction(
        database,
        transaction_id="tx-a",
        account_id="acct-shared",
        occurred_on=date(2026, 4, 15),
        amount=100.0,
        direction="transfer",
    )
    _insert_transaction(
        database,
        transaction_id="tx-b",
        account_id="acct-shared",
        occurred_on=date(2026, 4, 15),
        amount=-100.0,
        direction="transfer",
    )

    result = pair_transfers(database, PairTransfersRequest())
    assert result.pairs_created == 0
    ambiguous_ids = {a.transaction_id for a in result.ambiguous}
    assert ambiguous_ids == {"tx-a", "tx-b"}
    keys = _fetch_keys(database)
    assert keys["tx-a"] is None and keys["tx-b"] is None


def test_ambiguous_with_two_equal_delta_candidates(database: DatabaseManager) -> None:
    _insert_transaction(
        database,
        transaction_id="tx-source",
        account_id="acct-source",
        occurred_on=date(2026, 4, 15),
        amount=300.0,
        direction="transfer",
    )
    _insert_transaction(
        database,
        transaction_id="tx-cand-1",
        account_id="acct-x",
        occurred_on=date(2026, 4, 14),
        amount=-300.0,
        direction="transfer",
    )
    _insert_transaction(
        database,
        transaction_id="tx-cand-2",
        account_id="acct-y",
        occurred_on=date(2026, 4, 16),  # same 1-day delta
        amount=-300.0,
        direction="transfer",
    )

    result = pair_transfers(database, PairTransfersRequest())
    assert result.pairs_created == 0
    source_ambig = [a for a in result.ambiguous if a.transaction_id == "tx-source"]
    assert len(source_ambig) == 1
    assert set(source_ambig[0].candidates) == {"tx-cand-1", "tx-cand-2"}


def test_tiebreak_by_smallest_date_delta(database: DatabaseManager) -> None:
    _insert_transaction(
        database,
        transaction_id="tx-source",
        account_id="acct-source",
        occurred_on=date(2026, 4, 15),
        amount=400.0,
        direction="transfer",
    )
    _insert_transaction(
        database,
        transaction_id="tx-near",
        account_id="acct-x",
        occurred_on=date(2026, 4, 16),  # delta 1
        amount=-400.0,
        direction="transfer",
    )
    _insert_transaction(
        database,
        transaction_id="tx-far",
        account_id="acct-y",
        occurred_on=date(2026, 4, 18),  # delta 3
        amount=-400.0,
        direction="transfer",
    )

    result = pair_transfers(database, PairTransfersRequest(date_tolerance_days=3))
    assert result.pairs_created == 1
    keys = _fetch_keys(database)
    assert keys["tx-source"] == keys["tx-near"] is not None
    assert keys["tx-far"] is None
    assert {u.transaction_id for u in result.unpaired} == {"tx-far"}


def test_idempotent_rerun_produces_zero_new_pairs(database: DatabaseManager) -> None:
    _insert_transaction(
        database,
        transaction_id="tx-a",
        account_id="acct-a",
        occurred_on=date(2026, 4, 15),
        amount=500.0,
        direction="transfer",
    )
    _insert_transaction(
        database,
        transaction_id="tx-b",
        account_id="acct-b",
        occurred_on=date(2026, 4, 15),
        amount=-500.0,
        direction="transfer",
    )

    first = pair_transfers(database, PairTransfersRequest())
    assert first.pairs_created == 1
    assert first.already_paired_skipped == 0

    second = pair_transfers(database, PairTransfersRequest())
    assert second.pairs_created == 0
    assert second.candidates_examined == 0
    assert second.already_paired_skipped == 2
    assert second.unpaired == []


def test_dry_run_writes_nothing(database: DatabaseManager) -> None:
    _insert_transaction(
        database,
        transaction_id="tx-a",
        account_id="acct-a",
        occurred_on=date(2026, 4, 15),
        amount=600.0,
        direction="transfer",
    )
    _insert_transaction(
        database,
        transaction_id="tx-b",
        account_id="acct-b",
        occurred_on=date(2026, 4, 15),
        amount=-600.0,
        direction="transfer",
    )

    result = pair_transfers(database, PairTransfersRequest(dry_run=True))
    assert result.pairs_created == 1
    assert result.dry_run is True
    keys = _fetch_keys(database)
    assert keys["tx-a"] is None and keys["tx-b"] is None


def test_non_transfer_rows_surface_in_diagnostics_only(database: DatabaseManager) -> None:
    # A real Chase/Beacon transfer pair...
    _insert_transaction(
        database,
        transaction_id="tx-chase",
        account_id="acct-chase",
        occurred_on=date(2026, 4, 15),
        amount=750.0,
        direction="transfer",
        description="Payment Thank You",
    )
    _insert_transaction(
        database,
        transaction_id="tx-beacon",
        account_id="acct-beacon",
        occurred_on=date(2026, 4, 15),
        amount=-750.0,
        direction="transfer",
        description="CHASE CREDIT CRD EPAY",
    )
    # ...and a third-account row that LOOKS like a partner but was tagged
    # as 'outflow' by its parser. It should appear in suspected_untagged,
    # never in unpaired, and the DB must be untouched on that row.
    _insert_transaction(
        database,
        transaction_id="tx-webster",
        account_id="acct-webster",
        occurred_on=date(2026, 4, 16),
        amount=-750.0,
        direction="outflow",
        description="WEBSTR CK WEBXFR P2P ASHLEY M CALABR",
    )

    result = pair_transfers(database, PairTransfersRequest())
    assert result.pairs_created == 1
    assert "tx-webster" not in {u.transaction_id for u in result.unpaired}

    untagged_ids = {s.transaction_id for s in result.suspected_untagged}
    assert "tx-webster" in untagged_ids
    webster = next(s for s in result.suspected_untagged if s.transaction_id == "tx-webster")
    assert webster.confidence == "strong"
    keys = _fetch_keys(database)
    assert keys["tx-webster"] is None


def test_diagnostics_disabled_returns_empty_list(database: DatabaseManager) -> None:
    _insert_transaction(
        database,
        transaction_id="tx-chase",
        account_id="acct-chase",
        occurred_on=date(2026, 4, 15),
        amount=900.0,
        direction="transfer",
    )
    _insert_transaction(
        database,
        transaction_id="tx-beacon",
        account_id="acct-beacon",
        occurred_on=date(2026, 4, 15),
        amount=-900.0,
        direction="transfer",
    )
    _insert_transaction(
        database,
        transaction_id="tx-webster",
        account_id="acct-webster",
        occurred_on=date(2026, 4, 15),
        amount=-900.0,
        direction="outflow",
        description="WEBSTR CK WEBXFR P2P ASHLEY M CALABR",
    )

    result = pair_transfers(
        database, PairTransfersRequest(include_diagnostics=False)
    )
    assert result.pairs_created == 1
    assert result.suspected_untagged == []


def test_diagnostic_weak_classification_for_unknown_description(
    database: DatabaseManager,
) -> None:
    _insert_transaction(
        database,
        transaction_id="tx-chase",
        account_id="acct-chase",
        occurred_on=date(2026, 4, 15),
        amount=1000.0,
        direction="transfer",
    )
    _insert_transaction(
        database,
        transaction_id="tx-beacon",
        account_id="acct-beacon",
        occurred_on=date(2026, 4, 15),
        amount=-1000.0,
        direction="transfer",
    )
    _insert_transaction(
        database,
        transaction_id="tx-misc",
        account_id="acct-misc",
        occurred_on=date(2026, 4, 15),
        amount=-1000.0,
        direction="outflow",
        description="MISCELLANEOUS WITHDRAWAL",
    )

    result = pair_transfers(database, PairTransfersRequest())
    misc = next(s for s in result.suspected_untagged if s.transaction_id == "tx-misc")
    assert misc.confidence == "weak"
