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
class WealthBridgeConfig:
    """Parsed ``[wealth_bridge]`` section of ``balances.toml``.

    Feeds the monthly cashflow summary generator (the bridge document for the
    separate wealth-tracker project). Gross income and 401(k) figures are
    config-driven on purpose — there is no paystub ingest pipeline — so the
    theoretical savings-rate view in the document is only as accurate as these
    values. ``jeff_401k_monthly`` / ``ashley_401k_monthly`` ship as ``0``
    placeholders; ``has_placeholder_401k`` lets the server surface a startup
    warning until they are populated from the latest Novartis paystub.
    """

    gross_household_income_annual: float
    gross_household_income_monthly: float
    jeff_401k_monthly: float
    ashley_401k_monthly: float
    hsa_monthly: float
    hysa_target: float
    savings_rate_target_pct: float
    discretionary_ceiling_monthly: float
    hysa_floor_monthly_delta: float
    savings_rate_floor_pct: float
    abnormal_flag_threshold: float
    monthly_summary_output_dir: str

    @property
    def tax_advantaged_monthly(self) -> float:
        """Jeff + Ashley pre-tax 401(k) plus the HSA contribution."""
        return self.jeff_401k_monthly + self.ashley_401k_monthly + self.hsa_monthly

    @property
    def has_placeholder_401k(self) -> bool:
        """True while either 401(k) figure is still the ``0`` placeholder."""
        return self.jeff_401k_monthly == 0 or self.ashley_401k_monthly == 0


# Defaults mirror the spec'd ``[wealth_bridge]`` block. Used verbatim when the
# section (or the whole file) is absent so the summary generator still runs.
DEFAULT_WEALTH_BRIDGE = WealthBridgeConfig(
    gross_household_income_annual=512000.0,
    gross_household_income_monthly=42667.0,
    jeff_401k_monthly=0.0,
    ashley_401k_monthly=0.0,
    hsa_monthly=583.0,
    hysa_target=80000.0,
    savings_rate_target_pct=22.0,
    discretionary_ceiling_monthly=19000.0,
    hysa_floor_monthly_delta=2500.0,
    savings_rate_floor_pct=18.0,
    abnormal_flag_threshold=3000.0,
    monthly_summary_output_dir="monthly_summaries",
)


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
    wealth_bridge: WealthBridgeConfig

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


def _parse_wealth_bridge(section: dict) -> WealthBridgeConfig:
    """Build a WealthBridgeConfig from a raw ``[wealth_bridge]`` table.

    Every key falls back to ``DEFAULT_WEALTH_BRIDGE`` so a partial section (or
    a stale file written before this section existed) still yields a usable
    config rather than raising.
    """
    d = DEFAULT_WEALTH_BRIDGE

    def num(key: str, default: float) -> float:
        value = section.get(key)
        try:
            return float(value) if value is not None else default
        except (ValueError, TypeError):
            return default

    output_dir = section.get("monthly_summary_output_dir")
    return WealthBridgeConfig(
        gross_household_income_annual=num(
            "gross_household_income_annual", d.gross_household_income_annual
        ),
        gross_household_income_monthly=num(
            "gross_household_income_monthly", d.gross_household_income_monthly
        ),
        jeff_401k_monthly=num("jeff_401k_monthly", d.jeff_401k_monthly),
        ashley_401k_monthly=num("ashley_401k_monthly", d.ashley_401k_monthly),
        hsa_monthly=num("hsa_monthly", d.hsa_monthly),
        hysa_target=num("hysa_target", d.hysa_target),
        savings_rate_target_pct=num(
            "savings_rate_target_pct", d.savings_rate_target_pct
        ),
        discretionary_ceiling_monthly=num(
            "discretionary_ceiling_monthly", d.discretionary_ceiling_monthly
        ),
        hysa_floor_monthly_delta=num(
            "hysa_floor_monthly_delta", d.hysa_floor_monthly_delta
        ),
        savings_rate_floor_pct=num(
            "savings_rate_floor_pct", d.savings_rate_floor_pct
        ),
        abnormal_flag_threshold=num(
            "abnormal_flag_threshold", d.abnormal_flag_threshold
        ),
        monthly_summary_output_dir=(
            str(output_dir).strip() if output_dir else d.monthly_summary_output_dir
        ),
    )


def load_balances(watch_root: Path) -> BalancesConfig:
    path = watch_root / "balances.toml"
    if not path.exists():
        return BalancesConfig(
            accounts={},
            source_path=path,
            loaded=False,
            wealth_bridge=DEFAULT_WEALTH_BRIDGE,
        )

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

    return BalancesConfig(
        accounts=accounts,
        source_path=path,
        loaded=True,
        wealth_bridge=_parse_wealth_bridge(raw.get("wealth_bridge", {}) or {}),
    )
