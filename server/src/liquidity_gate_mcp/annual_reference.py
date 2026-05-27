"""Read-only loader for ``annual_household_reference.toml``.

Captures year-end W-2 / 401(k) / HSA / withholding totals that the
transaction stream cannot derive (these come from documents — pay stubs
and W-2s — not from bank activity). The user edits the TOML by hand; no
write tool exists by design.

The file lives in the watch root at ``annual_household_reference.toml``.
A missing file is not an error: ``get_annual_reference`` returns
``file_exists=False`` with an empty entries list so callers can show a
template hint instead of crashing.
"""

from __future__ import annotations

from pathlib import Path
import tomllib

from .models import AnnualReferenceEntry, GetAnnualReferenceResult


DEFAULT_FILENAME = "annual_household_reference.toml"


_NUMERIC_FIELDS = (
    "gross_w2_jeff",
    "gross_w2_ashley",
    "federal_withholding",
    "state_withholding",
    "fica_employee",
    "contribution_401k_jeff",
    "contribution_401k_ashley",
    "employer_match_401k_jeff",
    "employer_match_401k_ashley",
    "hsa_contribution",
    "hsa_employer_contribution",
)


def _parse_entry(raw: dict) -> AnnualReferenceEntry | None:
    year_value = raw.get("year")
    try:
        year = int(year_value)
    except (TypeError, ValueError):
        return None

    fields: dict[str, float] = {}
    populated = False
    for key in _NUMERIC_FIELDS:
        value = raw.get(key, 0.0)
        try:
            fnum = float(value) if value is not None else 0.0
        except (TypeError, ValueError):
            fnum = 0.0
        fields[key] = fnum
        if fnum != 0.0:
            populated = True

    notes = str(raw.get("notes", "") or "")
    if notes:
        populated = True

    return AnnualReferenceEntry(
        year=year,
        **fields,
        notes=notes,
        populated=populated,
    )


def load_annual_reference(
    watch_root: Path, path: Path | None = None
) -> GetAnnualReferenceResult:
    target = path if path else (watch_root / DEFAULT_FILENAME)
    if not target.exists():
        return GetAnnualReferenceResult(
            source_path=target.as_posix(),
            file_exists=False,
        )

    raw = tomllib.loads(target.read_text(encoding="utf-8"))
    year_blocks = raw.get("year")
    entries: list[AnnualReferenceEntry] = []
    if isinstance(year_blocks, list):
        for block in year_blocks:
            if not isinstance(block, dict):
                continue
            parsed = _parse_entry(block)
            if parsed is not None:
                entries.append(parsed)

    entries.sort(key=lambda e: e.year)
    return GetAnnualReferenceResult(
        source_path=target.as_posix(),
        file_exists=True,
        entries=entries,
    )


def get_annual_reference(
    watch_root: Path, year: int | None = None
) -> GetAnnualReferenceResult:
    """Return all entries, or just the requested year (with an empty
    placeholder entry when no row exists)."""
    full = load_annual_reference(watch_root)
    if year is None:
        return full

    matching = [e for e in full.entries if e.year == year]
    if not matching:
        # Synthesize an unpopulated entry so callers get a stable shape
        # regardless of whether the file has the requested year.
        matching = [AnnualReferenceEntry(year=year, populated=False)]
    return GetAnnualReferenceResult(
        source_path=full.source_path,
        file_exists=full.file_exists,
        entries=matching,
    )
