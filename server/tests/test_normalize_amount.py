from __future__ import annotations

from decimal import Decimal

import pytest

from liquidity_gate_mcp.database import DatabaseManager


@pytest.mark.parametrize(
    "amount, direction, expected",
    [
        (Decimal("100.00"), "outflow", -100.0),
        (Decimal("-100.00"), "outflow", -100.0),
        (Decimal("100.00"), "inflow", 100.0),
        (Decimal("-100.00"), "inflow", 100.0),
        (Decimal("250.50"), "transfer", 250.5),
        (Decimal("-250.50"), "transfer", -250.5),
    ],
)
def test_normalize_amount(amount: Decimal, direction: str, expected: float) -> None:
    assert DatabaseManager._normalize_amount(amount, direction) == pytest.approx(expected)
