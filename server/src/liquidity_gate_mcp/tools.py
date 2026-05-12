from __future__ import annotations

import csv
from datetime import datetime, timezone
from difflib import SequenceMatcher
from pathlib import Path
import re

from .config import ServerSettings
from .database import DatabaseManager
from .models import (
    DocumentMetadataEntry,
    DocumentMetadataSummary,
    DocumentTrackerRow,
    FileMatch,
    ReadDocumentMetadataResult,
    ReconcileTransactionsRequest,
    ReconcileTransactionsResult,
    SqlQueryRequest,
    SqlQueryResult,
)
from .watcher import CashFlowWatcher


IGNORED_DIRECTORIES = {
    ".git",
    "node_modules",
    "dist",
    "docs",
    "src",
    "src-tauri",
    "server",
    ".claudecowork",
}

MATCH_THRESHOLD = 0.35

STOP_WORDS = {
    "all",
    "and",
    "annual",
    "export",
    "history",
    "if",
    "in",
    "of",
    "or",
    "pdf",
    "screenshot",
    "the",
    "ytd",
    "2026",
    "2027",
}

_CAMEL_BOUNDARY = re.compile(r"(?<=[a-z])(?=[A-Z])|(?<=[A-Z])(?=[A-Z][a-z])")


def read_document_metadata(
    settings: ServerSettings,
    database: DatabaseManager,
    watcher: CashFlowWatcher,
    folder_path: str | None = None,
) -> ReadDocumentMetadataResult:
    scan_root = Path(folder_path).resolve() if folder_path else settings.watch_root
    tracker_rows = load_tracker_rows(settings.tracker_csv_path)
    candidates = list(iter_candidate_files(scan_root))

    items: list[DocumentMetadataEntry] = []
    for tracker_row in tracker_rows:
        matches = find_matches(tracker_row, scan_root, candidates)
        items.append(
            DocumentMetadataEntry(
                id=tracker_row.id,
                category=tracker_row.category,
                document=tracker_row.document,
                priority=tracker_row.priority,
                status="obtained" if matches else "missing",
                matched_files=matches,
            )
        )

    database.upsert_document_metadata(items)

    obtained_count = sum(1 for item in items if item.status == "obtained")
    result = ReadDocumentMetadataResult(
        summary=DocumentMetadataSummary(
            scanned_root=scan_root.as_posix(),
            files_scanned=len(candidates),
            tracked_documents=len(items),
            obtained_count=obtained_count,
            missing_count=len(items) - obtained_count,
        ),
        items=items,
        recent_events=watcher.recent_events(),
    )

    return result


def reconcile_transactions(
    database: DatabaseManager,
    request: ReconcileTransactionsRequest,
) -> ReconcileTransactionsResult:
    return database.reconcile_transactions(request)


def query_cashflow_data(database: DatabaseManager, request: SqlQueryRequest) -> SqlQueryResult:
    return database.query(request.sql, request.params)


def load_tracker_rows(csv_path: Path) -> list[DocumentTrackerRow]:
    with csv_path.open("r", encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        return [
            DocumentTrackerRow(
                id=f"doc-{index:03d}",
                category=row.get("Category", ""),
                document=row.get("Document", ""),
                subject_matter=row.get("Subject Matter", ""),
                format=row.get("Format", ""),
                priority=row.get("Priority", ""),
                source_where_to_get=row.get("Source / Where to Get", ""),
                why_needed=row.get("Why Needed", ""),
                obtained=normalize_flag(row.get("Obtained ✓", "")),
                date_added=row.get("Date Added", ""),
                notes=row.get("Notes", ""),
            )
            for index, row in enumerate(reader, start=1)
        ]


def normalize_flag(value: str) -> bool:
    normalized = value.strip().lower()
    return normalized in {"true", "yes", "y"} or "✓" in normalized or "☑" in normalized


def iter_candidate_files(scan_root: Path) -> list[Path]:
    if not scan_root.exists():
        return []

    files: list[Path] = []
    for path in scan_root.rglob("*"):
        if not path.is_file():
            continue
        if any(part in IGNORED_DIRECTORIES for part in path.parts):
            continue
        files.append(path)
    return files


def find_matches(row: DocumentTrackerRow, scan_root: Path, candidates: list[Path]) -> list[FileMatch]:
    matches: list[tuple[float, Path]] = []

    for candidate in candidates:
        score = score_candidate(row, candidate)
        if score >= MATCH_THRESHOLD:
            matches.append((score, candidate))

    matches.sort(key=lambda item: item[0], reverse=True)

    results: list[FileMatch] = []
    for score, candidate in matches[:3]:
        stat = candidate.stat()
        results.append(
            FileMatch(
                relative_path=candidate.relative_to(scan_root).as_posix(),
                score=round(score, 3),
                size_bytes=stat.st_size,
                modified_at=datetime.fromtimestamp(stat.st_mtime, tz=timezone.utc).isoformat(),
            )
        )
    return results


def score_candidate(row: DocumentTrackerRow, candidate: Path) -> float:
    haystack = " ".join(
        [
            row.document,
            row.category,
            row.subject_matter,
            row.why_needed,
        ]
    )
    candidate_text = " ".join([candidate.stem, candidate.parent.name, candidate.suffix])

    doc_tokens = tokenize(haystack)
    candidate_tokens = tokenize(candidate_text)
    overlap = len(doc_tokens & candidate_tokens) / max(len(doc_tokens), 1)
    ratio = SequenceMatcher(None, normalize_text(row.document), normalize_text(candidate.stem)).ratio()
    extension_bonus = 0.15 if candidate.suffix.lower() in expected_extensions(row.format) else 0.0

    return overlap * 0.65 + ratio * 0.35 + extension_bonus


def expected_extensions(format_text: str) -> set[str]:
    normalized = format_text.lower()
    extensions: set[str] = set()
    if "csv" in normalized:
        extensions.add(".csv")
    if "pdf" in normalized:
        extensions.add(".pdf")
    if "screenshot" in normalized:
        extensions.update({".png", ".jpg", ".jpeg", ".webp"})
    if "worksheet" in normalized:
        extensions.update({".csv", ".xlsx"})
    if "invoice" in normalized:
        extensions.update({".pdf", ".csv"})
    return extensions


def tokenize(value: str) -> set[str]:
    # Split compound names ("CardActivity" -> "Card Activity", "HSAElection" -> "HSA Election")
    # before lowercasing, then split letter/digit boundaries so "401k" -> ["401", "k"]
    # to align with how "401(k)" tokenizes from the tracker.
    spaced = _CAMEL_BOUNDARY.sub(" ", value)
    tokens: set[str] = set()
    for chunk in re.findall(r"[a-z0-9]+", spaced.lower()):
        for part in re.findall(r"[a-z]+|[0-9]+", chunk):
            if len(part) > 2 and part not in STOP_WORDS:
                tokens.add(part)
    return tokens


def normalize_text(value: str) -> str:
    return " ".join(sorted(tokenize(value)))
