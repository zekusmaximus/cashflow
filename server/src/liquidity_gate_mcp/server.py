from __future__ import annotations

import atexit
import logging
import sys

from mcp.server.fastmcp import FastMCP

from .annual_reference import load_annual_reference
from .balances import load_balances
from .checkpoints import upsert_balance_checkpoint as upsert_balance_checkpoint_impl
from .computed_balance import refresh_hysa_gate as refresh_hysa_gate_impl
from .computed_balance import seed_balance_anchors
from .config import load_settings
from .database import DatabaseManager
from .ingest import ingest_watch_root as ingest_watch_root_impl
from .monthly_summary import (
    compute_monthly_summary as compute_monthly_summary_impl,
    generate_monthly_summary as generate_monthly_summary_impl,
    placeholder_401k_warning,
)
from .models import (
    ApplyClassifierRequest,
    IngestCheckDepositLedgerRequest,
    IngestCheckRegisterRequest,
    LifecycleAuditRequest,
    PairTransfersRequest,
    ReconcilePeriodsRequest,
    ReconcileTransactionsRequest,
    SqlQueryRequest,
    UpsertBalanceCheckpointRequest,
    UpsertClassificationRuleRequest,
    UpsertTransactionOverrideRequest,
)
from .reconciliation import reconcile_periods as reconcile_periods_impl
from .tools import (
    apply_classifier as apply_classifier_impl,
    get_annual_reference as get_annual_reference_impl,
    ingest_check_deposit_ledger as ingest_check_deposit_ledger_impl,
    ingest_check_register as ingest_check_register_impl,
    list_classification_rules as list_classification_rules_impl,
    list_lifecycle_audit_candidates as list_lifecycle_audit_candidates_impl,
    query_cashflow_data as query_cashflow_data_impl,
    read_document_metadata as read_document_metadata_impl,
    reconcile_transactions as reconcile_transactions_impl,
    upsert_classification_rule as upsert_classification_rule_impl,
    upsert_transaction_override as upsert_transaction_override_impl,
)
from .transfers import pair_transfers as pair_transfers_impl
from .watcher import CashFlowWatcher

logger = logging.getLogger("liquidity_gate_mcp")

settings = load_settings()
database = DatabaseManager(settings.database_path, settings.schema_path)
database.initialize()
# Materialise balances.toml opening/closing balances as reconciliation_periods
# anchor rows so v_computed_balance is queryable immediately. Idempotent.
balances_config = load_balances(settings.watch_root)
seed_balance_anchors(database, balances_config)
watcher = CashFlowWatcher(settings.watch_root)
watcher.start()
atexit.register(watcher.stop)

# Surface — but do not block on — missing 401(k) config. The monthly summary's
# theoretical savings-rate view is untrustworthy until these are populated from
# the latest Novartis paystub.
_placeholder_warning = placeholder_401k_warning(balances_config.wealth_bridge)
if _placeholder_warning:
    logger.warning(_placeholder_warning)
    print(_placeholder_warning, file=sys.stderr)

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
def upsert_balance_checkpoint(request: dict) -> dict:
    """Assert a true, dated balance for a cash account and re-anchor from it.

    Writes a durable, human-auditable balance assertion (e.g. "Ally HYSA =
    $35,000 as of 2026-06-02") under ``[statement_closings.<account>]`` in
    ``balances.toml`` — NOT into the transactions table, so it survives
    re-ingest and reclassify. Existing entries and comments in the file are
    preserved (the TOML is round-tripped).

    Required keys inside *request*:

    - ``account`` (str): exact ``accounts.id`` OR a case-insensitive institution
      alias (``ally``, ``chase``, ``beacon``, ``webster``) — resolved id-first,
      then alias, the same order balances.toml uses.
    - ``date`` (str, ``YYYY-MM-DD``): the as-of date of the observed balance.
    - ``balance`` (number): the true end-of-day balance at ``date``.

    Optional:

    - ``note`` (str): free text stored as a TOML inline comment on the entry.

    The checkpoint asserts the end-of-day balance at ``date``; the reconcile
    pass verifies it against the prior chain (reporting a checkpoint-verification
    variance) and re-anchors so future periods chain forward from it. After
    writing, the Ally HYSA gate is refreshed and the affected account's
    reconcile pass re-runs.

    Checkpoints are cash-account only: a credit-card account is rejected.

    Returns the written entry plus the post-reconcile state for that account —
    the checkpoint period's ``statement_closing_balance``,
    ``computed_closing_balance``, ``variance_amount``, the re-anchored
    ``next_period_opening_balance``, and the ``checkpoint_stale`` flag.
    """
    parsed_request = UpsertBalanceCheckpointRequest.model_validate(request)
    return upsert_balance_checkpoint_impl(database, settings, parsed_request).model_dump()


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


@mcp.tool()
def ingest_check_register(request: dict | None = None) -> dict:
    """Attach payee + category info to outbound paper checks from a CSV register.

    Reads ``<watch_root>/check_register.csv`` (or the path supplied in
    ``register_path``) and writes a ``transaction_overrides`` row for every
    register entry that resolves to a known transaction. Required CSV
    columns: ``account, check_number, date_written, amount, payee``.
    Optional: ``primary_category, subcategory, lifecycle, household_role,
    notes``. Lines starting with ``#`` are skipped.

    Match priority: strict regex on ``description_raw`` (``CHECK# <n>``) at
    the same account + amount; fallback by date window when no strict match
    exists. Ambiguous fallbacks are reported in ``unmatched`` for manual
    triage. Pass ``dry_run=True`` to preview without writing.
    """
    parsed_request = IngestCheckRegisterRequest.model_validate(request or {})
    return ingest_check_register_impl(database, settings, parsed_request).model_dump()


@mcp.tool()
def ingest_check_deposit_ledger(request: dict | None = None) -> dict:
    """Attach source + category info to incoming mobile check deposits.

    Reads ``<watch_root>/check_deposits.csv`` (or the path supplied in
    ``ledger_path``). Required columns: ``account, date_deposited, amount,
    source``. Optional: ``primary_category`` (defaults to ``income``),
    ``subcategory, lifecycle, household_role, notes``. Match key is
    ``(account_id, amount, occurred_on ± 3 days)`` against rows whose
    ``description_raw`` matches ``MOBILE CHECK DEP``. ``dry_run`` previews.
    """
    parsed_request = IngestCheckDepositLedgerRequest.model_validate(request or {})
    return ingest_check_deposit_ledger_impl(database, settings, parsed_request).model_dump()


@mcp.tool()
def list_lifecycle_audit_candidates(request: dict | None = None) -> dict:
    """Surface transactions whose lifecycle / category looks suspicious.

    Returns a ranked list of review candidates across four heuristics
    (one_time-but-actually-recurring, recurring-but-singleton,
    discretionary outliers, high-value pet_care). Does NOT auto-reclassify
    — the analyst inspects and applies ``upsert_transaction_override``
    manually. Optional filters: ``primary_category``, ``min_amount``
    (default 100.0), ``limit`` (default 100).
    """
    parsed_request = LifecycleAuditRequest.model_validate(request or {})
    return list_lifecycle_audit_candidates_impl(database, parsed_request).model_dump()


@mcp.tool()
def get_annual_reference(year: int | None = None) -> dict:
    """Return year-end W-2 / 401(k) / HSA / withholding totals from the
    annual reference TOML file.

    Reads ``<watch_root>/annual_household_reference.toml``. Passing
    ``year`` returns just that year (synthesized as zero-valued
    ``populated=false`` entry when not present in the file). Passing
    nothing returns every year in the file. The file does not exist by
    default; the result reports ``file_exists`` so callers can guide the
    user to copy the template.
    """
    return get_annual_reference_impl(settings, year).model_dump()


@mcp.tool()
def compute_monthly_summary(year: int, month: int) -> dict:
    """Compute the monthly cashflow bridge summary — pure, no file I/O.

    Returns the structured summary dict for ``year``/``month``: HYSA status and
    $80K-target projection, transactions-view and theoretical net free cash
    flow + savings rate, the merchant-level spend narrative, and the
    auto-generated review flags. This is the programmatic surface for other
    Claude projects (e.g. the wealth tracker); ``generate_monthly_summary``
    writes the same data to a markdown file.

    The theoretical savings-rate view is config-sourced from ``[wealth_bridge]``
    in ``balances.toml`` — accurate only once the 401(k) placeholders are
    populated from a Novartis paystub.
    """
    balances = load_balances(settings.watch_root)
    annual = load_annual_reference(settings.watch_root)
    matching = next((e for e in annual.entries if e.year == year), None)
    return compute_monthly_summary_impl(
        database, balances.wealth_bridge, year, month, annual_reference=matching
    )


@mcp.tool()
def generate_monthly_summary(year: int, month: int) -> dict:
    """Generate the monthly cashflow bridge document — computes and writes .md.

    Calls ``compute_monthly_summary``, renders the markdown, and writes it to
    ``<watch_root>/monthly_summaries/YYYY-MM_Monthly_Cashflow_Summary.md``
    (directory created if absent). Returns the same dict as
    ``compute_monthly_summary`` plus ``markdown_path``. Any manual-notes block
    in a pre-existing file is preserved across regeneration.

    Scheduling: this tool is invoked by a Cowork scheduled task on the 1st of
    each month — shifted to the second business day when the 1st falls on a
    weekend or US federal holiday — always targeting the **prior** calendar
    month. No scheduler runs inside this server; the scheduler passes the
    correct ``(year, month)``. The scheduled-task config lives outside this
    repo.
    """
    balances = load_balances(settings.watch_root)
    annual = load_annual_reference(settings.watch_root)
    matching = next((e for e in annual.entries if e.year == year), None)
    return generate_monthly_summary_impl(
        database,
        balances.wealth_bridge,
        year,
        month,
        settings.watch_root,
        annual_reference=matching,
    )


def main() -> None:
    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
