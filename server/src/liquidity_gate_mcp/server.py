from __future__ import annotations

import atexit

from mcp.server.fastmcp import FastMCP

from .balances import load_balances
from .config import load_settings
from .database import DatabaseManager
from .ingest import ingest_watch_root as ingest_watch_root_impl
from .models import (
    ApplyClassifierRequest,
    PairTransfersRequest,
    ReconcilePeriodsRequest,
    ReconcileTransactionsRequest,
    SqlQueryRequest,
    UpsertClassificationRuleRequest,
    UpsertTransactionOverrideRequest,
)
from .reconciliation import reconcile_periods as reconcile_periods_impl
from .tools import (
    apply_classifier as apply_classifier_impl,
    list_classification_rules as list_classification_rules_impl,
    query_cashflow_data as query_cashflow_data_impl,
    read_document_metadata as read_document_metadata_impl,
    reconcile_transactions as reconcile_transactions_impl,
    upsert_classification_rule as upsert_classification_rule_impl,
    upsert_transaction_override as upsert_transaction_override_impl,
)
from .transfers import pair_transfers as pair_transfers_impl
from .watcher import CashFlowWatcher

settings = load_settings()
database = DatabaseManager(settings.database_path, settings.schema_path)
database.initialize()
watcher = CashFlowWatcher(settings.watch_root)
watcher.start()
atexit.register(watcher.stop)

mcp = FastMCP("Liquidity Gate MCP", json_response=True)


@mcp.resource("docs://master-index")
def master_index() -> str:
    return settings.master_index_path.read_text(encoding="utf-8")


@mcp.resource("docs://tracker")
def tracker_csv() -> str:
    return settings.tracker_csv_path.read_text(encoding="utf-8")


@mcp.resource("watch://recent-events")
def recent_watch_events() -> str:
    return "\n".join(event.model_dump_json() for event in watcher.recent_events())


@mcp.prompt(title="Financial Detective")
def financial_detective(
    objective: str = "Analyze monthly and annual household spending"
) -> str:
    return (
        "You are the Financial Detective for Liquidity Gate. Read docs://master-index first, then docs://tracker. "
        "Start with core transaction sources and current spending data, not full document completeness. "
        "Treat payroll, tax, insurance, debt, rental, and other planning files as optional unless the task explicitly depends on them. "
        "Never double-count transfers or card payments as spending, "
        f"and keep the current objective in focus: {objective}."
    )


@mcp.tool()
def read_document_metadata(folder_path: str | None = None) -> dict:
    return read_document_metadata_impl(settings, database, watcher, folder_path).model_dump()


@mcp.tool()
def reconcile_transactions(request: dict) -> dict:
    parsed_request = ReconcileTransactionsRequest.model_validate(request)
    return reconcile_transactions_impl(database, parsed_request).model_dump()


@mcp.tool()
def query_cashflow_data(request: dict) -> dict:
    parsed_request = SqlQueryRequest.model_validate(request)
    return query_cashflow_data_impl(database, parsed_request).model_dump()


@mcp.tool()
def upsert_transaction_override(request: dict) -> dict:
    parsed_request = UpsertTransactionOverrideRequest.model_validate(request)
    return upsert_transaction_override_impl(database, parsed_request).model_dump()


@mcp.tool()
def ingest_documents(folder_path: str | None = None) -> dict:
    return ingest_watch_root_impl(settings, database, folder_path).model_dump()


@mcp.tool()
def pair_transfers(request: dict | None = None) -> dict:
    parsed_request = PairTransfersRequest.model_validate(request or {})
    return pair_transfers_impl(database, parsed_request).model_dump()


@mcp.tool()
def reconcile_periods(request: dict) -> dict:
    parsed_request = ReconcilePeriodsRequest.model_validate(request)
    balances = load_balances(settings.watch_root)
    return reconcile_periods_impl(database, balances, parsed_request).model_dump()


@mcp.tool()
def apply_classifier(request: dict | None = None) -> dict:
    """Run the rule-based classifier over unclassified transactions.

    Pass ``{"reclassify_all": true}`` to re-run on all rows (manual overrides
    are still protected). Pass ``{"account_filter": "<account_id>"}`` to
    restrict to one account. ``{"dry_run": true}`` shows what would change
    without writing.
    """
    parsed_request = ApplyClassifierRequest.model_validate(request or {})
    return apply_classifier_impl(database, parsed_request).model_dump()


@mcp.tool()
def upsert_classification_rule(request: dict) -> dict:
    """Create or update a classification rule.

    Required: ``pattern`` (Python regex, case-insensitive) and at least one
    output field (``primary_category``, ``subcategory``, ``merchant_normalized``,
    ``household_role``, or ``lifecycle``).

    Optional: ``id`` (omit to auto-generate), ``account_filter``,
    ``direction_filter`` (``inflow``/``outflow``/``transfer``), ``confidence``
    (``high``/``medium``/``low``), ``priority`` (lower = checked first),
    ``notes``.
    """
    parsed_request = UpsertClassificationRuleRequest.model_validate(request)
    return upsert_classification_rule_impl(database, parsed_request).model_dump()


@mcp.tool()
def list_classification_rules() -> dict:
    """Return all classification rules ordered by priority then created_at."""
    return list_classification_rules_impl(database).model_dump()


def main() -> None:
    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
