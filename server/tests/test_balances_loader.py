from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest

from liquidity_gate_mcp.balances import MortgageConfig, load_balances


def test_missing_file_returns_unloaded_config(tmp_path: Path) -> None:
    config = load_balances(tmp_path)
    assert config.loaded is False
    assert config.accounts == {}
    assert config.source_path == tmp_path / "balances.toml"


def test_parses_opening_balances(tmp_path: Path) -> None:
    (tmp_path / "balances.toml").write_text(
        """
        [opening_balances]
        "acct-beacon-1234" = 16903.06
        chase = 3982.41
        ally = 3113.44
        webster = 6933.12
        """.strip(),
        encoding="utf-8",
    )

    config = load_balances(tmp_path)
    assert config.loaded is True
    # Source-key entry survives verbatim (lowercased).
    beacon = config.lookup(account_id="acct-beacon-1234", institution="Beacon")
    assert beacon.opening_balance == 16903.06
    # Institution alias resolves when no exact source_key match.
    chase = config.lookup(account_id="acct-chase-credit-card", institution="Chase")
    assert chase.opening_balance == 3982.41


def test_parses_statement_closings(tmp_path: Path) -> None:
    (tmp_path / "balances.toml").write_text(
        """
        [opening_balances]
        chase = 3982.41

        [statement_closings.chase]
        "2026-01-31" = 4521.07
        "2026-02-28" = 4112.55
        """.strip(),
        encoding="utf-8",
    )

    config = load_balances(tmp_path)
    chase = config.lookup(account_id="acct-chase-credit-card", institution="Chase")
    assert chase.statement_closings[date(2026, 1, 31)] == 4521.07
    assert chase.statement_closings[date(2026, 2, 28)] == 4112.55


def test_unknown_account_returns_empty_sentinel(tmp_path: Path) -> None:
    (tmp_path / "balances.toml").write_text(
        '[opening_balances]\nchase = 1.0\n', encoding="utf-8"
    )
    config = load_balances(tmp_path)
    # Nothing seeded for this account in any form.
    result = config.lookup(account_id="acct-unknown", institution="Mystery")
    assert result.opening_balance is None
    assert result.statement_closings == {}


def test_skips_unparseable_closing_dates(tmp_path: Path) -> None:
    (tmp_path / "balances.toml").write_text(
        """
        [statement_closings.chase]
        "2026-01-31" = 4521.07
        "not-a-date" = 9999.99
        """.strip(),
        encoding="utf-8",
    )
    config = load_balances(tmp_path)
    chase = config.lookup(account_id="acct-chase-credit-card", institution="Chase")
    assert date(2026, 1, 31) in chase.statement_closings
    assert len(chase.statement_closings) == 1


# ---------------------------------------------------------------------------
# Mortgage config — [mortgage.ion] parsing + amortization
# ---------------------------------------------------------------------------

_ION_BLOCK = """
[mortgage.ion]
merchant_match         = "IonBank Mortgage"
scheduled_payment      = 4044.66
principal_and_interest = 2611.83
escrow                 = 1432.83
annual_rate            = 0.04625
anchor_date            = "2026-06-17"
anchor_balance         = 475016.69
"""


def _ion() -> MortgageConfig:
    return MortgageConfig(
        merchant_match="IonBank Mortgage",
        scheduled_payment=4044.66,
        principal_and_interest=2611.83,
        escrow=1432.83,
        annual_rate=0.04625,
        anchor_date=date(2026, 6, 17),
        anchor_balance=475016.69,
    )


def test_parses_mortgage_block(tmp_path: Path) -> None:
    (tmp_path / "balances.toml").write_text(_ION_BLOCK.strip(), encoding="utf-8")
    config = load_balances(tmp_path)
    assert config.mortgage == _ion()


def test_missing_mortgage_block_is_none(tmp_path: Path) -> None:
    # A file with other sections but no [mortgage.ion] — older config.
    (tmp_path / "balances.toml").write_text(
        '[opening_balances]\nchase = 1.0\n', encoding="utf-8"
    )
    config = load_balances(tmp_path)
    assert config.mortgage is None


def test_missing_file_has_no_mortgage(tmp_path: Path) -> None:
    config = load_balances(tmp_path)
    assert config.mortgage is None


def test_malformed_mortgage_block_degrades_to_none(tmp_path: Path) -> None:
    # Missing a required amortization input (annual_rate) — treated as absent
    # rather than amortizing off garbage.
    (tmp_path / "balances.toml").write_text(
        '[mortgage.ion]\nscheduled_payment = 4044.66\n', encoding="utf-8"
    )
    config = load_balances(tmp_path)
    assert config.mortgage is None


def test_amortized_principal_at_anchor_month_matches_audit() -> None:
    # DoD: June 2026 scheduled principal ≈ $781 (interest at the 4.625% rate on
    # the $475,016.69 anchor balance leaves ~$781 of the $2,611.83 P&I payment).
    assert _ion().amortized_principal(2026, 6) == pytest.approx(781.04, abs=0.01)


def test_amortized_principal_rolls_backward_and_forward_deterministically() -> None:
    ion = _ion()
    # Principal grows month over month as the balance amortizes down; the
    # backward roll (Jan–May) and forward roll (Jul+) are strictly monotonic
    # around the anchor.
    principals = [ion.amortized_principal(2026, m) for m in range(1, 9)]
    assert principals == sorted(principals)
    assert all(p2 > p1 for p1, p2 in zip(principals, principals[1:]))
    # Backward roll is the exact inverse of the forward recurrence: rolling from
    # May forward one month must reproduce the June balance split.
    r = ion.annual_rate / 12.0
    may_balance = (ion.anchor_balance + ion.principal_and_interest) / (1.0 + r)
    may_principal = round(ion.principal_and_interest - may_balance * r, 2)
    assert ion.amortized_principal(2026, 5) == pytest.approx(may_principal, abs=0.01)
