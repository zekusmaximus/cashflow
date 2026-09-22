"""scripts/restore_state_from_export.py: the verify_state export round-trips."""

from __future__ import annotations

import hashlib
import importlib.util
import sys
from pathlib import Path

import pytest

from liquidity_gate_mcp.database import DatabaseManager
from liquidity_gate_mcp.models import UpsertTransactionOverrideRequest

from .verify_state_fixture import Fixture, build_fixture


SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "restore_state_from_export.py"
_spec = importlib.util.spec_from_file_location("restore_state_from_export", SCRIPT)
restore = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = restore  # dataclasses resolve annotations through sys.modules
_spec.loader.exec_module(restore)


def _dump(database: DatabaseManager, table: str) -> list[dict]:
    connection = database.connect(read_only=True)
    try:
        return [dict(r) for r in connection.execute(f"SELECT * FROM {table} ORDER BY id").fetchall()]
    finally:
        connection.close()


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


@pytest.fixture()
def exported(tmp_path: Path, schema_path: Path) -> Fixture:
    fx = build_fixture(tmp_path, schema_path)
    # Variety: a full-field override with a note, and a rule whose nullable
    # columns hold '' as well as NULL (the two must not collapse).
    fx.database.upsert_transaction_override(
        UpsertTransactionOverrideRequest(
            transaction_id="b11", primary_category="variable_lifestyle", subcategory="groceries",
            merchant_normalized="Whole Foods Market", household_role="jeff", lifecycle="recurring",
            note="weekly shop, says Jeff, \"verified\"",
        )
    )
    fx.execute(
        "INSERT INTO classification_rules (id, pattern, account_filter, direction_filter, "
        "primary_category, subcategory, confidence, priority, notes) "
        "VALUES ('rule-ui-7', '(?i)corner, cafe', '', 'outflow', 'variable_lifestyle', NULL, 'low', 7, '')"
    )
    result = fx.run(write=True)
    assert result.written is not None and result.written.errors == []
    return fx


def test_export_to_fresh_db_and_restore_reproduces_both_tables(
    exported: Fixture, tmp_path: Path, schema_path: Path
) -> None:
    fresh = DatabaseManager(tmp_path / "fresh.db", schema_path)
    fresh.initialize()
    exports = exported.watch_root / "_state_exports"

    lines = restore.run(fresh.database_path, exports, apply=True)

    for table in ("classification_rules", "transaction_overrides"):
        assert _dump(fresh, table) == _dump(exported.database, table)
    assert any(line.startswith("Backed up the database to ") for line in lines)
    assert list(tmp_path.glob("fresh.db.pre-restore-state.*.bak"))
    # Idempotent: a second plan finds nothing to do.
    again = restore.run(fresh.database_path, exports, apply=False)
    summaries = [line for line in again if line.startswith(("classification_rules:", "transaction_overrides:"))]
    assert len(summaries) == 2
    assert all("- 0 insert, 0 update" in line for line in summaries)


def test_restore_without_apply_writes_nothing(
    exported: Fixture, tmp_path: Path, schema_path: Path
) -> None:
    fresh = DatabaseManager(tmp_path / "fresh.db", schema_path)
    fresh.initialize()
    before = _sha(fresh.database_path)

    lines = restore.run(fresh.database_path, exported.watch_root / "_state_exports", apply=False)

    assert _sha(fresh.database_path) == before
    assert lines[-1].startswith("Dry run: nothing written.")
    assert not list(tmp_path.glob("fresh.db.pre-restore-state.*.bak"))
    rules_line = next(line for line in lines if line.startswith("classification_rules"))
    assert "1 insert" in rules_line  # rule-ui-7


def test_restore_puts_a_lost_override_back_on_its_transaction(exported: Fixture) -> None:
    exported.execute("DELETE FROM transaction_overrides")
    exported.execute("UPDATE transactions SET lifecycle = 'recurring' WHERE id = 'c03'")

    lines = restore.run(exported.database.database_path, exported.watch_root / "_state_exports", apply=True)

    [row] = exported.query("SELECT lifecycle FROM transactions WHERE id = 'c03'")
    assert row["lifecycle"] == "one_time"
    assert "reapply_all_overrides touched" in lines[-1]


def test_restore_rejects_an_export_whose_header_does_not_match(exported: Fixture, capsys) -> None:
    path = exported.watch_root / "_state_exports" / "classification_rules.csv"
    path.write_text("id,pattern\nx,y\n", encoding="utf-8")

    code = restore.main(
        ["--db", str(exported.database.database_path), "--exports", str(path.parent)]
    )

    assert code == 1
    assert "does not match the table columns" in capsys.readouterr().err
