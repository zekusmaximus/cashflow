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
        transaction_id="tx-fid",
        account_id="acct-fidelity",
        occurred_on=date(2026, 4, 16),
        amount=-750.0,
        direction="outflow",
        description="FID BKG SVC LLC MONEYLINE",
    )

    result = pair_transfers(database, PairTransfersRequest())
    assert result.pairs_created == 1
    assert "tx-fid" not in {u.transaction_id for u in result.unpaired}

    untagged_ids = {s.transaction_id for s in result.suspected_untagged}
    assert "tx-fid" in untagged_ids
    fid = next(s for s in result.suspected_untagged if s.transaction_id == "tx-fid")
    assert fid.confidence == "strong"
    keys = _fetch_keys(database)
    assert keys["tx-fid"] is None


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
        transaction_id="tx-fid",
        account_id="acct-fidelity",
        occurred_on=date(2026, 4, 15),
        amount=-900.0,
        direction="outflow",
        description="FID BKG SVC LLC MONEYLINE",
    )

    result = pair_transfers(
        database, PairTransfersRequest(include_diagnostics=False)
    )
    assert result.pairs_created == 1
    assert result.suspected_untagged == []


def test_nxn_same_day_cluster_pairs_one_to_one(database: DatabaseManager) -> None:
    # The real-world driver: five $5,000 Webster -> Beacon CK TRANSFER rows
    # on the same day. Mutual-best-match alone leaves all 10 ambiguous; the
    # cluster fallback retires them as five 1-to-1 pairs.
    for i in range(5):
        _insert_transaction(
            database,
            transaction_id=f"tx-webster-{i}",
            account_id="acct-webster",
            occurred_on=date(2026, 1, 21),
            amount=-5000.0,
            direction="transfer",
            description=f"ASHLEY M CALA CK TRANSFER ASHLEY M CALABRESE 1631829{i:04d}",
        )
        _insert_transaction(
            database,
            transaction_id=f"tx-beacon-{i}",
            account_id="acct-beacon",
            occurred_on=date(2026, 1, 21),
            amount=5000.0,
            direction="transfer",
            description=f"WEBSTR CK WEBXFR P2P ASHLEY M CALABR 26012{i}",
        )

    result = pair_transfers(database, PairTransfersRequest())

    assert result.pairs_created == 5
    assert result.ambiguous == []
    assert result.unpaired == []

    keys = _fetch_keys(database)
    # Every row has a transfer_group_key now.
    assert all(keys[f"tx-webster-{i}"] is not None for i in range(5))
    assert all(keys[f"tx-beacon-{i}"] is not None for i in range(5))
    # Each Webster row shares its key with exactly one Beacon row.
    webster_keys = {keys[f"tx-webster-{i}"] for i in range(5)}
    beacon_keys = {keys[f"tx-beacon-{i}"] for i in range(5)}
    assert webster_keys == beacon_keys
    assert len(webster_keys) == 5  # five distinct pair-keys


def test_nxn_cluster_idempotent_on_rerun(database: DatabaseManager) -> None:
    for i in range(3):
        _insert_transaction(
            database,
            transaction_id=f"tx-a-{i}",
            account_id="acct-a",
            occurred_on=date(2026, 4, 10),
            amount=-2500.0,
            direction="transfer",
        )
        _insert_transaction(
            database,
            transaction_id=f"tx-b-{i}",
            account_id="acct-b",
            occurred_on=date(2026, 4, 10),
            amount=2500.0,
            direction="transfer",
        )

    first = pair_transfers(database, PairTransfersRequest())
    assert first.pairs_created == 3

    second = pair_transfers(database, PairTransfersRequest())
    assert second.pairs_created == 0
    assert second.already_paired_skipped == 6
    assert second.ambiguous == []


def test_cluster_count_mismatch_stays_ambiguous(database: DatabaseManager) -> None:
    # 3 Webster outflows + 2 Beacon inflows on the same day, same amount.
    # Counts don't match -> cluster rule must not fire; everything stays
    # ambiguous for human review.
    for i in range(3):
        _insert_transaction(
            database,
            transaction_id=f"tx-w-{i}",
            account_id="acct-webster",
            occurred_on=date(2026, 2, 25),
            amount=-5000.0,
            direction="transfer",
        )
    for i in range(2):
        _insert_transaction(
            database,
            transaction_id=f"tx-b-{i}",
            account_id="acct-beacon",
            occurred_on=date(2026, 2, 25),
            amount=5000.0,
            direction="transfer",
        )

    result = pair_transfers(database, PairTransfersRequest())

    assert result.pairs_created == 0
    ambiguous_ids = {a.transaction_id for a in result.ambiguous}
    assert ambiguous_ids == {f"tx-w-{i}" for i in range(3)} | {f"tx-b-{i}" for i in range(2)}
    keys = _fetch_keys(database)
    assert all(keys[tx_id] is None for tx_id in ambiguous_ids)


def test_cluster_three_accounts_stays_ambiguous(database: DatabaseManager) -> None:
    # Same amount + date across three accounts. Cluster rule requires
    # exactly two accounts; otherwise we cannot tell which account is the
    # actual partner.
    for tag in ("webster", "beacon", "ally"):
        _insert_transaction(
            database,
            transaction_id=f"tx-{tag}-0",
            account_id=f"acct-{tag}",
            occurred_on=date(2026, 3, 16),
            amount=-1000.0 if tag == "webster" else 1000.0,
            direction="transfer",
        )

    result = pair_transfers(database, PairTransfersRequest())

    assert result.pairs_created == 0
    ambiguous_ids = {a.transaction_id for a in result.ambiguous}
    assert "tx-webster-0" in ambiguous_ids


def test_cluster_same_sign_does_not_pair(database: DatabaseManager) -> None:
    # Defensive: two outflows on two different accounts that happen to
    # match in bucket+date must not pair. Real transfer pairs always have
    # opposite signs (one debits, the other credits).
    for i in range(2):
        _insert_transaction(
            database,
            transaction_id=f"tx-a-{i}",
            account_id="acct-a",
            occurred_on=date(2026, 3, 1),
            amount=-300.0,
            direction="transfer",
        )
        _insert_transaction(
            database,
            transaction_id=f"tx-b-{i}",
            account_id="acct-b",
            occurred_on=date(2026, 3, 1),
            amount=-300.0,
            direction="transfer",
        )

    result = pair_transfers(database, PairTransfersRequest())

    assert result.pairs_created == 0


def test_cluster_isolates_subgroup_from_unrelated_bucket_noise(
    database: DatabaseManager,
) -> None:
    # The real-data 2026-01-21 case: 5 Webster outbounds + 5 Beacon
    # inbounds 2 days later form a coherent cluster, but the same cents
    # bucket also contains unrelated $5K rows from other dates that pull
    # a third account in. Connected-components must isolate the cluster.
    for i in range(5):
        _insert_transaction(
            database,
            transaction_id=f"tx-w-{i}",
            account_id="acct-webster",
            occurred_on=date(2026, 1, 21),
            amount=-5000.0,
            direction="transfer",
        )
        _insert_transaction(
            database,
            transaction_id=f"tx-b-{i}",
            account_id="acct-beacon",
            occurred_on=date(2026, 1, 23),
            amount=5000.0,
            direction="transfer",
        )
    # Unrelated noise in the same cents bucket — Chase $5K weeks later,
    # plus an ambiguous Beacon row pointing at unpaired Webster outbounds
    # on a separate date. Tolerance keeps these out of the 1/21 cluster's
    # best_partners but they share the bucket.
    _insert_transaction(
        database,
        transaction_id="tx-chase-far",
        account_id="acct-chase",
        occurred_on=date(2026, 4, 3),
        amount=5000.0,
        direction="transfer",
    )
    _insert_transaction(
        database,
        transaction_id="tx-w-far-a",
        account_id="acct-webster",
        occurred_on=date(2026, 4, 6),
        amount=-5000.0,
        direction="transfer",
    )
    _insert_transaction(
        database,
        transaction_id="tx-w-far-b",
        account_id="acct-webster",
        occurred_on=date(2026, 4, 6),
        amount=-5000.0,
        direction="transfer",
    )

    result = pair_transfers(database, PairTransfersRequest())

    # The 1/21 ↔ 1/23 cluster pairs 1-to-1 (5 pairs).
    keys = _fetch_keys(database)
    cluster_keys = {keys[f"tx-w-{i}"] for i in range(5)} | {
        keys[f"tx-b-{i}"] for i in range(5)
    }
    assert None not in cluster_keys
    assert result.pairs_created >= 5
    # The far Chase row sees both 4/6 Webster rows as best partners
    # (delta=3 tie), so it stays ambiguous; the unrelated subgroup must
    # not have pulled the 1/21 cluster's rows into its own component.
    paired_ids = {tx for tx, key in keys.items() if key is not None}
    assert paired_ids.issuperset({f"tx-w-{i}" for i in range(5)})
    assert paired_ids.issuperset({f"tx-b-{i}" for i in range(5)})


def test_cluster_partial_subgroup_does_not_pair(database: DatabaseManager) -> None:
    # 3 Webster on 1/21 + 2 Webster on 1/22 vs 2 Beacon on 1/21 +
    # 3 Beacon on 1/22. The bucket totals are 5+5 but the within-date
    # subgroups (3/2 and 2/3) don't form a clean cluster — each row's
    # best_partners is only the matching-date partners (delta=0 wins over
    # delta=1), so the mutual-set check fails. Stays ambiguous.
    for i in range(3):
        _insert_transaction(
            database,
            transaction_id=f"tx-w-21-{i}",
            account_id="acct-webster",
            occurred_on=date(2026, 1, 21),
            amount=-5000.0,
            direction="transfer",
        )
    for i in range(2):
        _insert_transaction(
            database,
            transaction_id=f"tx-w-22-{i}",
            account_id="acct-webster",
            occurred_on=date(2026, 1, 22),
            amount=-5000.0,
            direction="transfer",
        )
    for i in range(2):
        _insert_transaction(
            database,
            transaction_id=f"tx-b-21-{i}",
            account_id="acct-beacon",
            occurred_on=date(2026, 1, 21),
            amount=5000.0,
            direction="transfer",
        )
    for i in range(3):
        _insert_transaction(
            database,
            transaction_id=f"tx-b-22-{i}",
            account_id="acct-beacon",
            occurred_on=date(2026, 1, 22),
            amount=5000.0,
            direction="transfer",
        )

    result = pair_transfers(database, PairTransfersRequest())

    # The 3/2 and 2/3 within-date subgroups can't pair cleanly via the
    # cluster rule (mutual-set mismatch). They stay ambiguous.
    assert result.pairs_created == 0
    assert len(result.ambiguous) == 10


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


def _fetch_directions(database: DatabaseManager) -> dict[str, str]:
    connection = database.connect(read_only=True)
    try:
        rows = connection.execute(
            "SELECT id, direction FROM transactions"
        ).fetchall()
    finally:
        connection.close()
    return {row["id"]: row["direction"] for row in rows}


def test_ally_unpaired_inbound_from_reclassified_as_inflow(
    database: DatabaseManager,
) -> None:
    # Ally "Requested transfer from <name> Ally Bank Transfer" rows arrive
    # tagged direction='transfer' so they can pair with a Beacon debit leg.
    # When no Beacon counterpart exists (external deposit such as a bonus
    # check or direct ACH) the row must be reclassified as 'inflow'.
    _insert_transaction(
        database,
        transaction_id="tx-ally-bonus",
        account_id="acct-ally-hysa",
        occurred_on=date(2026, 1, 5),
        amount=4500.0,
        direction="transfer",
        description="Requested transfer from JEFFREY A ZYJESKI Ally Bank Transfer",
    )

    result = pair_transfers(database, PairTransfersRequest())

    assert result.pairs_created == 0
    assert result.ally_inbound_reclassified == 1
    # Row must not appear in the unpaired list (it was reclassified, not left orphaned).
    assert result.unpaired == []
    # DB row must now carry direction='inflow'.
    assert _fetch_directions(database)["tx-ally-bonus"] == "inflow"


def test_ally_inbound_from_dry_run_does_not_write(database: DatabaseManager) -> None:
    _insert_transaction(
        database,
        transaction_id="tx-ally-bonus",
        account_id="acct-ally-hysa",
        occurred_on=date(2026, 1, 5),
        amount=4500.0,
        direction="transfer",
        description="Requested transfer from JEFFREY A ZYJESKI Ally Bank Transfer",
    )

    result = pair_transfers(database, PairTransfersRequest(dry_run=True))

    assert result.dry_run is True
    assert result.ally_inbound_reclassified == 1
    # dry_run must not touch the DB.
    assert _fetch_directions(database)["tx-ally-bonus"] == "transfer"


def test_ally_paired_inbound_from_not_reclassified(database: DatabaseManager) -> None:
    # An Ally "from" row that successfully pairs with a Beacon debit should
    # NOT be reclassified — it's a genuine internal transfer.
    _insert_transaction(
        database,
        transaction_id="tx-ally-from",
        account_id="acct-ally-hysa",
        occurred_on=date(2026, 3, 10),
        amount=5000.0,
        direction="transfer",
        description="Requested transfer from JEFFREY A ZYJESKI Ally Bank Transfer",
    )
    _insert_transaction(
        database,
        transaction_id="tx-beacon-debit",
        account_id="acct-beacon",
        occurred_on=date(2026, 3, 10),
        amount=-5000.0,
        direction="transfer",
        description="ALLY BANK TRANSFER",
    )

    result = pair_transfers(database, PairTransfersRequest())

    assert result.pairs_created == 1
    assert result.ally_inbound_reclassified == 0
    assert result.unpaired == []
    # Both rows must stay as 'transfer' with a shared group key.
    directions = _fetch_directions(database)
    assert directions["tx-ally-from"] == "transfer"
    assert directions["tx-beacon-debit"] == "transfer"
    keys = _fetch_keys(database)
    assert keys["tx-ally-from"] == keys["tx-beacon-debit"] is not None


def test_non_ally_unpaired_transfer_stays_unpaired(database: DatabaseManager) -> None:
    # An "inbound from" description on a NON-Ally account must not be
    # reclassified — only the Ally HYSA account gets this treatment.
    _insert_transaction(
        database,
        transaction_id="tx-other-inbound",
        account_id="acct-beacon",
        occurred_on=date(2026, 2, 1),
        amount=1200.0,
        direction="transfer",
        description="Requested transfer from SOMEONE Ally Bank Transfer",
    )

    result = pair_transfers(database, PairTransfersRequest())

    assert result.ally_inbound_reclassified == 0
    assert {u.transaction_id for u in result.unpaired} == {"tx-other-inbound"}
    assert _fetch_directions(database)["tx-other-inbound"] == "transfer"
