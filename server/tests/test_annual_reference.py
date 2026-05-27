"""Tests for the annual household reference TOML loader."""

from __future__ import annotations

from pathlib import Path
from textwrap import dedent

from liquidity_gate_mcp.annual_reference import (
    get_annual_reference,
    load_annual_reference,
)


def _write(path: Path, content: str) -> None:
    path.write_text(dedent(content), encoding="utf-8")


def test_load_missing_file_returns_empty(tmp_path: Path) -> None:
    result = load_annual_reference(tmp_path)
    assert result.file_exists is False
    assert result.entries == []


def test_get_year_synthesizes_empty_entry_when_missing(tmp_path: Path) -> None:
    """Caller asks for 2026 from a missing file → still gets a zeroed
    placeholder, marked unpopulated, so dashboards have a stable shape."""
    result = get_annual_reference(tmp_path, year=2026)
    assert result.file_exists is False
    assert len(result.entries) == 1
    entry = result.entries[0]
    assert entry.year == 2026
    assert entry.populated is False
    assert entry.gross_w2_jeff == 0.0


def test_load_parses_populated_year(tmp_path: Path) -> None:
    _write(
        tmp_path / "annual_household_reference.toml",
        """\
        [[year]]
        year = 2026
        gross_w2_jeff = 250000.0
        gross_w2_ashley = 150000.0
        federal_withholding = 80000.0
        contribution_401k_jeff = 23000.0
        notes = "Both maxed out 401(k)"
        """,
    )
    result = load_annual_reference(tmp_path)
    assert result.file_exists is True
    assert len(result.entries) == 1
    e = result.entries[0]
    assert e.year == 2026
    assert e.gross_w2_jeff == 250000.0
    assert e.gross_w2_ashley == 150000.0
    assert e.contribution_401k_jeff == 23000.0
    assert e.notes == "Both maxed out 401(k)"
    assert e.populated is True


def test_load_zero_values_only_marked_unpopulated(tmp_path: Path) -> None:
    _write(
        tmp_path / "annual_household_reference.toml",
        """\
        [[year]]
        year = 2026
        gross_w2_jeff = 0.0
        """,
    )
    result = load_annual_reference(tmp_path)
    assert len(result.entries) == 1
    assert result.entries[0].populated is False


def test_load_multiple_years_sorted(tmp_path: Path) -> None:
    _write(
        tmp_path / "annual_household_reference.toml",
        """\
        [[year]]
        year = 2027

        [[year]]
        year = 2025
        gross_w2_jeff = 100.0

        [[year]]
        year = 2026
        """,
    )
    result = load_annual_reference(tmp_path)
    assert [e.year for e in result.entries] == [2025, 2026, 2027]


def test_get_specific_year_filters(tmp_path: Path) -> None:
    _write(
        tmp_path / "annual_household_reference.toml",
        """\
        [[year]]
        year = 2025
        gross_w2_jeff = 100.0

        [[year]]
        year = 2026
        gross_w2_jeff = 200.0
        """,
    )
    result = get_annual_reference(tmp_path, year=2026)
    assert len(result.entries) == 1
    assert result.entries[0].year == 2026
    assert result.entries[0].gross_w2_jeff == 200.0
