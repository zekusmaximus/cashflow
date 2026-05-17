from __future__ import annotations

from datetime import date
from pathlib import Path

from liquidity_gate_mcp.balances import load_balances


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
