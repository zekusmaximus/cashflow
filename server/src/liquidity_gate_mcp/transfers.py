from __future__ import annotations

import re
import sqlite3
from collections import defaultdict
from dataclasses import dataclass
from datetime import date
from typing import Iterable
from uuid import uuid4

from .database import DatabaseManager
from .models import (
    AmbiguousTransfer,
    PairTransfersRequest,
    PairTransfersResult,
    SuspectedUntagged,
    UnpairedTransfer,
)


# Descriptions Jeff has flagged as likely-untagged transfer pairs but where
# the parsers do not yet auto-tag direction='transfer'. The diagnostic pass
# surfaces these as "strong" confidence when they match a known transfer's
# amount and date on a different account. Any other amount+date match is
# reported as "weak" so a human can decide whether to expand the parser
# regex lists.
_STRONG_UNTAGGED_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"webstr.*ashley\s*m\s*calabr", re.IGNORECASE),
    re.compile(r"fid\s*bkg\s*svc\s*llc\s*moneyline", re.IGNORECASE),
    re.compile(r"venmo\s*payment", re.IGNORECASE),
    re.compile(r"mobile\s*check\s*dep", re.IGNORECASE),
)


@dataclass(frozen=True)
class _Row:
    id: str
    account_id: str
    occurred_on: date
    amount: float
    direction: str
    description: str
    transfer_group_key: str | None

    @property
    def bucket(self) -> int:
        # Cents-as-int keys avoid float equality pitfalls in dict lookups.
        return round(abs(self.amount) * 100)


def pair_transfers(
    database: DatabaseManager, request: PairTransfersRequest
) -> PairTransfersResult:
    """Pair direction='transfer' rows across accounts by amount + date.

    Reads and writes happen on a single write connection inside one
    transaction so a mid-run failure rolls back cleanly. Rows that already
    have a non-null transfer_group_key are skipped — re-running is safe and
    produces zero new pairs when nothing has changed.
    """
    connection = database.connect()
    try:
        rows = _load_rows(connection)
        already_paired_skipped = sum(1 for r in rows if r.transfer_group_key is not None)

        candidates = [
            r
            for r in rows
            if r.transfer_group_key is None and r.direction == "transfer"
        ]
        non_transfers = [r for r in rows if r.direction != "transfer"]

        pairings, unpaired, ambiguous = _resolve_pairs(
            candidates, request.date_tolerance_days
        )

        if not request.dry_run and pairings:
            for left, right in pairings:
                key = uuid4().hex
                connection.execute(
                    "UPDATE transactions SET transfer_group_key = ? "
                    "WHERE id = ? AND transfer_group_key IS NULL",
                    (key, left.id),
                )
                connection.execute(
                    "UPDATE transactions SET transfer_group_key = ? "
                    "WHERE id = ? AND transfer_group_key IS NULL",
                    (key, right.id),
                )
            connection.commit()
        else:
            connection.rollback()

        suspected: list[SuspectedUntagged] = []
        if request.include_diagnostics:
            suspected = _diagnose_untagged(
                pairings, non_transfers, request.date_tolerance_days
            )
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()

    return PairTransfersResult(
        pairs_created=len(pairings),
        candidates_examined=len(candidates),
        already_paired_skipped=already_paired_skipped,
        unpaired=unpaired,
        ambiguous=ambiguous,
        suspected_untagged=suspected,
        dry_run=request.dry_run,
    )


def _load_rows(connection: sqlite3.Connection) -> list[_Row]:
    cursor = connection.execute(
        "SELECT id, account_id, occurred_on, amount, direction, "
        "description_raw, transfer_group_key FROM transactions"
    )
    out: list[_Row] = []
    for row in cursor.fetchall():
        out.append(
            _Row(
                id=row["id"],
                account_id=row["account_id"],
                occurred_on=date.fromisoformat(row["occurred_on"]),
                amount=float(row["amount"]),
                direction=row["direction"],
                description=row["description_raw"],
                transfer_group_key=row["transfer_group_key"],
            )
        )
    return out


def _resolve_pairs(
    candidates: list[_Row], tolerance_days: int
) -> tuple[list[tuple[_Row, _Row]], list[UnpairedTransfer], list[AmbiguousTransfer]]:
    buckets: dict[int, list[_Row]] = defaultdict(list)
    for row in candidates:
        buckets[row.bucket].append(row)
    by_id = {row.id: row for row in candidates}

    # For each row, compute the set of best (minimum-delta) cross-account
    # partners within tolerance, plus any same-account hits separately.
    best_partners: dict[str, list[str]] = {}
    same_account_hits: dict[str, list[str]] = defaultdict(list)
    has_any_in_bucket: dict[str, bool] = {}

    for row in candidates:
        bucket = buckets[row.bucket]
        has_any_in_bucket[row.id] = len(bucket) > 1
        cross_account: list[tuple[int, _Row]] = []
        for partner in bucket:
            if partner.id == row.id:
                continue
            delta = abs((partner.occurred_on - row.occurred_on).days)
            if delta > tolerance_days:
                continue
            if partner.account_id == row.account_id:
                same_account_hits[row.id].append(partner.id)
                continue
            cross_account.append((delta, partner))

        if not cross_account:
            best_partners[row.id] = []
            continue
        min_delta = min(delta for delta, _ in cross_account)
        best_partners[row.id] = sorted(
            partner.id for delta, partner in cross_account if delta == min_delta
        )

    pairings: list[tuple[_Row, _Row]] = []
    ambiguous_map: dict[str, AmbiguousTransfer] = {}
    unpaired_map: dict[str, UnpairedTransfer] = {}
    paired_ids: set[str] = set()

    # Pair only when both sides see each other as their unique best partner.
    for row in sorted(candidates, key=lambda r: (r.occurred_on, r.id)):
        if row.id in paired_ids:
            continue
        partners = best_partners[row.id]

        if len(partners) > 1:
            ambiguous_map[row.id] = _ambiguous(row, partners)
            continue

        if not partners:
            # No cross-account match. If there's a same-account collision,
            # surface it via the post-pass below; otherwise mark unpaired.
            if same_account_hits.get(row.id):
                continue
            reason = (
                "no candidate within date_tolerance_days"
                if not has_any_in_bucket[row.id]
                else "no different-account candidate within tolerance"
            )
            unpaired_map[row.id] = _unpaired(row, reason)
            continue

        partner_id = partners[0]
        if partner_id in paired_ids:
            unpaired_map[row.id] = _unpaired(
                row, "preferred partner already paired with a closer match"
            )
            continue

        partner_partners = best_partners[partner_id]
        if len(partner_partners) > 1 or partner_partners[0] != row.id:
            # Partner is ambiguous or prefers someone else. Don't pair.
            unpaired_map[row.id] = _unpaired(
                row, "partner had a closer or ambiguous match elsewhere"
            )
            continue

        partner = by_id[partner_id]
        pairings.append((row, partner))
        paired_ids.add(row.id)
        paired_ids.add(partner.id)
        unpaired_map.pop(partner.id, None)

    # Same-account-only collisions become ambiguous if no real pair landed.
    for row_id, partners in same_account_hits.items():
        if row_id in paired_ids or row_id in ambiguous_map:
            continue
        row = by_id[row_id]
        if best_partners[row.id]:
            # Cross-account candidate existed but didn't pair — let the
            # cross-account reason dominate. Leave the same-account hit
            # out of `ambiguous` to avoid double-reporting.
            continue
        ambiguous_map[row_id] = AmbiguousTransfer(
            transaction_id=row.id,
            account_id=row.account_id,
            occurred_on=row.occurred_on.isoformat(),
            amount=row.amount,
            candidates=partners,
        )

    unpaired = [
        u
        for u in unpaired_map.values()
        if u.transaction_id not in paired_ids
        and u.transaction_id not in ambiguous_map
    ]

    return pairings, unpaired, list(ambiguous_map.values())


def _ambiguous(row: _Row, candidates: list[str]) -> AmbiguousTransfer:
    return AmbiguousTransfer(
        transaction_id=row.id,
        account_id=row.account_id,
        occurred_on=row.occurred_on.isoformat(),
        amount=row.amount,
        candidates=candidates,
    )


def _unpaired(row: _Row, reason: str) -> UnpairedTransfer:
    return UnpairedTransfer(
        transaction_id=row.id,
        account_id=row.account_id,
        occurred_on=row.occurred_on.isoformat(),
        amount=row.amount,
        direction=row.direction,
        description=row.description,
        likely_reason=reason,
    )


def _diagnose_untagged(
    pairings: Iterable[tuple[_Row, _Row]],
    non_transfers: list[_Row],
    tolerance_days: int,
) -> list[SuspectedUntagged]:
    by_bucket: dict[int, list[_Row]] = defaultdict(list)
    for row in non_transfers:
        by_bucket[row.bucket].append(row)

    out: list[SuspectedUntagged] = []
    seen: set[str] = set()
    for left, right in pairings:
        partner_accounts = {left.account_id, right.account_id}
        for leg in (left, right):
            for candidate in by_bucket.get(leg.bucket, []):
                if candidate.id in seen:
                    continue
                if candidate.account_id in partner_accounts:
                    continue
                if abs((candidate.occurred_on - leg.occurred_on).days) > tolerance_days:
                    continue
                confidence = (
                    "strong"
                    if _matches_strong_pattern(candidate.description)
                    else "weak"
                )
                out.append(
                    SuspectedUntagged(
                        transaction_id=candidate.id,
                        description=candidate.description,
                        likely_partner_id=leg.id,
                        confidence=confidence,
                    )
                )
                seen.add(candidate.id)
    return out


def _matches_strong_pattern(description: str) -> bool:
    return any(pattern.search(description) for pattern in _STRONG_UNTAGGED_PATTERNS)
