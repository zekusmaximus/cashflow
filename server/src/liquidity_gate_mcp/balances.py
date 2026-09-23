from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
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
    savings_rate_floor_pct=18.0,
    abnormal_flag_threshold=3000.0,
    monthly_summary_output_dir="monthly_summaries",
)


class MortgageConfigError(ValueError):
    """A ``[mortgage.ion]`` block that is present but malformed.

    Raised at load time for a broken ``schedule`` / ``anchor`` array (unsorted,
    duplicate, unparseable, non-positive). Silently degrading to ``None`` would
    disable the whole carve-out and restate every month without a trace, so a
    block that is present and broken fails loudly instead.
    """


@dataclass(frozen=True)
class PaymentTier:
    """One ``[[mortgage.ion.schedule]]`` entry: the payment in effect from a date.

    A tier changes on an escrow re-analysis (``scheduled_payment`` / ``escrow``
    move, P&I does not) or on a rate change / recast (P&I moves). Payments above
    the tier's ``scheduled_payment`` are NOT a new tier — the excess is extra
    principal, booked as non-scheduled by the monthly summary.
    """

    effective_from: date
    scheduled_payment: float
    principal_and_interest: float
    escrow: float


@dataclass(frozen=True)
class StatementAnchor:
    """One ``[[mortgage.ion.anchor]]`` entry, stored as the servicer printed it.

    A statement dated month M reports the outstanding principal from which the
    month M+1 payment is computed (6/17 → July, 8/18 → September, 9/17 →
    October; each month's interest equals balance × rate/12). ``anchor_month``
    is therefore the calendar month *after* ``statement_date``.
    """

    statement_date: date
    balance: float

    @property
    def anchor_month_index(self) -> int:
        """``year * 12 + month0`` of the month this balance governs."""
        return self.statement_date.year * 12 + self.statement_date.month


def _month_index(d: date) -> int:
    return d.year * 12 + (d.month - 1)


@dataclass(frozen=True)
class MortgageConfig:
    """Parsed ``[mortgage.ion]`` section of ``balances.toml``.

    Drives the ``mortgage_principal`` debt-paydown carve-out in the monthly /
    annual summary. The whole point is that a mortgage payment is part
    consumption (interest + escrow) and part saving (principal — balance-sheet
    neutral debt paydown). This config lets the summary amortize the principal
    portion of the scheduled payment out of ``net_consumption`` without ever
    reclassifying a transaction row.

    Payment schedule: ``schedule`` is an optional effective-dated list of
    ``PaymentTier``; a row dated D uses the tier with the greatest
    ``effective_from <= D`` (a date before the first tier uses the first tier).
    With no ``schedule``, the flat ``scheduled_payment`` /
    ``principal_and_interest`` / ``escrow`` fields are the single timeless tier.

    Balance anchors: ``anchors`` is an optional additive list of
    ``StatementAnchor``, one per servicer statement. With no ``anchors``, the
    legacy single ``anchor_date`` / ``anchor_balance`` pair is used exactly as
    before (anchor month = ``anchor_date``'s own month). See
    ``amortized_principal`` for how anchors are resolved.
    """

    merchant_match: str
    scheduled_payment: float
    principal_and_interest: float
    escrow: float
    annual_rate: float
    anchor_date: date | None = None
    anchor_balance: float | None = None
    schedule: tuple[PaymentTier, ...] = ()
    anchors: tuple[StatementAnchor, ...] = ()

    def __post_init__(self) -> None:
        for prev, cur in zip(self.schedule, self.schedule[1:]):
            if cur.effective_from <= prev.effective_from:
                raise MortgageConfigError(
                    "mortgage.ion.schedule: effective_from dates must be strictly "
                    f"ascending (got {prev.effective_from} then {cur.effective_from})"
                )
        for tier in self.schedule:
            if tier.scheduled_payment <= 0 or tier.principal_and_interest <= 0:
                raise MortgageConfigError(
                    f"mortgage.ion.schedule[{tier.effective_from}]: scheduled_payment "
                    "and principal_and_interest must be positive"
                )
            if tier.escrow < 0:
                raise MortgageConfigError(
                    f"mortgage.ion.schedule[{tier.effective_from}]: escrow must not "
                    "be negative"
                )
        for prev, cur in zip(self.anchors, self.anchors[1:]):
            if cur.statement_date <= prev.statement_date:
                raise MortgageConfigError(
                    "mortgage.ion.anchor: statement_date values must be strictly "
                    f"ascending (got {prev.statement_date} then {cur.statement_date})"
                )
            if cur.anchor_month_index == prev.anchor_month_index:
                raise MortgageConfigError(
                    "mortgage.ion.anchor: one anchor per month — "
                    f"{prev.statement_date} and {cur.statement_date} fall in the "
                    "same month"
                )
        for anchor in self.anchors:
            if anchor.balance <= 0:
                raise MortgageConfigError(
                    f"mortgage.ion.anchor[{anchor.statement_date}]: balance must be "
                    "positive"
                )
        if not self.anchors and (self.anchor_date is None or self.anchor_balance is None):
            raise MortgageConfigError(
                "mortgage.ion: needs an [[mortgage.ion.anchor]] array or the flat "
                "anchor_date / anchor_balance keys"
            )

    def tier_for(self, on: date) -> PaymentTier:
        """The payment tier in effect on ``on``.

        Greatest ``effective_from <= on``; a date before the first tier uses the
        first tier. Without a ``schedule`` the flat keys form the only tier.
        """
        if not self.schedule:
            return PaymentTier(
                effective_from=date.min,
                scheduled_payment=self.scheduled_payment,
                principal_and_interest=self.principal_and_interest,
                escrow=self.escrow,
            )
        chosen = self.schedule[0]
        for tier in self.schedule:
            if tier.effective_from <= on:
                chosen = tier
            else:
                break
        return chosen

    def _pi_for_index(self, index: int) -> float:
        # A month rolls with the P&I of the tier in effect on its 1st.
        year, month0 = divmod(index, 12)
        return self.tier_for(date(year, month0 + 1, 1)).principal_and_interest

    def amortized_principal(self, year: int, month: int) -> float:
        """Principal portion of the scheduled payment posted in ``year``/``month``.

        Rolls the outstanding balance from an anchor to the start of the target
        month, then splits that month's P&I payment:
        ``interest = balance * annual_rate/12``; ``principal = P&I - interest``.
        Each month rolled through uses the P&I of the schedule tier in effect for
        that month. Forward months apply ``next = balance - (P&I - interest)``;
        backward months invert it as ``prev = (balance + P&I) / (1 + r)``.

        Anchor resolution (``anchors`` present): for target month T use the
        anchor with the latest anchor month <= T and chain *forward*; if T
        precedes every anchor month, use the earliest anchor and chain
        *backward*. A backward chain never crosses a later anchor.

        Why one anchor per statement: the recurrence models only the scheduled
        payment. A non-scheduled principal payment (a lump sum, or the standing
        excess above the scheduled payment) changes the balance after the
        payment that carried it, and the next statement's anchor absorbs it
        exactly. Between statements, a forward-chained month drifts by roughly
        ``excess * rate/12`` per month of unmodelled excess (about $0.37 at a
        $97.35 standing excess) until the next anchor is appended. Appending an
        anchor from every servicer statement is the operational rule; earlier
        months are unaffected by a new anchor because each resolves to the
        latest anchor at or before it.

        Legacy fallback (no ``anchors``): ``anchor_balance`` is the balance from
        which ``anchor_date``'s own month's payment is computed, and the target
        month chains forward or backward from it.
        """
        rate = self.annual_rate / 12.0
        target_index = year * 12 + (month - 1)

        if self.anchors:
            anchor = self.anchors[0]
            for candidate in self.anchors:
                if candidate.anchor_month_index <= target_index:
                    anchor = candidate
                else:
                    break
            anchor_index = anchor.anchor_month_index
            balance = anchor.balance
        else:
            assert self.anchor_date is not None and self.anchor_balance is not None
            anchor_index = _month_index(self.anchor_date)
            balance = self.anchor_balance

        index = anchor_index
        while index < target_index:
            interest = balance * rate
            balance -= self._pi_for_index(index) - interest
            index += 1
        while index > target_index:
            index -= 1
            balance = (balance + self._pi_for_index(index)) / (1.0 + rate)

        interest = balance * rate
        return round(self._pi_for_index(target_index) - interest, 2)


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

    ``mortgage`` is ``None`` when the ``[mortgage.ion]`` block is absent (older
    configs) — the summary then reports ``mortgage_principal = 0`` for the
    scheduled portion and skips amortization, while still carving out any
    non-scheduled IonBank debt-paydown rows.
    """

    accounts: dict[str, AccountBalances]
    source_path: Path
    loaded: bool
    wealth_bridge: WealthBridgeConfig
    mortgage: MortgageConfig | None = None

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
    config rather than raising. Keys this parser does not read — including ones
    since retired from the template — are ignored, so an older file never fails
    to load.
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


def _config_date(value: object, where: str) -> date:
    # TOML allows a native date (unquoted) or a quoted ISO string; accept both.
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    try:
        return date.fromisoformat(str(value).strip())
    except ValueError as exc:
        raise MortgageConfigError(f"{where}: unparseable date {value!r}") from exc


def _config_number(entry: dict, key: str, where: str) -> float:
    if key not in entry:
        raise MortgageConfigError(f"{where}: missing required key {key!r}")
    value = entry[key]
    if isinstance(value, bool):
        raise MortgageConfigError(f"{where}: {key} must be a number, got {value!r}")
    try:
        return float(value)
    except (ValueError, TypeError) as exc:
        raise MortgageConfigError(
            f"{where}: {key} must be a number, got {value!r}"
        ) from exc


def _config_tables(section: dict, key: str) -> list[dict]:
    raw = section[key]
    if not isinstance(raw, list) or not raw or not all(isinstance(e, dict) for e in raw):
        raise MortgageConfigError(
            f"mortgage.ion.{key}: must be a non-empty array of tables "
            f"([[mortgage.ion.{key}]])"
        )
    return raw


def _parse_schedule(section: dict) -> tuple[PaymentTier, ...]:
    tiers = []
    for i, entry in enumerate(_config_tables(section, "schedule")):
        where = f"mortgage.ion.schedule[{i}]"
        if "effective_from" not in entry:
            raise MortgageConfigError(f"{where}: missing required key 'effective_from'")
        tiers.append(
            PaymentTier(
                effective_from=_config_date(entry["effective_from"], where),
                scheduled_payment=_config_number(entry, "scheduled_payment", where),
                principal_and_interest=_config_number(
                    entry, "principal_and_interest", where
                ),
                escrow=_config_number(entry, "escrow", where),
            )
        )
    return tuple(tiers)


def _parse_anchors(section: dict) -> tuple[StatementAnchor, ...]:
    anchors = []
    for i, entry in enumerate(_config_tables(section, "anchor")):
        where = f"mortgage.ion.anchor[{i}]"
        if "statement_date" not in entry:
            raise MortgageConfigError(f"{where}: missing required key 'statement_date'")
        anchors.append(
            StatementAnchor(
                statement_date=_config_date(entry["statement_date"], where),
                balance=_config_number(entry, "balance", where),
            )
        )
    return tuple(anchors)


def _parse_mortgage(section: dict) -> MortgageConfig | None:
    """Build a ``MortgageConfig`` from a raw ``[mortgage.ion]`` table.

    Returns ``None`` when the section is absent. A flat-keys-only block (no
    ``schedule`` / ``anchor`` array — the pre-2026-09 shape) keeps its original
    tolerance: missing or malformed amortization inputs degrade to ``None`` (the
    carve-out falls back to non-scheduled rows only) rather than amortizing off
    garbage.

    A block that carries a ``schedule`` or ``anchor`` array is the current
    shape and is validated strictly: any problem — unsorted or duplicate dates,
    two anchors in one month, unparseable dates, non-positive amounts, a missing
    ``annual_rate`` — raises ``MortgageConfigError`` at load time instead of
    silently disabling the carve-out. When an array is present it governs; the
    matching flat keys are then optional.
    """
    if not section:
        return None

    merchant = str(section.get("merchant_match", "IonBank Mortgage")).strip()

    if "schedule" not in section and "anchor" not in section:
        try:
            return MortgageConfig(
                merchant_match=merchant,
                scheduled_payment=float(section["scheduled_payment"]),
                principal_and_interest=float(section["principal_and_interest"]),
                escrow=float(section.get("escrow", 0.0)),
                annual_rate=float(section["annual_rate"]),
                anchor_date=date.fromisoformat(str(section["anchor_date"])),
                anchor_balance=float(section["anchor_balance"]),
            )
        except (KeyError, ValueError, TypeError):
            return None

    where = "mortgage.ion"
    schedule = _parse_schedule(section) if "schedule" in section else ()
    anchors = _parse_anchors(section) if "anchor" in section else ()

    if schedule:
        # Flat keys are superseded; mirror the first tier when they are absent.
        first = schedule[0]
        scheduled_payment = float(section.get("scheduled_payment", first.scheduled_payment))
        principal_and_interest = float(
            section.get("principal_and_interest", first.principal_and_interest)
        )
        escrow = float(section.get("escrow", first.escrow))
    else:
        scheduled_payment = _config_number(section, "scheduled_payment", where)
        principal_and_interest = _config_number(section, "principal_and_interest", where)
        escrow = float(section.get("escrow", 0.0))

    anchor_date: date | None = None
    anchor_balance: float | None = None
    if not anchors:
        if "anchor_date" not in section:
            raise MortgageConfigError(f"{where}: missing required key 'anchor_date'")
        anchor_date = _config_date(section["anchor_date"], where)
        anchor_balance = _config_number(section, "anchor_balance", where)
        if anchor_balance <= 0:
            raise MortgageConfigError(f"{where}: anchor_balance must be positive")
    elif "anchor_date" in section and "anchor_balance" in section:
        # Carried for reference only; the anchor array governs.
        anchor_date = _config_date(section["anchor_date"], where)
        anchor_balance = _config_number(section, "anchor_balance", where)

    return MortgageConfig(
        merchant_match=merchant,
        scheduled_payment=scheduled_payment,
        principal_and_interest=principal_and_interest,
        escrow=escrow,
        annual_rate=_config_number(section, "annual_rate", where),
        anchor_date=anchor_date,
        anchor_balance=anchor_balance,
        schedule=schedule,
        anchors=anchors,
    )


def load_balances(watch_root: Path) -> BalancesConfig:
    path = watch_root / "balances.toml"
    if not path.exists():
        return BalancesConfig(
            accounts={},
            source_path=path,
            loaded=False,
            wealth_bridge=DEFAULT_WEALTH_BRIDGE,
            mortgage=None,
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

    mortgage_section = (raw.get("mortgage") or {}).get("ion", {}) or {}

    return BalancesConfig(
        accounts=accounts,
        source_path=path,
        loaded=True,
        wealth_bridge=_parse_wealth_bridge(raw.get("wealth_bridge", {}) or {}),
        mortgage=_parse_mortgage(mortgage_section),
    )
