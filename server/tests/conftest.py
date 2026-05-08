from __future__ import annotations

from pathlib import Path

import pytest

from liquidity_gate_mcp.database import DatabaseManager


SCHEMA_PATH = Path(__file__).resolve().parents[2] / "server" / "sql" / "schema.sql"


@pytest.fixture()
def schema_path() -> Path:
    return SCHEMA_PATH


@pytest.fixture()
def database(tmp_path: Path) -> DatabaseManager:
    db_path = tmp_path / "test.db"
    manager = DatabaseManager(db_path, SCHEMA_PATH)
    manager.initialize()
    return manager
