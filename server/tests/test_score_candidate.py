from __future__ import annotations

from pathlib import Path

from liquidity_gate_mcp.models import DocumentTrackerRow
from liquidity_gate_mcp.tools import MATCH_THRESHOLD, score_candidate


def make_row(document: str, category: str = "Banking", fmt: str = "PDF") -> DocumentTrackerRow:
    return DocumentTrackerRow(
        id="doc-001",
        category=category,
        document=document,
        subject_matter="",
        format=fmt,
        priority="High",
        source_where_to_get="",
        why_needed="",
    )


def test_score_candidate_strong_match_above_threshold() -> None:
    row = make_row("Chase Checking statement")
    candidate = Path("/tmp/some/dir/Chase_Checking_Statement_2026_01.pdf")
    score = score_candidate(row, candidate)
    assert score >= 0.42


def test_score_candidate_irrelevant_below_threshold() -> None:
    row = make_row("Chase Checking statement")
    candidate = Path("/tmp/random/photo_of_dog.jpeg")
    score = score_candidate(row, candidate)
    assert score < 0.42


def test_score_candidate_extension_bonus_applied() -> None:
    row = make_row("Receipt for car repair", fmt="PDF")
    pdf_score = score_candidate(row, Path("/tmp/receipt_car_repair.pdf"))
    txt_score = score_candidate(row, Path("/tmp/receipt_car_repair.txt"))
    assert pdf_score > txt_score


def test_score_candidate_camelcase_compound_split() -> None:
    # CamelCase compounds like "CardActivity" should split into "Card" + "Activity"
    # so they line up with the tracker tokens.
    row = make_row("Chase credit-card statements", category="A. Core Transactions", fmt="CSV")
    score = score_candidate(row, Path("/tmp/2026-05-12_Chase_YTD_CardActivity.csv"))
    assert score >= MATCH_THRESHOLD


def test_score_candidate_letter_digit_split_for_401k() -> None:
    # "401k" in a filename should tokenize so its "401" component matches
    # the tracker's "401(k)" -> "401" token.
    row = make_row(
        "Current 401(k) election screenshots - both spouses",
        category="B. Income & Payroll",
        fmt="Screenshot / PDF",
    )
    score = score_candidate(row, Path("/tmp/2026-05-12_Jeff_401k_Election.png"))
    assert score >= MATCH_THRESHOLD
