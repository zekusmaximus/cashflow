from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import os


@dataclass(frozen=True)
class ServerSettings:
    project_root: Path
    docs_dir: Path
    tracker_csv_path: Path
    master_index_path: Path
    database_path: Path
    schema_path: Path
    watch_root: Path


def load_settings() -> ServerSettings:
    default_root = Path(__file__).resolve().parents[3]
    project_root = Path(os.getenv("LIQUIDITY_GATE_ROOT", default_root)).resolve()
    docs_dir = project_root / "docs"
    watch_root = Path(os.getenv("LIQUIDITY_GATE_WATCH_ROOT", project_root)).resolve()

    return ServerSettings(
        project_root=project_root,
        docs_dir=docs_dir,
        tracker_csv_path=docs_dir / "Spreadsheet_checklist_for_document_tracking.csv",
        master_index_path=docs_dir / "00_CASH_FLOW_MASTER_INDEX.md",
        database_path=project_root / "liquidity-gate.db",
        schema_path=project_root / "server" / "sql" / "schema.sql",
        watch_root=watch_root,
    )
