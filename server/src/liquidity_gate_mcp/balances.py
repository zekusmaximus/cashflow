from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
import tomllib


@dataclass(frozen=True)
class AccountBalances:
    opening_balance: float | None = None
    statement_closings: dict[date, float] = field(default_factory=dict)


@dataclass(frozen=True)
class BalancesConfig:
    """Parsed contents of ``balances.toml`` in the watch root.

    The loader is tolerant of a missing or empty file: callers receive an
    empty config and surface a UI hint instead of failing. Beacon and
    Webster derive statement closings from CSV ``running_balance`` metadata,
    so only Chase and Ally normally need entries; the loader treats every
    account uniformly to avoid baking institution policy into config.

    Keys are matched against the DB by exact ``accounts.id`` first, then
    by case-insensitive institution alias (``chase``, ``beacon``, ``ally``,
    ``webster``) so Jeff can edit the file without memorizing source_keys.
    """

    accounts: dict[str, AccountBalances]
    source_path: Path
    loaded: bool

    def lookup(
        self,
        *,
        account_id: str,
        institution: str,
    ) -> AccountBalances:
        # Exact account_id wins; fall back to a single-institution alias if
        # only one entry matches. Empty AccountBalances is a clean sentinel —
        # opening_balance None, no statement closings.
        if account_id in self.accounts:
            return self.accounts[account_id]
        alias = institution.strip().lower()
        if alias in self.accounts:
            return self.accounts[alias]
        return AccountBalances()


def load_balances(watch_root: Path) -> BalancesConfig:
    path = watch_root / "balances.toml"
    if not path.exists():
        return BalancesConfig(accounts={}, source_path=path, loaded=False)

    raw = tomllib.loads(path.read_text(encoding="utf-8"))
    opening_section = raw.get("opening_balances", {}) or {}
    closings_section = raw.get("statement_closings", {}) or {}

    keys = set(opening_section.keys()) | set(closings_section.keys())
    accounts: dict[str, AccountBalances] = {}
    for key in keys:
        opening_val = opening_section.get(key)
        closings_raw = closings_section.get(key, {}) or {}
        closings: dict[date, float] = {}
        for period_end, balance in closings_raw.items():
            if balance is None:
                continue
            try:
                closings[date.fromisoformat(str(period_end))] = float(balance)
            except (ValueError, TypeError):
                # Tolerate stray comment-style entries; the loader is a
                # convenience surface, not a strict validator.
                continue
        accounts[str(key).strip().lower()] = AccountBalances(
            opening_balance=float(opening_val) if opening_val is not None else None,
            statement_closings=closings,
        )

    return BalancesConfig(accounts=accounts, source_path=path, loaded=True)
