from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


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


class UpsertTransactionOverrideRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    transaction_id: str
    merchant_normalized: str | None = None
    primary_category: str | None = None
    subcategory: str | None = None
    household_role: str | None = None
    lifecycle: str | None = None
    note: str = ""

    @field_validator(
        "merchant_normalized",
        "primary_category",
        "subcategory",
        "household_role",
        "lifecycle",
        mode="before",
    )
    @classmethod
    def strip_optional_text(cls, value: Any) -> str | None:
        if value is None:
            return None
        text = str(value).strip()
        return text or None

    @field_validator("note", mode="before")
    @classmethod
    def strip_note(cls, value: Any) -> str:
        if value is None:
            return ""
        return str(value).strip()

    @model_validator(mode="after")
    def ensure_override_value_present(self) -> "UpsertTransactionOverrideRequest":
        if not any(
            value is not None
            for value in (
                self.merchant_normalized,
                self.primary_category,
                self.subcategory,
                self.household_role,
                self.lifecycle,
            )
        ) and not self.note:
            raise ValueError("At least one override field or note is required.")
        return self


class UpsertTransactionOverrideResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    transaction_id: str
    match_key: str
    updated_transactions: int
    merchant_normalized: str | None = None
    primary_category: str | None = None
    subcategory: str | None = None
    household_role: str | None = None
    lifecycle: str | None = None
    note: str = ""


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
    classifier: ApplyClassifierResult | None = None


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


class ReconcilePeriodsRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    period_start: date
    period_end: date
    account_ids: list[str] = Field(default_factory=list)


class ReconciliationPeriodSummary(BaseModel):
    model_config = ConfigDict(extra="forbid")

    account_id: str
    account_label: str
    account_type: str
    period_start: str
    period_end: str
    statement_opening_balance: float | None
    statement_closing_balance: float | None
    closing_balance_source: str | None
    computed_inflows: float
    computed_outflows: float
    computed_transfers_in: float
    computed_transfers_out: float
    computed_closing_balance: float | None
    variance_amount: float | None
    variance_explanation: str = ""


class ReconcilePeriodsResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    balances_file_loaded: bool
    balances_file_path: str | None
    period_start: str
    period_end: str
    accounts_processed: int
    periods_written: int
    summaries: list[ReconciliationPeriodSummary] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# Classification rules
# ---------------------------------------------------------------------------


class ClassificationRule(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    pattern: str
    account_filter: str | None
    direction_filter: str | None
    primary_category: str | None
    subcategory: str | None
    merchant_normalized: str | None
    household_role: str | None
    lifecycle: str | None
    confidence: str
    priority: int
    notes: str
    created_at: str
    updated_at: str


class UpsertClassificationRuleRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str | None = None
    pattern: str
    account_filter: str | None = None
    direction_filter: str | None = None
    primary_category: str | None = None
    subcategory: str | None = None
    merchant_normalized: str | None = None
    household_role: str | None = None
    lifecycle: str | None = None
    confidence: str = "medium"
    priority: int = 100
    notes: str = ""

    @model_validator(mode="after")
    def require_at_least_one_output(self) -> "UpsertClassificationRuleRequest":
        if not any([
            self.primary_category,
            self.subcategory,
            self.merchant_normalized,
            self.household_role,
            self.lifecycle,
        ]):
            raise ValueError(
                "At least one classification output field is required "
                "(primary_category, subcategory, merchant_normalized, household_role, or lifecycle)."
            )
        return self


class UpsertClassificationRuleResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    created: bool
    rule: ClassificationRule


class ApplyClassifierRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    account_filter: str | None = None
    dry_run: bool = False
    reclassify_all: bool = False


class ApplyClassifierResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    transactions_scanned: int
    transactions_matched: int
    transactions_unclassified: int
    dry_run: bool
    rules_applied: int


class ListClassificationRulesResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    rules: list[ClassificationRule]
    total: int
