"""Regression coverage for the 2026-05 monthly-migration pilot bugs.

B1 — ``ingest_documents`` must not recurse into set-aside folders
(``archive/`` and ``.`` / ``_`` prefixed directories), and it must agree
with ``read_document_metadata`` on scan scope (both share
``iter_candidate_files``).

B2 — dedup is account-scoped, not filename-scoped: re-importing the same
transactions under a different source filename updates in place rather than
inserting duplicates, while genuine same-day/same-amount duplicates survive.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from liquidity_gate_mcp.config import ServerSettings
from liquidity_gate_mcp.database import DatabaseManager
from liquidity_gate_mcp.ingest import ingest_watch_root
from liquidity_gate_mcp.tools import iter_candidate_files, read_document_metadata


PROJECT_ROOT = Path(__file__).resolve().parents[2]

CHASE_HEADER = "Transaction Date,Post Date,Description,Category,Type,Amount,Memo\n"
# A single transaction shared by the YTD export and the per-month file.
SHARED_PURCHASE = '2/15/2026,2/16/2026,"TARGET STORE #123",Shopping,Sale,-42.10,\n'
# Two genuinely-distinct same-day/same-amount/same-merchant charges — must
# stay two rows (the documented 2026-02-27 -10.31 pair).
GENUINE_DUP_A = '2/27/2026,2/28/2026,"TST* 3570 CT GENERAL ASSE",Food,Sale,-10.31,\n'
GENUINE_DUP_B = '2/27/2026,2/28/2026,"TST* 3570 CT GENERAL ASSE",Food,Sale,-10.31,\n'
ONLY_IN_YTD = '1/10/2026,1/11/2026,"OLD GYM MEMBERSHIP",Health,Sale,-29.99,\n'
ONLY_IN_MONTHLY = '3/05/2026,3/06/2026,"NEW STREAMING SVC",Entertainment,Sale,-12.99,\n'


def _make_settings(watch_root: Path, database: DatabaseManager, schema_path: Path) -> ServerSettings:
    return ServerSettings(
        project_root=PROJECT_ROOT,
        docs_dir=PROJECT_ROOT / "docs",
        tracker_csv_path=PROJECT_ROOT / "docs" / "Spreadsheet_checklist_for_document_tracking.csv",
        master_index_path=PROJECT_ROOT / "docs" / "00_CASH_FLOW_MASTER_INDEX.md",
        project_status_path=PROJECT_ROOT / "docs" / "PROJECT_STATUS.md",
        database_path=database.database_path,
        schema_path=schema_path,
        watch_root=watch_root,
    )


def _write(directory: Path, filename: str, body: str) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / filename
    path.write_text(CHASE_HEADER + body, encoding="utf-8")
    return path


def _count(database: DatabaseManager, sql: str) -> int:
    connection = database.connect()
    try:
        return int(connection.execute(sql).fetchone()[0])
    finally:
        connection.close()


class _Watcher:
    def recent_events(self) -> list:
        return []


# ---------------------------------------------------------------------------
# B1 — scan scope
# ---------------------------------------------------------------------------


def test_iter_candidate_files_skips_archive_and_set_aside_dirs(tmp_path: Path) -> None:
    root = tmp_path / "watch"
    top = _write(root, "2026-05_Chase_Credit_Card.csv", SHARED_PURCHASE)
    nested_live = _write(root / "current", "2026-04_Chase_Credit_Card.csv", SHARED_PURCHASE)
    _write(root / "archive" / "ytd_superseded", "2026-05-12_Chase_Credit_Card.csv", SHARED_PURCHASE)
    _write(root / "_probe_hold", "held.csv", SHARED_PURCHASE)
    _write(root / ".trash", "deleted.csv", SHARED_PURCHASE)

    found = set(iter_candidate_files(root))

    # Top-level and ordinary nested folders are still scanned (recursion is
    # preserved); only set-aside directories are excluded.
    assert top in found
    assert nested_live in found
    assert not any("archive" in p.parts for p in found)
    assert not any(part.startswith("_") or part.startswith(".") for p in found for part in p.relative_to(root).parts[:-1])


def test_ingest_does_not_reingest_archived_file(tmp_path: Path, database: DatabaseManager, schema_path: Path) -> None:
    root = tmp_path / "watch"
    _write(root, "2026-02_Chase_Credit_Card.csv", SHARED_PURCHASE)
    # An archived YTD export that overlaps the live file — must be ignored.
    _write(root / "archive", "2026-05-12_Chase_Credit_Card.csv", SHARED_PURCHASE + ONLY_IN_YTD)

    settings = _make_settings(root, database, schema_path)
    result = ingest_watch_root(settings, database)

    assert result.totals.files_seen == 1
    assert _count(database, "SELECT COUNT(*) FROM transactions") == 1


def test_ingest_and_metadata_agree_on_scan_scope(tmp_path: Path, database: DatabaseManager, schema_path: Path) -> None:
    root = tmp_path / "watch"
    _write(root, "2026-02_Chase_Credit_Card.csv", SHARED_PURCHASE)
    _write(root / "archive", "old_Chase_Credit_Card.csv", SHARED_PURCHASE)

    settings = _make_settings(root, database, schema_path)
    ingest_files = set(iter_candidate_files(root))
    metadata = read_document_metadata(settings, database, _Watcher())

    assert metadata.summary.files_scanned == len(ingest_files) == 1


# ---------------------------------------------------------------------------
# B2 — account-scoped dedup
# ---------------------------------------------------------------------------


def test_overlapping_files_dedup_to_union_not_sum(tmp_path: Path, database: DatabaseManager, schema_path: Path) -> None:
    root = tmp_path / "watch"
    settings = _make_settings(root, database, schema_path)

    # YTD-style file loaded first: shared purchase + a YTD-only row.
    ytd = _write(root, "2026-05-12_Chase_Credit_Card.csv", SHARED_PURCHASE + ONLY_IN_YTD)
    ingest_watch_root(settings, database)
    assert _count(database, "SELECT COUNT(*) FROM transactions") == 2

    # Swap in a differently-named monthly file that overlaps on the shared
    # purchase and adds a monthly-only row. Final state must be the union (3),
    # not the sum (4).
    ytd.unlink()
    _write(root, "2026-03_Chase_Credit_Card.csv", SHARED_PURCHASE + ONLY_IN_MONTHLY)
    ingest_watch_root(settings, database)

    assert _count(database, "SELECT COUNT(*) FROM transactions") == 3


def test_reingest_is_idempotent_across_filenames(tmp_path: Path, database: DatabaseManager, schema_path: Path) -> None:
    root = tmp_path / "watch"
    settings = _make_settings(root, database, schema_path)

    first = _write(root, "2026-01_Chase_Credit_Card.csv", SHARED_PURCHASE + ONLY_IN_YTD)
    ingest_watch_root(settings, database)
    first.unlink()

    _write(root, "totally_different_Chase_Credit_Card_name.csv", SHARED_PURCHASE + ONLY_IN_YTD)
    second = ingest_watch_root(settings, database)

    assert second.totals.inserted == 0
    assert second.totals.updated == 2
    assert _count(database, "SELECT COUNT(*) FROM transactions") == 2


def test_genuine_same_day_duplicates_survive_dedup(tmp_path: Path, database: DatabaseManager, schema_path: Path) -> None:
    root = tmp_path / "watch"
    settings = _make_settings(root, database, schema_path)

    # The 2026-02-27 -10.31 pair appears in both a YTD and a monthly file.
    ytd = _write(root, "2026-05-12_Chase_Credit_Card.csv", GENUINE_DUP_A + GENUINE_DUP_B)
    ingest_watch_root(settings, database)
    assert _count(database, "SELECT COUNT(*) FROM transactions") == 2

    ytd.unlink()
    _write(root, "2026-02_Chase_Credit_Card.csv", GENUINE_DUP_A + GENUINE_DUP_B)
    ingest_watch_root(settings, database)

    # Still exactly two rows — the genuine pair is preserved, not merged.
    rows = _count(
        database,
        "SELECT COUNT(*) FROM transactions WHERE amount = -10.31 AND occurred_on = '2026-02-27'",
    )
    assert rows == 2
    assert _count(database, "SELECT COUNT(*) FROM transactions") == 2
