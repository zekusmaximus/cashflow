"""End-to-end Self-Help FCU ingestion: dispatch, classify, pair, re-ingest.

These are the handoff's definition-of-done checks, run through the real
``ingest_watch_root`` dispatch (tracker matcher included) rather than by
calling the parser directly, because two of them are about the wiring:
doc-088 has to win the tracker match, and re-ingesting the superseded export
has to add nothing.

The Ally side is seeded to mirror the live database at the time of this
change: four "Requested transfer from" legs that ``pair_transfers`` had
already reclassified to ``direction='inflow'`` with no transfer_group_key.
That is the state the re-arm in ``transfers.py`` exists to recover from — see
``test_rearm_recovers_the_four_stranded_ally_legs``.
"""

from __future__ import annotations

import sqlite3
from datetime import date
from pathlib import Path

from liquidity_gate_mcp.config import ServerSettings
from liquidity_gate_mcp.database import DatabaseManager
from liquidity_gate_mcp.ingest import ingest_watch_root
from liquidity_gate_mcp.models import PairTransfersRequest
from liquidity_gate_mcp.transfers import pair_transfers

from . import selfhelp_fixtures as fx


PROJECT_ROOT = Path(__file__).resolve().parents[2]

SELFHELP_ACCOUNT_ID = "acct-selfhelp-savings"
ALLY_ACCOUNT_ID = "acct-ally-hysa"

# The Ally-side legs of the Self-Help sweeps, exactly as they sit in the live
# database: amount, date, and already flipped to 'inflow' by a prior run.
ALLY_LEGS = (
    (date(2026, 1, 5), 4500.00),
    (date(2026, 4, 8), 5080.00),
    (date(2026, 5, 8), 1000.00),
    (date(2026, 7, 21), 2901.69),
)


def _settings(watch_root: Path, database: DatabaseManager, schema_path: Path) -> ServerSettings:
    return ServerSettings(
        project_root=PROJECT_ROOT,
        docs_dir=PROJECT_ROOT / "docs",
        tracker_csv_path=(
            PROJECT_ROOT / "docs" / "Spreadsheet_checklist_for_document_tracking.csv"
        ),
        master_index_path=PROJECT_ROOT / "docs" / "00_CASH_FLOW_MASTER_INDEX.md",
        project_status_path=PROJECT_ROOT / "docs" / "PROJECT_STATUS.md",
        database_path=database.database_path,
        schema_path=schema_path,
        watch_root=watch_root,
    )


def _seed_ally_legs(database: DatabaseManager) -> None:
    connection = database.connect()
    try:
        connection.execute(
            "INSERT OR IGNORE INTO accounts (id, institution, account_name, "
            "account_type, owner, currency) VALUES (?, 'Ally', 'Ally HYSA', "
            "'savings', 'joint', 'USD')",
            (ALLY_ACCOUNT_ID,),
        )
        connection.execute(
            "INSERT OR IGNORE INTO import_batches (id, source_name, parser_version, "
            "imported_at, raw_payload) VALUES ('batch-ally-seed', 'seed', 'test', "
            "'2026-09-13T00:00:00Z', '{}')"
        )
        for index, (occurred_on, amount) in enumerate(ALLY_LEGS):
            connection.execute(
                """
                INSERT INTO transactions (
                  id, account_id, import_batch_id, source_record_key,
                  source_document_name, occurred_on, posted_on, description_raw,
                  merchant_normalized, amount, direction, currency,
                  primary_category, subcategory, household_role, lifecycle,
                  transfer_group_key, statement_period, metadata_json
                ) VALUES (?, ?, 'batch-ally-seed', ?, 'ally-seed.csv', ?, ?, ?,
                          NULL, ?, 'inflow', 'USD', 'transfer', NULL, 'joint',
                          'recurring', NULL, NULL, '{}')
                """,
                (
                    f"tx-ally-{index}",
                    ALLY_ACCOUNT_ID,
                    f"ally-key-{index}",
                    occurred_on.isoformat(),
                    occurred_on.isoformat(),
                    "Requested transfer from JEFFREY A ZYJESKI Ally Bank Transfer",
                    amount,
                ),
            )
        connection.commit()
    finally:
        connection.close()


def _rows(database: DatabaseManager, sql: str, params: tuple = ()) -> list[sqlite3.Row]:
    connection = database.connect(read_only=True)
    try:
        return connection.execute(sql, params).fetchall()
    finally:
        connection.close()


def _scalar(database: DatabaseManager, sql: str, params: tuple = ()):
    return _rows(database, sql, params)[0][0]


def _ingest(watch_root: Path, database: DatabaseManager, schema_path: Path):
    return ingest_watch_root(_settings(watch_root, database, schema_path), database)


# --------------------------------------------------------------------------
# Dispatch and load
# --------------------------------------------------------------------------


def test_life_of_account_file_routes_to_doc_088(
    tmp_path: Path, database: DatabaseManager, schema_path: Path
) -> None:
    """DoD #1/#2 — the tracker matcher must hand this filename to doc-088.

    Before the doc-088 tracker row existed, the best score any of the 87 rows
    could manage was 0.346, below the 0.35 threshold, so the file landed as
    ``no_parser``.
    """
    watch_root = tmp_path / "watch"
    watch_root.mkdir()
    fx.write_life_of_account(watch_root)

    result = _ingest(watch_root, database, schema_path)

    [summary] = result.files
    assert summary.doc_id == "doc-088"
    assert summary.parser == "selfhelp-savings"
    assert summary.status == "ingested"
    assert summary.inserted == 21
    assert summary.errors == []

    [account] = _rows(
        database, "SELECT * FROM accounts WHERE id = ?", (SELFHELP_ACCOUNT_ID,)
    )
    assert account["institution"] == "Self-Help"
    assert account["account_name"] == "Self-Help FCU (rental)"
    assert account["account_type"] == "savings"
    assert account["owner"] == "jeff"
    assert account["currency"] == "USD"
    assert account["is_active"] == 1


def test_ingested_ledger_matches_the_file(
    tmp_path: Path, database: DatabaseManager, schema_path: Path
) -> None:
    """DoD #2/#3/#4/#6."""
    watch_root = tmp_path / "watch"
    watch_root.mkdir()
    fx.write_life_of_account(watch_root)
    _ingest(watch_root, database, schema_path)

    scope = "WHERE account_id = ?"
    assert _scalar(database, f"SELECT COUNT(*) FROM transactions {scope}", (SELFHELP_ACCOUNT_ID,)) == 21
    assert _scalar(database, f"SELECT MIN(occurred_on) FROM transactions {scope}", (SELFHELP_ACCOUNT_ID,)) == "2026-01-05"
    assert _scalar(database, f"SELECT MAX(occurred_on) FROM transactions {scope}", (SELFHELP_ACCOUNT_ID,)) == "2026-09-02"

    # DoD #3 — description_raw is NOT NULL; empty or whitespace-only is the
    # failure this whole parser exists to prevent.
    assert _scalar(
        database,
        f"SELECT COUNT(*) FROM transactions {scope} AND TRIM(description_raw) = ''",
        (SELFHELP_ACCOUNT_ID,),
    ) == 0

    # DoD #4 — opening 5,660.69 + (-1,823.25) = 3,837.44, the 9/2 closing.
    total = _scalar(
        database, f"SELECT ROUND(SUM(amount), 2) FROM transactions {scope}", (SELFHELP_ACCOUNT_ID,)
    )
    assert total == -1823.25
    assert round(fx.OPENING_BALANCE + total, 2) == fx.CLOSING_BALANCE

    # DoD #6 — regression guard against the superseded 19-row export.
    prefix = _rows(
        database,
        f"SELECT COUNT(*) AS n, ROUND(SUM(amount), 2) AS total FROM transactions "
        f"{scope} AND occurred_on <= '2026-08-03'",
        (SELFHELP_ACCOUNT_ID,),
    )[0]
    assert (prefix["n"], prefix["total"]) == (19, -3118.88)


def test_classification_counts(
    tmp_path: Path, database: DatabaseManager, schema_path: Path
) -> None:
    """DoD #7 — every row lands in exactly the right bucket, and none is left
    unclassified. The dividend and rent rules match on the Ext half of
    description_raw, which is the only place the type appears on 8 of 21 rows."""
    watch_root = tmp_path / "watch"
    watch_root.mkdir()
    fx.write_life_of_account(watch_root)
    _ingest(watch_root, database, schema_path)

    rent = _rows(
        database,
        "SELECT amount, household_role FROM transactions WHERE account_id = ? "
        "AND primary_category = 'income' AND subcategory = 'rental_income'",
        (SELFHELP_ACCOUNT_ID,),
    )
    assert len(rent) == 9
    assert {row["amount"] for row in rent} == {1295.0}
    assert {row["household_role"] for row in rent} == {"jeff"}

    interest = _rows(
        database,
        "SELECT amount FROM transactions WHERE account_id = ? "
        "AND primary_category = 'income' AND subcategory = 'interest'",
        (SELFHELP_ACCOUNT_ID,),
    )
    assert len(interest) == 5
    assert round(sum(row["amount"] for row in interest), 2) == 3.44

    sweeps = _rows(
        database,
        "SELECT amount FROM transactions WHERE account_id = ? AND direction = 'transfer' "
        "AND primary_category = 'transfer' AND description_raw LIKE 'ALLY BANK $TRANSFER%'",
        (SELFHELP_ACCOUNT_ID,),
    )
    assert len(sweeps) == 4
    assert round(sum(row["amount"] for row in sweeps), 2) == -13481.69

    verify = _rows(
        database,
        "SELECT amount FROM transactions WHERE account_id = ? "
        "AND description_raw LIKE 'ALLY BANK ACCTVERIFY%' AND direction = 'transfer' "
        "AND primary_category = 'transfer'",
        (SELFHELP_ACCOUNT_ID,),
    )
    assert len(verify) == 3
    assert round(sum(row["amount"] for row in verify), 2) == 0.00

    assert _scalar(
        database,
        "SELECT COUNT(*) FROM transactions WHERE account_id = ? "
        "AND primary_category = 'unclassified'",
        (SELFHELP_ACCOUNT_ID,),
    ) == 0


def test_dividend_rule_catches_both_description_formats(
    tmp_path: Path, database: DatabaseManager, schema_path: Path
) -> None:
    """The five dividends use two description formats ("TERM: ... APYE:" and
    "Annual Percentage Yield Earned:"). A description-text rule would catch at
    most three; matching the Ext token catches all five."""
    watch_root = tmp_path / "watch"
    watch_root.mkdir()
    fx.write_life_of_account(watch_root)
    _ingest(watch_root, database, schema_path)

    formats = _rows(
        database,
        "SELECT description_raw FROM transactions WHERE account_id = ? "
        "AND subcategory = 'interest'",
        (SELFHELP_ACCOUNT_ID,),
    )
    apye_term = sum(1 for row in formats if "APYE:" in row["description_raw"])
    spelled_out = sum(
        1 for row in formats if "Annual Percentage Yield" in row["description_raw"]
    )
    assert (apye_term, spelled_out) == (2, 3)


# --------------------------------------------------------------------------
# Idempotency
# --------------------------------------------------------------------------


def test_reingest_is_idempotent_across_both_exports(
    tmp_path: Path, database: DatabaseManager, schema_path: Path
) -> None:
    """DoD #9 — re-running the same file adds nothing, AND ingesting the
    superseded export (same 19 transactions, UNMASKED ACH ids) adds nothing.

    The second half is the one that fails if source_record_key is derived
    from description text: the masked and unmasked spellings hash apart and
    every sweep row double-books.
    """
    watch_root = tmp_path / "watch"
    watch_root.mkdir()
    fx.write_life_of_account(watch_root)

    first = _ingest(watch_root, database, schema_path)
    assert first.totals.inserted == 21

    second = _ingest(watch_root, database, schema_path)
    assert second.totals.inserted == 0
    assert second.totals.updated == 21

    fx.write_ytd_prefix(watch_root)
    third = _ingest(watch_root, database, schema_path)
    assert third.totals.inserted == 0
    assert _scalar(
        database,
        "SELECT COUNT(*) FROM transactions WHERE account_id = ?",
        (SELFHELP_ACCOUNT_ID,),
    ) == 21


# --------------------------------------------------------------------------
# Pairing — the acceptance test
# --------------------------------------------------------------------------


def test_rearm_recovers_the_four_stranded_ally_legs(
    tmp_path: Path, database: DatabaseManager, schema_path: Path
) -> None:
    """DoD #8 — after pairing, no Ally "Requested transfer%" row is left
    unpaired.

    All four Ally legs start as direction='inflow' with a NULL
    transfer_group_key, which is where a previous ``pair_transfers`` run left
    them. Without the re-arm they are invisible to ``_resolve_pairs`` and none
    of them can pair however good the Self-Help parser is.
    """
    watch_root = tmp_path / "watch"
    watch_root.mkdir()
    fx.write_life_of_account(watch_root)
    _seed_ally_legs(database)
    _ingest(watch_root, database, schema_path)

    result = pair_transfers(database, PairTransfersRequest())

    assert result.inbound_rearmed == 4
    assert result.inbound_rearmed_paired == 4
    assert result.pairs_created == 4
    assert result.ally_inbound_reclassified == 0

    assert _scalar(
        database,
        "SELECT COUNT(*) FROM transactions t JOIN accounts a ON a.id = t.account_id "
        "WHERE a.institution = 'Ally' AND t.description_raw LIKE 'Requested transfer%' "
        "AND t.transfer_group_key IS NULL",
    ) == 0

    # Every leg is stored back as a transfer, and the four Self-Help sweeps
    # are the partners.
    assert _scalar(
        database,
        "SELECT COUNT(*) FROM transactions WHERE account_id = ? AND direction = 'transfer'",
        (ALLY_ACCOUNT_ID,),
    ) == 4
    paired_selfhelp = _rows(
        database,
        "SELECT occurred_on, amount FROM transactions WHERE account_id = ? "
        "AND transfer_group_key IS NOT NULL ORDER BY occurred_on",
        (SELFHELP_ACCOUNT_ID,),
    )
    assert [(row["occurred_on"], row["amount"]) for row in paired_selfhelp] == [
        ("2026-01-05", -4500.0),
        ("2026-04-08", -5080.0),
        ("2026-05-08", -1000.0),
        ("2026-07-17", -2901.69),
    ]


def test_july_leg_needs_the_five_day_window(
    tmp_path: Path, database: DatabaseManager, schema_path: Path
) -> None:
    """The July leg clears 2026-07-17 on Self-Help and 2026-07-21 at Ally —
    four days. At the old default of 3 it cannot pair; the other three legs
    still can."""
    watch_root = tmp_path / "watch"
    watch_root.mkdir()
    fx.write_life_of_account(watch_root)
    _seed_ally_legs(database)
    _ingest(watch_root, database, schema_path)

    narrow = pair_transfers(database, PairTransfersRequest(date_tolerance_days=3))
    assert narrow.pairs_created == 3

    # The unpaired July leg is reclassified back to 'inflow' by the same run,
    # exactly as before — and the next run re-arms it.
    assert narrow.ally_inbound_reclassified == 1

    wide = pair_transfers(database, PairTransfersRequest(date_tolerance_days=5))
    assert wide.pairs_created == 1
    assert wide.inbound_rearmed == 1
    assert _scalar(
        database,
        "SELECT COUNT(*) FROM transactions t JOIN accounts a ON a.id = t.account_id "
        "WHERE a.institution = 'Ally' AND t.description_raw LIKE 'Requested transfer%' "
        "AND t.transfer_group_key IS NULL",
    ) == 0


def test_pairing_is_idempotent_once_everything_is_paired(
    tmp_path: Path, database: DatabaseManager, schema_path: Path
) -> None:
    watch_root = tmp_path / "watch"
    watch_root.mkdir()
    fx.write_life_of_account(watch_root)
    _seed_ally_legs(database)
    _ingest(watch_root, database, schema_path)

    pair_transfers(database, PairTransfersRequest())
    again = pair_transfers(database, PairTransfersRequest())

    assert again.pairs_created == 0
    assert again.inbound_rearmed == 0
    assert again.already_paired_skipped == 8


# --------------------------------------------------------------------------
# The Ally gate must not move
# --------------------------------------------------------------------------


def test_ally_gate_is_unmoved_by_the_reclassification(
    tmp_path: Path, database: DatabaseManager, schema_path: Path
) -> None:
    """DoD #10 — this change reclassifies what *feeds* Ally; it must never
    move Ally's own balance.

    The four legs go from direction='inflow' to direction='transfer', and
    ``v_computed_balance`` scores those two identically for a positive
    amount (``inflow`` -> +ABS(amount), ``transfer`` -> +amount). Pinning it
    here so a future change to that CASE expression cannot silently shift the
    HYSA gate.
    """
    watch_root = tmp_path / "watch"
    watch_root.mkdir()
    fx.write_life_of_account(watch_root)
    _seed_ally_legs(database)
    _ingest(watch_root, database, schema_path)

    connection = database.connect()
    try:
        connection.execute(
            "INSERT INTO reconciliation_periods (id, account_id, period_start, "
            "period_end, statement_closing_balance, closing_balance_source) "
            "VALUES ('rp-ally-seed', ?, '2025-12-01', '2025-12-31', 40000.0, 'seed')",
            (ALLY_ACCOUNT_ID,),
        )
        connection.commit()
    finally:
        connection.close()

    before = _scalar(
        database,
        "SELECT computed_balance FROM v_computed_balance WHERE account_id = ?",
        (ALLY_ACCOUNT_ID,),
    )
    pair_transfers(database, PairTransfersRequest())
    after = _scalar(
        database,
        "SELECT computed_balance FROM v_computed_balance WHERE account_id = ?",
        (ALLY_ACCOUNT_ID,),
    )

    assert before == after == round(40000.0 + sum(amount for _, amount in ALLY_LEGS), 2)
