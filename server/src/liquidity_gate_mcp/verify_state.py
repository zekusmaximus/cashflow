"""verify_state — every post-ingest check in one read-only call.

Turns the watch root's ``MONTHLY_RUNBOOK.md`` step 6/6a checklist into code: each
check returns ``pass`` / ``info`` / ``warn`` / ``fail`` (or ``error`` when the
check itself raised), and one failing check never stops the others.

Read-only against the database in both modes. Its own queries run on
``DatabaseManager.connect(read_only=True)``; recomputation goes only through the
pure compute paths (``compute_monthly_summary`` / ``compute_annual_summary``).
It never calls ``ingest_documents``, ``apply_classifier``, ``pair_transfers``,
``reconcile_periods``, ``refresh_hysa_gate``, any ``upsert_*`` or any
``generate_*``. With ``write=True`` it writes exactly two things in the watch
root, both files: ``STATUS_DATA.md`` and the ``_state_exports/`` folder (the
underscore keeps the ingest walker out of it).

Configuration lives in ``<watch_root>/verify_state.toml`` (hand-edited); when
that file is absent the seeded ``server/templates/verify_state.template.toml``
is loaded instead and the ``config_source`` check says so. A file that is
present but malformed fails the whole run loudly.

``[[benign]]`` entries suppress matching findings of exactly one check. Their
``amount`` / ``variance`` / ``expected_count`` / ``expected_net`` fields are
exact-match falsifiers: once the data stops matching, the entry stops
suppressing and ``benign_stale`` reports it.
"""

from __future__ import annotations

import csv
import json
import re
import sqlite3
import time
import tomllib
from calendar import monthrange
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path
from typing import Any, Callable

from .annual_reference import load_annual_reference
from .annual_summary import compute_annual_summary
from .balances import BalancesConfig, load_balances
from .config import ServerSettings
from .database import (
    OVERRIDABLE_TRANSACTION_FIELDS,
    OVERRIDE_TUPLE_MATCH_SQL,
    DatabaseManager,
)
from .models import (
    PairTransfersRequest,
    VerifyCheckResult,
    VerifyStateRequest,
    VerifyStateResult,
    VerifyStateWritten,
)
from .monthly_summary import _FALLBACK_MORTGAGE_MERCHANT, compute_monthly_summary
from .reconciliation import (
    BALANCE_EPSILON,
    OPENING_SEED_EFFECTIVE_DATE,
    _server_today,
    reconstruct_intraday_chain,
)
from .tools import iter_candidate_files


CONFIG_FILENAME = "verify_state.toml"
TEMPLATE_RELATIVE_PATH = Path("server") / "templates" / "verify_state.template.toml"
STATUS_DATA_FILENAME = "STATUS_DATA.md"
EXPORTS_DIRNAME = "_state_exports"
OVERRIDES_EXPORT_FILENAME = "transaction_overrides.csv"
RULES_EXPORT_FILENAME = "classification_rules.csv"
# SQL NULL in the state-export CSVs (the MySQL / PostgreSQL COPY convention).
# CSV has no NULL, and NULL vs '' matters here: a rule whose primary_category is
# '' stamps '' while NULL leaves the field alone. restore_state_from_export.py
# reads the same marker back.
EXPORT_NULL = r"\N"

MAX_DETAILS = 50
# Recurring-income paychecks straddle month-end: a month's window runs through
# this many days of the next month.
RECURRING_INCOME_GRACE_DAYS = 3
STATUS_FRESHNESS_MAX_DAYS = 31

# Closed vocabulary (exact strings; not DB-enforced).
PRIMARY_CATEGORIES = (
    "income",
    "fixed_obligation",
    "variable_lifestyle",
    "transfer",
    "tax",
    "rental",
    "medical",
    "investment",
    "abnormal",
    "business_expense",
)
LIFECYCLES = ("recurring", "seasonal", "one_time", "unknown")
HOUSEHOLD_ROLES = ("jeff", "ashley", "joint", "rental", "pet", "professional", "tax")
RULE_CONFIDENCES = ("high", "medium", "low")
SPEND_RULE_CATEGORIES = (
    "fixed_obligation",
    "variable_lifestyle",
    "medical",
    "abnormal",
    "business_expense",
)
# The spend bridge's categories (monthly_summary: fixed_obligation +
# DISCRETIONARY_CATEGORIES).
SPEND_CATEGORIES = ("fixed_obligation", "variable_lifestyle", "medical", "abnormal")

STRUCTURAL_PRIORITY = 5
UI_PRIORITY_BAND = (6, 9)

# Checks whose findings a [[benign]] entry may suppress.
TRANSACTION_BENIGN_CHECKS = ("orphan_transfers", "unpaired_card_payments")
PERIOD_BENIGN_CHECKS = ("stored_reconciliation",)

DRIFT_CITATION = (
    "regenerate per DL-2026-08-03-C — after an ingest this is expected "
    "(late-posting rows), not a pipeline error"
)

_DATED_MONTHLY_FILE = re.compile(r"^(\d{4})-(\d{2})_")
_LAST_VERIFIED = re.compile(r"_Last verified:\s*(\d{4}-\d{2}-\d{2})")

_STATUS_RANK = {"pass": 0, "info": 0, "warn": 1, "fail": 2, "error": 2}


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------


class VerifyConfigError(ValueError):
    """``verify_state.toml`` is present but malformed."""


@dataclass(frozen=True)
class RecurringIncomeStream:
    name: str
    account_id: str
    description_pattern: re.Pattern[str]
    min_amount: float
    max_amount: float | None
    min_per_month: int
    expected_subcategory: str | None
    expected_household_role: str | None


@dataclass(frozen=True)
class BenignEntry:
    id: str
    check: str
    account_id: str | None
    occurred_on: str | None
    period_end: str | None
    description_pattern: re.Pattern[str] | None
    amount: float | None
    variance: float | None
    expected_count: int | None
    expected_net: float | None
    status_ref: str
    reason: str
    falsifier: str


@dataclass(frozen=True)
class VerifyConfig:
    anchor_accounts: tuple[str, ...]
    life_of_account_files: frozenset[str]
    card_payment_patterns: tuple[re.Pattern[str], ...]
    tail_gap_days: dict[str, int]
    recurring_income: tuple[RecurringIncomeStream, ...]
    spend_direction_exceptions: frozenset[str]
    taxonomy_aliases: dict[str, str]
    taxonomy_subcategories: dict[str, frozenset[str]]
    benign: tuple[BenignEntry, ...]


def template_path(settings: ServerSettings) -> Path:
    return settings.project_root / TEMPLATE_RELATIVE_PATH


def load_verify_config(settings: ServerSettings) -> tuple[VerifyConfig, str, Path]:
    """Load ``verify_state.toml``; fall back to the template when it is absent.

    Returns ``(config, source, path)`` with ``source`` ``"watch_root"`` or
    ``"template"``. Raises ``VerifyConfigError`` (with the file path and the
    parse message) when the file that is used is malformed.
    """
    watch_path = settings.watch_root / CONFIG_FILENAME
    if watch_path.exists():
        path, source = watch_path, "watch_root"
    else:
        path, source = template_path(settings), "template"
    try:
        raw = tomllib.loads(path.read_text(encoding="utf-8"))
        return _parse_config(raw), source, path
    except VerifyConfigError as exc:
        raise VerifyConfigError(f"{path}: {exc}") from exc
    except (tomllib.TOMLDecodeError, OSError, UnicodeDecodeError) as exc:
        raise VerifyConfigError(f"{path}: {exc}") from exc


def _parse_config(raw: dict[str, Any]) -> VerifyConfig:
    completeness = _table(raw, "completeness")
    rules = _table(raw, "rules")
    taxonomy = _table(raw, "taxonomy")

    tail_gap_raw = _table(completeness, "tail_gap_days", "completeness.")
    tail_gap: dict[str, int] = {}
    for account_id, days in tail_gap_raw.items():
        if isinstance(days, bool) or not isinstance(days, int) or days < 0:
            raise VerifyConfigError(
                f"completeness.tail_gap_days.{account_id}: must be a non-negative "
                f"integer, got {days!r}"
            )
        tail_gap[str(account_id)] = days

    streams: list[RecurringIncomeStream] = []
    for index, entry in enumerate(_array_of_tables(raw, "recurring_income")):
        where = f"recurring_income[{index}]"
        max_amount = entry.get("max_amount")
        min_per_month = entry.get("min_per_month", 1)
        if isinstance(min_per_month, bool) or not isinstance(min_per_month, int) or min_per_month < 1:
            raise VerifyConfigError(f"{where}.min_per_month: must be an integer >= 1")
        streams.append(
            RecurringIncomeStream(
                name=_required_str(entry, "name", where),
                account_id=_required_str(entry, "account_id", where),
                description_pattern=_regex(
                    _required_str(entry, "description_pattern", where),
                    f"{where}.description_pattern",
                ),
                min_amount=_number(entry.get("min_amount", 0.0), f"{where}.min_amount"),
                max_amount=(
                    None if max_amount is None else _number(max_amount, f"{where}.max_amount")
                ),
                min_per_month=min_per_month,
                expected_subcategory=_optional_str(entry, "expected_subcategory", where),
                expected_household_role=_optional_str(entry, "expected_household_role", where),
            )
        )

    aliases_raw = _table(taxonomy, "aliases", "taxonomy.")
    subcategories_raw = _table(taxonomy, "subcategories", "taxonomy.")
    subcategories: dict[str, frozenset[str]] = {}
    for category, values in subcategories_raw.items():
        subcategories[str(category)] = frozenset(
            _string_list(values, f"taxonomy.subcategories.{category}")
        )

    benign: list[BenignEntry] = []
    seen_ids: set[str] = set()
    for index, entry in enumerate(_array_of_tables(raw, "benign")):
        where = f"benign[{index}]"
        entry_id = _required_str(entry, "id", where)
        if entry_id in seen_ids:
            raise VerifyConfigError(f"{where}.id: duplicate id {entry_id!r}")
        seen_ids.add(entry_id)
        pattern = _optional_str(entry, "description_pattern", where)
        expected_count = entry.get("expected_count")
        if expected_count is not None and (
            isinstance(expected_count, bool) or not isinstance(expected_count, int)
        ):
            raise VerifyConfigError(f"{where}.expected_count: must be an integer")
        benign.append(
            BenignEntry(
                id=entry_id,
                check=_required_str(entry, "check", where),
                account_id=_optional_str(entry, "account_id", where),
                occurred_on=_optional_date(entry, "occurred_on", where),
                period_end=_optional_date(entry, "period_end", where),
                description_pattern=(
                    None if pattern is None else _regex(pattern, f"{where}.description_pattern")
                ),
                amount=_optional_number(entry, "amount", where),
                variance=_optional_number(entry, "variance", where),
                expected_count=expected_count,
                expected_net=_optional_number(entry, "expected_net", where),
                status_ref=str(entry.get("status_ref", "")),
                reason=str(entry.get("reason", "")),
                falsifier=str(entry.get("falsifier", "")),
            )
        )

    return VerifyConfig(
        anchor_accounts=tuple(
            _string_list(completeness.get("anchor_accounts", []), "completeness.anchor_accounts")
        ),
        life_of_account_files=frozenset(
            _string_list(
                completeness.get("life_of_account_files", []),
                "completeness.life_of_account_files",
            )
        ),
        card_payment_patterns=tuple(
            _regex(p, "completeness.card_payment_patterns")
            for p in _string_list(
                completeness.get("card_payment_patterns", []),
                "completeness.card_payment_patterns",
            )
        ),
        tail_gap_days=tail_gap,
        recurring_income=tuple(streams),
        spend_direction_exceptions=frozenset(
            _string_list(
                rules.get("spend_direction_exceptions", []),
                "rules.spend_direction_exceptions",
            )
        ),
        taxonomy_aliases={str(k): str(v) for k, v in aliases_raw.items()},
        taxonomy_subcategories=subcategories,
        benign=tuple(benign),
    )


def _table(raw: dict[str, Any], key: str, prefix: str = "") -> dict[str, Any]:
    value = raw.get(key, {})
    if not isinstance(value, dict):
        raise VerifyConfigError(f"{prefix}{key}: must be a table")
    return value


def _array_of_tables(raw: dict[str, Any], key: str) -> list[dict[str, Any]]:
    value = raw.get(key, [])
    if not isinstance(value, list) or not all(isinstance(e, dict) for e in value):
        raise VerifyConfigError(f"{key}: must be an array of tables ([[{key}]])")
    return value


def _string_list(value: Any, where: str) -> list[str]:
    if not isinstance(value, list) or not all(isinstance(v, str) for v in value):
        raise VerifyConfigError(f"{where}: must be a list of strings")
    return list(value)


def _required_str(entry: dict[str, Any], key: str, where: str) -> str:
    value = entry.get(key)
    if not isinstance(value, str) or not value.strip():
        raise VerifyConfigError(f"{where}.{key}: required string")
    return value


def _optional_str(entry: dict[str, Any], key: str, where: str) -> str | None:
    value = entry.get(key)
    if value is None:
        return None
    if not isinstance(value, str):
        raise VerifyConfigError(f"{where}.{key}: must be a string")
    return value


def _optional_date(entry: dict[str, Any], key: str, where: str) -> str | None:
    value = entry.get(key)
    if value is None:
        return None
    if isinstance(value, date) and not isinstance(value, datetime):
        return value.isoformat()
    try:
        return date.fromisoformat(str(value)).isoformat()
    except ValueError as exc:
        raise VerifyConfigError(f"{where}.{key}: unparseable date {value!r}") from exc


def _number(value: Any, where: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise VerifyConfigError(f"{where}: must be a number, got {value!r}")
    return float(value)


def _optional_number(entry: dict[str, Any], key: str, where: str) -> float | None:
    value = entry.get(key)
    return None if value is None else _number(value, f"{where}.{key}")


def _regex(pattern: str, where: str) -> re.Pattern[str]:
    try:
        return re.compile(pattern)
    except re.error as exc:
        raise VerifyConfigError(f"{where}: invalid regex {pattern!r}: {exc}") from exc


# ---------------------------------------------------------------------------
# Small pure helpers
# ---------------------------------------------------------------------------


def _cents(value: float | int | str) -> int:
    """Whole cents, rounded half-up — money is never compared as raw floats."""
    return int((Decimal(str(value)) * 100).quantize(Decimal("1"), rounding=ROUND_HALF_UP))


def _dollars(cents: int) -> float:
    return cents / 100.0


def _fmt(cents: int) -> str:
    sign = "-" if cents < 0 else ""
    return f"{sign}${abs(cents) / 100:,.2f}"


def _month_index(label: str) -> int:
    return int(label[:4]) * 12 + int(label[5:7]) - 1


def _month_label(index: int) -> str:
    year, month0 = divmod(index, 12)
    return f"{year:04d}-{month0 + 1:02d}"


def _month_start(label: str) -> date:
    return date(int(label[:4]), int(label[5:7]), 1)


def _month_end(label: str) -> date:
    year, month = int(label[:4]), int(label[5:7])
    return date(year, month, monthrange(year, month)[1])


def _is_flagged(metadata: dict[str, Any]) -> bool:
    return metadata.get("manual_override_applied") in (True, 1, "true")


def _parse_metadata(raw: str | None) -> dict[str, Any]:
    if not raw:
        return {}
    try:
        value = json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return {}
    return value if isinstance(value, dict) else {}


# ---------------------------------------------------------------------------
# Run context
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class _Tx:
    id: str
    account_id: str
    occurred_on: str
    amount: float
    direction: str
    primary_category: str | None
    subcategory: str | None
    household_role: str | None
    lifecycle: str | None
    merchant_normalized: str | None
    description_raw: str
    source_document_name: str
    transfer_group_key: str | None
    metadata: dict[str, Any]

    @property
    def cents(self) -> int:
        return _cents(self.amount)

    @property
    def running_balance(self) -> float | None:
        value = self.metadata.get("running_balance")
        try:
            return None if value is None else float(value)
        except (TypeError, ValueError):
            return None

    def brief(self) -> dict[str, Any]:
        return {
            "transaction_id": self.id,
            "account_id": self.account_id,
            "occurred_on": self.occurred_on,
            "amount": round(self.amount, 2),
            "direction": self.direction,
            "primary_category": self.primary_category,
            "description_raw": self.description_raw,
        }


@dataclass
class _Outcome:
    status: str
    summary: str
    details: list[dict[str, Any]] = field(default_factory=list)
    suppressed_by: list[str] = field(default_factory=list)


@dataclass
class _BenignEval:
    entry: BenignEntry
    matched: list[Any]
    falsified: list[str]

    @property
    def suppressing(self) -> bool:
        return bool(self.matched) and not self.falsified


@dataclass
class _MonthChain:
    month: str
    opening: float | None
    closing: float | None
    first_day: str
    last_day: str
    opening_ambiguous: bool
    closing_ambiguous: bool


class _Context:
    def __init__(
        self,
        settings: ServerSettings,
        database: DatabaseManager,
        request: VerifyStateRequest,
        connection: sqlite3.Connection,
        config: VerifyConfig,
        config_source: str,
        config_path: Path,
    ) -> None:
        self.settings = settings
        self.database = database
        self.request = request
        self.conn = connection
        self.config = config
        self.config_source = config_source
        self.config_path = config_path
        self.today = _server_today(connection)
        self._cache: dict[str, Any] = {}
        self.accounts = {
            row["id"]: dict(row)
            for row in connection.execute(
                "SELECT id, institution, account_name, account_type FROM accounts ORDER BY id"
            ).fetchall()
        }
        self.transactions = [
            _Tx(
                id=row["id"],
                account_id=row["account_id"],
                occurred_on=str(row["occurred_on"])[:10],
                amount=float(row["amount"]),
                direction=row["direction"],
                primary_category=row["primary_category"],
                subcategory=row["subcategory"],
                household_role=row["household_role"],
                lifecycle=row["lifecycle"],
                merchant_normalized=row["merchant_normalized"],
                description_raw=row["description_raw"] or "",
                source_document_name=row["source_document_name"] or "",
                transfer_group_key=row["transfer_group_key"],
                metadata=_parse_metadata(row["metadata_json"]),
            )
            for row in connection.execute(
                "SELECT id, account_id, occurred_on, amount, direction, primary_category, "
                "subcategory, household_role, lifecycle, merchant_normalized, "
                "description_raw, source_document_name, transfer_group_key, metadata_json "
                "FROM transactions ORDER BY occurred_on, id"
            ).fetchall()
        ]
        self.by_account: dict[str, list[_Tx]] = defaultdict(list)
        for tx in self.transactions:
            self.by_account[tx.account_id].append(tx)
        self.as_of, self.as_of_note = self._resolve_as_of()

    # -- caching -------------------------------------------------------------

    def cached(self, key: str, compute: Callable[[], Any]) -> Any:
        if key not in self._cache:
            self._cache[key] = compute()
        return self._cache[key]

    # -- config-dependent lazy loads (their errors surface per check) --------

    @property
    def balances(self) -> BalancesConfig:
        return self.cached("balances", lambda: load_balances(self.settings.watch_root))

    def annual_reference(self, year: int) -> Any:
        entries = self.cached(
            "annual_reference", lambda: load_annual_reference(self.settings.watch_root).entries
        )
        return next((entry for entry in entries if entry.year == year), None)

    def monthly_summary(self, label: str) -> dict[str, Any]:
        def compute() -> dict[str, Any]:
            year, month = int(label[:4]), int(label[5:7])
            return compute_monthly_summary(
                self.database,
                self.balances.wealth_bridge,
                year,
                month,
                annual_reference=self.annual_reference(year),
                mortgage=self.balances.mortgage,
            )

        return self.cached(f"monthly:{label}", compute)

    def annual_summary(self, year: int) -> dict[str, Any]:
        return self.cached(
            f"annual:{year}",
            lambda: compute_annual_summary(
                self.database,
                self.balances.wealth_bridge,
                year,
                annual_reference=self.annual_reference(year),
                mortgage=self.balances.mortgage,
            ),
        )

    def account_type(self, account_id: str) -> str | None:
        account = self.accounts.get(account_id)
        return account["account_type"] if account else None

    def account_label(self, account_id: str) -> str:
        account = self.accounts.get(account_id)
        if not account:
            return account_id
        return f"{account['institution']} · {account['account_name']}"

    # -- as-of month ---------------------------------------------------------

    def _resolve_as_of(self) -> tuple[str | None, dict[str, Any]]:
        latest: dict[str, str] = {}
        for account_id in self.config.anchor_accounts:
            rows = self.by_account.get(account_id)
            if rows:
                latest[account_id] = max(tx.occurred_on for tx in rows)[:7]
        missing = [a for a in self.config.anchor_accounts if a not in latest]
        latest_complete: str | None = None
        limiting: str | None = None
        if latest:
            limiting = min(latest, key=lambda a: (latest[a], a))
            latest_complete = latest[limiting]
        note = {
            "anchor_latest_months": latest,
            "anchor_accounts_without_rows": missing,
            "latest_complete_month": latest_complete,
            "limiting_account": limiting,
            "explicit": self.request.month is not None,
        }
        if self.request.month is not None:
            return self.request.month, note
        return latest_complete, note


# ---------------------------------------------------------------------------
# Shared analyses (cached on the context)
# ---------------------------------------------------------------------------


def _unpaired_analysis(ctx: _Context) -> dict[str, Any]:
    """Unpaired transfer rows split into balanced clusters and the rest.

    Unpaired = ``primary_category='transfer' AND transfer_group_key IS NULL``, any
    direction. Rows are bucketed by absolute cents and, within a bucket, linked
    when within ``PairTransfersRequest().date_tolerance_days`` of each other
    (single linkage). A linked component spanning two or more accounts whose
    signed sum is 0.00 is a balanced cluster: the money is internally accounted
    for even though ``pair_transfers`` could not retire it 1-to-1.
    """

    def compute() -> dict[str, Any]:
        tolerance = PairTransfersRequest().date_tolerance_days
        unpaired = [
            tx
            for tx in ctx.transactions
            if tx.primary_category == "transfer" and tx.transfer_group_key is None
        ]
        buckets: dict[int, list[_Tx]] = defaultdict(list)
        for tx in unpaired:
            buckets[abs(tx.cents)].append(tx)

        clusters: list[list[_Tx]] = []
        for bucket_rows in buckets.values():
            ordered = sorted(bucket_rows, key=lambda t: (t.occurred_on, t.id))
            component: list[_Tx] = []
            for tx in ordered:
                if component and (
                    date.fromisoformat(tx.occurred_on)
                    - date.fromisoformat(component[-1].occurred_on)
                ).days > tolerance:
                    clusters.append(component)
                    component = []
                component.append(tx)
            if component:
                clusters.append(component)

        balanced = [
            sorted(c, key=lambda t: (t.occurred_on, t.account_id, t.id))
            for c in clusters
            if len(c) >= 2
            and len({t.account_id for t in c}) >= 2
            and sum(t.cents for t in c) == 0
        ]
        balanced.sort(key=lambda c: (c[0].occurred_on, abs(c[0].cents)))
        clustered_ids = {t.id for c in balanced for t in c}
        remaining = [t for t in unpaired if t.id not in clustered_ids]
        return {
            "unpaired": unpaired,
            "balanced": balanced,
            "clustered_ids": clustered_ids,
            "remaining": remaining,
        }

    return ctx.cached("unpaired", compute)


def _card_payment_candidates(ctx: _Context) -> list[_Tx]:
    def is_card_payment(tx: _Tx) -> bool:
        account_type = ctx.account_type(tx.account_id)
        if account_type == "credit_card":
            return tx.cents > 0
        if account_type == "checking":
            return any(p.search(tx.description_raw) for p in ctx.config.card_payment_patterns)
        return False

    return ctx.cached(
        "card_payment_candidates",
        lambda: [tx for tx in _unpaired_analysis(ctx)["remaining"] if is_card_payment(tx)],
    )


def _benign_transaction_eval(ctx: _Context, check: str, findings: list[_Tx]) -> list[_BenignEval]:
    """Evaluate the [[benign]] entries for one transaction-level check.

    The rows an entry *names* are those matching its ``account_id``,
    ``description_pattern`` and ``occurred_on`` (each when given); it suppresses
    the check's findings among them. Falsifiers:

    * ``amount`` — every named row carries exactly this amount;
    * ``expected_count`` / ``expected_net`` — over every transaction with the
      entry's account and pattern, *any date, paired or not*, so a new matching
      row (a fourth ACCTVERIFY, a second Axos) falsifies the entry. Without a
      ``description_pattern`` they count the named rows instead.
    """

    def compute() -> list[_BenignEval]:
        results: list[_BenignEval] = []
        for entry in ctx.config.benign:
            if entry.check != check:
                continue

            def identity(tx: _Tx, entry: BenignEntry = entry) -> bool:
                if entry.account_id is not None and tx.account_id != entry.account_id:
                    return False
                if entry.description_pattern is not None:
                    return bool(entry.description_pattern.search(tx.description_raw))
                return entry.occurred_on is None or tx.occurred_on == entry.occurred_on

            def named(tx: _Tx, entry: BenignEntry = entry) -> bool:
                return identity(tx, entry) and (
                    entry.occurred_on is None or tx.occurred_on == entry.occurred_on
                )

            matched = [tx for tx in findings if named(tx)]
            scope = [tx for tx in ctx.transactions if identity(tx)]
            falsified: list[str] = []
            if entry.expected_count is not None and len(scope) != entry.expected_count:
                falsified.append(
                    f"expected_count {entry.expected_count}, found {len(scope)} matching rows"
                )
            if entry.expected_net is not None:
                net = sum(tx.cents for tx in scope)
                if net != _cents(entry.expected_net):
                    falsified.append(
                        f"expected_net {_fmt(_cents(entry.expected_net))}, rows net to {_fmt(net)}"
                    )
            if entry.amount is not None:
                off = sorted(
                    {_fmt(tx.cents) for tx in ctx.transactions if named(tx) and tx.cents != _cents(entry.amount)}
                )
                if off:
                    falsified.append(
                        f"amount {_fmt(_cents(entry.amount))}, found {', '.join(off)}"
                    )
            if entry.variance is not None:
                falsified.append("variance applies to period-level checks only")
            results.append(_BenignEval(entry=entry, matched=matched, falsified=falsified))
        return results

    return ctx.cached(f"benign:{check}", compute)


def _benign_period_eval(
    ctx: _Context, check: str, findings: list[dict[str, Any]]
) -> list[_BenignEval]:
    """Evaluate [[benign]] entries for a period-level check (``stored_reconciliation``).

    Selectors: ``account_id`` + ``period_end``. Falsifier: ``variance`` must equal
    the finding's variance to the cent.
    """

    def compute() -> list[_BenignEval]:
        results: list[_BenignEval] = []
        for entry in ctx.config.benign:
            if entry.check != check:
                continue
            selected = [
                f
                for f in findings
                if (entry.account_id is None or f["account_id"] == entry.account_id)
                and (entry.period_end is None or f["period_end"] == entry.period_end)
            ]
            falsified: list[str] = []
            if entry.variance is not None:
                for finding in selected:
                    variance = finding.get("variance")
                    if variance is None or _cents(variance) != _cents(entry.variance):
                        falsified.append(
                            f"variance {_fmt(_cents(entry.variance))}, found "
                            + ("none" if variance is None else _fmt(_cents(variance)))
                        )
            for name in ("expected_count", "expected_net", "amount"):
                if getattr(entry, name) is not None:
                    falsified.append(f"{name} applies to transaction-level checks only")
            results.append(_BenignEval(entry=entry, matched=selected, falsified=falsified))
        return results

    return ctx.cached(f"benign:{check}", compute)


def _suppress_transactions(
    evals: list[_BenignEval], findings: list[_Tx]
) -> tuple[list[_Tx], dict[str, list[_Tx]]]:
    suppressed: dict[str, list[_Tx]] = {}
    suppressed_ids: set[str] = set()
    for ev in evals:
        if ev.suppressing:
            rows = [tx for tx in ev.matched if tx.id not in suppressed_ids]
            if rows:
                suppressed[ev.entry.id] = rows
                suppressed_ids.update(tx.id for tx in rows)
    return [tx for tx in findings if tx.id not in suppressed_ids], suppressed


def _orphan_evaluation(ctx: _Context) -> tuple[list[_Tx], dict[str, list[_Tx]]]:
    remaining = _unpaired_analysis(ctx)["remaining"]
    evals = _benign_transaction_eval(ctx, "orphan_transfers", remaining)
    return _suppress_transactions(evals, remaining)


def _account_chains(ctx: _Context) -> dict[str, Any]:
    """Month-boundary running-balance chains for the self-anchoring accounts.

    Self-anchoring = every row carries ``metadata_json.running_balance``. For each
    active month the opening is the first day's chain opening and the closing the
    last day's chain closing, from ``reconstruct_intraday_chain`` (row order is
    never consulted, so forward- and reverse-chronological exports behave alike).
    """

    def compute() -> dict[str, Any]:
        chains: dict[str, list[_MonthChain]] = {}
        mixed: list[str] = []
        for account_id, rows in sorted(ctx.by_account.items()):
            with_balance = [tx for tx in rows if tx.running_balance is not None]
            if not with_balance:
                continue
            if len(with_balance) != len(rows):
                mixed.append(account_id)
                continue
            by_day: dict[str, list[_Tx]] = defaultdict(list)
            for tx in rows:
                by_day[tx.occurred_on].append(tx)
            by_month: dict[str, list[str]] = defaultdict(list)
            for day in sorted(by_day):
                by_month[day[:7]].append(day)
            months: list[_MonthChain] = []
            for month in sorted(by_month):
                first_day, last_day = by_month[month][0], by_month[month][-1]
                first = _day_chain(by_day[first_day])
                last = first if last_day == first_day else _day_chain(by_day[last_day])
                months.append(
                    _MonthChain(
                        month=month,
                        opening=first.opening,
                        closing=last.closing,
                        first_day=first_day,
                        last_day=last_day,
                        opening_ambiguous=first.ambiguous,
                        closing_ambiguous=last.ambiguous,
                    )
                )
            chains[account_id] = months
        return {"chains": chains, "mixed": mixed}

    return ctx.cached("chains", compute)


def _day_chain(rows: list[_Tx]) -> Any:
    return reconstruct_intraday_chain(
        [{"amount": tx.amount, "running_balance": tx.running_balance} for tx in rows]
    )


def _mortgage_merchant(ctx: _Context) -> str:
    mortgage = ctx.balances.mortgage
    return mortgage.merchant_match if mortgage is not None else _FALLBACK_MORTGAGE_MERCHANT


def _mortgage_months(ctx: _Context) -> list[str]:
    def compute() -> list[str]:
        merchant = _mortgage_merchant(ctx)
        return sorted(
            {
                tx.occurred_on[:7]
                for tx in ctx.transactions
                if tx.merchant_normalized == merchant
                and tx.primary_category == "fixed_obligation"
                and tx.direction != "inflow"
            }
        )

    return ctx.cached("mortgage_months", compute)


# ---------------------------------------------------------------------------
# Checks — integrity
# ---------------------------------------------------------------------------


def check_duplicate_keys(ctx: _Context) -> _Outcome:
    rows = ctx.conn.execute(
        "SELECT account_id, source_record_key, COUNT(*) AS n FROM transactions "
        "GROUP BY account_id, source_record_key HAVING COUNT(*) > 1 "
        "ORDER BY account_id, source_record_key"
    ).fetchall()
    details = [dict(row) for row in rows]
    if details:
        return _Outcome("fail", f"{len(details)} duplicated (account_id, source_record_key) keys", details)
    return _Outcome("pass", "0 duplicated (account_id, source_record_key) keys")


def check_cross_file_duplicates(ctx: _Context) -> _Outcome:
    rows = ctx.conn.execute(
        "SELECT account_id, occurred_on, ROUND(amount, 2) AS amount, description_raw, "
        "COUNT(*) AS n, COUNT(DISTINCT source_document_name) AS files, "
        "GROUP_CONCAT(DISTINCT source_document_name) AS documents "
        "FROM transactions GROUP BY account_id, occurred_on, ROUND(amount, 2), description_raw "
        "HAVING COUNT(*) > 1 ORDER BY occurred_on, account_id"
    ).fetchall()
    cross = [dict(row) for row in rows if row["files"] > 1]
    same_file = [row for row in rows if row["files"] == 1]
    same_file_detail = {
        "kind": "same_file_repeats",
        "level": "info",
        "groups": len(same_file),
        "rows": sum(int(row["n"]) for row in same_file),
        "note": "same-file repeats are genuine (e.g. 4 x $15 Fidelity MoneyLine a month)",
    }
    tail = f"{len(same_file)} same-file repeat groups are genuine"
    if cross:
        details = [{"kind": "cross_file", **row} for row in cross] + [same_file_detail]
        return _Outcome("fail", f"{len(cross)} cross-file duplicate groups ({tail})", details)
    return _Outcome("pass", f"0 cross-file duplicate groups ({tail})", [same_file_detail])


def check_vocabulary(ctx: _Context) -> _Outcome:
    details: list[dict[str, Any]] = []
    for column, allowed in (
        ("primary_category", PRIMARY_CATEGORIES),
        ("lifecycle", LIFECYCLES),
        ("household_role", HOUSEHOLD_ROLES),
    ):
        counts = Counter(getattr(tx, column) for tx in ctx.transactions)
        for value, n in sorted(counts.items(), key=lambda kv: str(kv[0])):
            if value not in allowed:
                details.append(
                    {"table": "transactions", "field": column, "value": value, "rows": n}
                )
    for rule in ctx.conn.execute(
        "SELECT id, primary_category, lifecycle, household_role, confidence "
        "FROM classification_rules ORDER BY id"
    ).fetchall():
        for column, allowed in (
            ("primary_category", PRIMARY_CATEGORIES),
            ("lifecycle", LIFECYCLES),
            ("household_role", HOUSEHOLD_ROLES),
        ):
            value = rule[column]
            if value is not None and value not in allowed:
                details.append(
                    {"table": "classification_rules", "rule_id": rule["id"], "field": column, "value": value}
                )
        if rule["confidence"] not in RULE_CONFIDENCES:
            details.append(
                {
                    "table": "classification_rules",
                    "rule_id": rule["id"],
                    "field": "confidence",
                    "value": rule["confidence"],
                }
            )
    if details:
        rows = sum(d.get("rows", 0) for d in details)
        rules = sum(1 for d in details if d["table"] == "classification_rules")
        return _Outcome(
            "fail",
            f"{rows} transaction rows and {rules} rule fields outside the closed vocabulary",
            details,
        )
    return _Outcome("pass", "every transaction and rule is inside the closed vocabulary")


# ---------------------------------------------------------------------------
# Checks — rules
# ---------------------------------------------------------------------------


def _rules(ctx: _Context) -> list[dict[str, Any]]:
    return ctx.cached(
        "rules",
        lambda: [
            dict(row)
            for row in ctx.conn.execute(
                "SELECT id, pattern, account_filter, direction_filter, primary_category, "
                "subcategory, confidence, priority FROM classification_rules "
                "ORDER BY priority, created_at, id"
            ).fetchall()
        ],
    )


def _rule_brief(rule: dict[str, Any]) -> dict[str, Any]:
    return {
        "rule_id": rule["id"],
        "priority": rule["priority"],
        "pattern": rule["pattern"],
        "primary_category": rule["primary_category"],
        "direction_filter": rule["direction_filter"],
    }


def check_income_rule_direction(ctx: _Context) -> _Outcome:
    bad = [
        _rule_brief(r)
        for r in _rules(ctx)
        if r["primary_category"] == "income" and r["direction_filter"] != "inflow"
    ]
    if bad:
        return _Outcome(
            "warn",
            f"{len(bad)} income rules without direction_filter='inflow' "
            "(an outflow or transfer matching them would be stamped income)",
            bad,
        )
    return _Outcome("pass", "every income rule has direction_filter='inflow'")


def check_spend_rule_direction(ctx: _Context) -> _Outcome:
    exceptions = ctx.config.spend_direction_exceptions
    bad = [
        _rule_brief(r)
        for r in _rules(ctx)
        if r["primary_category"] in SPEND_RULE_CATEGORIES
        and r["direction_filter"] != "outflow"
        and r["id"] not in exceptions
    ]
    if bad:
        return _Outcome(
            "warn",
            f"{len(bad)} spend rules without direction_filter='outflow' outside "
            "[rules].spend_direction_exceptions",
            bad,
        )
    return _Outcome(
        "pass",
        f"every spend rule has direction_filter='outflow' or is a documented exception "
        f"({len(exceptions)} listed)",
    )


def check_rule_bands(ctx: _Context) -> _Outcome:
    details: list[dict[str, Any]] = []
    failures = 0
    for rule in _rules(ctx):
        try:
            re.compile(rule["pattern"], re.IGNORECASE)
        except re.error as exc:
            failures += 1
            details.append({**_rule_brief(rule), "problem": f"pattern does not compile: {exc}"})
        priority = rule["priority"]
        if priority == STRUCTURAL_PRIORITY and rule["primary_category"] != "transfer":
            failures += 1
            details.append(
                {**_rule_brief(rule), "problem": "priority 5 is the structural transfer band"}
            )
        elif UI_PRIORITY_BAND[0] <= priority <= UI_PRIORITY_BAND[1]:
            details.append({**_rule_brief(rule), "level": "info", "problem": "UI manual band (6-9)"})
        elif priority < STRUCTURAL_PRIORITY:
            details.append(
                {**_rule_brief(rule), "level": "info", "problem": "priority below every documented band"}
            )
    ui = sum(1 for d in details if d.get("problem") == "UI manual band (6-9)")
    structural = sum(1 for r in _rules(ctx) if r["priority"] == STRUCTURAL_PRIORITY)
    tail = f"{structural} at priority 5, {ui} in the UI band 6-9"
    if failures:
        return _Outcome("fail", f"{failures} rule band / pattern problems ({tail})", details)
    return _Outcome("info" if details else "pass", f"rule bands clean ({tail})", details)


# ---------------------------------------------------------------------------
# Checks — overrides
# ---------------------------------------------------------------------------


def check_orphaned_overrides(ctx: _Context) -> _Outcome:
    rows = ctx.conn.execute(
        "SELECT o.id, o.account_id, o.occurred_on, o.amount, o.description_raw, "
        "o.primary_category, o.subcategory, o.lifecycle, o.note FROM transaction_overrides o "
        f"WHERE NOT EXISTS (SELECT 1 FROM transactions WHERE {OVERRIDE_TUPLE_MATCH_SQL}) "
        "ORDER BY o.occurred_on, o.id"
    ).fetchall()
    details = [{**dict(row), "override_id": row["id"]} for row in rows]
    for d in details:
        d.pop("id", None)
    total = ctx.conn.execute("SELECT COUNT(*) AS n FROM transaction_overrides").fetchone()["n"]
    if details:
        return _Outcome(
            "warn",
            f"{len(details)} of {total} overrides match no transaction by "
            "(account_id, occurred_on, amount, description_raw)",
            details,
        )
    return _Outcome("pass", f"all {total} overrides match a transaction")


def check_override_in_effect(ctx: _Context) -> _Outcome:
    fields = ", ".join(
        f"transactions.{f} AS t_{f}, o.{f} AS o_{f}" for f in OVERRIDABLE_TRANSACTION_FIELDS
    )
    rows = ctx.conn.execute(
        f"SELECT transactions.id AS transaction_id, transactions.account_id, "
        f"transactions.occurred_on, transactions.amount, transactions.description_raw, "
        f"transactions.metadata_json, o.id AS override_id, {fields} "
        f"FROM transactions JOIN transaction_overrides o ON {OVERRIDE_TUPLE_MATCH_SQL} "
        f"ORDER BY transactions.occurred_on, transactions.id"
    ).fetchall()
    details: list[dict[str, Any]] = []
    matched_ids: set[str] = set()
    for row in rows:
        matched_ids.add(row["transaction_id"])
        diffs = {
            f: {"override": row[f"o_{f}"], "current": row[f"t_{f}"]}
            for f in OVERRIDABLE_TRANSACTION_FIELDS
            if row[f"o_{f}"] is not None and row[f"o_{f}"] != row[f"t_{f}"]
        }
        flagged = _is_flagged(_parse_metadata(row["metadata_json"]))
        if diffs or not flagged:
            details.append(
                {
                    "transaction_id": row["transaction_id"],
                    "override_id": row["override_id"],
                    "account_id": row["account_id"],
                    "occurred_on": row["occurred_on"],
                    "amount": row["amount"],
                    "description_raw": row["description_raw"],
                    "differs": diffs,
                    "manual_override_applied": flagged,
                }
            )
    not_in_effect = len(details)
    flagged_unmatched = [
        tx
        for tx in ctx.transactions
        if _is_flagged(tx.metadata) and tx.id not in matched_ids
    ]
    details.extend(
        {**tx.brief(), "level": "info", "problem": "flagged manual_override_applied but matches no override tuple"}
        for tx in flagged_unmatched
    )
    info_tail = (
        f"; {len(flagged_unmatched)} flagged rows match no override tuple (info)"
        if flagged_unmatched
        else ""
    )
    if not_in_effect:
        return _Outcome(
            "warn",
            f"{not_in_effect} of {len(matched_ids)} override-governed rows are not in effect "
            f"(a field differs or the manual_override_applied flag is missing){info_tail}",
            details,
        )
    return _Outcome(
        "pass", f"all {len(matched_ids)} override-governed rows show their override{info_tail}", details
    )


# ---------------------------------------------------------------------------
# Checks — completeness
# ---------------------------------------------------------------------------


def check_recurring_income(ctx: _Context) -> _Outcome:
    """Each stream's months are filled greedily in date order from the account's
    first month to the as-of month. A month's window is its own days plus the
    first ``RECURRING_INCOME_GRACE_DAYS`` of the next month; each row satisfies at
    most one month; a month consumes up to ``min_per_month`` rows. Consumption is
    what stops a paycheck dated the 2nd from satisfying two months."""
    if ctx.as_of is None:
        return _Outcome("info", "no as-of month (no anchor-account rows); nothing to check")
    as_of_index = _month_index(ctx.as_of)
    details: list[dict[str, Any]] = []
    fail_months = warn_months = 0
    stream_notes: list[str] = []
    for stream in ctx.config.recurring_income:
        account_rows = ctx.by_account.get(stream.account_id, [])
        if not account_rows:
            fail_months += 1
            details.append(
                {"stream": stream.name, "month": ctx.as_of, "found": 0,
                 "required": stream.min_per_month, "severity": "fail",
                 "problem": f"account {stream.account_id} has no rows"}
            )
            stream_notes.append(f"{stream.name}: account has no rows")
            continue
        first_index = _month_index(account_rows[0].occurred_on[:7])
        window_end = _month_end(ctx.as_of) + timedelta(days=RECURRING_INCOME_GRACE_DAYS)
        start = _month_start(_month_label(first_index)).isoformat()
        matching = [
            tx
            for tx in account_rows
            if tx.cents > 0
            and stream.description_pattern.search(tx.description_raw)
            and tx.cents >= _cents(stream.min_amount)
            and (stream.max_amount is None or tx.cents <= _cents(stream.max_amount))
            and start <= tx.occurred_on <= window_end.isoformat()
        ]
        consumed: set[str] = set()
        short: list[str] = []
        for index in range(first_index, as_of_index + 1):
            label = _month_label(index)
            lo = _month_start(label).isoformat()
            hi = (_month_end(label) + timedelta(days=RECURRING_INCOME_GRACE_DAYS)).isoformat()
            taken = [
                tx for tx in matching if tx.id not in consumed and lo <= tx.occurred_on <= hi
            ][: stream.min_per_month]
            consumed.update(tx.id for tx in taken)
            if len(taken) < stream.min_per_month:
                severity = "fail" if index == as_of_index else "warn"
                if severity == "fail":
                    fail_months += 1
                else:
                    warn_months += 1
                short.append(label)
                details.append(
                    {"stream": stream.name, "month": label, "found": len(taken),
                     "required": stream.min_per_month, "severity": severity,
                     "window": f"{lo}..{hi}"}
                )
        months_checked = max(0, as_of_index - first_index + 1)
        stream_notes.append(
            f"{stream.name}: {months_checked - len(short)}/{months_checked} months"
            + (f" (short: {', '.join(short)})" if short else "")
        )
        for tx in matching:
            drift_sub = (
                stream.expected_subcategory is not None
                and tx.subcategory != stream.expected_subcategory
            )
            drift_role = (
                stream.expected_household_role is not None
                and tx.household_role != stream.expected_household_role
            )
            if drift_sub or drift_role:
                details.append(
                    {"kind": "label_drift", "level": "info", "stream": stream.name,
                     **tx.brief(), "subcategory": tx.subcategory,
                     "household_role": tx.household_role,
                     "expected_subcategory": stream.expected_subcategory,
                     "expected_household_role": stream.expected_household_role}
                )
    drift = sum(1 for d in details if d.get("kind") == "label_drift")
    summary = "; ".join(stream_notes) + (f"; {drift} rows with label drift (info)" if drift else "")
    if fail_months:
        return _Outcome("fail", f"as-of month {ctx.as_of} short: " + summary, details)
    if warn_months:
        return _Outcome("warn", summary, details)
    return _Outcome("pass", summary, details)


def _dated_monthly_files(ctx: _Context) -> list[dict[str, Any]]:
    """Dated monthly files (``YYYY-MM_*``) of the tail-gap accounts, from the DB."""

    def compute() -> list[dict[str, Any]]:
        out = []
        for row in ctx.conn.execute(
            "SELECT source_document_name AS file, account_id, MAX(occurred_on) AS last_row, "
            "COUNT(*) AS n FROM transactions GROUP BY source_document_name, account_id "
            "ORDER BY source_document_name"
        ).fetchall():
            name = row["file"] or ""
            match = _DATED_MONTHLY_FILE.match(name)
            if not match or name in ctx.config.life_of_account_files:
                continue
            if row["account_id"] not in ctx.config.tail_gap_days:
                continue
            out.append(
                {"file": name, "account_id": row["account_id"],
                 "month": f"{match.group(1)}-{match.group(2)}",
                 "last_row": str(row["last_row"])[:10], "rows": int(row["n"])}
            )
        return out

    return ctx.cached("dated_files", compute)


def check_tail_gap(ctx: _Context) -> _Outcome:
    if ctx.as_of is None:
        return _Outcome("info", "no as-of month (no anchor-account rows); nothing to check")
    details: list[dict[str, Any]] = []
    checked = 0
    for item in _dated_monthly_files(ctx):
        if item["month"] > ctx.as_of:
            continue
        checked += 1
        gap = (_month_end(item["month"]) - date.fromisoformat(item["last_row"])).days
        threshold = ctx.config.tail_gap_days[item["account_id"]]
        if gap > threshold:
            details.append({**item, "gap_days": gap, "threshold_days": threshold})
    for account_id in sorted(ctx.config.tail_gap_days):
        if not any(tx.occurred_on[:7] == ctx.as_of for tx in ctx.by_account.get(account_id, [])):
            details.append(
                {"account_id": account_id, "month": ctx.as_of,
                 "problem": "no rows at all for the as-of month"}
            )
    if details:
        return _Outcome(
            "warn",
            f"{len(details)} tail gaps across {checked} dated monthly files "
            f"(export likely truncated before month-end posting)",
            details,
        )
    return _Outcome("pass", f"{checked} dated monthly files reach within their tail-gap threshold")


def check_early_download(ctx: _Context) -> _Outcome:
    if ctx.as_of is None:
        return _Outcome("info", "no as-of month (no anchor-account rows); nothing to check")
    month_end = _month_end(ctx.as_of)
    on_disk: dict[str, list[Path]] = defaultdict(list)
    for path in iter_candidate_files(ctx.settings.watch_root):
        on_disk[path.name].append(path)
    details: list[dict[str, Any]] = []
    early = checked = 0
    files = [f for f in _dated_monthly_files(ctx) if f["month"] == ctx.as_of]
    for account_id in sorted(ctx.config.tail_gap_days):
        account_files = [f for f in files if f["account_id"] == account_id]
        if not account_files:
            details.append(
                {"account_id": account_id, "month": ctx.as_of, "level": "info",
                 "note": "no dated as-of-month file in the DB for this account"}
            )
        for item in account_files:
            paths = on_disk.get(item["file"], [])
            if not paths:
                details.append(
                    {**item, "level": "info",
                     "note": "file not found under the watch root (archived?); skipped"}
                )
                continue
            checked += 1
            path = paths[0]
            modified = datetime.fromtimestamp(path.stat().st_mtime).date()
            entry = {**item, "path": path.relative_to(ctx.settings.watch_root).as_posix(),
                     "modified_on": modified.isoformat(), "month_end": month_end.isoformat()}
            if len(paths) > 1:
                entry["duplicates"] = [
                    p.relative_to(ctx.settings.watch_root).as_posix() for p in paths[1:]
                ]
            if modified <= month_end:
                early += 1
                details.append({**entry, "problem": "downloaded on or before month-end"})
            elif len(paths) > 1:
                details.append({**entry, "level": "info", "note": "same file name in more than one place"})
    if early:
        return _Outcome(
            "warn",
            f"{early} as-of-month files were downloaded on or before {month_end} "
            "(before month-end posting settled)",
            details,
        )
    return _Outcome("pass", f"{checked} as-of-month files downloaded after {month_end}", details)


def _unpaired_card_payment_evaluation(ctx: _Context) -> tuple[list[_Tx], dict[str, list[_Tx]]]:
    candidates = _card_payment_candidates(ctx)
    evals = _benign_transaction_eval(ctx, "unpaired_card_payments", candidates)
    return _suppress_transactions(evals, candidates)


def check_unpaired_card_payments(ctx: _Context) -> _Outcome:
    remaining, suppressed = _unpaired_card_payment_evaluation(ctx)
    details = [
        {**tx.brief(), "account_type": ctx.account_type(tx.account_id)} for tx in remaining
    ]
    suppressed_count = sum(len(rows) for rows in suppressed.values())
    tail = f"{suppressed_count} benign-suppressed" if suppressed_count else "none benign-suppressed"
    if remaining:
        return _Outcome(
            "fail",
            f"{len(remaining)} unpaired card payments ({tail}) — the funding "
            "account's export is likely short",
            details,
            sorted(suppressed),
        )
    return _Outcome("pass", f"0 unpaired card payments ({tail})", [], sorted(suppressed))


# ---------------------------------------------------------------------------
# Checks — balances
# ---------------------------------------------------------------------------


def check_self_anchor_chain(ctx: _Context) -> _Outcome:
    analysis = _account_chains(ctx)
    chains: dict[str, list[_MonthChain]] = analysis["chains"]
    details: list[dict[str, Any]] = []
    breaks = ambiguous = 0
    seed_month = _month_label(_month_index(OPENING_SEED_EFFECTIVE_DATE.isoformat()[:7]) + 1)
    for account_id, months in chains.items():
        account = ctx.accounts.get(account_id, {"institution": ""})
        entry = ctx.balances.lookup(account_id=account_id, institution=account.get("institution", ""))
        seed_checked = False
        previous: _MonthChain | None = None
        for current in months:
            if current.opening_ambiguous:
                ambiguous += 1
                details.append(
                    {"account_id": account_id, "month": current.month, "day": current.first_day,
                     "severity": "warn", "problem": "ambiguous intraday chain on the month's first day"}
                )
            if current.closing_ambiguous and current.last_day != current.first_day:
                ambiguous += 1
                details.append(
                    {"account_id": account_id, "month": current.month, "day": current.last_day,
                     "severity": "warn", "problem": "ambiguous intraday chain on the month's last day"}
                )
            if not seed_checked and current.month >= seed_month:
                seed_checked = True
                if entry.opening_balance is None:
                    details.append(
                        {"account_id": account_id, "month": current.month, "level": "info",
                         "note": "no balances.toml opening balance; seed not checked"}
                    )
                elif current.opening is not None and not current.opening_ambiguous and abs(
                    current.opening - entry.opening_balance
                ) > BALANCE_EPSILON:
                    breaks += 1
                    details.append(
                        {"account_id": account_id, "month": current.month, "severity": "fail",
                         "problem": "first month's opening differs from the balances.toml opening",
                         "opening": round(current.opening, 2),
                         "expected": round(entry.opening_balance, 2),
                         "delta": round(current.opening - entry.opening_balance, 2)}
                    )
            if (
                previous is not None
                and current.opening is not None
                and previous.closing is not None
                and not current.opening_ambiguous
                and not previous.closing_ambiguous
                and abs(current.opening - previous.closing) > BALANCE_EPSILON
            ):
                breaks += 1
                details.append(
                    {"account_id": account_id, "month": current.month, "severity": "fail",
                     "problem": "month opening does not continue the prior active month's closing",
                     "opening": round(current.opening, 2), "prior_month": previous.month,
                     "prior_closing": round(previous.closing, 2),
                     "delta": round(current.opening - previous.closing, 2)}
                )
            previous = current
    for account_id in analysis["mixed"]:
        details.append(
            {"account_id": account_id, "level": "info",
             "note": "running_balance on some rows only; not chain-checked"}
        )
    names = ", ".join(ctx.account_label(a) for a in chains) or "none"
    month_count = sum(len(m) for m in chains.values())
    if breaks:
        return _Outcome("fail", f"{breaks} running-balance breaks ({names})", details)
    if ambiguous:
        return _Outcome("warn", f"{ambiguous} ambiguous month-boundary day chains ({names})", details)
    return _Outcome(
        "pass", f"{len(chains)} self-anchoring accounts chain across {month_count} account-months ({names})",
        details,
    )


def _as_of_chain_closing(months: list[_MonthChain], as_of: str) -> _MonthChain | None:
    eligible = [m for m in months if m.month <= as_of]
    return eligible[-1] if eligible else None


def _reconciliation_findings(ctx: _Context) -> dict[str, Any]:
    """Stored-reconciliation findings before benign suppression, plus info rows."""

    def compute() -> dict[str, Any]:
        findings: list[dict[str, Any]] = []
        info: list[dict[str, Any]] = []
        passes: list[str] = []
        as_of = ctx.as_of
        chains = _account_chains(ctx)["chains"]
        periods = [
            dict(row)
            for row in ctx.conn.execute(
                "SELECT account_id, period_start, period_end, statement_closing_balance, "
                "closing_balance_source, computed_closing_balance, variance_amount "
                "FROM reconciliation_periods ORDER BY account_id, period_end, period_start"
            ).fetchall()
        ]
        by_account: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for period in periods:
            by_account[period["account_id"]].append(period)

        # Self-anchoring accounts: the as-of month's stored row.
        if as_of is not None:
            start, end = _month_start(as_of).isoformat(), _month_end(as_of).isoformat()
            for account_id, months in chains.items():
                row = next(
                    (p for p in by_account.get(account_id, [])
                     if p["period_start"] == start and p["period_end"] == end),
                    None,
                )
                base = {"account_id": account_id, "period_end": end, "kind": "self_anchoring"}
                chain = _as_of_chain_closing(months, as_of)
                if row is None:
                    findings.append({**base, "variance": None,
                                     "problem": "no reconciliation_periods row for the as-of month; run reconcile_periods"})
                    continue
                problems = []
                if row["variance_amount"] is None:
                    problems.append("variance_amount is null (no statement closing)")
                elif _cents(row["variance_amount"]) != 0:
                    problems.append(f"variance {_fmt(_cents(row['variance_amount']))}")
                if chain is None or chain.closing is None or chain.closing_ambiguous:
                    problems.append("no unambiguous running-balance closing to compare")
                elif row["computed_closing_balance"] is None or _cents(
                    row["computed_closing_balance"]
                ) != _cents(chain.closing):
                    problems.append(
                        "computed_closing_balance "
                        + ("null" if row["computed_closing_balance"] is None
                           else _fmt(_cents(row["computed_closing_balance"])))
                        + f" != chain closing {_fmt(_cents(chain.closing))} (stale; rerun reconcile_periods)"
                    )
                if problems:
                    findings.append({**base, "variance": row["variance_amount"],
                                     "computed_closing_balance": row["computed_closing_balance"],
                                     "chain_closing": None if chain is None else chain.closing,
                                     "problem": "; ".join(problems)})
                else:
                    passes.append(account_id)

        # Checkpoint-anchored cash accounts (no running balance, not a card).
        for account_id, account in ctx.accounts.items():
            if account_id in chains or account_id in _account_chains(ctx)["mixed"]:
                continue
            if account["account_type"] == "credit_card":
                continue
            entry = ctx.balances.lookup(account_id=account_id, institution=account["institution"])
            account_periods = by_account.get(account_id, [])
            for checkpoint in sorted(entry.statement_closings):
                d = checkpoint.isoformat()
                balance = entry.statement_closings[checkpoint]
                base = {"account_id": account_id, "period_end": d, "kind": "checkpoint",
                        "checkpoint_balance": round(balance, 2)}
                at_d = [p for p in account_periods if p["period_end"] == d]
                with_variance = next((p for p in at_d if p["variance_amount"] is not None), None)
                if with_variance is not None:
                    variance = with_variance["variance_amount"]
                    if _cents(variance) != 0:
                        findings.append({**base, "variance": variance,
                                         "period_start": with_variance["period_start"],
                                         "problem": f"checkpoint variance {_fmt(_cents(variance))}"})
                    else:
                        passes.append(f"{account_id}@{d}")
                    continue
                if not at_d:
                    findings.append({**base, "variance": None,
                                     "problem": "checkpoint not materialised in reconciliation_periods; "
                                                "run refresh_hysa_gate / reconcile_periods"})
                    continue
                # A single-day row (seeded for a checkpoint on the 1st) carries no
                # computed side: verify it against the period ending the day before
                # — its computed closing, else its statement closing (the Dec-2025
                # seed row, or an earlier checkpoint) — plus any rows dated D.
                prior_end = (checkpoint - timedelta(days=1)).isoformat()
                prior_closing = next(
                    (
                        closing
                        for p in reversed(account_periods)
                        if p["period_end"] == prior_end
                        for closing in (
                            p["computed_closing_balance"]
                            if p["computed_closing_balance"] is not None
                            else p["statement_closing_balance"],
                        )
                        if closing is not None
                    ),
                    None,
                )
                if prior_closing is None:
                    findings.append({**base, "variance": None,
                                     "problem": f"no period ending {prior_end} with a closing to verify against"})
                    continue
                same_day = sum(tx.cents for tx in ctx.by_account.get(account_id, []) if tx.occurred_on == d)
                expected = _cents(prior_closing) + same_day
                variance_cents = _cents(balance) - expected
                if variance_cents != 0:
                    findings.append({**base, "variance": _dollars(variance_cents),
                                     "computed_balance": _dollars(expected),
                                     "problem": f"checkpoint differs from the computed balance by {_fmt(variance_cents)}"})
                else:
                    passes.append(f"{account_id}@{d}")

        # Credit cards: info only.
        for account_id, account in ctx.accounts.items():
            if account["account_type"] != "credit_card":
                continue
            row = None
            if as_of is not None:
                start, end = _month_start(as_of).isoformat(), _month_end(as_of).isoformat()
                row = next(
                    (p for p in by_account.get(account_id, [])
                     if p["period_start"] == start and p["period_end"] == end),
                    None,
                )
            view = ctx.conn.execute(
                "SELECT computed_balance, anchor_date FROM v_computed_balance WHERE account_id = ?",
                (account_id,),
            ).fetchone()
            info.append(
                {"account_id": account_id, "level": "info", "kind": "credit_card",
                 "as_of_month": as_of,
                 "chain_close": None if row is None else row["computed_closing_balance"],
                 "v_computed_balance": None if view is None else view["computed_balance"],
                 "note": "reported side by side; the view's liability sign convention is a Cowork decision"}
            )
        return {"findings": findings, "info": info, "passes": passes}

    return ctx.cached("reconciliation", compute)


def check_stored_reconciliation(ctx: _Context) -> _Outcome:
    analysis = _reconciliation_findings(ctx)
    findings = analysis["findings"]
    evals = _benign_period_eval(ctx, "stored_reconciliation", findings)
    suppressed_ids: list[str] = []
    suppressed_keys: set[tuple[str, str]] = set()
    for ev in evals:
        if ev.suppressing:
            suppressed_ids.append(ev.entry.id)
            suppressed_keys.update((f["account_id"], f["period_end"]) for f in ev.matched)
    open_findings = [f for f in findings if (f["account_id"], f["period_end"]) not in suppressed_keys]
    suppressed = [
        {**f, "level": "info", "suppressed_by": next(
            ev.entry.id for ev in evals if ev.suppressing and f in ev.matched)}
        for f in findings
        if (f["account_id"], f["period_end"]) in suppressed_keys
    ]
    details = open_findings + suppressed + analysis["info"]
    cards = len(analysis["info"])
    summary_tail = (
        f"{len(analysis['passes'])} rows/checkpoints agree, {len(suppressed)} benign-suppressed, "
        f"{cards} credit cards reported (info)"
    )
    if open_findings:
        return _Outcome("warn", f"{len(open_findings)} reconciliation problems; " + summary_tail,
                        details, sorted(suppressed_ids))
    return _Outcome("pass", summary_tail, details, sorted(suppressed_ids))


# ---------------------------------------------------------------------------
# Checks — transfers
# ---------------------------------------------------------------------------


def check_balanced_clusters(ctx: _Context) -> _Outcome:
    balanced = _unpaired_analysis(ctx)["balanced"]
    details = [
        {"amount": _dollars(abs(cluster[0].cents)),
         "first": cluster[0].occurred_on, "last": cluster[-1].occurred_on,
         "accounts": sorted({t.account_id for t in cluster}),
         "legs": [t.brief() for t in cluster]}
        for cluster in balanced
    ]
    legs = sum(len(c) for c in balanced)
    if balanced:
        return _Outcome(
            "info",
            f"{len(balanced)} balanced clusters, {legs} legs (unpaired but net to 0.00 across accounts)",
            details,
        )
    return _Outcome("pass", "0 balanced clusters")


def check_orphan_transfers(ctx: _Context) -> _Outcome:
    analysis = _unpaired_analysis(ctx)
    remaining, suppressed = _orphan_evaluation(ctx)
    clustered = len(analysis["clustered_ids"])
    suppressed_count = sum(len(rows) for rows in suppressed.values())
    summary = (
        f"{len(remaining)} unexplained unpaired transfer rows "
        f"({suppressed_count} benign-suppressed, {clustered} in balanced clusters)"
    )
    details = [tx.brief() for tx in remaining]
    if remaining:
        return _Outcome("warn", summary, details, sorted(suppressed))
    return _Outcome("pass", summary, [], sorted(suppressed))


def check_pair_integrity(ctx: _Context) -> _Outcome:
    groups: dict[str, list[_Tx]] = defaultdict(list)
    for tx in ctx.transactions:
        if tx.transfer_group_key is not None:
            groups[tx.transfer_group_key].append(tx)
    transfer_rows = sum(1 for tx in ctx.transactions if tx.primary_category == "transfer")
    if not groups and transfer_rows:
        return _Outcome(
            "fail",
            f"0 transfer groups but {transfer_rows} transfer rows — run pair_transfers first",
            [],
        )
    details: list[dict[str, Any]] = []
    for key, rows in sorted(groups.items()):
        problems = []
        if len(rows) != 2:
            problems.append(f"{len(rows)} rows (expected 2)")
        else:
            a, b = rows
            if a.account_id == b.account_id:
                problems.append("both legs on one account")
            if not (a.cents > 0 > b.cents or b.cents > 0 > a.cents):
                problems.append("legs do not have opposite signs")
            if a.cents + b.cents != 0:
                problems.append(f"legs sum to {_fmt(a.cents + b.cents)}")
        off_category = [t.id for t in rows if t.primary_category != "transfer"]
        if off_category:
            problems.append("leg not primary_category='transfer': " + ", ".join(off_category))
        if problems:
            details.append({"transfer_group_key": key, "problems": problems,
                            "legs": [t.brief() for t in rows]})
    if details:
        return _Outcome("fail", f"{len(details)} of {len(groups)} transfer groups are malformed", details)
    return _Outcome("pass", f"{len(groups)} transfer groups, each two opposite legs netting to 0.00")


# ---------------------------------------------------------------------------
# Checks — mortgage
# ---------------------------------------------------------------------------


def check_mortgage_schedule_miss(ctx: _Context) -> _Outcome:
    months = _mortgage_months(ctx)
    details = []
    for label in months:
        consumption = ctx.monthly_summary(label)["consumption"]
        if _cents(consumption["mortgage_principal_scheduled"]) == 0:
            details.append(
                {"month": label, "mortgage_principal_scheduled": consumption["mortgage_principal_scheduled"],
                 "debt_paydown_nonscheduled": consumption["debt_paydown_nonscheduled"],
                 "problem": "mortgage rows present but no scheduled principal computed "
                            "(missing schedule tier, missed payment, or no [mortgage.ion] block)"}
            )
    if details:
        return _Outcome(
            "warn",
            f"{len(details)} of {len(months)} mortgage months compute 0.00 scheduled principal: "
            + ", ".join(d["month"] for d in details),
            details,
        )
    return _Outcome("pass", f"all {len(months)} mortgage months compute scheduled principal")


def _latest_anchor_month(ctx: _Context) -> str | None:
    mortgage = ctx.balances.mortgage
    if mortgage is None:
        return None
    if mortgage.anchors:
        return _month_label(max(a.anchor_month_index for a in mortgage.anchors))
    if mortgage.anchor_date is not None:
        return mortgage.anchor_date.isoformat()[:7]
    return None


def check_mortgage_anchor_stale(ctx: _Context) -> _Outcome:
    months = _mortgage_months(ctx)
    if not months:
        return _Outcome("pass", "no mortgage rows")
    latest_row = months[-1]
    if ctx.balances.mortgage is None:
        return _Outcome(
            "warn",
            f"mortgage rows through {latest_row} but balances.toml has no [mortgage.ion] block",
            [{"latest_mortgage_month": latest_row}],
        )
    anchor = _latest_anchor_month(ctx)
    detail = {"latest_anchor_month": anchor, "latest_mortgage_month": latest_row,
              "anchor_source": "anchor list" if ctx.balances.mortgage.anchors else "flat anchor_date"}
    if anchor is None or anchor < latest_row:
        return _Outcome(
            "warn",
            f"latest mortgage anchor governs {anchor}, mortgage rows run through {latest_row} — "
            "append an anchor from the latest Ion statement",
            [detail],
        )
    return _Outcome("pass", f"latest anchor governs {anchor}; mortgage rows through {latest_row}", [detail])


# ---------------------------------------------------------------------------
# Checks — summaries
# ---------------------------------------------------------------------------


class SummaryParseError(ValueError):
    """A stored summary file does not carry the rows the renderer writes."""


# Row labels exactly as monthly_summary_renderer.render_markdown writes them.
MONTHLY_SUMMARY_ROW_LABELS: dict[str, str] = {
    "inflows": "Inflows this month",
    "fixed_obligations": "Fixed obligations",
    "discretionary": "Discretionary outflows",
    "net_fcf": "**Net free cash flow**",
    "net_consumption": "**Net consumption**",
    "mortgage_principal_scheduled": "&nbsp;&nbsp;• scheduled (amortized)",
    "debt_paydown_nonscheduled": "&nbsp;&nbsp;• non-scheduled (HELOC + prepayments)",
}
_MONTHLY_REQUIRED = ("inflows", "fixed_obligations", "discretionary", "net_fcf")
# Section headings as annual_summary_renderer.render_annual_markdown writes them.
_ANNUAL_BRIDGE_HEADING = "Monthly Spend Bridge"
_ANNUAL_CONSUMPTION_HEADING = "Net Consumption Bridge"
_ANNUAL_FIELDS = ("income", "fixed_obligations", "discretionary", "net_fcf")
_MONEY = re.compile(r"([+\-\u2212]?)\$(-?)([\d,]+(?:\.\d+)?)")


def _parse_money(text: str) -> float:
    match = _MONEY.fullmatch(text.replace("*", "").strip())
    if not match:
        raise SummaryParseError(f"not a money value: {text!r}")
    value = Decimal(match.group(3).replace(",", ""))
    if match.group(1) in ("-", "\u2212") or match.group(2):
        value = -value
    return float(value)


def _table_cells(line: str) -> list[str] | None:
    stripped = line.strip()
    if len(stripped) < 2 or not (stripped.startswith("|") and stripped.endswith("|")):
        return None
    return [cell.strip() for cell in stripped[1:-1].split("|")]


def parse_monthly_summary(text: str) -> dict[str, float]:
    """Figures from a stored monthly summary, keyed like ``MONTHLY_SUMMARY_ROW_LABELS``.

    Raises ``SummaryParseError`` when a required row (inflows, fixed obligations,
    discretionary, net FCF) is missing; the net-consumption rows are optional so
    a file rendered before they existed parses and reports them as absent.
    """
    by_label = {label: key for key, label in MONTHLY_SUMMARY_ROW_LABELS.items()}
    values: dict[str, float] = {}
    for line in text.splitlines():
        cells = _table_cells(line)
        if not cells or len(cells) < 2:
            continue
        key = by_label.get(cells[0])
        if key is not None and key not in values:
            values[key] = _parse_money(cells[1])
    missing = [key for key in _MONTHLY_REQUIRED if key not in values]
    if missing:
        raise SummaryParseError("missing rows: " + ", ".join(missing))
    return values


def parse_annual_summary(text: str) -> dict[str, Any]:
    """Figures from a stored annual summary: ``{"months": {label: {...}}, "totals": {...}}``.

    Section 1 rows give income / fixed obligations / discretionary / net FCF;
    section 2 rows give net consumption (optional, for files rendered before it
    existed). Raises ``SummaryParseError`` when section 1's Total row is missing.
    """
    months: dict[str, dict[str, float]] = {}
    totals: dict[str, float] = {}
    section: str | None = None
    for line in text.splitlines():
        if line.startswith("## "):
            if _ANNUAL_BRIDGE_HEADING in line:
                section = "bridge"
            elif _ANNUAL_CONSUMPTION_HEADING in line:
                section = "consumption"
            else:
                section = None
            continue
        cells = _table_cells(line)
        if section is None or not cells or len(cells) < 5:
            continue
        head = cells[0].replace("*", "").strip()
        if head == "Total":
            target = totals
        elif re.fullmatch(r"\d{4}-\d{2}", head):
            target = months.setdefault(head, {})
        else:
            continue
        if section == "bridge":
            for key, cell in zip(_ANNUAL_FIELDS, cells[1:5]):
                target[key] = _parse_money(cell)
        else:
            target["net_consumption"] = _parse_money(cells[4])
    missing = [key for key in _ANNUAL_FIELDS if key not in totals]
    if missing:
        raise SummaryParseError("missing Total row in the monthly spend bridge")
    return {"months": months, "totals": totals}


def _compare(stored: dict[str, float], computed: dict[str, float], prefix: str = "") -> list[dict[str, Any]]:
    deltas = []
    for key, value in computed.items():
        if key not in stored:
            deltas.append({"field": f"{prefix}{key}", "stored": None, "computed": round(value, 2),
                           "delta": None, "note": "absent from the stored file"})
            continue
        diff = _cents(value) - _cents(stored[key])
        if diff != 0:
            deltas.append({"field": f"{prefix}{key}", "stored": round(stored[key], 2),
                           "computed": round(value, 2), "delta": _dollars(diff)})
    return deltas


def _summary_evaluation(ctx: _Context) -> dict[str, Any]:
    def compute() -> dict[str, Any]:
        directory = ctx.settings.watch_root / ctx.balances.wealth_bridge.monthly_summary_output_dir
        files: list[dict[str, Any]] = []
        stored_months: set[str] = set()
        stored_years: set[int] = set()
        paths = sorted(directory.glob("*_Cashflow_Summary.md")) if directory.is_dir() else []
        for path in paths:
            relative = _display_path(path, ctx.settings.watch_root)
            monthly = re.fullmatch(r"(\d{4}-\d{2})_Monthly_Cashflow_Summary\.md", path.name)
            annual = re.fullmatch(r"(\d{4})_Annual_Cashflow_Summary\.md", path.name)
            if not monthly and not annual:
                continue
            try:
                text = path.read_text(encoding="utf-8")
                if monthly:
                    label = monthly.group(1)
                    stored_months.add(label)
                    stored = parse_monthly_summary(text)
                    summary = ctx.monthly_summary(label)
                    computed = {
                        "inflows": summary["fcf_transactions"]["inflows"],
                        "fixed_obligations": summary["fcf_transactions"]["fixed_obligations"],
                        "discretionary": summary["fcf_transactions"]["discretionary"],
                        "net_fcf": summary["fcf_transactions"]["net_fcf"],
                        "net_consumption": summary["consumption"]["net_consumption"],
                        "mortgage_principal_scheduled": summary["consumption"]["mortgage_principal_scheduled"],
                        "debt_paydown_nonscheduled": summary["consumption"]["debt_paydown_nonscheduled"],
                    }
                    deltas = _compare(stored, computed)
                else:
                    year = int(annual.group(1))
                    stored_years.add(year)
                    stored = parse_annual_summary(text)
                    summary = ctx.annual_summary(year)
                    deltas = _compare(
                        stored["totals"],
                        {**summary["totals"],
                         "net_consumption": summary["consumption_totals"]["net_consumption"]},
                        "total.",
                    )
                    computed_months = {row["month"]: row for row in summary["months"]}
                    for label in sorted(set(computed_months) | set(stored["months"])):
                        row = computed_months.get(label)
                        if row is None:
                            deltas.append({"field": label, "note": "month in the stored file has no transactions now"})
                            continue
                        deltas.extend(
                            _compare(
                                stored["months"].get(label, {}),
                                {"income": row["income"], "fixed_obligations": row["fixed_obligations"],
                                 "discretionary": row["discretionary"], "net_fcf": row["net_fcf"],
                                 "net_consumption": row["consumption"]["net_consumption"]},
                                f"{label}.",
                            )
                        )
                files.append({"file": relative, "in_sync": not deltas, "deltas": deltas})
            except (SummaryParseError, OSError, UnicodeDecodeError) as exc:
                files.append({"file": relative, "in_sync": None, "problem": f"unparseable: {relative} ({exc})"})

        missing: list[dict[str, Any]] = []
        if ctx.as_of is not None:
            txn_months = sorted({tx.occurred_on[:7] for tx in ctx.transactions if tx.occurred_on[:7] <= ctx.as_of})
            for label in txn_months:
                if label not in stored_months:
                    missing.append({"month": label, "level": "info", "note": "transactions but no stored monthly summary"})
            for year in sorted({int(m[:4]) for m in txn_months}):
                if year not in stored_years:
                    missing.append({"year": year, "level": "info", "note": "transactions but no stored annual summary"})
        return {"files": files, "missing": missing}

    return ctx.cached("summaries", compute)


def check_summary_drift(ctx: _Context) -> _Outcome:
    evaluation = _summary_evaluation(ctx)
    files, missing = evaluation["files"], evaluation["missing"]
    drifted = [f for f in files if f["in_sync"] is False]
    unparseable = [f for f in files if f["in_sync"] is None]
    in_sync = sum(1 for f in files if f["in_sync"])
    details = drifted + unparseable + missing
    if drifted or unparseable:
        parts = []
        if drifted:
            parts.append(f"{len(drifted)} stored summaries drifted ({', '.join(f['file'].rsplit('/', 1)[-1] for f in drifted)})")
        if unparseable:
            parts.append(", ".join(f["problem"] for f in unparseable))
        return _Outcome("warn", "; ".join(parts) + f" — {DRIFT_CITATION}", details)
    tail = f"; {len(missing)} months/years without a stored file (info)" if missing else ""
    return _Outcome("info" if missing else "pass", f"{in_sync} stored summaries in sync{tail}", details)


def check_status_freshness(ctx: _Context) -> _Outcome:
    path = ctx.settings.watch_root / "STATUS.md"
    if not path.exists():
        return _Outcome("warn", "STATUS.md not found in the watch root")
    match = _LAST_VERIFIED.search(path.read_text(encoding="utf-8"))
    if not match:
        return _Outcome("warn", "STATUS.md has no '_Last verified: YYYY-MM-DD' line")
    verified = date.fromisoformat(match.group(1))
    row = ctx.conn.execute("SELECT MAX(imported_at) AS m FROM import_batches").fetchone()
    latest_import = _local_date(row["m"]) if row and row["m"] else None
    detail = {"last_verified": verified.isoformat(), "today": ctx.today.isoformat(),
              "latest_import": None if latest_import is None else latest_import.isoformat()}
    age = (ctx.today - verified).days
    problems = []
    if age > STATUS_FRESHNESS_MAX_DAYS:
        problems.append(f"last verified {age} days ago (> {STATUS_FRESHNESS_MAX_DAYS})")
    if latest_import is not None and verified < latest_import:
        problems.append(f"last verified {verified} is before the latest import {latest_import}")
    if problems:
        return _Outcome("warn", "STATUS.md is stale: " + "; ".join(problems), [detail])
    return _Outcome("pass", f"STATUS.md last verified {verified} ({age} days ago, after the latest import)", [detail])


def _display_path(path: Path, root: Path) -> str:
    try:
        return path.relative_to(root).as_posix()
    except ValueError:
        return path.as_posix()


def _local_date(timestamp: str) -> date:
    try:
        parsed = datetime.fromisoformat(str(timestamp).replace("Z", "+00:00"))
    except ValueError:
        return date.fromisoformat(str(timestamp)[:10])
    if parsed.tzinfo is None:
        return parsed.date()
    return parsed.astimezone().date()


# ---------------------------------------------------------------------------
# Checks — labels
# ---------------------------------------------------------------------------


def check_taxonomy(ctx: _Context) -> _Outcome:
    aliases = ctx.config.taxonomy_aliases
    canonical = ctx.config.taxonomy_subcategories
    alias_rows: Counter[tuple[str | None, str]] = Counter()
    noncanonical: Counter[tuple[str | None, str | None]] = Counter()
    uncovered: Counter[str] = Counter()
    used_under: dict[str, set[str]] = defaultdict(set)
    for tx in ctx.transactions:
        category, sub = tx.primary_category, tx.subcategory
        if sub is not None and category is not None:
            used_under[sub].add(category)
        if sub is not None and sub in aliases:
            alias_rows[(category, sub)] += 1
            continue
        if category not in canonical:
            if category in PRIMARY_CATEGORIES:
                uncovered[category] += 1
            continue
        if sub is None:
            if category != "transfer":
                noncanonical[(category, None)] += 1
        elif sub not in canonical[category]:
            noncanonical[(category, sub)] += 1
    details: list[dict[str, Any]] = [
        {"kind": "alias", "primary_category": c, "subcategory": s, "canonical": aliases[s], "rows": n}
        for (c, s), n in sorted(alias_rows.items(), key=lambda kv: (str(kv[0][0]), kv[0][1]))
    ]
    details += [
        {"kind": "not_canonical", "primary_category": c, "subcategory": s, "rows": n}
        for (c, s), n in sorted(noncanonical.items(), key=lambda kv: (str(kv[0][0]), str(kv[0][1])))
    ]
    details += [
        {"kind": "no_canonical_list", "level": "info", "primary_category": c, "rows": n}
        for c, n in sorted(uncovered.items())
    ]
    details += [
        {"kind": "multi_category_subcategory", "level": "info", "subcategory": s,
         "primary_categories": sorted(cats)}
        for s, cats in sorted(used_under.items())
        if len(cats) > 1
    ]
    alias_text = "; ".join(
        f"{s} -> {aliases[s]}: {n} rows" for (_, s), n in sorted(alias_rows.items(), key=lambda kv: kv[0][1])
    )
    noncanonical_rows = sum(noncanonical.values())
    if alias_rows or noncanonical:
        parts = []
        if alias_rows:
            parts.append(f"alias subcategories ({alias_text})")
        if noncanonical:
            parts.append(f"{noncanonical_rows} rows with a non-canonical subcategory")
        return _Outcome("warn", "; ".join(parts), details)
    return _Outcome("pass", "every subcategory is canonical for its primary_category", details)


def check_null_merchant_spend(ctx: _Context) -> _Outcome:
    rows = [
        tx
        for tx in ctx.transactions
        if tx.primary_category in SPEND_CATEGORIES
        and tx.direction != "inflow"
        and tx.merchant_normalized is None
    ]
    total = sum(abs(tx.cents) for tx in rows)
    by_category: dict[str, list[int]] = defaultdict(lambda: [0, 0])
    for tx in rows:
        by_category[tx.primary_category][0] += 1
        by_category[tx.primary_category][1] += abs(tx.cents)
    details = [
        {"primary_category": c, "rows": n, "total": _dollars(cents)}
        for c, (n, cents) in sorted(by_category.items())
    ]
    if rows:
        return _Outcome("info", f"{len(rows)} spend rows / {_fmt(total)} with NULL merchant_normalized", details)
    return _Outcome("pass", "0 spend rows with NULL merchant_normalized")


# ---------------------------------------------------------------------------
# Checks — meta
# ---------------------------------------------------------------------------


def check_benign_stale(ctx: _Context) -> _Outcome:
    details: list[dict[str, Any]] = []
    evaluations: dict[str, _BenignEval] = {}
    if any(e.check == "orphan_transfers" for e in ctx.config.benign):
        for ev in _benign_transaction_eval(ctx, "orphan_transfers", _unpaired_analysis(ctx)["remaining"]):
            evaluations[ev.entry.id] = ev
    if any(e.check == "unpaired_card_payments" for e in ctx.config.benign):
        for ev in _benign_transaction_eval(ctx, "unpaired_card_payments", _card_payment_candidates(ctx)):
            evaluations[ev.entry.id] = ev
    if any(e.check == "stored_reconciliation" for e in ctx.config.benign):
        for ev in _benign_period_eval(ctx, "stored_reconciliation", _reconciliation_findings(ctx)["findings"]):
            evaluations[ev.entry.id] = ev
    for entry in ctx.config.benign:
        base = {"id": entry.id, "check": entry.check, "status_ref": entry.status_ref,
                "falsifier": entry.falsifier}
        ev = evaluations.get(entry.id)
        if ev is None:
            supported = ", ".join(TRANSACTION_BENIGN_CHECKS + PERIOD_BENIGN_CHECKS)
            details.append({**base, "problem": f"check {entry.check!r} takes no benign entries ({supported})"})
            continue
        if ev.falsified:
            details.append({**base, "problem": "falsified: " + "; ".join(ev.falsified)})
        elif not ev.matched:
            details.append({**base, "problem": "suppressed nothing (the finding it explained is gone)"})
    if details:
        return _Outcome("warn", f"{len(details)} of {len(ctx.config.benign)} benign entries are stale", details)
    return _Outcome("pass", f"all {len(ctx.config.benign)} benign entries suppress a live finding")


def check_config_source(ctx: _Context) -> _Outcome:
    detail = {"source": ctx.config_source, "path": str(ctx.config_path)}
    if ctx.config_source == "template":
        return _Outcome(
            "info",
            f"{CONFIG_FILENAME} not found in the watch root; loaded the template "
            f"({TEMPLATE_RELATIVE_PATH.as_posix()}). Copy it to the watch root to customise.",
            [detail],
        )
    return _Outcome("info", f"loaded {ctx.config_path}", [detail])


def check_as_of_month(ctx: _Context) -> _Outcome:
    note = ctx.as_of_note
    detail = {**note, "as_of_month": ctx.as_of}
    latest = note["latest_complete_month"]
    if ctx.as_of is None:
        return _Outcome("warn", "no as-of month: the anchor accounts have no rows", [detail])
    if note["explicit"]:
        if latest is None or ctx.as_of > latest:
            return _Outcome(
                "warn",
                f"as-of month {ctx.as_of} requested explicitly, but the latest complete month is "
                f"{latest} — data looks incomplete for {ctx.as_of}",
                [detail],
            )
        return _Outcome("info", f"as-of month {ctx.as_of} requested explicitly (latest complete: {latest})", [detail])
    limiting = note["limiting_account"]
    others = {a: m for a, m in note["anchor_latest_months"].items() if a != limiting}
    text = f"latest complete month {ctx.as_of}, limited by {limiting}"
    if others:
        text += " (" + ", ".join(f"{a} reaches {m}" for a, m in sorted(others.items())) + ")"
    if note["anchor_accounts_without_rows"]:
        text += "; anchor accounts without rows: " + ", ".join(note["anchor_accounts_without_rows"])
    return _Outcome("info", text, [detail])


# Registry: (key, family, function), in report order.
CHECKS: tuple[tuple[str, str, Callable[[_Context], _Outcome]], ...] = (
    ("duplicate_keys", "integrity", check_duplicate_keys),
    ("cross_file_duplicates", "integrity", check_cross_file_duplicates),
    ("vocabulary", "integrity", check_vocabulary),
    ("income_rule_direction", "rules", check_income_rule_direction),
    ("spend_rule_direction", "rules", check_spend_rule_direction),
    ("rule_bands", "rules", check_rule_bands),
    ("orphaned_overrides", "overrides", check_orphaned_overrides),
    ("override_in_effect", "overrides", check_override_in_effect),
    ("recurring_income", "completeness", check_recurring_income),
    ("tail_gap", "completeness", check_tail_gap),
    ("early_download", "completeness", check_early_download),
    ("unpaired_card_payments", "completeness", check_unpaired_card_payments),
    ("self_anchor_chain", "balances", check_self_anchor_chain),
    ("stored_reconciliation", "balances", check_stored_reconciliation),
    ("balanced_clusters", "transfers", check_balanced_clusters),
    ("orphan_transfers", "transfers", check_orphan_transfers),
    ("pair_integrity", "transfers", check_pair_integrity),
    ("mortgage_schedule_miss", "mortgage", check_mortgage_schedule_miss),
    ("mortgage_anchor_stale", "mortgage", check_mortgage_anchor_stale),
    ("summary_drift", "summaries", check_summary_drift),
    ("status_freshness", "summaries", check_status_freshness),
    ("taxonomy", "labels", check_taxonomy),
    ("null_merchant_spend", "labels", check_null_merchant_spend),
    ("benign_stale", "meta", check_benign_stale),
    ("config_source", "meta", check_config_source),
    ("as_of_month", "meta", check_as_of_month),
)
CHECK_KEYS: tuple[str, ...] = tuple(key for key, _, _ in CHECKS)


# ---------------------------------------------------------------------------
# Metrics (the STATUS_DATA.md data block)
# ---------------------------------------------------------------------------


def _metrics(ctx: _Context) -> dict[str, Any]:
    metrics: dict[str, Any] = {}
    errors: list[str] = []

    def section(name: str, compute: Callable[[], Any]) -> None:
        try:
            metrics[name] = compute()
        except Exception as exc:  # noqa: BLE001 — one bad section never sinks the rest
            metrics[name] = None
            errors.append(f"{name}: {type(exc).__name__}: {exc}")

    def transactions() -> dict[str, Any]:
        by_account = []
        for account_id in sorted(set(ctx.accounts) | set(ctx.by_account)):
            rows = ctx.by_account.get(account_id, [])
            account = ctx.accounts.get(account_id, {})
            by_account.append(
                {"account_id": account_id, "institution": account.get("institution"),
                 "account_type": account.get("account_type"), "rows": len(rows),
                 "first": rows[0].occurred_on if rows else None,
                 "last": rows[-1].occurred_on if rows else None}
            )
        return {"total": len(ctx.transactions), "by_account": by_account}

    def categories() -> dict[str, Any]:
        year = int(ctx.as_of[:4]) if ctx.as_of else None
        all_time = Counter(str(tx.primary_category) for tx in ctx.transactions)
        in_year = Counter(
            str(tx.primary_category) for tx in ctx.transactions
            if year is not None and tx.occurred_on[:4] == str(year)
        )
        return {"all_time": dict(sorted(all_time.items())),
                "as_of_year": {"year": year, "counts": dict(sorted(in_year.items()))}}

    def overrides() -> dict[str, Any]:
        count = ctx.conn.execute("SELECT COUNT(*) AS n FROM transaction_overrides").fetchone()["n"]
        matching = ctx.conn.execute(
            "SELECT COUNT(DISTINCT transactions.id) AS n FROM transactions "
            f"JOIN transaction_overrides o ON {OVERRIDE_TUPLE_MATCH_SQL}"
        ).fetchone()["n"]
        stamped = sum(1 for tx in ctx.transactions if _is_flagged(tx.metadata))
        return {"count": count, "rows_matching": matching, "rows_stamped": stamped}

    def rules() -> dict[str, Any]:
        all_rules = _rules(ctx)
        bands = {"below_5": 0, "5": 0, "6-9": 0, "10+": 0}
        for rule in all_rules:
            p = rule["priority"]
            key = "below_5" if p < 5 else "5" if p == 5 else "6-9" if p <= 9 else "10+"
            bands[key] += 1
        return {"count": len(all_rules), "bands": bands}

    def transfers() -> dict[str, Any]:
        analysis = _unpaired_analysis(ctx)
        groups = {tx.transfer_group_key for tx in ctx.transactions if tx.transfer_group_key}
        remaining, suppressed = _orphan_evaluation(ctx)
        disposition = {f"benign:{k}": len(v) for k, v in sorted(suppressed.items())}
        disposition["balanced_cluster"] = len(analysis["clustered_ids"])
        disposition["orphan"] = len(remaining)
        return {"groups": len(groups),
                "grouped_rows": sum(1 for tx in ctx.transactions if tx.transfer_group_key),
                "unpaired_rows": len(analysis["unpaired"]),
                "unpaired_by_disposition": disposition}

    def reconciliation() -> list[dict[str, Any]]:
        return [
            dict(row)
            for row in ctx.conn.execute(
                "SELECT rp.account_id, rp.period_start, rp.period_end, rp.closing_balance_source, "
                "rp.statement_closing_balance, rp.computed_closing_balance, rp.variance_amount "
                "FROM reconciliation_periods rp WHERE NOT EXISTS ("
                "  SELECT 1 FROM reconciliation_periods r2 WHERE r2.account_id = rp.account_id"
                "  AND (r2.period_end > rp.period_end OR (r2.period_end = rp.period_end"
                "  AND r2.period_start > rp.period_start))) ORDER BY rp.account_id"
            ).fetchall()
        ]

    def gate() -> dict[str, Any] | None:
        row = ctx.conn.execute(
            "SELECT gate_key, label, current_amount, target_amount, target_date "
            "FROM liquidity_gates WHERE gate_key = 'ally_hysa'"
        ).fetchone()
        return dict(row) if row else None

    def summaries() -> list[dict[str, Any]]:
        return [{"file": f["file"], "in_sync": f["in_sync"]} for f in _summary_evaluation(ctx)["files"]]

    def database() -> dict[str, Any]:
        path = ctx.database.database_path
        wal = Path(f"{path}-wal")
        batches = ctx.conn.execute(
            "SELECT COUNT(*) AS n, COALESCE(SUM(LENGTH(raw_payload)), 0) AS payload FROM import_batches"
        ).fetchone()
        return {"path": str(path), "size_bytes": path.stat().st_size if path.exists() else None,
                "wal_bytes": wal.stat().st_size if wal.exists() else 0,
                "import_batches": batches["n"], "raw_payload_bytes": batches["payload"]}

    section("transactions", transactions)
    section("primary_category", categories)
    section("overrides", overrides)
    section("rules", rules)
    section("transfers", transfers)
    section("reconciliation_latest", reconciliation)
    section("ally_hysa_gate", gate)
    section("summaries", summaries)
    section("database", database)
    metrics["errors"] = errors
    return metrics


# ---------------------------------------------------------------------------
# Rendering
# ---------------------------------------------------------------------------


def _cell(value: Any) -> str:
    text = "" if value is None else str(value)
    return text.replace("|", "\\|").replace("\n", " ")


def _money_cell(value: Any) -> str:
    return "" if value is None else f"{float(value):,.2f}"


def _check_table(checks: list[VerifyCheckResult]) -> str:
    lines = ["| Check | Family | Status | Summary |", "| --- | --- | --- | --- |"]
    lines += [
        f"| `{c.key}` | {c.family} | {c.status} | {_cell(c.summary)} |" for c in checks
    ]
    return "\n".join(lines)


def render_report(result: VerifyStateResult) -> str:
    counts = result.counts
    lines = [
        f"# verify_state report — {result.generated_at[:10]}",
        "",
        f"_Generated {result.generated_at} · as-of month {result.as_of_month} · overall "
        f"**{result.status.upper()}** · fail {counts.get('fail', 0)} · warn {counts.get('warn', 0)} · "
        f"info {counts.get('info', 0)} · pass {counts.get('pass', 0)} · error {counts.get('error', 0)}_",
        "",
        _check_table(result.checks),
    ]
    flagged = [c for c in result.checks if c.status in ("fail", "error", "warn", "info")]
    if flagged:
        lines += ["", "## Findings"]
    for check in flagged:
        lines += ["", f"### `{check.key}` — {check.status.upper()}", "", check.summary]
        if check.suppressed_by:
            lines.append(f"Suppressed by: {', '.join(check.suppressed_by)}")
        if check.details:
            lines.append("")
            lines += [f"- `{json.dumps(d, sort_keys=True, default=str)}`" for d in check.details]
            if check.details_total > len(check.details):
                lines.append(f"- … {check.details_total - len(check.details)} more (capped at {MAX_DETAILS})")
    return "\n".join(lines) + "\n"


def render_status_data(result: VerifyStateResult) -> str:
    m = result.metrics
    lines = [
        f"<!-- Generated by verify_state at {result.generated_at}. Do not hand-edit; rerun verify_state. -->",
        "# Current data state (generated)",
        "",
        f"As-of month **{result.as_of_month}** · verify_state overall **{result.status.upper()}** "
        f"(fail {result.counts.get('fail', 0)}, warn {result.counts.get('warn', 0)}, "
        f"info {result.counts.get('info', 0)}, pass {result.counts.get('pass', 0)}, "
        f"error {result.counts.get('error', 0)}).",
    ]

    tx = m.get("transactions")
    if tx:
        lines += ["", "## Transactions by account", "",
                  "| Account | Institution | Type | Rows | First | Last |",
                  "| --- | --- | --- | ---: | --- | --- |"]
        lines += [
            f"| `{a['account_id']}` | {_cell(a['institution'])} | {_cell(a['account_type'])} | "
            f"{a['rows']:,} | {_cell(a['first'])} | {_cell(a['last'])} |"
            for a in tx["by_account"]
        ]
        lines.append(f"| **Total** | | | **{tx['total']:,}** | | |")

    cats = m.get("primary_category")
    if cats:
        year = cats["as_of_year"]["year"]
        lines += ["", "## primary_category counts", "",
                  f"| primary_category | All-time | {year} |", "| --- | ---: | ---: |"]
        for category, n in cats["all_time"].items():
            lines.append(f"| {category} | {n:,} | {cats['as_of_year']['counts'].get(category, 0):,} |")

    ov = m.get("overrides")
    if ov:
        lines += ["", "## Overrides", "",
                  f"- Stored overrides: {ov['count']:,}",
                  f"- Rows matching an override tuple: {ov['rows_matching']:,}",
                  f"- Rows stamped `manual_override_applied`: {ov['rows_stamped']:,}"]

    rl = m.get("rules")
    if rl:
        b = rl["bands"]
        lines += ["", "## Classification rules", "",
                  "| Priority band | Rules |", "| --- | ---: |",
                  f"| 5 (structural transfer) | {b['5']} |",
                  f"| 6–9 (UI manual) | {b['6-9']} |",
                  f"| 10+ (general) | {b['10+']} |",
                  f"| below 5 | {b['below_5']} |",
                  f"| **Total** | **{rl['count']}** |"]

    tr = m.get("transfers")
    if tr:
        lines += ["", "## Transfers", "",
                  f"- Transfer groups: {tr['groups']} ({tr['grouped_rows']} rows)",
                  f"- Unpaired transfer rows: {tr['unpaired_rows']}", "",
                  "| Disposition | Rows |", "| --- | ---: |"]
        lines += [f"| {k} | {v} |" for k, v in tr["unpaired_by_disposition"].items()]

    rc = m.get("reconciliation_latest")
    if rc:
        lines += ["", "## Latest reconciliation per account", "",
                  "| Account | Period | Closing source | Statement closing | Computed closing | Variance |",
                  "| --- | --- | --- | ---: | ---: | ---: |"]
        lines += [
            f"| `{r['account_id']}` | {r['period_start']}..{r['period_end']} | "
            f"{_cell(r['closing_balance_source'])} | {_money_cell(r['statement_closing_balance'])} | "
            f"{_money_cell(r['computed_closing_balance'])} | {_money_cell(r['variance_amount'])} |"
            for r in rc
        ]

    gate = m.get("ally_hysa_gate")
    if gate:
        lines += ["", "## Ally HYSA gate", "",
                  "| Gate | Current | Target | Target date |", "| --- | ---: | ---: | --- |",
                  f"| {_cell(gate['label'])} | {_money_cell(gate['current_amount'])} | "
                  f"{_money_cell(gate['target_amount'])} | {gate['target_date']} |"]

    sm = m.get("summaries")
    if sm is not None:
        lines += ["", "## Stored summaries", "", "| File | In sync |", "| --- | --- |"]
        lines += [
            f"| {_cell(s['file'])} | "
            f"{'yes' if s['in_sync'] else 'unparseable' if s['in_sync'] is None else 'no'} |"
            for s in sm
        ] or ["| _(none)_ | |"]

    db = m.get("database")
    if db:
        lines += ["", "## Database", "",
                  f"- File size: {db['size_bytes'] or 0:,} bytes (+ `-wal` {db['wal_bytes']:,} bytes)",
                  f"- `import_batches`: {db['import_batches']:,} "
                  f"(`SUM(LENGTH(raw_payload))` {db['raw_payload_bytes']:,})"]

    if m.get("errors"):
        lines += ["", "## Metric errors", ""] + [f"- {_cell(e)}" for e in m["errors"]]

    lines += ["", "## Checks", "", _check_table(result.checks)]
    return "\n".join(lines) + "\n"


# ---------------------------------------------------------------------------
# Write mode
# ---------------------------------------------------------------------------


def _write_text(path: Path, text: str) -> None:
    with path.open("w", encoding="utf-8", newline="\n") as handle:
        handle.write(text)


def export_table_csv(connection: sqlite3.Connection, table: str, path: Path) -> int:
    """Every column of ``table``, header row, ordered by ``created_at, id``.

    NULL is written as ``EXPORT_NULL``. Returns the number of data rows.
    """
    cursor = connection.execute(f"SELECT * FROM {table} ORDER BY created_at, id")
    columns = [d[0] for d in cursor.description]
    rows = cursor.fetchall()
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle, lineterminator="\n")
        writer.writerow(columns)
        for row in rows:
            writer.writerow([EXPORT_NULL if row[c] is None else row[c] for c in columns])
    return len(rows)


def _write_outputs(ctx: _Context, result: VerifyStateResult) -> VerifyStateWritten:
    written = VerifyStateWritten()
    root = ctx.settings.watch_root
    try:
        path = root / STATUS_DATA_FILENAME
        _write_text(path, render_status_data(result))
        written.status_data = str(path)
    except OSError as exc:
        written.errors.append(f"{STATUS_DATA_FILENAME}: {exc}")
    exports = root / EXPORTS_DIRNAME
    try:
        exports.mkdir(exist_ok=True)
    except OSError as exc:
        written.errors.append(f"{EXPORTS_DIRNAME}/: {exc}")
        return written
    for table, filename in (
        ("transaction_overrides", OVERRIDES_EXPORT_FILENAME),
        ("classification_rules", RULES_EXPORT_FILENAME),
    ):
        try:
            export_table_csv(ctx.conn, table, exports / filename)
            written.exports.append(str(exports / filename))
        except (OSError, sqlite3.Error) as exc:
            written.errors.append(f"{EXPORTS_DIRNAME}/{filename}: {exc}")
    report = exports / f"verify_state_{ctx.today.isoformat()}.md"
    try:
        _write_text(report, result.report_markdown)
        written.exports.append(str(report))
    except OSError as exc:
        written.errors.append(f"{EXPORTS_DIRNAME}/{report.name}: {exc}")
    return written


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def _run_check(ctx: _Context, key: str, family: str, fn: Callable[[_Context], _Outcome]) -> VerifyCheckResult:
    try:
        outcome = fn(ctx)
    except Exception as exc:  # noqa: BLE001 — one failing check never stops the others
        outcome = _Outcome("error", f"{type(exc).__name__}: {exc}")
    return VerifyCheckResult(
        key=key,
        family=family,
        status=outcome.status,
        summary=outcome.summary,
        details=outcome.details[:MAX_DETAILS],
        details_total=len(outcome.details),
        suppressed_by=outcome.suppressed_by,
    )


def _overall(checks: list[VerifyCheckResult]) -> str:
    worst = max((_STATUS_RANK[c.status] for c in checks), default=0)
    return {0: "pass", 1: "warn", 2: "fail"}[worst]


def verify_state(
    settings: ServerSettings,
    database: DatabaseManager,
    request: VerifyStateRequest,
) -> VerifyStateResult:
    """Run the post-ingest checks read-only; optionally write the two outputs."""
    started = time.perf_counter()
    generated_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
    unknown = [key for key in request.checks if key not in CHECK_KEYS]
    if unknown:
        raise ValueError(
            f"unknown check keys: {', '.join(unknown)}. Known: {', '.join(CHECK_KEYS)}"
        )
    try:
        config, source, config_path = load_verify_config(settings)
    except VerifyConfigError as exc:
        message = f"verify_state.toml is malformed: {exc}"
        return VerifyStateResult(
            generated_at=generated_at,
            status="error",
            error=message,
            report_markdown=f"# verify_state report — {generated_at[:10]}\n\n**ERROR:** {message}\n",
        )

    connection = database.connect(read_only=True)
    try:
        ctx = _Context(settings, database, request, connection, config, source, config_path)
        selected = set(request.checks) or set(CHECK_KEYS)
        checks = [
            _run_check(ctx, key, family, fn) for key, family, fn in CHECKS if key in selected
        ]
        counts = {s: sum(1 for c in checks if c.status == s) for s in ("fail", "warn", "info", "pass", "error")}
        metrics = _metrics(ctx)
        metrics["runtime_seconds"] = round(time.perf_counter() - started, 3)
        result = VerifyStateResult(
            generated_at=generated_at,
            as_of_month=ctx.as_of,
            status=_overall(checks),
            counts=counts,
            checks=checks,
            metrics=metrics,
        )
        result.report_markdown = render_report(result)
        if request.write:
            result.written = _write_outputs(ctx, result)
        return result
    finally:
        connection.close()
