from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class DocumentTrackerRow(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    category: str
    document: str
    subject_matter: str
    format: str
    priority: str
    source_where_to_get: str
    why_needed: str
    obtained: bool = False
    date_added: str = ""
    notes: str = ""


class FileEventSnapshot(BaseModel):
    model_config = ConfigDict(extra="forbid")

    event_type: str
    path: str
    is_directory: bool
    occurred_at: str


class FileMatch(BaseModel):
    model_config = ConfigDict(extra="forbid")

    relative_path: str
    score: float
    size_bytes: int
    modified_at: str


class DocumentMetadataEntry(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    category: str
    document: str
    priority: str
    status: Literal["obtained", "missing"]
    matched_files: list[FileMatch] = Field(default_factory=list)


class DocumentMetadataSummary(BaseModel):
    model_config = ConfigDict(extra="forbid")

    scanned_root: str
    files_scanned: int
    tracked_documents: int
    obtained_count: int
    missing_count: int


class ReadDocumentMetadataResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    summary: DocumentMetadataSummary
    items: list[DocumentMetadataEntry]
    recent_events: list[FileEventSnapshot] = Field(default_factory=list)


class AccountReference(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source_key: str
    institution: str
    account_name: str
    account_type: str
    owner: str = "joint"
    currency: str = "USD"


class ParsedTransaction(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source_record_key: str
    source_document_name: str
    occurred_on: date
    posted_on: date | None = None
    description_raw: str
    merchant_normalized: str | None = None
    amount: Decimal
    direction: Literal["inflow", "outflow", "transfer"]
    primary_category: str
    subcategory: str | None = None
    household_role: str = "joint"
    lifecycle: str = "recurring"
    transfer_group_key: str | None = None
    statement_period: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)
    account: AccountReference


class ReconcileTransactionsRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source_name: str
    parser_version: str = "claude-cowork"
    dry_run: bool = False
    transactions: list[ParsedTransaction]


class ReconcileTransactionsResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source_name: str
    parser_version: str
    transaction_count: int
    inserted: int
    updated: int
    would_insert: int = 0
    would_update: int = 0
    dry_run: bool
    account_keys: list[str]


class SqlQueryRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    sql: str
    params: list[str | int | float] = Field(default_factory=list)


class SqlQueryResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    row_count: int
    rows: list[dict[str, Any]]


class IngestFileSummary(BaseModel):
    model_config = ConfigDict(extra="forbid")

    file: str
    doc_id: str | None
    parser: str | None
    status: Literal["ingested", "no_parser", "error"]
    inserted: int = 0
    updated: int = 0
    skipped_pending: int = 0
    errors: list[str] = Field(default_factory=list)


class IngestRunTotals(BaseModel):
    model_config = ConfigDict(extra="forbid")

    files_seen: int
    files_ingested: int
    files_without_parser: int
    files_with_errors: int
    inserted: int
    updated: int
    skipped_pending: int


class IngestRunResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    scanned_root: str
    totals: IngestRunTotals
    files: list[IngestFileSummary]


class PairTransfersRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    date_tolerance_days: int = 3
    dry_run: bool = False
    include_diagnostics: bool = True


class UnpairedTransfer(BaseModel):
    model_config = ConfigDict(extra="forbid")

    transaction_id: str
    account_id: str
    occurred_on: str
    amount: float
    direction: str
    description: str
    likely_reason: str | None = None


class AmbiguousTransfer(BaseModel):
    model_config = ConfigDict(extra="forbid")

    transaction_id: str
    account_id: str
    occurred_on: str
    amount: float
    candidates: list[str] = Field(default_factory=list)


class SuspectedUntagged(BaseModel):
    model_config = ConfigDict(extra="forbid")

    transaction_id: str
    description: str
    likely_partner_id: str
    confidence: Literal["strong", "weak"]


class PairTransfersResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    pairs_created: int = 0
    candidates_examined: int = 0
    already_paired_skipped: int = 0
    unpaired: list[UnpairedTransfer] = Field(default_factory=list)
    ambiguous: list[AmbiguousTransfer] = Field(default_factory=list)
    suspected_untagged: list[SuspectedUntagged] = Field(default_factory=list)
    dry_run: bool = False
