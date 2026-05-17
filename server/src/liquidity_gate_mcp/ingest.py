from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from .config import ServerSettings
from .database import DatabaseManager
from .models import (
    IngestFileSummary,
    IngestRunResult,
    IngestRunTotals,
    ReconcileTransactionsRequest,
)
from .parsers import (
    ALLY_PARSER_VERSION,
    BEACON_PARSER_VERSION,
    CHASE_PARSER_VERSION,
    WEBSTER_PARSER_VERSION,
    ParseResult,
    parse_ally_csv,
    parse_beacon_csv,
    parse_chase_csv,
    parse_webster_csv,
)
from .tools import (
    MATCH_THRESHOLD,
    iter_candidate_files,
    load_tracker_rows,
    score_candidate,
)


@dataclass(frozen=True)
class ParserRegistration:
    name: str
    version: str
    parse: Callable[[Path], ParseResult]


# Add a new doc_id -> ParserRegistration entry to wire up another source.
# The dispatch uses the existing tracker matcher to decide which parser
# owns a file.
PARSERS: dict[str, ParserRegistration] = {
    "doc-001": ParserRegistration(
        name="chase-credit-card",
        version=CHASE_PARSER_VERSION,
        parse=parse_chase_csv,
    ),
    "doc-002": ParserRegistration(
        name="beacon-checking",
        version=BEACON_PARSER_VERSION,
        parse=parse_beacon_csv,
    ),
    "doc-003": ParserRegistration(
        name="ally-hysa",
        version=ALLY_PARSER_VERSION,
        parse=parse_ally_csv,
    ),
    "doc-004": ParserRegistration(
        name="webster-checking",
        version=WEBSTER_PARSER_VERSION,
        parse=parse_webster_csv,
    ),
}


def ingest_watch_root(
    settings: ServerSettings,
    database: DatabaseManager,
    folder_path: str | None = None,
) -> IngestRunResult:
    scan_root = Path(folder_path).resolve() if folder_path else settings.watch_root
    tracker_rows = load_tracker_rows(settings.tracker_csv_path)
    candidates = list(iter_candidate_files(scan_root))

    summaries: list[IngestFileSummary] = []
    if scan_root.exists():
        file_to_doc = _assign_files_to_docs(tracker_rows, candidates)
        for candidate in candidates:
            doc_id = file_to_doc.get(candidate)
            registration = PARSERS.get(doc_id) if doc_id else None
            if registration is None:
                summaries.append(
                    IngestFileSummary(
                        file=candidate.name,
                        doc_id=doc_id,
                        parser=None,
                        status="no_parser",
                    )
                )
                continue

            summaries.append(_ingest_file(database, candidate, doc_id, registration))

    totals = _totals(summaries)
    return IngestRunResult(
        scanned_root=scan_root.as_posix(),
        totals=totals,
        files=summaries,
    )


def _ingest_file(
    database: DatabaseManager,
    file_path: Path,
    doc_id: str,
    registration: ParserRegistration,
) -> IngestFileSummary:
    try:
        result = registration.parse(file_path)
    except Exception as exc:  # parser-level failure (I/O, malformed file, etc.)
        return IngestFileSummary(
            file=file_path.name,
            doc_id=doc_id,
            parser=registration.name,
            status="error",
            errors=[f"{type(exc).__name__}: {exc}"],
        )

    if result.errors and not result.transactions:
        return IngestFileSummary(
            file=file_path.name,
            doc_id=doc_id,
            parser=registration.name,
            status="error",
            skipped_pending=len(result.skipped),
            errors=list(result.errors),
        )

    reconcile_result = database.reconcile_transactions(
        ReconcileTransactionsRequest(
            source_name=file_path.name,
            parser_version=registration.version,
            dry_run=False,
            transactions=list(result.transactions),
        )
    )

    return IngestFileSummary(
        file=file_path.name,
        doc_id=doc_id,
        parser=registration.name,
        status="ingested",
        inserted=reconcile_result.inserted,
        updated=reconcile_result.updated,
        skipped_pending=len(result.skipped),
        errors=list(result.errors),
    )


def _assign_files_to_docs(tracker_rows: list, candidates: list[Path]) -> dict[Path, str]:
    # File-first winner-takes-all, matching the frontend's checklist.ts
    # assignment: each file goes to its single highest-scoring tracker row
    # above MATCH_THRESHOLD. This prevents a Chase CSV that also overlaps
    # doc-005 ("Other credit cards") from being claimed by both rows.
    assignments: dict[Path, str] = {}
    for candidate in candidates:
        best_score = -1.0
        best_row_id: str | None = None
        for row in tracker_rows:
            score = score_candidate(row, candidate)
            if score > best_score:
                best_score = score
                best_row_id = row.id
        if best_row_id is not None and best_score >= MATCH_THRESHOLD:
            assignments[candidate] = best_row_id
    return assignments


def _totals(summaries: list[IngestFileSummary]) -> IngestRunTotals:
    inserted = sum(s.inserted for s in summaries)
    updated = sum(s.updated for s in summaries)
    skipped_pending = sum(s.skipped_pending for s in summaries)
    files_ingested = sum(1 for s in summaries if s.status == "ingested")
    files_without_parser = sum(1 for s in summaries if s.status == "no_parser")
    files_with_errors = sum(1 for s in summaries if s.status == "error")
    return IngestRunTotals(
        files_seen=len(summaries),
        files_ingested=files_ingested,
        files_without_parser=files_without_parser,
        files_with_errors=files_with_errors,
        inserted=inserted,
        updated=updated,
        skipped_pending=skipped_pending,
    )
