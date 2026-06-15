"""Tests for the annual cashflow summary and the category-driven
``monthly_cashflow_summary`` view.

The reconciliation tests are the important ones: they pin the SQL view and the
annual rollup to ``compute_monthly_summary`` so the three surfaces (view, monthly
summary, annual summary) can never silently drift onto different spend
definitions.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import date
from pathlib import Path

import pytest

from liquidity_gate_mcp.annual_summary import (
    compute_annual_summary,
    generate_annual_summary,
)
from liquidity_gate_mcp.balances import WealthBridgeConfig
from liquidity_gate_mcp.database import DatabaseManager
from liquidity_gate_mcp.monthly_summary import compute_monthly_summary


# ---------------------------------------------------------------------------
# Seeding helpers
# ---------------------------------------------------------------------------


def _wealth_bridge(**overrides: object) -> WealthBridgeConfig:
    base = dict(
        gross_household_income_annual=512000.0,
        gross_household_income_monthly=42667.0,
        jeff_401k_monthly=2000.0,
        ashley_401k_monthly=3000.0,
        hsa_monthly=583.0,
        hysa_target=80000.0,
        savings_rate_target_pct=22.0,
        discretionary_ceiling_monthly=19000.0,
        hysa_floor_monthly_delta=2500.0,
        savings_rate_floor_pct=18.0,
        abnormal_flag_threshold=3000.0,
        monthly_summary_output_dir="monthly_summaries",
    )
    base.update(overrides)
    return WealthBridgeConfig(**base)  # type: ignore[arg-type]


def _seed_account(connection: sqlite3.Connection, account_id: str = "acct-beacon-1234") -> None:
    connection.execute(
        "INSERT OR REPLACE INTO accounts (id, institution, account_name, "
        "account_type, owner, currency) VALUES (?, ?, ?, ?, ?, ?)",
        (account_id, "Beacon", "Test", "checking", "joint", "USD"),
    )


_TX_COUNTER = 0


def _insert_tx(
    connection: sqlite3.Connection,
    *,
    occurred_on: date,
    amount: float,
    direction: str,
    primary_category: str,
    account_id: str = "acct-beacon-1234",
    subcategory: str | None = None,
    description: str = "row",
) -> None:
    global _TX_COUNTER
    _TX_COUNTER += 1
    connection.execute(
        "INSERT OR IGNORE INTO import_batches (id, source_name, parser_version, "
        "imported_at, raw_payload) VALUES (?, ?, ?, ?, ?)",
        ("batch-test", "seed", "test", "2026-05-21T00:00:00Z", "{}"),
    )
    connection.execute(
        """
        INSERT INTO transactions (
          id, account_id, import_batch_id, source_record_key,
          source_document_name, occurred_on, posted_on, description_raw,
          merchant_normalized, amount, direction, currency,
          primary_category, subcategory, household_role, lifecycle,
          transfer_group_key, statement_period, metadata_json
        ) VALUES (?, ?, 'batch-test', ?, 'seed.csv', ?, ?, ?,
                  NULL, ?, ?, 'USD', ?, ?, 'joint', 'recurring', NULL, NULL, ?)
        """,
        (
            f"tx-{_TX_COUNTER}",
            account_id,
            f"key-{_TX_COUNTER}",
            occurred_on.isoformat(),
            occurred_on.isoformat(),
            description,
            amount,
            direction,
            primary_category,
            subcategory,
            json.dumps({}),
        ),
    )


def _seed_multi_month(connection: sqlite3.Connection) -> None:
    """Seed three months spanning the spend bridge, including the IonBank
    mortgage case (fixed_obligation tagged direction='transfer') and a refund
    (inflow in a discretionary category)."""
    _seed_account(connection)
    # January
    _insert_tx(connection, occurred_on=date(2026, 1, 5), amount=10000.0,
               direction="inflow", primary_category="income")
    _insert_tx(connection, occurred_on=date(2026, 1, 6), amount=-1200.0,
               direction="outflow", primary_category="variable_lifestyle")
    _insert_tx(connection, occurred_on=date(2026, 1, 7), amount=-800.0,
               direction="outflow", primary_category="fixed_obligation")
    # February — the mortgage arrives as a transfer-direction fixed_obligation.
    _insert_tx(connection, occurred_on=date(2026, 2, 3), amount=9000.0,
               direction="inflow", primary_category="income")
    _insert_tx(connection, occurred_on=date(2026, 2, 4), amount=-3500.0,
               direction="transfer", primary_category="fixed_obligation",
               subcategory="mortgage")
    _insert_tx(connection, occurred_on=date(2026, 2, 5), amount=-600.0,
               direction="outflow", primary_category="medical")
    # A refund: inflow in a discretionary category nets out by exclusion.
    _insert_tx(connection, occurred_on=date(2026, 2, 6), amount=150.0,
               direction="inflow", primary_category="variable_lifestyle")
    # Excluded categories that must never enter the spend bridge.
    _insert_tx(connection, occurred_on=date(2026, 2, 7), amount=-5000.0,
               direction="transfer", primary_category="transfer")
    _insert_tx(connection, occurred_on=date(2026, 2, 8), amount=-2000.0,
               direction="outflow", primary_category="investment")
    # March
    _insert_tx(connection, occurred_on=date(2026, 3, 2), amount=11000.0,
               direction="inflow", primary_category="income")
    _insert_tx(connection, occurred_on=date(2026, 3, 3), amount=-450.0,
               direction="outflow", primary_category="abnormal")
    _insert_tx(connection, occurred_on=date(2026, 3, 4), amount=-2200.0,
               direction="outflow", primary_category="variable_lifestyle")
    connection.commit()


def _view_rows(database: DatabaseManager) -> dict[str, dict[str, float]]:
    connection = database.connect(read_only=True)
    try:
        rows = connection.execute(
            "SELECT month, inflow, outflow, net_cash_flow FROM monthly_cashflow_summary"
        ).fetchall()
    finally:
        connection.close()
    return {
        row["month"]: {
            "inflow": row["inflow"],
            "outflow": row["outflow"],
            "net_cash_flow": row["net_cash_flow"],
        }
        for row in rows
    }


# ---------------------------------------------------------------------------
# Part 1 — view reconciliation
# ---------------------------------------------------------------------------


def test_view_reconciles_to_compute_monthly_summary_every_month(
    database: DatabaseManager,
) -> None:
    connection = database.connect()
    try:
        _seed_multi_month(connection)
    finally:
        connection.close()

    view = _view_rows(database)
    assert view, "expected the view to return at least one month"

    for month_label, columns in view.items():
        year, month = int(month_label[:4]), int(month_label[5:7])
        summary = compute_monthly_summary(database, _wealth_bridge(), year, month)
        fcf = summary["fcf_transactions"]
        expected_outflow = round(fcf["fixed_obligations"] + fcf["discretionary"], 2)

        assert columns["inflow"] == pytest.approx(fcf["inflows"], abs=0.01)
        assert columns["outflow"] == pytest.approx(expected_outflow, abs=0.01)
        assert columns["net_cash_flow"] == pytest.approx(
            columns["inflow"] - columns["outflow"], abs=0.01
        )


def test_view_outflow_includes_transfer_direction_fixed_obligation(
    database: DatabaseManager,
) -> None:
    # March-2026 sanity in miniature: a fixed_obligation row tagged
    # direction='transfer' (the IonBank mortgage) must land in `outflow`. The old
    # direction-based view would have excluded it entirely.
    connection = database.connect()
    try:
        _seed_account(connection)
        _insert_tx(connection, occurred_on=date(2026, 3, 1), amount=-1000.0,
                   direction="outflow", primary_category="variable_lifestyle")
        _insert_tx(connection, occurred_on=date(2026, 3, 2), amount=-35183.19,
                   direction="transfer", primary_category="fixed_obligation",
                   subcategory="mortgage")
        connection.commit()
    finally:
        connection.close()

    view = _view_rows(database)["2026-03"]
    # Direction-based outflow would have been just the $1,000 discretionary row;
    # category-driven outflow includes the $35,183.19 mortgage.
    assert view["outflow"] == pytest.approx(36183.19, abs=0.01)
    assert view["outflow"] > 1000.0


# ---------------------------------------------------------------------------
# Part 2 — annual rollup
# ---------------------------------------------------------------------------


def test_annual_months_reconcile_to_monthly_and_totals_are_column_sums(
    database: DatabaseManager,
) -> None:
    connection = database.connect()
    try:
        _seed_multi_month(connection)
    finally:
        connection.close()

    annual = compute_annual_summary(database, _wealth_bridge(), 2026)

    # One row per month with transactions, ascending.
    assert [m["month"] for m in annual["months"]] == ["2026-01", "2026-02", "2026-03"]

    running = {"income": 0.0, "fixed_obligations": 0.0, "discretionary": 0.0, "net_fcf": 0.0}
    for row in annual["months"]:
        year, month = int(row["month"][:4]), int(row["month"][5:7])
        monthly = compute_monthly_summary(database, _wealth_bridge(), year, month)
        fcf = monthly["fcf_transactions"]
        assert row["income"] == pytest.approx(fcf["inflows"], abs=0.01)
        assert row["fixed_obligations"] == pytest.approx(fcf["fixed_obligations"], abs=0.01)
        assert row["discretionary"] == pytest.approx(fcf["discretionary"], abs=0.01)
        assert row["net_fcf"] == pytest.approx(fcf["net_fcf"], abs=0.01)
        for key in running:
            running[key] += row[key]

    for key in running:
        assert annual["totals"][key] == pytest.approx(round(running[key], 2), abs=0.01)


def test_annual_category_breakdown_spans_all_categories_descending(
    database: DatabaseManager,
) -> None:
    connection = database.connect()
    try:
        _seed_multi_month(connection)
    finally:
        connection.close()

    annual = compute_annual_summary(database, _wealth_bridge(), 2026)
    breakdown = annual["category_breakdown"]

    # Reference table spans every category present, including the excluded ones.
    categories = {row["primary_category"] for row in breakdown}
    assert {"income", "transfer", "investment", "fixed_obligation"} <= categories

    # Descending by total_usd, ties broken by primary_category ascending.
    keys = [(-row["total_usd"], row["primary_category"]) for row in breakdown]
    assert keys == sorted(keys)


def test_annual_methodology_records_the_spend_definition(
    database: DatabaseManager,
) -> None:
    annual = compute_annual_summary(database, _wealth_bridge(), 2026)
    methodology = annual["methodology"]
    assert methodology["discretionary_categories"] == ["variable_lifestyle", "medical", "abnormal"]
    assert methodology["excluded_categories"] == [
        "transfer", "income", "tax", "investment", "rental", "business_expense",
    ]
    assert "fixed_obligation" in methodology["spend_definition"]


def test_annual_empty_year_is_zeroed_not_null(database: DatabaseManager) -> None:
    annual = compute_annual_summary(database, _wealth_bridge(), 2099)
    assert annual["months"] == []
    assert annual["totals"] == {
        "income": 0.0, "fixed_obligations": 0.0, "discretionary": 0.0, "net_fcf": 0.0,
    }
    assert annual["category_breakdown"] == []


def test_generate_annual_summary_writes_file_and_returns_path(
    database: DatabaseManager, tmp_path: Path
) -> None:
    connection = database.connect()
    try:
        _seed_multi_month(connection)
    finally:
        connection.close()

    summary = generate_annual_summary(database, _wealth_bridge(), 2026, tmp_path)
    written = Path(summary["markdown_path"])
    assert written.exists()
    assert written.name == "2026_Annual_Cashflow_Summary.md"
    assert written.parent.name == "monthly_summaries"
    assert summary["manual_block_preserved"] is False

    # Regeneration preserves a hand-written manual block.
    original = written.read_text(encoding="utf-8")
    edited = original.replace(
        "<!-- MANUAL NOTES -->",
        "<!-- MANUAL NOTES -->\nReviewed by Jeff — Q1 looks on track.\n",
        1,
    )
    written.write_text(edited, encoding="utf-8")
    regenerated = generate_annual_summary(database, _wealth_bridge(), 2026, tmp_path)
    assert regenerated["manual_block_preserved"] is True
    assert "Reviewed by Jeff — Q1 looks on track." in written.read_text(encoding="utf-8")
