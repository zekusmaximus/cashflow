from __future__ import annotations

from pathlib import Path

from liquidity_gate_mcp.config import ServerSettings
from liquidity_gate_mcp.database import DatabaseManager
from liquidity_gate_mcp.tools import read_document_metadata


PROJECT_ROOT = Path(__file__).resolve().parents[2]


class _Watcher:
    def recent_events(self) -> list:
        return []


def test_read_document_metadata_assigns_each_file_to_single_best_tracker_row(
    tmp_path: Path, database: DatabaseManager, schema_path: Path
) -> None:
    watch_root = tmp_path / "watch"
    watch_root.mkdir()
    (watch_root / "2026-05-12_Chase_Credit_Card.csv").write_text("", encoding="utf-8")

    settings = ServerSettings(
        project_root=PROJECT_ROOT,
        docs_dir=PROJECT_ROOT / "docs",
        tracker_csv_path=PROJECT_ROOT / "docs" / "Spreadsheet_checklist_for_document_tracking.csv",
        master_index_path=PROJECT_ROOT / "docs" / "00_CASH_FLOW_MASTER_INDEX.md",
        project_status_path=PROJECT_ROOT / "docs" / "PROJECT_STATUS.md",
        database_path=database.database_path,
        schema_path=schema_path,
        watch_root=watch_root,
    )

    result = read_document_metadata(settings, database, _Watcher())

    items_by_id = {item.id: item for item in result.items}

    assert result.summary.obtained_count == 1
    assert sum(len(item.matched_files) for item in result.items) == 1
    assert items_by_id["doc-001"].status == "obtained"
    assert items_by_id["doc-001"].matched_files[0].relative_path == "2026-05-12_Chase_Credit_Card.csv"
    assert items_by_id["doc-005"].status == "missing"
    assert items_by_id["doc-005"].matched_files == []


def test_read_document_metadata_honors_manual_tracker_obtained_flags(
    tmp_path: Path, database: DatabaseManager, schema_path: Path
) -> None:
    tracker_csv = tmp_path / "tracker.csv"
    tracker_csv.write_text(
        "Category,Document,Subject Matter,Format,Priority,Source / Where to Get,Why Needed,Obtained ✓,Date Added,Notes\n"
        "A. Core Transactions,Manual source,Spending,CSV,Essential core,Bank portal,Manual override,yes,,\n",
        encoding="utf-8",
    )

    watch_root = tmp_path / "watch"
    watch_root.mkdir()

    settings = ServerSettings(
        project_root=PROJECT_ROOT,
        docs_dir=PROJECT_ROOT / "docs",
        tracker_csv_path=tracker_csv,
        master_index_path=PROJECT_ROOT / "docs" / "00_CASH_FLOW_MASTER_INDEX.md",
        project_status_path=PROJECT_ROOT / "docs" / "PROJECT_STATUS.md",
        database_path=database.database_path,
        schema_path=schema_path,
        watch_root=watch_root,
    )

    result = read_document_metadata(settings, database, _Watcher())

    [item] = result.items

    assert result.summary.obtained_count == 1
    assert result.summary.missing_count == 0
    assert item.id == "doc-001"
    assert item.status == "obtained"
    assert item.matched_files == []