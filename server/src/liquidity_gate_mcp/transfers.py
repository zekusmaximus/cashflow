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

    The primary rule is mutual-best-match: a pair forms only when each row
    sees the other as its unique closest cross-account partner. After that
    pass, a cluster fallback handles the N-vs-N case (e.g. five same-day
    $5K Webster -> Beacon transfers): if a cents bucket contains exactly
    two accounts with equal row counts, opposite signs, and each row's
    best_partners equals the full other side, the cluster pairs 1-to-1 by
    stable id-sort. Any deterministic pairing is correct since the rows
    cancel identically in the ledger.

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

    # Cluster resolution: ambiguous rows in a cents bucket form a graph
    # via the best_partners edges. Each connected component is checked
    # independently — a single bucket can contain several unrelated
    # clusters (e.g. all $5K transfers across the year live in one cents
    # bucket but split into clusters by date proximity). When a component
    # has exactly two accounts with equal row counts, opposite signs, and
    # mutual best_partners sets, pair the rows 1-to-1 by stable id-sort.
    # Any deterministic 1-to-1 pairing is correct because the rows cancel
    # in the ledger identically; we are not trying to match physical legs,
    # just to retire the cluster.
    for bucket_rows in buckets.values():
        bucket_ambiguous = [r for r in bucket_rows if r.id in ambiguous_map]
        if len(bucket_ambiguous) < 2:
            continue

        for component_rows in _connected_components(bucket_ambiguous, best_partners):
            if len(component_rows) < 2:
                continue

            by_account: dict[str, list[_Row]] = defaultdict(list)
            for r in component_rows:
                by_account[r.account_id].append(r)
            if len(by_account) != 2:
                continue

            key_a, key_b = sorted(by_account.keys())
            side_a = by_account[key_a]
            side_b = by_account[key_b]
            if len(side_a) != len(side_b):
                continue

            sign_a = _uniform_sign(side_a)
            sign_b = _uniform_sign(side_b)
            if sign_a is None or sign_b is None or sign_a == sign_b:
                continue

            expected_for_a = {r.id for r in side_b}
            expected_for_b = {r.id for r in side_a}
            if any(set(best_partners[r.id]) != expected_for_a for r in side_a):
                continue
            if any(set(best_partners[r.id]) != expected_for_b for r in side_b):
                continue

            for left, right in zip(
                sorted(side_a, key=lambda r: r.id),
                sorted(side_b, key=lambda r: r.id),
            ):
                pairings.append((left, right))
                paired_ids.add(left.id)
                paired_ids.add(right.id)
                ambiguous_map.pop(left.id, None)
                ambiguous_map.pop(right.id, None)

    unpaired = [
        u
        for u in unpaired_map.values()
        if u.transaction_id not in paired_ids
        and u.transaction_id not in ambiguous_map
    ]

    return pairings, unpaired, list(ambiguous_map.values())


def _connected_components(
    rows: list[_Row], best_partners: dict[str, list[str]]
) -> list[list[_Row]]:
    # Union-find with path compression. Two rows belong to the same
    # component if either appears in the other's best_partners list.
    # Restricts to the input row set so external partners (e.g. already-
    # paired rows) don't pull unrelated components together.
    by_id = {r.id: r for r in rows}
    parent: dict[str, str] = {r.id: r.id for r in rows}

    def find(node: str) -> str:
        while parent[node] != node:
            parent[node] = parent[parent[node]]
            node = parent[node]
        return node

    def union(a: str, b: str) -> None:
        root_a, root_b = find(a), find(b)
        if root_a != root_b:
            parent[root_a] = root_b

    for row in rows:
        for partner_id in best_partners.get(row.id, []):
            if partner_id in parent:
                union(row.id, partner_id)

    components: dict[str, list[_Row]] = defaultdict(list)
    for row in rows:
        components[find(row.id)].append(by_id[row.id])
    return list(components.values())


def _uniform_sign(rows: list[_Row]) -> int | None:
    # +1 if every row's amount is strictly positive, -1 if strictly
    # negative, None if mixed or any row is zero.
    if not rows:
        return None
    signs = {1 if r.amount > 0 else -1 if r.amount < 0 else 0 for r in rows}
    if len(signs) != 1:
        return None
    sign = next(iter(signs))
    return sign if sign != 0 else None


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
