from __future__ import annotations

import re
from dataclasses import dataclass, field

from ..models import ParsedTransaction


STATEMENT_PERIOD_RE = re.compile(r"(20\d{2})[-_](\d{1,2})")


@dataclass(frozen=True)
class SkippedRow:
    row_index: int
    reason: str
    raw: dict[str, str]


@dataclass
class ParseResult:
    transactions: list[ParsedTransaction] = field(default_factory=list)
    skipped: list[SkippedRow] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)


def derive_statement_period(stem: str) -> str | None:
    match = STATEMENT_PERIOD_RE.search(stem)
    if not match:
        return None
    year = match.group(1)
    month = int(match.group(2))
    if not 1 <= month <= 12:
        return None
    return f"{year}-{month:02d}"
