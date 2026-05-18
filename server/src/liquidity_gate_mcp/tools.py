from __future__ import annotations

import csv
from datetime import datetime, timezone
from difflib import SequenceMatcher
from pathlib import Path
import re

from .config import ServerSettings
from .database import DatabaseManager
from .classifier import (
    apply_classifier as apply_classifier_impl,
    list_classification_rules as list_classification_rules_impl,
    upsert_classification_rule as upsert_classification_rule_impl,
)
from .models import (
    ApplyClassifierRequest,
    ApplyClassifierResult,
    DocumentMetadataEntry,
    DocumentMetadataSummary,
    DocumentTrackerRow,
    FileMatch,
    ListClassificationRulesResult,
    ReadDocumentMetadataResult,
    ReconcileTransactionsRequest,
    ReconcileTransactionsResult,
    SqlQueryRequest,
    SqlQueryResult,
    UpsertClassificationRuleRequest,
    UpsertClassificationRuleResult,
    UpsertTransactionOverrideRequest,
    UpsertTransactionOverrideResult,
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
    matched_by_row = assign_files_to_tracker_rows(tracker_rows, candidates)

    items: list[DocumentMetadataEntry] = []
    for tracker_row in tracker_rows:
        matches = build_file_matches(scan_root, matched_by_row.get(tracker_row.id, []))
        items.append(
            DocumentMetadataEntry(
                id=tracker_row.id,
                category=tracker_row.category,
                document=tracker_row.document,
                priority=tracker_row.priority,
                status="obtained" if tracker_row.obtained or matches else "missing",
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


def upsert_transaction_override(
    database: DatabaseManager, request: UpsertTransactionOverrideRequest
) -> UpsertTransactionOverrideResult:
    return database.upsert_transaction_override(request)


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


def assign_files_to_tracker_rows(
    tracker_rows: list[DocumentTrackerRow], candidates: list[Path]
) -> dict[str, list[tuple[float, Path]]]:
    # File-first winner-takes-all, matching the desktop checklist logic.
    # Without this, one Chase CSV can mark both the primary Chase row and
    # doc-005 (other credit cards) as obtained in MCP metadata.
    assignments = {row.id: [] for row in tracker_rows}

    for candidate in candidates:
        best_score = -1.0
        best_row_id: str | None = None
        for row in tracker_rows:
            score = score_candidate(row, candidate)
            if score > best_score:
                best_score = score
                best_row_id = row.id
        if best_row_id is not None and best_score >= MATCH_THRESHOLD:
            assignments[best_row_id].append((best_score, candidate))

    for matches in assignments.values():
        matches.sort(key=lambda item: item[0], reverse=True)

    return assignments


def build_file_matches(
    scan_root: Path, matches: list[tuple[float, Path]]
) -> list[FileMatch]:
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


def find_matches(row: DocumentTrackerRow, scan_root: Path, candidates: list[Path]) -> list[FileMatch]:
    matches: list[tuple[float, Path]] = []

    for candidate in candidates:
        score = score_candidate(row, candidate)
        if score >= MATCH_THRESHOLD:
            matches.append((score, candidate))

    matches.sort(key=lambda item: item[0], reverse=True)
    return build_file_matches(scan_root, matches)


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


# ---------------------------------------------------------------------------
# Classifier tool wrappers
# ---------------------------------------------------------------------------


def apply_classifier(
    database: DatabaseManager, request: ApplyClassifierRequest
) -> ApplyClassifierResult:
    return apply_classifier_impl(database, request)


def upsert_classification_rule(
    database: DatabaseManager, request: UpsertClassificationRuleRequest
) -> UpsertClassificationRuleResult:
    return upsert_classification_rule_impl(database, request)


def list_classification_rules(database: DatabaseManager) -> ListClassificationRulesResult:
    return list_classification_rules_impl(database)


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
