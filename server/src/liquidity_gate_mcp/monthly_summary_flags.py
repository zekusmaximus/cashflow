"""Section 4 flag detectors for the monthly cashflow summary.

Four detectors, each a pure function over already-computed scalars (plus the
month's ``abnormal``-category transactions). No database access, no file I/O —
so every detector is unit-testable in isolation. ``detect_flags`` runs all four
in the locked spec order and concatenates their results.

Each flag is a plain dict ``{"code", "message", "severity"}`` where severity is
``"warn"`` (a trend/threshold breach to investigate) or ``"alert"`` (a specific
large transaction needing eyes).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class FlagInputs:
    """Everything the four detectors need, gathered by the compute pass."""

    hysa_monthly_delta: float
    savings_rate_transactions_pct: float
    discretionary_this_month: float
    # Each abnormal txn: {"merchant": str, "amount": float, "date": str}.
    # Passed unfiltered — detector 4 applies the threshold itself.
    abnormal_txns: list[dict[str, Any]] = field(default_factory=list)
    hysa_floor: float = 2500.0
    savings_rate_floor: float = 18.0
    discretionary_ceiling: float = 19000.0
    abnormal_threshold: float = 3000.0


def _flag(code: str, message: str, severity: str) -> dict[str, str]:
    return {"code": code, "message": message, "severity": severity}


def detect_hysa_delta_floor(inputs: FlagInputs) -> list[dict[str, str]]:
    """Detector 1 — HYSA monthly net delta below the floor."""
    if inputs.hysa_monthly_delta < inputs.hysa_floor:
        return [
            _flag(
                "hysa_delta_below_floor",
                f"HYSA delta ${inputs.hysa_monthly_delta:,.0f} — below "
                f"${inputs.hysa_floor:,.0f} floor. Investigate source of leak.",
                "warn",
            )
        ]
    return []


def detect_savings_rate_floor(inputs: FlagInputs) -> list[dict[str, str]]:
    """Detector 2 — transactions-view effective savings rate below the floor."""
    if inputs.savings_rate_transactions_pct < inputs.savings_rate_floor:
        return [
            _flag(
                "savings_rate_below_floor",
                f"Savings rate {inputs.savings_rate_transactions_pct:.1f}% — below "
                f"{inputs.savings_rate_floor:.0f}% floor (target 22%).",
                "warn",
            )
        ]
    return []


def detect_discretionary_ceiling(inputs: FlagInputs) -> list[dict[str, str]]:
    """Detector 3 — discretionary spend above the monthly ceiling."""
    if inputs.discretionary_this_month > inputs.discretionary_ceiling:
        return [
            _flag(
                "discretionary_over_ceiling",
                f"Discretionary ${inputs.discretionary_this_month:,.0f} — exceeds "
                f"${inputs.discretionary_ceiling:,.0f} ceiling (~$228K annualized).",
                "warn",
            )
        ]
    return []


def detect_abnormal_outflows(inputs: FlagInputs) -> list[dict[str, str]]:
    """Detector 4 — one flag per abnormal-category txn over the threshold."""
    out: list[dict[str, str]] = []
    for txn in inputs.abnormal_txns:
        amount = abs(float(txn.get("amount", 0.0)))
        if amount > inputs.abnormal_threshold:
            out.append(
                _flag(
                    "abnormal_outflow",
                    f"Abnormal outflow: {txn.get('merchant', 'Unknown')} "
                    f"${amount:,.0f} on {txn.get('date', 'unknown date')}.",
                    "alert",
                )
            )
    return out


def detect_flags(inputs: FlagInputs) -> list[dict[str, str]]:
    """Run all four detectors in spec order; concatenate triggered flags."""
    flags: list[dict[str, str]] = []
    flags += detect_hysa_delta_floor(inputs)
    flags += detect_savings_rate_floor(inputs)
    flags += detect_discretionary_ceiling(inputs)
    flags += detect_abnormal_outflows(inputs)
    return flags
