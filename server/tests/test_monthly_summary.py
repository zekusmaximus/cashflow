"""Tests for the monthly cashflow summary generator."""

from __future__ import annotations

import json
import sqlite3
from datetime import date
from pathlib import Path

import pytest

from liquidity_gate_mcp.balances import (
    DEFAULT_WEALTH_BRIDGE,
    MortgageConfig,
    WealthBridgeConfig,
    load_balances,
)
from liquidity_gate_mcp.database import DatabaseManager
from liquidity_gate_mcp.monthly_summary import (
    compute_monthly_summary,
    generate_monthly_summary,
    normalize_merchant,
    placeholder_401k_warning,
    project_hysa_hit_date,
    select_top_movers,
)
from liquidity_gate_mcp.monthly_summary_flags import (
    FlagInputs,
    detect_abnormal_outflows,
    detect_discretionary_ceiling,
    detect_flags,
    detect_hysa_delta_floor,
    detect_savings_rate_floor,
)
from liquidity_gate_mcp.monthly_summary_renderer import (
    _render_movers,
    render_markdown,
    write_monthly_summary,
)


# ---------------------------------------------------------------------------
# Seeding helpers
# ---------------------------------------------------------------------------


def _wealth_bridge(**overrides: object) -> WealthBridgeConfig:
    """A WealthBridgeConfig with sensible test defaults, overridable per test."""
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


def _seed_account(
    connection: sqlite3.Connection,
    *,
    account_id: str = "acct-beacon-1234",
    institution: str = "Beacon",
    account_type: str = "checking",
) -> None:
    connection.execute(
        "INSERT OR REPLACE INTO accounts (id, institution, account_name, "
        "account_type, owner, currency) VALUES (?, ?, ?, ?, ?, ?)",
        (account_id, institution, "Test", account_type, "joint", "USD"),
    )


def _insert_tx(
    connection: sqlite3.Connection,
    *,
    account_id: str,
    occurred_on: date,
    amount: float,
    direction: str,
    primary_category: str = "unclassified",
    subcategory: str | None = None,
    merchant_normalized: str | None = None,
    description: str = "row",
    tx_id: str | None = None,
) -> None:
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
                  ?, ?, ?, 'USD', ?, ?, 'joint', 'recurring', NULL, NULL, ?)
        """,
        (
            tx_id or f"tx-{account_id}-{occurred_on.isoformat()}-{amount}-{direction}",
            account_id,
            tx_id or f"key-{occurred_on.isoformat()}-{amount}-{direction}-{description}",
            occurred_on.isoformat(),
            occurred_on.isoformat(),
            description,
            merchant_normalized,
            amount,
            direction,
            primary_category,
            subcategory,
            json.dumps({}),
        ),
    )


def _seed_hysa_anchor(connection: sqlite3.Connection, closing: float) -> None:
    """Seed an Ally HYSA account + a Dec-2025 closing anchor for v_computed_balance."""
    _seed_account(
        connection,
        account_id="acct-ally-hysa",
        institution="Ally",
        account_type="savings",
    )
    connection.execute(
        "INSERT INTO reconciliation_periods (id, account_id, period_start, "
        "period_end, statement_opening_balance, statement_closing_balance, "
        "closing_balance_source) VALUES ('recon-anchor', 'acct-ally-hysa', "
        "'2025-12-01', '2025-12-31', NULL, ?, 'balances_toml')",
        (closing,),
    )


# ---------------------------------------------------------------------------
# Merchant normalization
# ---------------------------------------------------------------------------


def test_normalize_merchant_collapses_trailing_transaction_ids() -> None:
    # Same merchant, different trailing transaction IDs → one stable key.
    assert normalize_merchant("AMAZON #12345") == normalize_merchant("amazon #98765")
    assert normalize_merchant("SQ *COFFEE BAR 0091823") == normalize_merchant(
        "SQ *Coffee Bar 7766541"
    )


def test_normalize_merchant_is_date_independent_and_idempotent() -> None:
    # The same raw description on different days collapses identically.
    desc = "WHOLE FOODS MARKET CAMBRIDGE MA"
    first = normalize_merchant(desc)
    second = normalize_merchant(desc)
    assert first == second
    # Location suffix stripped; result is stable when re-normalized.
    assert normalize_merchant(first) == first
    assert first == "whole foods market cambridge"


# ---------------------------------------------------------------------------
# HYSA projection methodology — three scenarios
# ---------------------------------------------------------------------------


def test_projection_positive_trailing_delta_yields_a_date() -> None:
    hit_date, months = project_hysa_hit_date(50000.0, 80000.0, 5000.0, 2026, 5)
    # gap 30000 / 5000 = 6 months → end of 2026-11.
    assert months == 6
    assert hit_date == "2026-11-30"


def test_projection_zero_trailing_delta_is_indefinite() -> None:
    hit_date, months = project_hysa_hit_date(50000.0, 80000.0, 0.0, 2026, 5)
    assert hit_date == "indefinite"
    assert months is None


def test_projection_negative_trailing_delta_is_indefinite() -> None:
    hit_date, months = project_hysa_hit_date(50000.0, 80000.0, -250.0, 2026, 5)
    assert hit_date == "indefinite"
    assert months is None


def test_projection_already_at_target_returns_target_month_end() -> None:
    hit_date, months = project_hysa_hit_date(85000.0, 80000.0, 5000.0, 2026, 5)
    assert hit_date == "2026-05-31"
    assert months == 0


# ---------------------------------------------------------------------------
# Top movers filter
# ---------------------------------------------------------------------------


def test_top_movers_filter_includes_and_excludes_correctly() -> None:
    this_month = {
        "amazon": 500.0,      # prior 250, delta 250 — qualifies
        "costco": 1200.0,     # prior 100, delta 1100 — qualifies
        "smalls": 60.0,       # new, < $300 — excluded
        "newbig": 400.0,      # new, >= $300 — qualifies
        "subdelta": 250.0,    # prior 200, delta 50 — excluded (sub-$200 delta)
        "lowprior": 500.0,    # prior 50, delta 450 — excluded (prior < $100)
    }
    prior_month = {
        "amazon": 250.0,
        "costco": 100.0,
        "subdelta": 200.0,
        "lowprior": 50.0,
        "bigprior": 800.0,    # this 0, delta -800 — qualifies
    }
    names = {k: k.title() for k in set(this_month) | set(prior_month)}

    movers = select_top_movers(this_month, prior_month, names)

    # Ranked by abs(delta) desc: costco 1100, bigprior 800, newbig 400.
    assert [m["merchant"] for m in movers] == ["Costco", "Bigprior", "Newbig"]
    assert {m["merchant"] for m in movers}.isdisjoint(
        {"Smalls", "Subdelta", "Lowprior", "Amazon"}
    )
    newbig = next(m for m in movers if m["merchant"] == "Newbig")
    assert newbig["is_new"] is True
    costco = next(m for m in movers if m["merchant"] == "Costco")
    assert costco["is_new"] is False


# ---------------------------------------------------------------------------
# Flag detectors — one synthetic case per detector
# ---------------------------------------------------------------------------


def test_each_flag_detector_fires_individually() -> None:
    hysa = detect_hysa_delta_floor(
        FlagInputs(hysa_monthly_delta=1000.0, savings_rate_transactions_pct=99.0,
                   discretionary_this_month=0.0)
    )
    assert hysa and hysa[0]["code"] == "hysa_delta_below_floor"

    savings = detect_savings_rate_floor(
        FlagInputs(hysa_monthly_delta=99999.0, savings_rate_transactions_pct=10.0,
                   discretionary_this_month=0.0)
    )
    assert savings and savings[0]["code"] == "savings_rate_below_floor"

    discretionary = detect_discretionary_ceiling(
        FlagInputs(hysa_monthly_delta=99999.0, savings_rate_transactions_pct=99.0,
                   discretionary_this_month=20000.0)
    )
    assert discretionary and discretionary[0]["code"] == "discretionary_over_ceiling"

    abnormal = detect_abnormal_outflows(
        FlagInputs(
            hysa_monthly_delta=99999.0,
            savings_rate_transactions_pct=99.0,
            discretionary_this_month=0.0,
            abnormal_txns=[{"merchant": "Roof Co", "amount": 5200.0, "date": "2026-05-09"}],
        )
    )
    assert abnormal and abnormal[0]["code"] == "abnormal_outflow"


def test_detect_flags_fires_all_four_in_order() -> None:
    flags = detect_flags(
        FlagInputs(
            hysa_monthly_delta=500.0,
            savings_rate_transactions_pct=5.0,
            discretionary_this_month=25000.0,
            abnormal_txns=[{"merchant": "Roof Co", "amount": 9000.0, "date": "2026-05-09"}],
        )
    )
    assert [f["code"] for f in flags] == [
        "hysa_delta_below_floor",
        "savings_rate_below_floor",
        "discretionary_over_ceiling",
        "abnormal_outflow",
    ]


def test_no_flags_when_nothing_trips() -> None:
    flags = detect_flags(
        FlagInputs(
            hysa_monthly_delta=9000.0,
            savings_rate_transactions_pct=30.0,
            discretionary_this_month=5000.0,
            abnormal_txns=[{"merchant": "Small", "amount": 100.0, "date": "2026-05-01"}],
        )
    )
    assert flags == []


# ---------------------------------------------------------------------------
# Discretionary scope SQL
# ---------------------------------------------------------------------------


def test_discretionary_scope_includes_only_locked_categories(
    database: DatabaseManager,
) -> None:
    connection = database.connect()
    try:
        _seed_account(connection)
        common = dict(account_id="acct-beacon-1234", occurred_on=date(2026, 5, 10))
        # In scope: variable_lifestyle, medical, abnormal outflows.
        _insert_tx(connection, **common, amount=-100.0, direction="outflow",
                   primary_category="variable_lifestyle", tx_id="t-vl")
        _insert_tx(connection, **common, amount=-50.0, direction="outflow",
                   primary_category="medical", tx_id="t-med")
        _insert_tx(connection, **common, amount=-200.0, direction="outflow",
                   primary_category="abnormal", tx_id="t-ab")
        # Out of scope: every other category, plus a transfer and an inflow.
        _insert_tx(connection, **common, amount=-999.0, direction="outflow",
                   primary_category="fixed_obligation", tx_id="t-fo")
        _insert_tx(connection, **common, amount=-999.0, direction="outflow",
                   primary_category="investment", tx_id="t-inv")
        _insert_tx(connection, **common, amount=-999.0, direction="transfer",
                   primary_category="transfer", tx_id="t-xfer")
        _insert_tx(connection, **common, amount=5000.0, direction="inflow",
                   primary_category="income", tx_id="t-inc")
        connection.commit()
    finally:
        connection.close()

    summary = compute_monthly_summary(database, _wealth_bridge(), 2026, 5)
    assert summary["fcf_transactions"]["discretionary"] == 350.0
    assert summary["fcf_transactions"]["fixed_obligations"] == 999.0
    assert summary["fcf_transactions"]["inflows"] == 5000.0


def test_fixed_obligation_transfer_direction_counts(
    database: DatabaseManager,
) -> None:
    # The IonBank mortgage case: a row the parser tagged direction='transfer'
    # (correct for reconciliation) but reclassified to primary_category=
    # 'fixed_obligation'. It is real spending and must land in fixed_obligations.
    connection = database.connect()
    try:
        _seed_account(connection)
        common = dict(account_id="acct-beacon-1234", occurred_on=date(2026, 5, 10))
        _insert_tx(connection, **common, amount=-1000.0, direction="outflow",
                   primary_category="fixed_obligation", tx_id="fo-outflow")
        _insert_tx(connection, **common, amount=4044.66, direction="transfer",
                   primary_category="fixed_obligation", subcategory="mortgage",
                   tx_id="fo-ionbank")
        connection.commit()
    finally:
        connection.close()

    summary = compute_monthly_summary(database, _wealth_bridge(), 2026, 5)
    # 1000 outflow + 4044.66 transfer-direction mortgage = 5044.66.
    assert summary["fcf_transactions"]["fixed_obligations"] == 5044.66


def _ion_mortgage() -> MortgageConfig:
    return MortgageConfig(
        merchant_match="IonBank Mortgage",
        scheduled_payment=4044.66,
        principal_and_interest=2611.83,
        escrow=1432.83,
        annual_rate=0.04625,
        anchor_date=date(2026, 6, 17),
        anchor_balance=475016.69,
    )


def _seed_june_consumption(connection: sqlite3.Connection) -> None:
    """A June-2026 scenario exercising every reimbursement + mortgage path."""
    _seed_account(connection)
    acct = dict(account_id="acct-beacon-1234")
    # Income line: payroll (earned), two reimbursement subcats (offsets), and a
    # tax_refund that must NOT be netted (belongs to a tax view).
    _insert_tx(connection, **acct, occurred_on=date(2026, 6, 2), amount=10000.0,
               direction="inflow", primary_category="income", subcategory="payroll",
               tx_id="jun-payroll")
    _insert_tx(connection, **acct, occurred_on=date(2026, 6, 3), amount=500.0,
               direction="inflow", primary_category="income",
               subcategory="insurance_reimbursement", tx_id="jun-ins")
    _insert_tx(connection, **acct, occurred_on=date(2026, 6, 4), amount=100.0,
               direction="inflow", primary_category="income", subcategory="refund",
               tx_id="jun-refund")
    _insert_tx(connection, **acct, occurred_on=date(2026, 6, 5), amount=300.0,
               direction="inflow", primary_category="income", subcategory="tax_refund",
               tx_id="jun-taxrefund")
    # In-category refund (spend-side offset).
    _insert_tx(connection, **acct, occurred_on=date(2026, 6, 6), amount=200.0,
               direction="inflow", primary_category="variable_lifestyle",
               subcategory="refund", tx_id="jun-vl-refund")
    # Discretionary spend.
    _insert_tx(connection, **acct, occurred_on=date(2026, 6, 7), amount=-2000.0,
               direction="outflow", primary_category="variable_lifestyle",
               tx_id="jun-vl-spend")
    # Scheduled mortgage (P&I + escrow) + two non-scheduled IonBank debt-paydown
    # rows (HELOC + prepayment) + one non-IonBank fixed obligation.
    _insert_tx(connection, **acct, occurred_on=date(2026, 6, 8), amount=-4044.66,
               direction="transfer", primary_category="fixed_obligation",
               subcategory="mortgage", merchant_normalized="IonBank Mortgage",
               tx_id="jun-mortgage")
    _insert_tx(connection, **acct, occurred_on=date(2026, 6, 9), amount=-1775.00,
               direction="outflow", primary_category="fixed_obligation",
               merchant_normalized="IonBank Mortgage", tx_id="jun-heloc")
    _insert_tx(connection, **acct, occurred_on=date(2026, 6, 10), amount=-1207.44,
               direction="outflow", primary_category="fixed_obligation",
               merchant_normalized="IonBank Mortgage", tx_id="jun-prepay")
    _insert_tx(connection, **acct, occurred_on=date(2026, 6, 11), amount=-800.0,
               direction="outflow", primary_category="fixed_obligation",
               tx_id="jun-other-fo")
    connection.commit()


def test_net_consumption_reimbursements_and_mortgage_principal(
    database: DatabaseManager,
) -> None:
    connection = database.connect()
    try:
        _seed_june_consumption(connection)
    finally:
        connection.close()

    summary = compute_monthly_summary(
        database, _wealth_bridge(), 2026, 6, mortgage=_ion_mortgage()
    )
    fcf = summary["fcf_transactions"]
    con = summary["consumption"]

    # Frozen fields are unchanged by the additive layer.
    assert fcf["inflows"] == 10900.0  # payroll + ins + refund + tax_refund
    assert fcf["fixed_obligations"] == 7827.10  # 4044.66 + 1775 + 1207.44 + 800
    assert fcf["discretionary"] == 2000.0
    assert fcf["net_fcf"] == pytest.approx(1072.90, abs=0.01)

    # Reimbursements: income-side (ins 500 + refund 100; tax_refund excluded) +
    # spend-side in-category refund (200).
    assert con["reimbursements_income_side"] == 600.0
    assert con["reimbursements_spend_side"] == 200.0
    assert con["reimbursements"] == 800.0
    # Earned income = income inflows minus income-side reimbursements only.
    assert con["earned_income"] == 10300.0

    # Mortgage principal: amortized scheduled (~781) + the two non-scheduled
    # IonBank rows (1775 + 1207.44); the non-IonBank $800 stays in consumption.
    assert con["mortgage_principal_scheduled"] == pytest.approx(781.04, abs=0.01)
    assert con["debt_paydown_nonscheduled"] == 2982.44
    assert con["mortgage_principal"] == pytest.approx(3763.48, abs=0.01)

    # Net consumption = FO + D − reimbursements − mortgage_principal.
    expected_nc = round(7827.10 + 2000.0 - 800.0 - 3763.48, 2)
    assert con["net_consumption"] == pytest.approx(expected_nc, abs=0.01)


def test_net_consumption_identity_holds_to_the_cent(
    database: DatabaseManager,
) -> None:
    # The full always-true identity: income minus true consumption equals cash
    # saved (net_fcf) plus equity built via principal plus in-category refunds
    # that never touched the frozen income line. (The reimbursements booked as
    # income cancel between the two sides and do not appear.)
    connection = database.connect()
    try:
        _seed_june_consumption(connection)
    finally:
        connection.close()

    summary = compute_monthly_summary(
        database, _wealth_bridge(), 2026, 6, mortgage=_ion_mortgage()
    )
    fcf = summary["fcf_transactions"]
    con = summary["consumption"]

    lhs = round(con["earned_income"] - con["net_consumption"], 2)
    rhs = round(
        fcf["net_fcf"] + con["mortgage_principal"] + con["reimbursements_spend_side"], 2
    )
    assert lhs == pytest.approx(rhs, abs=0.01)


def test_mortgage_principal_without_config_carves_nonscheduled_only(
    database: DatabaseManager,
) -> None:
    # Older config with no [mortgage.ion]: scheduled principal degrades to 0 (no
    # anchor to amortize), but non-scheduled IonBank rows are still carved out.
    connection = database.connect()
    try:
        _seed_june_consumption(connection)
    finally:
        connection.close()

    summary = compute_monthly_summary(database, _wealth_bridge(), 2026, 6, mortgage=None)
    con = summary["consumption"]
    assert con["mortgage_principal_scheduled"] == 0.0
    assert con["debt_paydown_nonscheduled"] == 2982.44
    assert con["mortgage_principal"] == 2982.44


def test_income_outflow_direction_excluded_from_inflows(
    database: DatabaseManager,
) -> None:
    # The AND-guard on inflows: a primary_category='income' row tagged
    # direction='outflow' (e.g. a clawback) must NOT count as an inflow.
    connection = database.connect()
    try:
        _seed_account(connection)
        common = dict(account_id="acct-beacon-1234", occurred_on=date(2026, 5, 10))
        _insert_tx(connection, **common, amount=5000.0, direction="inflow",
                   primary_category="income", tx_id="inc-inflow")
        _insert_tx(connection, **common, amount=-750.0, direction="outflow",
                   primary_category="income", tx_id="inc-outflow")
        connection.commit()
    finally:
        connection.close()

    summary = compute_monthly_summary(database, _wealth_bridge(), 2026, 5)
    # Only the inflow income counts; the outflow income row is excluded.
    assert summary["fcf_transactions"]["inflows"] == 5000.0


def test_transfer_category_never_counts_regardless_of_direction(
    database: DatabaseManager,
) -> None:
    # primary_category='transfer' is excluded from every spend bucket no matter
    # the direction — category, not direction, is the gate.
    connection = database.connect()
    try:
        _seed_account(connection)
        common = dict(account_id="acct-beacon-1234", occurred_on=date(2026, 5, 10))
        _insert_tx(connection, **common, amount=-1500.0, direction="outflow",
                   primary_category="transfer", tx_id="xfer-outflow")
        _insert_tx(connection, **common, amount=-2500.0, direction="transfer",
                   primary_category="transfer", tx_id="xfer-transfer")
        _insert_tx(connection, **common, amount=3500.0, direction="inflow",
                   primary_category="transfer", tx_id="xfer-inflow")
        connection.commit()
    finally:
        connection.close()

    summary = compute_monthly_summary(database, _wealth_bridge(), 2026, 5)
    fcf = summary["fcf_transactions"]
    assert fcf["fixed_obligations"] == 0.0
    assert fcf["discretionary"] == 0.0
    assert fcf["inflows"] == 0.0


def test_hysa_delta_is_account_scoped_all_categories(
    database: DatabaseManager,
) -> None:
    # The HYSA delta counts every transaction on the account — transfers in
    # included, since account scope is the filter (Decision 2 of the build).
    connection = database.connect()
    try:
        _seed_hysa_anchor(connection, closing=50000.0)
        common = dict(account_id="acct-ally-hysa", occurred_on=date(2026, 5, 12))
        _insert_tx(connection, **common, amount=4000.0, direction="transfer",
                   primary_category="transfer", tx_id="h-xfer-in")
        _insert_tx(connection, **common, amount=120.0, direction="inflow",
                   primary_category="income", subcategory="interest", tx_id="h-int")
        _insert_tx(connection, **common, amount=-500.0, direction="transfer",
                   primary_category="transfer", tx_id="h-xfer-out")
        connection.commit()
    finally:
        connection.close()

    summary = compute_monthly_summary(database, _wealth_bridge(), 2026, 5)
    # 4000 transfer in + 120 interest - 500 transfer out = 3620.
    assert summary["hysa"]["monthly_delta"] == 3620.0
    assert summary["hysa"]["balance"] == 53620.0


# ---------------------------------------------------------------------------
# Implied withholding never goes negative
# ---------------------------------------------------------------------------


def test_implied_withholding_floors_at_zero_and_flags(
    database: DatabaseManager,
) -> None:
    connection = database.connect()
    try:
        _seed_account(connection)
        # Payroll deposits far exceeding gross config → implied withholding
        # would be negative.
        _insert_tx(connection, account_id="acct-beacon-1234",
                   occurred_on=date(2026, 5, 15), amount=60000.0, direction="inflow",
                   primary_category="income", subcategory="payroll", tx_id="big-payroll")
        connection.commit()
    finally:
        connection.close()

    summary = compute_monthly_summary(database, _wealth_bridge(), 2026, 5)
    assert summary["fcf_theoretical"]["implied_withholding"] == 0.0
    codes = {f["code"] for f in summary["flags"]["auto"]}
    assert "implied_withholding_negative" in codes


# ---------------------------------------------------------------------------
# Manual notes block preservation
# ---------------------------------------------------------------------------


def test_manual_notes_block_survives_regeneration(
    database: DatabaseManager, tmp_path: Path
) -> None:
    summary = compute_monthly_summary(database, _wealth_bridge(), 2026, 5)

    # First generation — fresh file with the default placeholder manual body.
    path, preserved_first = write_monthly_summary(summary, tmp_path, "monthly_summaries")
    assert preserved_first is False

    # Jeff edits the manual notes block in place.
    original = path.read_text(encoding="utf-8")
    edited = original.replace(
        "<!-- MANUAL NOTES -->",
        "<!-- MANUAL NOTES -->\nReviewed by Jeff — roof repair is the abnormal item.\n",
        1,
    )
    path.write_text(edited, encoding="utf-8")

    # Regenerate — the hand-written note must survive.
    _, preserved_second = write_monthly_summary(summary, tmp_path, "monthly_summaries")
    assert preserved_second is True
    regenerated = path.read_text(encoding="utf-8")
    assert "Reviewed by Jeff — roof repair is the abnormal item." in regenerated
    # The auto-flags block is still present and exactly once.
    assert regenerated.count("<!-- AUTO-FLAGS -->") == 1


def test_mover_that_dropped_to_zero_renders_as_stopped() -> None:
    rendered = _render_movers(
        [
            {"merchant": "Delta Air", "this_month": 0.0, "prior_month": 245.0,
             "delta": -245.0, "is_new": False},
            {"merchant": "Costco", "this_month": 800.0, "prior_month": 300.0,
             "delta": 500.0, "is_new": False},
            {"merchant": "Newbie", "this_month": 400.0, "prior_month": 0.0,
             "delta": 400.0, "is_new": True},
        ]
    )
    assert "Delta Air: stopped this month (was $245.00)" in rendered
    assert "Costco: $800.00 (Δ +$500.00 from prior month)" in rendered
    assert "Newbie: $400.00 (new this month)" in rendered


def test_theoretical_banner_present_only_when_config_incomplete(
    database: DatabaseManager,
) -> None:
    placeholder = compute_monthly_summary(
        database, _wealth_bridge(jeff_401k_monthly=0.0, ashley_401k_monthly=0.0), 2026, 5
    )
    assert placeholder["fcf_theoretical"]["config_incomplete"] is True
    assert "Theoretical view incomplete" in render_markdown(placeholder, "\n")

    configured = compute_monthly_summary(
        database,
        _wealth_bridge(jeff_401k_monthly=1800.0, ashley_401k_monthly=2400.0),
        2026,
        5,
    )
    assert configured["fcf_theoretical"]["config_incomplete"] is False
    assert "Theoretical view incomplete" not in render_markdown(configured, "\n")


def test_generate_writes_file_and_returns_path(
    database: DatabaseManager, tmp_path: Path
) -> None:
    summary = generate_monthly_summary(database, _wealth_bridge(), 2026, 5, tmp_path)
    written = Path(summary["markdown_path"])
    assert written.exists()
    assert written.name == "2026-05_Monthly_Cashflow_Summary.md"
    assert written.parent.name == "monthly_summaries"


# ---------------------------------------------------------------------------
# Config loader — 401(k) placeholder warning
# ---------------------------------------------------------------------------


def test_config_loader_warns_when_401k_is_placeholder(tmp_path: Path) -> None:
    (tmp_path / "balances.toml").write_text(
        "[wealth_bridge]\n"
        "jeff_401k_monthly = 0\n"
        "ashley_401k_monthly = 0\n",
        encoding="utf-8",
    )
    config = load_balances(tmp_path)
    assert config.wealth_bridge.has_placeholder_401k is True
    warning = placeholder_401k_warning(config.wealth_bridge)
    assert warning is not None
    assert "jeff_401k_monthly" in warning
    assert "ashley_401k_monthly" in warning


def test_config_loader_silent_when_401k_populated(tmp_path: Path) -> None:
    (tmp_path / "balances.toml").write_text(
        "[wealth_bridge]\n"
        "jeff_401k_monthly = 1800\n"
        "ashley_401k_monthly = 2400\n",
        encoding="utf-8",
    )
    config = load_balances(tmp_path)
    assert config.wealth_bridge.has_placeholder_401k is False
    assert placeholder_401k_warning(config.wealth_bridge) is None
    assert config.wealth_bridge.tax_advantaged_monthly == 1800 + 2400 + 583


def test_missing_file_uses_default_wealth_bridge(tmp_path: Path) -> None:
    config = load_balances(tmp_path)
    assert config.wealth_bridge == DEFAULT_WEALTH_BRIDGE
    # Defaults carry the 0 placeholders, so the warning fires.
    assert config.wealth_bridge.has_placeholder_401k is True
