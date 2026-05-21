from __future__ import annotations

import atexit

from mcp.server.fastmcp import FastMCP

from .balances import load_balances
from .computed_balance import refresh_hysa_gate as refresh_hysa_gate_impl
from .computed_balance import seed_balance_anchors
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
# Materialise balances.toml opening/closing balances as reconciliation_periods
# anchor rows so v_computed_balance is queryable immediately. Idempotent.
seed_balance_anchors(database, load_balances(settings.watch_root))
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


@mcp.resource("docs://project-status")
def project_status() -> str:
    path = settings.project_status_path
    if path is None:
        return "(project_status_path not configured)"
    return path.read_text(encoding="utf-8")


@mcp.resource("watch://recent-events")
def recent_watch_events() -> str:
    return "\n".join(event.model_dump_json() for event in watcher.recent_events())


@mcp.prompt(title="Financial Detective")
def financial_detective(
    objective: str = "Analyze monthly and annual household spending"
) -> str:
    return (
        "You are the Financial Detective for Liquidity Gate.\n\n"
        "Start this session by reading docs://master-index, then docs://project-status to load the current "
        "roadmap position and classification coverage, then docs://tracker. "
        "Identify core transaction sources first; treat broader planning files as optional unless the task explicitly depends on them.\n\n"
        "Operating rules:\n"
        "- Start from transaction exports, bank activity, and credit-card activity that directly reconstruct household spending.\n"
        "- Use broader planning documents (payroll, tax, insurance, debt, rental) only when the user explicitly asks or the spending question cannot be answered without them.\n"
        "- Treat manual explanations as classification help, not a replacement for evidence.\n"
        "- Do not double-count card payments, savings transfers, or inter-account transfers as spending.\n"
        "- Prefer monthly and annual spending summaries, merchant patterns, recurring charges, one-time items, and unusual month-over-month changes.\n"
        "- Ask for additional source accounts only when transaction coverage is incomplete or materially distorted.\n"
        "- Keep every workflow local. Do not propose cloud sync or external data upload.\n\n"
        "Default workflow:\n"
        "1. Call read_document_metadata to see which core transaction sources are present. Do not treat every missing tracker row as a blocker.\n"
        "2. If new parser-backed files are available, call ingest_documents.\n"
        "3. Call pair_transfers so card payments and inter-account moves do not inflate spending.\n"
        "4. To improve classification, call list_classification_rules, upsert_classification_rule "
        "(pattern → primary_category, subcategory, merchant_normalized, household_role, lifecycle), "
        "then apply_classifier. For one-off corrections that survive re-imports, use upsert_transaction_override.\n"
        "   If you have already parsed a batch of transactions outside of ingest_documents (e.g. from a "
        "custom ad-hoc parser), use reconcile_transactions to write them directly; it accepts the same "
        "ParsedTransaction structure and applies stored overrides automatically.\n"
        "5. Use query_cashflow_data for read-only verification, monthly summaries, merchant and category review, "
        "recurring-charge review, and anomaly checks.\n"
        "6. Ask for additional source accounts only when coverage is incomplete or a material gap remains.\n"
        "7. Avoid requesting payroll, tax, insurance, debt, rental, or planning artifacts unless the user explicitly asks.\n\n"
        f"Current objective: {objective}."
    )


@mcp.tool()
def read_document_metadata(folder_path: str | None = None) -> dict:
    return read_document_metadata_impl(settings, database, watcher, folder_path).model_dump()


@mcp.tool()
def reconcile_transactions(request: dict) -> dict:
    """Persist a batch of pre-parsed transactions into the local database.

    Use this tool when you have already built structured ``ParsedTransaction``
    objects (e.g. from a custom or ad-hoc parser) and want to write them
    directly — bypassing the file-watcher and the built-in parsers used by
    ``ingest_documents``.

    Required keys inside *request*:

    - ``source_name`` (str): Human-readable label for the data source
      (e.g. ``"Chase Checking 2024-01"``).
    - ``transactions`` (list): Each item must include ``account``,
      ``occurred_on``, ``description_raw``, ``amount``, ``direction``,
      ``primary_category``, ``source_record_key``, and
      ``source_document_name``.

    Optional keys:

    - ``parser_version`` (str, default ``"claude-cowork"``): Version tag
      stored alongside the import batch for audit purposes.
    - ``dry_run`` (bool, default ``false``): When ``true``, reports what
      would be inserted or updated without writing anything.

    Each transaction is matched against existing ``transaction_overrides``
    before being written, so manual corrections applied earlier survive
    re-imports automatically.

    Returns counts of inserted and updated rows (or ``would_insert`` /
    ``would_update`` in dry-run mode).
    """
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


@mcp.tool()
def refresh_hysa_gate() -> dict:
    """Recompute the Ally HYSA liquidity gate from the anchor-based balance.

    Re-seeds the balances.toml anchors into ``reconciliation_periods``, reads
    ``v_computed_balance`` for the Ally HYSA account, and writes the result
    onto ``liquidity_gates.current_amount`` for ``gate_key = 'ally_hysa'``.

    Run this on demand — typically after adding a statement closing under
    ``[statement_closings.ally]`` in balances.toml. Takes no arguments.

    Returns ``anchor_date``, ``anchor_balance``, ``net_since_anchor``,
    ``computed_balance``, and ``updated`` (False when no anchor exists).
    """
    seed_balance_anchors(database, load_balances(settings.watch_root))
    return refresh_hysa_gate_impl(database).as_dict()


def main() -> None:
    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
