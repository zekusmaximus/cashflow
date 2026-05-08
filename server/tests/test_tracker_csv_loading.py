from __future__ import annotations

from pathlib import Path
from textwrap import dedent

from liquidity_gate_mcp.tools import load_tracker_rows, normalize_flag


def write_csv(tmp_path: Path, content: str) -> Path:
    path = tmp_path / "tracker.csv"
    path.write_text(content, encoding="utf-8")
    return path


def test_load_tracker_rows_assigns_padded_ids(tmp_path: Path) -> None:
    csv_text = dedent(
        """\
        Category,Document,Subject Matter,Format,Priority,Source / Where to Get,Why Needed,Obtained ✓,Date Added,Notes
        Banking,Statement,Joint checking,PDF,High,Bank,Reconcile,yes,2026-01-01,
        Tax,W-2,Jeff,PDF,High,Employer,Income proof,no,2026-02-01,
        """
    )
    rows = load_tracker_rows(write_csv(tmp_path, csv_text))
    assert [row.id for row in rows] == ["doc-001", "doc-002"]
    assert rows[0].obtained is True
    assert rows[1].obtained is False


def test_load_tracker_rows_round_trip_priority_format(tmp_path: Path) -> None:
    csv_text = dedent(
        """\
        Category,Document,Subject Matter,Format,Priority,Source / Where to Get,Why Needed,Obtained ✓,Date Added,Notes
        Compensation,RSU vest,Jeff,Worksheet,High,Carta,RSU split,☑,2026-03-01,Some notes
        """
    )
    rows = load_tracker_rows(write_csv(tmp_path, csv_text))
    assert len(rows) == 1
    row = rows[0]
    assert row.category == "Compensation"
    assert row.format == "Worksheet"
    assert row.priority == "High"
    assert row.obtained is True
    assert row.notes == "Some notes"


def test_normalize_flag_handles_truthy_strings() -> None:
    assert normalize_flag("yes") is True
    assert normalize_flag("Y") is True
    assert normalize_flag("true") is True
    assert normalize_flag("✓") is True
    assert normalize_flag("☑ done") is True
    assert normalize_flag("no") is False
    assert normalize_flag("") is False
