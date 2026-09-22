"""Mortgage carve-out: floor-plus-excess matching, the effective-dated payment
schedule, and per-statement balance anchors.

Expected values are the Ion Bank servicer's own figures (statements dated
2026-06-17, 2026-08-18 and 2026-09-17, loan 1900811505), not re-derivations.
"""

from __future__ import annotations

import logging
import sqlite3
from datetime import date
from pathlib import Path

import pytest

from liquidity_gate_mcp.annual_summary import compute_annual_summary
from liquidity_gate_mcp.balances import (
    MortgageConfig,
    MortgageConfigError,
    PaymentTier,
    StatementAnchor,
    WealthBridgeConfig,
    load_balances,
)
from liquidity_gate_mcp.database import DatabaseManager
from liquidity_gate_mcp.monthly_summary import (
    _mortgage_principal,
    _month_window,
    compute_monthly_summary,
)


# ---------------------------------------------------------------------------
# Config fixtures
# ---------------------------------------------------------------------------

# Mirrors the live [mortgage.ion] block: flat keys retained, arrays govern.
_LIVE_ION_BLOCK = """
[mortgage.ion]
merchant_match         = "IonBank Mortgage"
scheduled_payment      = 4044.66
principal_and_interest = 2611.83
escrow                 = 1432.83
annual_rate            = 0.04625
anchor_date            = "2026-06-17"
anchor_balance         = 475016.69

[[mortgage.ion.schedule]]
effective_from         = "2026-01-01"
scheduled_payment      = 4044.66
principal_and_interest = 2611.83
escrow                 = 1432.83

[[mortgage.ion.schedule]]
effective_from         = "2026-09-01"
scheduled_payment      = 4102.65
principal_and_interest = 2611.83
escrow                 = 1490.82

[[mortgage.ion.anchor]]
statement_date = "2026-06-17"
balance        = 475016.69

[[mortgage.ion.anchor]]
statement_date = "2026-08-18"
balance        = 472996.26

[[mortgage.ion.anchor]]
statement_date = "2026-09-17"
balance        = 472110.09
"""

_TIER_1 = PaymentTier(date(2026, 1, 1), 4044.66, 2611.83, 1432.83)
_TIER_2 = PaymentTier(date(2026, 9, 1), 4102.65, 2611.83, 1490.82)
_ANCHORS = (
    StatementAnchor(date(2026, 6, 17), 475016.69),
    StatementAnchor(date(2026, 8, 18), 472996.26),
    StatementAnchor(date(2026, 9, 17), 472110.09),
)


def _live_ion(tmp_path: Path) -> MortgageConfig:
    (tmp_path / "balances.toml").write_text(_LIVE_ION_BLOCK.strip(), encoding="utf-8")
    mortgage = load_balances(tmp_path).mortgage
    assert mortgage is not None
    return mortgage


def _flat_ion(**overrides: object) -> MortgageConfig:
    base: dict[str, object] = dict(
        merchant_match="IonBank Mortgage",
        scheduled_payment=4044.66,
        principal_and_interest=2611.83,
        escrow=1432.83,
        annual_rate=0.04625,
        anchor_date=date(2026, 6, 17),
        anchor_balance=475016.69,
    )
    base.update(overrides)
    return MortgageConfig(**base)  # type: ignore[arg-type]


def _wealth_bridge() -> WealthBridgeConfig:
    return WealthBridgeConfig(
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


# ---------------------------------------------------------------------------
# DB seeding
# ---------------------------------------------------------------------------

_ACCOUNT = "acct-beacon-1234"


def _seed_account(connection: sqlite3.Connection) -> None:
    connection.execute(
        "INSERT OR REPLACE INTO accounts (id, institution, account_name, "
        "account_type, owner, currency) VALUES (?, ?, ?, ?, ?, ?)",
        (_ACCOUNT, "Beacon", "Test", "checking", "joint", "USD"),
    )
    connection.execute(
        "INSERT OR IGNORE INTO import_batches (id, source_name, parser_version, "
        "imported_at, raw_payload) VALUES (?, ?, ?, ?, ?)",
        ("batch-test", "seed", "test", "2026-05-21T00:00:00Z", "{}"),
    )


def _insert(
    connection: sqlite3.Connection,
    tx_id: str,
    occurred_on: date,
    amount: float,
    *,
    direction: str = "transfer",
    primary_category: str = "fixed_obligation",
    subcategory: str | None = "mortgage",
    merchant_normalized: str | None = "IonBank Mortgage",
) -> None:
    connection.execute(
        """
        INSERT INTO transactions (
          id, account_id, import_batch_id, source_record_key,
          source_document_name, occurred_on, posted_on, description_raw,
          merchant_normalized, amount, direction, currency,
          primary_category, subcategory, household_role, lifecycle,
          transfer_group_key, statement_period, metadata_json
        ) VALUES (?, ?, 'batch-test', ?, 'seed.csv', ?, ?, 'row',
                  ?, ?, ?, 'USD', ?, ?, 'joint', 'recurring', NULL, NULL, '{}')
        """,
        (
            tx_id,
            _ACCOUNT,
            f"key-{tx_id}",
            occurred_on.isoformat(),
            occurred_on.isoformat(),
            merchant_normalized,
            amount,
            direction,
            primary_category,
            subcategory,
        ),
    )


def _seed_ion_rows(database: DatabaseManager, rows: list[tuple[date, float]]) -> None:
    connection = database.connect()
    try:
        _seed_account(connection)
        for i, (occurred_on, amount) in enumerate(rows):
            _insert(connection, f"ion-{i}", occurred_on, -amount)
        connection.commit()
    finally:
        connection.close()


def _split(
    database: DatabaseManager, year: int, month: int, mortgage: MortgageConfig | None
) -> tuple[float, float, float]:
    start, end = _month_window(year, month)
    connection = database.connect(read_only=True)
    try:
        return _mortgage_principal(connection, year, month, start, end, mortgage)
    finally:
        connection.close()


# Every 2026 IonBank row in the live DB through August. The HELOC-era rows are
# grouped into the months whose non-scheduled totals the live DB produces
# (day-of-month is illustrative except for the scheduled payments).
_IONBANK_2026 = [
    (date(2026, 1, 5), 4044.66),
    (date(2026, 1, 8), 750.00),
    (date(2026, 1, 12), 813.94),
    (date(2026, 1, 20), 2000.00),
    (date(2026, 1, 27), 1220.00),
    (date(2026, 2, 3), 4044.66),
    (date(2026, 2, 9), 1220.00),
    (date(2026, 2, 16), 831.46),
    (date(2026, 2, 23), 1000.00),
    (date(2026, 3, 3), 4044.66),
    (date(2026, 3, 16), 1220.00),
    (date(2026, 4, 2), 4044.66),
    (date(2026, 4, 9), 319.58),
    (date(2026, 4, 16), 1534.91),
    (date(2026, 4, 23), 1775.00),
    (date(2026, 5, 4), 4044.66),
    (date(2026, 5, 11), 500.00),
    (date(2026, 5, 18), 1775.00),
    (date(2026, 6, 2), 4044.66),
    (date(2026, 6, 9), 1775.00),
    (date(2026, 6, 10), 1207.44),
    (date(2026, 7, 2), 4044.66),
    (date(2026, 8, 4), 4500.00),
]

# (scheduled, non-scheduled, total) — the handoff's definition-of-done table.
_EXPECTED_2026 = {
    1: (763.22, 4783.94, 5547.16),
    2: (766.16, 3051.46, 3817.62),
    3: (769.11, 1220.00, 1989.11),
    4: (772.07, 3629.49, 4401.56),
    5: (775.05, 2275.00, 3050.05),
    6: (778.04, 2982.44, 3760.48),
    7: (781.04, 0.00, 781.04),
    8: (784.05, 455.34, 1239.39),
}


# ---------------------------------------------------------------------------
# Change 1 — floor-plus-excess matching
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("amount", "expected_nonscheduled"),
    [
        (4044.66, 0.00),  # exact scheduled payment
        (4500.00, 455.34),  # scheduled + material excess (the 8/4 payment)
        (4074.66, 0.00),  # +$30 near-miss: rounding, not a prepayment
        (4014.66, 0.00),  # −$30 near-miss: still clears the floor
        (3994.66, 0.00),  # exactly on the floor
    ],
)
def test_row_clearing_the_floor_is_one_scheduled_payment(
    database: DatabaseManager, tmp_path: Path, amount: float, expected_nonscheduled: float
) -> None:
    _seed_ion_rows(database, [(date(2026, 8, 4), amount)])
    total, scheduled, nonscheduled = _split(database, 2026, 8, _live_ion(tmp_path))
    assert scheduled == 784.05
    assert nonscheduled == expected_nonscheduled
    assert total == round(784.05 + expected_nonscheduled, 2)


def test_row_below_the_floor_is_fully_nonscheduled_and_warns(
    database: DatabaseManager, tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    _seed_ion_rows(database, [(date(2026, 6, 9), 1775.00), (date(2026, 6, 10), 3994.65)])
    with caplog.at_level(logging.WARNING, logger="liquidity_gate_mcp"):
        total, scheduled, nonscheduled = _split(database, 2026, 6, _live_ion(tmp_path))
    assert scheduled == 0.0
    assert nonscheduled == 5769.65
    assert total == 5769.65
    assert "none cleared the scheduled-payment floor" in caplog.text


def test_two_qualifying_rows_each_count_and_each_carry_their_own_excess(
    database: DatabaseManager, tmp_path: Path
) -> None:
    _seed_ion_rows(database, [(date(2026, 8, 1), 4044.66), (date(2026, 8, 28), 4500.00)])
    total, scheduled, nonscheduled = _split(database, 2026, 8, _live_ion(tmp_path))
    assert scheduled == 1568.10  # 2 × 784.05
    assert nonscheduled == 455.34
    assert total == 2023.44


def test_rows_on_each_side_of_a_tier_boundary(
    database: DatabaseManager, tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    ion = _live_ion(tmp_path)
    # $4,102.65 on 8/31 is tier 1 ($4,044.66): $57.99 above it is real excess.
    _seed_ion_rows(database, [(date(2026, 8, 31), 4102.65)])
    assert _split(database, 2026, 8, ion) == (842.04, 784.05, 57.99)

    # The same amount on 9/1 is tier 2's scheduled payment exactly.
    connection = database.connect()
    try:
        _insert(connection, "ion-sep", date(2026, 9, 1), -4102.65)
        # The old $4,044.66 amount on 9/2 falls below tier 2's floor.
        _insert(connection, "ion-oct", date(2026, 10, 2), -4044.66)
        connection.commit()
    finally:
        connection.close()
    assert _split(database, 2026, 9, ion) == (788.82, 788.82, 0.0)
    with caplog.at_level(logging.WARNING, logger="liquidity_gate_mcp"):
        assert _split(database, 2026, 10, ion) == (4044.66, 0.0, 4044.66)
    assert "$4102.65" in caplog.text


def test_no_config_applies_floor_plus_excess_to_the_fallback_payment(
    database: DatabaseManager,
) -> None:
    _seed_ion_rows(database, [(date(2026, 8, 4), 4500.00), (date(2026, 8, 20), 1775.00)])
    # No amortization without a config, but the excess and the below-floor row
    # are still carved out.
    assert _split(database, 2026, 8, None) == (2230.34, 0.0, 2230.34)


# ---------------------------------------------------------------------------
# Synthetic September / October rows against the live config
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("occurred_on", "amount", "expected"),
    [
        # The standing payment, servicer-verified by the 9/17 statement.
        (date(2026, 9, 2), 4200.00, (886.17, 788.82, 97.35)),
        # The new regular payment: escrow increase is not extra principal.
        (date(2026, 9, 2), 4102.65, (788.82, 788.82, 0.00)),
        # $37.35 excess is under the guard: a clean scheduled payment.
        (date(2026, 9, 2), 4140.00, (788.82, 788.82, 0.00)),
        # October uses the 9/17 statement's own anchor.
        (date(2026, 10, 2), 4200.00, (889.59, 792.24, 97.35)),
    ],
)
def test_synthetic_standing_payment_cases(
    database: DatabaseManager,
    tmp_path: Path,
    occurred_on: date,
    amount: float,
    expected: tuple[float, float, float],
) -> None:
    _seed_ion_rows(database, [(occurred_on, amount)])
    summary = compute_monthly_summary(
        database, _wealth_bridge(), occurred_on.year, occurred_on.month,
        mortgage=_live_ion(tmp_path),
    )
    con = summary["consumption"]
    assert (
        con["mortgage_principal"],
        con["mortgage_principal_scheduled"],
        con["debt_paydown_nonscheduled"],
    ) == expected


# ---------------------------------------------------------------------------
# Full-year reproduction of the servicer figures
# ---------------------------------------------------------------------------


def test_2026_ionbank_rows_reproduce_servicer_split_every_month(
    database: DatabaseManager, tmp_path: Path
) -> None:
    _seed_ion_rows(database, _IONBANK_2026)
    ion = _live_ion(tmp_path)
    for month, (scheduled, nonscheduled, total) in _EXPECTED_2026.items():
        con = compute_monthly_summary(
            database, _wealth_bridge(), 2026, month, mortgage=ion
        )["consumption"]
        assert con["mortgage_principal_scheduled"] == scheduled, month
        assert con["debt_paydown_nonscheduled"] == nonscheduled, month
        assert con["mortgage_principal"] == total, month

    annual = compute_annual_summary(database, _wealth_bridge(), 2026, mortgage=ion)
    totals = annual["consumption_totals"]
    assert round(totals["mortgage_principal_scheduled"], 2) == 6188.74
    assert round(totals["debt_paydown_nonscheduled"], 2) == 18397.67
    assert round(totals["mortgage_principal"], 2) == 24586.41


def test_august_identity_with_standing_excess(
    database: DatabaseManager, tmp_path: Path
) -> None:
    # Reconstructs the live August 2026 aggregates: earned income 17,016.15,
    # spend-side refunds 770.34, IonBank $4,500.00, net_fcf −12,245.69.
    connection = database.connect()
    try:
        _seed_account(connection)
        _insert(connection, "aug-pay", date(2026, 8, 15), 17016.15, direction="inflow",
                primary_category="income", subcategory="payroll", merchant_normalized=None)
        _insert(connection, "aug-refund", date(2026, 8, 18), 770.34, direction="inflow",
                primary_category="variable_lifestyle", subcategory="refund",
                merchant_normalized=None)
        _insert(connection, "aug-spend", date(2026, 8, 20), -24761.84, direction="outflow",
                primary_category="variable_lifestyle", subcategory=None,
                merchant_normalized=None)
        _insert(connection, "aug-ion", date(2026, 8, 4), -4500.00)
        connection.commit()
    finally:
        connection.close()

    summary = compute_monthly_summary(
        database, _wealth_bridge(), 2026, 8, mortgage=_live_ion(tmp_path)
    )
    fcf = summary["fcf_transactions"]
    con = summary["consumption"]
    assert fcf["net_fcf"] == -12245.69
    assert con["earned_income"] == 17016.15
    assert con["mortgage_principal"] == 1239.39
    assert con["reimbursements_spend_side"] == 770.34
    assert con["net_consumption"] == 27252.11
    assert round(con["earned_income"] - con["net_consumption"], 2) == round(
        fcf["net_fcf"] + con["mortgage_principal"] + con["reimbursements_spend_side"], 2
    )


# ---------------------------------------------------------------------------
# Change 2 — schedule resolution
# ---------------------------------------------------------------------------


def test_schedule_resolution(tmp_path: Path) -> None:
    ion = _live_ion(tmp_path)
    assert ion.schedule == (_TIER_1, _TIER_2)
    assert ion.tier_for(date(2025, 12, 31)) == _TIER_1  # before the first tier
    assert ion.tier_for(date(2026, 8, 31)) == _TIER_1
    assert ion.tier_for(date(2026, 9, 1)) == _TIER_2  # on the boundary
    assert ion.tier_for(date(2031, 1, 1)) == _TIER_2  # after the last tier


def test_schedule_absent_falls_back_to_flat_keys() -> None:
    tier = _flat_ion().tier_for(date(2026, 9, 2))
    assert (tier.scheduled_payment, tier.principal_and_interest, tier.escrow) == (
        4044.66, 2611.83, 1432.83,
    )


def test_amortization_uses_each_months_tier_pi() -> None:
    # A hypothetical recast from 2026-08 (P&I 2,700.00) must move July's
    # forward chain into August and August's own split.
    base = MortgageConfig(
        merchant_match="IonBank Mortgage", scheduled_payment=4044.66,
        principal_and_interest=2611.83, escrow=1432.83, annual_rate=0.04625,
        schedule=(_TIER_1,), anchors=_ANCHORS[:1],
    )
    recast = MortgageConfig(
        merchant_match="IonBank Mortgage", scheduled_payment=4044.66,
        principal_and_interest=2611.83, escrow=1432.83, annual_rate=0.04625,
        schedule=(_TIER_1, PaymentTier(date(2026, 8, 1), 4132.83, 2700.00, 1432.83)),
        anchors=_ANCHORS[:1],
    )
    assert recast.amortized_principal(2026, 7) == base.amortized_principal(2026, 7)
    assert recast.amortized_principal(2026, 8) == round(
        base.amortized_principal(2026, 8) + (2700.00 - 2611.83), 2
    )


# ---------------------------------------------------------------------------
# Change 3 — anchor resolution
# ---------------------------------------------------------------------------


def test_anchor_resolution_matches_servicer_figures(tmp_path: Path) -> None:
    ion = _live_ion(tmp_path)
    assert ion.anchors == _ANCHORS
    assert ion.amortized_principal(2026, 7) == 781.04  # = 6/17 anchor month
    assert ion.amortized_principal(2026, 8) == 784.05  # forward from 6/17
    assert ion.amortized_principal(2026, 9) == 788.82  # = 8/18 anchor month
    assert ion.amortized_principal(2026, 10) == 792.24  # = 9/17 anchor month
    assert ion.amortized_principal(2026, 1) == 763.22  # backward from 6/17
    assert round(sum(ion.amortized_principal(2026, m) for m in range(1, 9)), 2) == 6188.74


def test_later_anchor_never_restates_earlier_months(tmp_path: Path) -> None:
    ion = _live_ion(tmp_path)
    first_only = MortgageConfig(
        merchant_match=ion.merchant_match, scheduled_payment=ion.scheduled_payment,
        principal_and_interest=ion.principal_and_interest, escrow=ion.escrow,
        annual_rate=ion.annual_rate, schedule=ion.schedule, anchors=_ANCHORS[:1],
    )
    for month in range(1, 9):
        assert ion.amortized_principal(2026, month) == first_only.amortized_principal(
            2026, month
        )
    # Before the anchors were appended, September chained forward across the
    # 8/4 extra principal and drifted; the 8/18 anchor pins it.
    assert first_only.amortized_principal(2026, 9) != 788.82


def test_target_before_every_anchor_chains_backward_from_the_earliest() -> None:
    only_later = MortgageConfig(
        merchant_match="IonBank Mortgage", scheduled_payment=4044.66,
        principal_and_interest=2611.83, escrow=1432.83, annual_rate=0.04625,
        anchors=_ANCHORS[1:],
    )
    # Earliest anchor governs 2026-09; one month back is August's balance
    # after chaining back (not the 6/17 statement, which isn't in this list).
    r = 0.04625 / 12
    aug_balance = (472996.26 + 2611.83) / (1 + r)
    assert only_later.amortized_principal(2026, 8) == round(2611.83 - aug_balance * r, 2)


def test_no_anchor_array_keeps_the_flat_single_anchor_semantics() -> None:
    # Legacy: anchor month is anchor_date's own month (June for 2026-06-17),
    # rolled with the constant flat P&I — the pre-change recurrence verbatim.
    ion = _flat_ion()
    assert ion.anchors == ()
    r = 0.04625 / 12
    expected = {}
    balance = 475016.69
    for month in range(6, 13):
        expected[month] = round(2611.83 - balance * r, 2)
        balance -= 2611.83 - balance * r
    balance = 475016.69
    for month in range(5, 0, -1):
        balance = (balance + 2611.83) / (1 + r)
        expected[month] = round(2611.83 - balance * r, 2)
    assert expected[6] == 781.04
    for month, value in expected.items():
        assert ion.amortized_principal(2026, month) == value, month


def test_boundary_amounts_are_decided_in_whole_cents(
    database: DatabaseManager, tmp_path: Path
) -> None:
    # Exactly $50.00 over is still rounding; one cent more is a prepayment.
    _seed_ion_rows(database, [(date(2026, 8, 4), 4094.66), (date(2026, 8, 5), 4094.67)])
    assert _split(database, 2026, 8, _live_ion(tmp_path)) == (1618.11, 1568.10, 50.01)


# ---------------------------------------------------------------------------
# Config validation — present-but-broken arrays fail loudly at load time
# ---------------------------------------------------------------------------

_HEAD = """
[mortgage.ion]
merchant_match = "IonBank Mortgage"
annual_rate    = 0.04625
"""


def _anchor(statement_date: str, balance: str) -> str:
    return (
        f"\n[[mortgage.ion.anchor]]\nstatement_date = {statement_date}\n"
        f"balance = {balance}\n"
    )


def _tier(effective_from: str, payment: str = "4044.66") -> str:
    return (
        f"\n[[mortgage.ion.schedule]]\neffective_from = {effective_from}\n"
        f"scheduled_payment = {payment}\nprincipal_and_interest = 2611.83\n"
        "escrow = 1432.83\n"
    )


_GOOD_TIER = _tier('"2026-01-01"')
_GOOD_ANCHOR = _anchor('"2026-06-17"', "475016.69")


@pytest.mark.parametrize(
    "body",
    [
        _GOOD_TIER + _anchor('"2026-08-18"', "472996.26") + _anchor('"2026-06-17"', "475016.69"),
        _GOOD_TIER + _GOOD_ANCHOR + _anchor('"2026-06-17"', "475016.69"),
        _GOOD_TIER + _GOOD_ANCHOR + _anchor('"2026-06-28"', "474900.00"),  # same month
        _GOOD_TIER + _anchor('"2026-06-31"', "475016.69"),
        _GOOD_TIER + _anchor('"not a date"', "475016.69"),
        _GOOD_TIER + _anchor('"2026-06-17"', "0"),
        _GOOD_TIER + _anchor('"2026-06-17"', "-1.00"),
        _GOOD_TIER + _anchor('"2026-06-17"', '"475016.69x"'),
        _tier('"2026-09-01"') + _tier('"2026-01-01"') + _GOOD_ANCHOR,
        _tier('"2026-01-01"') + _tier('"2026-01-01"') + _GOOD_ANCHOR,
        _tier('"2026-13-01"') + _GOOD_ANCHOR,
        _tier('"2026-01-01"', "0") + _GOOD_ANCHOR,
        _tier('"2026-01-01"', "-4044.66") + _GOOD_ANCHOR,
        "\n[[mortgage.ion.schedule]]\neffective_from = \"2026-01-01\"\n" + _GOOD_ANCHOR,
    ],
    ids=[
        "anchors-unsorted",
        "anchors-duplicate",
        "anchors-same-month",
        "anchor-impossible-date",
        "anchor-unparseable-date",
        "anchor-zero-balance",
        "anchor-negative-balance",
        "anchor-non-numeric-balance",
        "schedule-unsorted",
        "schedule-duplicate",
        "schedule-unparseable-date",
        "schedule-zero-payment",
        "schedule-negative-payment",
        "schedule-missing-keys",
    ],
)
def test_malformed_arrays_raise_at_load(tmp_path: Path, body: str) -> None:
    (tmp_path / "balances.toml").write_text(_HEAD + body, encoding="utf-8")
    with pytest.raises(MortgageConfigError):
        load_balances(tmp_path)


def test_array_block_missing_annual_rate_raises(tmp_path: Path) -> None:
    (tmp_path / "balances.toml").write_text(
        '[mortgage.ion]\nmerchant_match = "IonBank Mortgage"\n' + _GOOD_TIER + _GOOD_ANCHOR,
        encoding="utf-8",
    )
    with pytest.raises(MortgageConfigError):
        load_balances(tmp_path)


def test_arrays_alone_load_without_flat_keys(tmp_path: Path) -> None:
    # Native TOML dates are accepted alongside quoted ISO strings.
    (tmp_path / "balances.toml").write_text(
        _HEAD + _tier("2026-01-01") + _anchor("2026-06-17", "475016.69"), encoding="utf-8"
    )
    ion = load_balances(tmp_path).mortgage
    assert ion is not None
    assert ion.anchor_date is None and ion.anchor_balance is None
    assert ion.scheduled_payment == 4044.66
    assert ion.amortized_principal(2026, 7) == 781.04


def test_direct_construction_validates_too() -> None:
    with pytest.raises(MortgageConfigError):
        _flat_ion(anchors=(_ANCHORS[1], _ANCHORS[0]))
    with pytest.raises(MortgageConfigError):
        MortgageConfig(
            merchant_match="IonBank Mortgage", scheduled_payment=4044.66,
            principal_and_interest=2611.83, escrow=1432.83, annual_rate=0.04625,
        )


def test_live_block_survives_a_balance_checkpoint_round_trip(tmp_path: Path) -> None:
    # upsert_balance_checkpoint rewrites balances.toml with tomlkit; the
    # [[mortgage.ion.*]] arrays and their comments must come through intact.
    tomlkit = pytest.importorskip("tomlkit")
    text = _LIVE_ION_BLOCK.replace(
        "balance        = 472110.09", "balance        = 472110.09  # 9/17 statement"
    )
    doc = tomlkit.parse(text)
    closings = tomlkit.table(is_super_table=True)
    closings["ally"] = {"2026-09-30": 35000.0}
    doc["statement_closings"] = closings
    out = tomlkit.dumps(doc)
    assert "# 9/17 statement" in out
    (tmp_path / "balances.toml").write_text(out, encoding="utf-8")
    reference = tmp_path / "reference"
    reference.mkdir()
    assert load_balances(tmp_path).mortgage == _live_ion(reference)
