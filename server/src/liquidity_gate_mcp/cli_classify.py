"""CLI entry point for the rule-based classifier — the single classification
engine shared by the MCP server and the Tauri desktop app.

The desktop app (Rust/React) cannot reach the Python classifier in-process, so
it shells out to this module as a sidecar to (optionally) create a
classification rule and run the classifier over the shared SQLite database.
Reusing :func:`liquidity_gate_mcp.classifier.apply_classifier` guarantees the
UI and the MCP server always produce identical classifications — there is no
second matching engine.

Invocation mirrors ``computed_balance.main`` and the ``.mcp.json`` launch
contract (interpreter ``server/.venv``, ``cwd=server``,
``LIQUIDITY_GATE_ROOT=..``). It is registered both as the ``apply-classifier``
console script and runnable as ``python -m liquidity_gate_mcp.cli_classify``.

A single JSON object is emitted to stdout::

    {"ok": true, "rule_id": "...", "scanned": N, "matched": N,
     "unclassified": N, "dry_run": false}

On error a ``{"ok": false, "error": "..."}`` object is printed and the process
exits non-zero. The module is import-light and side-effect-free on import so
``-m`` invocation stays clean.
"""

from __future__ import annotations

import argparse
import json
import sys
from typing import Any

from .classifier import apply_classifier, upsert_classification_rule
from .database import DatabaseManager
from .models import ApplyClassifierRequest, UpsertClassificationRuleRequest


def run_classify(
    database: DatabaseManager,
    *,
    rule: dict[str, Any] | None = None,
    reclassify_all: bool = True,
    dry_run: bool = False,
    account_filter: str | None = None,
) -> dict[str, Any]:
    """Optionally upsert a rule, run the classifier, then re-apply overrides.

    Steps, in order:

    1. If ``rule`` is provided, upsert it via
       :func:`upsert_classification_rule` and capture its id.
    2. Run :func:`apply_classifier` with the requested scope. ``reclassify_all``
       defaults to ``True`` because a UI-captured manual rule must be able to
       re-bucket rows the classifier previously assigned a *general* guess —
       the default ``unclassified``-only scope would never touch them. Manual
       overrides stay protected via ``metadata_json.manual_override_applied``.
    3. Unless this is a dry run, re-apply every stored override back onto its
       transactions (defense in depth: a manual override always wins over a
       rule, enforced both by the reclassify skip-list and this pass).

    Returns a plain dict ready to serialise to stdout. ``unclassified`` is
    counted exactly as the header pill / Classify view counts it
    (``primary_category = 'unclassified'`` excluding transfers) so the UI and
    the engine agree.
    """
    rule_id: str | None = None
    if rule is not None:
        upsert_result = upsert_classification_rule(
            database, UpsertClassificationRuleRequest(**rule)
        )
        rule_id = upsert_result.id

    result = apply_classifier(
        database,
        ApplyClassifierRequest(
            reclassify_all=reclassify_all,
            dry_run=dry_run,
            account_filter=account_filter,
        ),
    )

    if not dry_run:
        # Override precedence is inviolable — re-stamp manual overrides after
        # the classify pass so a conflicting rule can never win.
        database.reapply_all_overrides()

    unclassified = _count_unclassified(database)

    return {
        "ok": True,
        "rule_id": rule_id,
        "scanned": result.transactions_scanned,
        "matched": result.transactions_matched,
        "unclassified": unclassified,
        "dry_run": dry_run,
    }


def _count_unclassified(database: DatabaseManager) -> int:
    """Rows still unclassified, transfers excluded — matches the header pill."""
    with database.connect(read_only=True) as conn:
        row = conn.execute(
            "SELECT COUNT(*) AS n FROM transactions "
            "WHERE primary_category = 'unclassified' AND direction != 'transfer'"
        ).fetchone()
    return int(row["n"]) if row else 0


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="apply-classifier",
        description="Upsert a classification rule (optional) and run the classifier.",
    )
    parser.add_argument(
        "--rule",
        type=str,
        default=None,
        help="JSON object of UpsertClassificationRuleRequest fields to upsert "
        "before classifying. Omit to only re-run existing rules.",
    )
    parser.add_argument(
        "--reclassify-all",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Re-run over all rows (manual overrides protected). Default: on.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Compute matches without writing any classification.",
    )
    parser.add_argument(
        "--account-filter",
        type=str,
        default=None,
        help="Restrict classification to a single accounts.id.",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    """Console-script entry point for the ``apply-classifier`` command."""
    parser = _build_parser()
    args = parser.parse_args(argv)

    try:
        rule: dict[str, Any] | None = None
        if args.rule:
            parsed = json.loads(args.rule)
            if not isinstance(parsed, dict):
                raise ValueError("--rule must be a JSON object.")
            rule = parsed

        from .config import load_settings

        settings = load_settings()
        database = DatabaseManager(settings.database_path, settings.schema_path)
        database.initialize()

        payload = run_classify(
            database,
            rule=rule,
            reclassify_all=args.reclassify_all,
            dry_run=args.dry_run,
            account_filter=args.account_filter,
        )
    except Exception as exc:  # noqa: BLE001 — surface any failure as JSON
        print(json.dumps({"ok": False, "error": str(exc)}))
        return 1

    print(json.dumps(payload))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
