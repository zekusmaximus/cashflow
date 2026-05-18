from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import os
import sys


# Must match `identifier` in src-tauri/tauri.conf.json. Tauri's plugin-sql
# resolves `sqlite:liquidity-gate.db` against appConfigDir/<identifier>/, so
# the MCP server has to write to the same directory or the desktop app sees
# an empty database.
TAURI_IDENTIFIER = "com.jeff.liquiditygate"


@dataclass(frozen=True)
class ServerSettings:
    project_root: Path
    docs_dir: Path
    tracker_csv_path: Path
    master_index_path: Path
    database_path: Path
    schema_path: Path
    watch_root: Path


def default_watch_root() -> Path:
    """Default location for real financial documents.

    Defaults to ``~/Documents/Cashflow`` so private statements never live
    inside the repository. Override with ``LIQUIDITY_GATE_WATCH_ROOT``.
    """
    return Path.home() / "Documents" / "Cashflow"


def default_database_path() -> Path:
    """Default SQLite database location.

    Mirrors Tauri's appConfigDir() convention so the desktop app
    (@tauri-apps/plugin-sql, which resolves ``sqlite:liquidity-gate.db``
    against appConfigDir/<identifier>/) and the MCP server share one file.
    Override with ``LIQUIDITY_GATE_DB_PATH``.
    """
    if sys.platform == "win32":
        base = Path(os.environ.get("APPDATA", Path.home() / "AppData" / "Roaming"))
    elif sys.platform == "darwin":
        base = Path.home() / "Library" / "Application Support"
    else:
        base = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config"))
    return base / TAURI_IDENTIFIER / "liquidity-gate.db"


def load_settings() -> ServerSettings:
    default_root = Path(__file__).resolve().parents[3]
    project_root = Path(os.getenv("LIQUIDITY_GATE_ROOT", default_root)).resolve()
    docs_dir = project_root / "docs"

    watch_root_env = os.getenv("LIQUIDITY_GATE_WATCH_ROOT")
    watch_root = (
        Path(watch_root_env).expanduser().resolve()
        if watch_root_env
        else default_watch_root()
    )

    db_path_env = os.getenv("LIQUIDITY_GATE_DB_PATH")
    database_path = (
        Path(db_path_env).expanduser().resolve()
        if db_path_env
        else default_database_path()
    )
    database_path.parent.mkdir(parents=True, exist_ok=True)

    return ServerSettings(
        project_root=project_root,
        docs_dir=docs_dir,
        tracker_csv_path=docs_dir / "Spreadsheet_checklist_for_document_tracking.csv",
        master_index_path=docs_dir / "00_CASH_FLOW_MASTER_INDEX.md",
        database_path=database_path,
        schema_path=project_root / "server" / "sql" / "schema.sql",
        watch_root=watch_root,
    )
