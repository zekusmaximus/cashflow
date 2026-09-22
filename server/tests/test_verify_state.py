"""verify_state: one passing and one firing case per check, plus the read-only
and write-mode guarantees. Fixture DBs only — nothing here reads the live DB."""

from __future__ import annotations

import hashlib
import os
import shutil
from datetime import date, datetime, timedelta
from pathlib import Path

import pytest

from liquidity_gate_mcp import verify_state as vs
from liquidity_gate_mcp.annual_summary import compute_annual_summary
from liquidity_gate_mcp.annual_summary_renderer import render_annual_markdown
from liquidity_gate_mcp.balances import load_balances
from liquidity_gate_mcp.models import VerifyStateRequest
from liquidity_gate_mcp.monthly_summary import compute_monthly_summary
from liquidity_gate_mcp.monthly_summary_renderer import render_markdown
from liquidity_gate_mcp.tools import (
    MATCH_THRESHOLD,
    load_tracker_rows,
    read_document_metadata,
    score_candidate,
)

from .verify_state_fixture import (
    ALLY,
    BALANCES_TOML,
    BEACON,
    CHASE,
    MORTGAGE_BLOCK,
    SELFHELP,
    TEMPLATE_PATH,
    WEBSTER,
    Fixture,
    Row,
    build_fixture,
)


@pytest.fixture()
def fx(tmp_path: Path, schema_path: Path) -> Fixture:
    return build_fixture(tmp_path, schema_path)


def _statuses(result) -> dict[str, str]:
    return {c.key: c.status for c in result.checks}


def _details(check, key: str = "transaction_id") -> set:
    return {d.get(key) for d in check.details if key in d}


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _snapshot(root: Path) -> dict[str, str]:
    return {
        p.relative_to(root).as_posix(): _sha(p) for p in sorted(root.rglob("*")) if p.is_file()
    }


# ---------------------------------------------------------------------------
# Baseline: the healthy fixture
# ---------------------------------------------------------------------------


def test_baseline_every_check_passes_or_is_informational(fx: Fixture) -> None:
    result = fx.run()

    assert result.as_of_month == "2026-08"
    assert result.status == "pass"
    assert [c.key for c in result.checks] == list(vs.CHECK_KEYS)
    informational = {"balanced_clusters", "config_source", "as_of_month"}
    assert _statuses(result) == {
        key: ("info" if key in informational else "pass") for key in vs.CHECK_KEYS
    }
    assert result.counts == {"fail": 0, "warn": 0, "info": 3, "pass": 23, "error": 0}
    assert result.written is None
    assert result.report_markdown.startswith("# verify_state report")


def test_overall_status_is_worst_and_info_never_raises_it(fx: Fixture) -> None:
    fx.execute("UPDATE transactions SET subcategory = 'dining' WHERE id = 'c07'")
    assert fx.run().status == "warn"
    fx.execute("UPDATE transactions SET primary_category = 'unclassified' WHERE id = 'c09'")
    assert fx.run().status == "fail"


def test_a_raising_check_is_an_error_and_the_others_still_run(
    fx: Fixture, monkeypatch: pytest.MonkeyPatch
) -> None:
    def boom(ctx):
        raise RuntimeError("kaboom")

    patched = tuple(
        (key, family, boom if key == "taxonomy" else fn) for key, family, fn in vs.CHECKS
    )
    monkeypatch.setattr(vs, "CHECKS", patched)

    result = fx.run()

    statuses = _statuses(result)
    assert statuses["taxonomy"] == "error"
    assert "kaboom" in next(c for c in result.checks if c.key == "taxonomy").summary
    assert statuses["duplicate_keys"] == "pass" and statuses["null_merchant_spend"] == "pass"
    assert result.counts["error"] == 1
    assert result.status == "fail"


def test_checks_subset_and_unknown_key(fx: Fixture) -> None:
    result = fx.run(checks=["taxonomy", "pair_integrity"])
    assert [c.key for c in result.checks] == ["pair_integrity", "taxonomy"]
    with pytest.raises(ValueError, match="unknown check keys: nope"):
        fx.run(checks=["nope"])


def test_month_must_be_yyyy_mm() -> None:
    with pytest.raises(ValueError):
        VerifyStateRequest(month="2026-13")
    assert VerifyStateRequest(month="2026-08").month == "2026-08"


def test_details_are_capped_at_fifty_with_a_total(fx: Fixture) -> None:
    for i in range(60):
        fx.insert(Row(f"x{i:02d}", WEBSTER, "2026-03-01", 100.0 + i, "transfer", "STRAY", "transfer"))
    check = fx.check("orphan_transfers")
    assert check.status == "warn"
    assert len(check.details) == 50
    assert check.details_total == 60


# ---------------------------------------------------------------------------
# Integrity
# ---------------------------------------------------------------------------


def test_duplicate_keys_fires_on_a_repeated_account_scoped_key(fx: Fixture) -> None:
    assert fx.check("duplicate_keys").status == "pass"
    # The live schema forbids this (UNIQUE), so rebuild the table without it.
    for sql in (
        "DROP VIEW v_computed_balance",
        "DROP VIEW monthly_cashflow_summary",
        "CREATE TABLE t_copy AS SELECT * FROM transactions",
        "DROP TABLE transactions",
        "ALTER TABLE t_copy RENAME TO transactions",
        "INSERT INTO transactions SELECT 'c01-again', account_id, import_batch_id, source_record_key, "
        "source_document_name, occurred_on, posted_on, description_raw, merchant_normalized, amount, "
        "direction, currency, primary_category, subcategory, household_role, lifecycle, "
        "transfer_group_key, is_reconciled, statement_period, metadata_json, created_at "
        "FROM transactions WHERE id = 'c01'",
    ):
        fx.execute(sql)
    check = fx.check("duplicate_keys")
    assert check.status == "fail"
    assert check.details == [{"account_id": CHASE, "source_record_key": "key-c01", "n": 2}]


def test_cross_file_duplicates_fire_and_same_file_repeats_are_details_only(fx: Fixture) -> None:
    baseline = fx.check("cross_file_duplicates")
    assert baseline.status == "pass"
    [same_file] = baseline.details
    assert same_file["kind"] == "same_file_repeats" and same_file["groups"] == 1

    fx.insert(
        Row("c01-ytd", CHASE, "2026-06-05", -120.55, "outflow", "WHOLEFDS #123",
            "variable_lifestyle", "groceries", merchant="Whole Foods"),
        file="2026-06-12_Chase_YTD.csv",
    )
    check = fx.check("cross_file_duplicates")
    assert check.status == "fail"
    cross = [d for d in check.details if d["kind"] == "cross_file"]
    assert len(cross) == 1 and cross[0]["files"] == 2


def test_vocabulary_fires_on_unclassified_rows_and_bad_rule_fields(fx: Fixture) -> None:
    assert fx.check("vocabulary").status == "pass"
    fx.execute("UPDATE transactions SET primary_category = 'unclassified' WHERE id = 'c09'")
    fx.execute("UPDATE classification_rules SET confidence = 'certain' WHERE id = 'rule-amazon'")
    check = fx.check("vocabulary")
    assert check.status == "fail"
    assert {"table": "transactions", "field": "primary_category", "value": "unclassified", "rows": 1} in check.details
    assert any(d.get("rule_id") == "rule-amazon" and d["field"] == "confidence" for d in check.details)


# ---------------------------------------------------------------------------
# Rules
# ---------------------------------------------------------------------------


def _add_rule(fx: Fixture, rule_id: str, pattern: str, category: str, *, priority: int = 100,
              direction: str | None = None) -> None:
    fx.execute(
        "INSERT INTO classification_rules (id, pattern, direction_filter, primary_category, "
        "confidence, priority) VALUES (?, ?, ?, ?, 'high', ?)",
        (rule_id, pattern, direction, category, priority),
    )


def test_income_rule_direction(fx: Fixture) -> None:
    assert fx.check("income_rule_direction").status == "pass"
    fx.execute("UPDATE classification_rules SET direction_filter = NULL WHERE id = 'rule-interest-paid'")
    check = fx.check("income_rule_direction")
    assert check.status == "warn"
    assert _details(check, "rule_id") == {"rule-interest-paid"}


def test_spend_rule_direction_honours_the_exception_list(fx: Fixture) -> None:
    _add_rule(fx, "rule-zara", "(?i)zara", "variable_lifestyle")  # documented exception
    assert fx.check("spend_rule_direction").status == "pass"
    _add_rule(fx, "rule-test-spend", "(?i)target", "variable_lifestyle")
    check = fx.check("spend_rule_direction")
    assert check.status == "warn"
    assert _details(check, "rule_id") == {"rule-test-spend"}


def test_rule_bands(fx: Fixture) -> None:
    _add_rule(fx, "rule-ui", "(?i)ui manual", "variable_lifestyle", priority=7, direction="outflow")
    check = fx.check("rule_bands")
    assert check.status == "info"
    assert _details(check, "rule_id") == {"rule-ui"}

    _add_rule(fx, "rule-bad-band", "(?i)coffee", "variable_lifestyle", priority=5, direction="outflow")
    _add_rule(fx, "rule-bad-regex", "(unclosed", "variable_lifestyle", direction="outflow")
    check = fx.check("rule_bands")
    assert check.status == "fail"
    assert {"rule-bad-band", "rule-bad-regex"} <= _details(check, "rule_id")


# ---------------------------------------------------------------------------
# Overrides
# ---------------------------------------------------------------------------


def test_orphaned_overrides(fx: Fixture) -> None:
    assert fx.check("orphaned_overrides").status == "pass"
    fx.execute(
        "INSERT INTO transaction_overrides (id, match_key, account_id, occurred_on, amount, "
        "description_raw, lifecycle) VALUES ('ov-orphan', 'mk-orphan', ?, '2026-07-12', -61.00, "
        "'TST* THE PLACE', 'one_time')",
        (CHASE,),
    )
    check = fx.check("orphaned_overrides")
    assert check.status == "warn"
    assert _details(check, "override_id") == {"ov-orphan"}


def test_override_in_effect_fires_for_a_lifecycle_only_override_not_applied(fx: Fixture) -> None:
    assert fx.check("override_in_effect").status == "pass"
    # The live symptom: override says one_time, the row says recurring, and the
    # row is still flagged so it looks protected.
    fx.execute("UPDATE transactions SET lifecycle = 'recurring' WHERE id = 'c03'")
    check = fx.check("override_in_effect")
    assert check.status == "warn"
    [detail] = check.details
    assert detail["transaction_id"] == "c03"
    assert detail["differs"] == {"lifecycle": {"override": "one_time", "current": "recurring"}}
    assert detail["manual_override_applied"] is True


def test_override_in_effect_reports_flagged_rows_without_an_override_as_info(fx: Fixture) -> None:
    fx.execute(
        "UPDATE transactions SET metadata_json = json_set(metadata_json, "
        "'$.manual_override_applied', json('true')) WHERE id = 'c09'"
    )
    check = fx.check("override_in_effect")
    assert check.status == "pass"
    assert [d["transaction_id"] for d in check.details if d.get("level") == "info"] == ["c09"]


# ---------------------------------------------------------------------------
# Completeness
# ---------------------------------------------------------------------------


def _paycheck(tx_id: str, on: str) -> Row:
    return Row(tx_id, BEACON, on, 4694.21, "inflow", "MOBILE CHECK DEP", "income", "paycheck",
               "jeff", merchant="Jeff Paycheck")


def test_recurring_income_fires_for_a_missing_paycheck(fx: Fixture) -> None:
    fx.delete("b07")  # July
    check = fx.check("recurring_income")
    assert check.status == "warn"
    [short] = [d for d in check.details if d.get("stream") == "Jeff monthly paycheck" and "found" in d]
    assert (short["month"], short["found"], short["severity"]) == ("2026-07", 0, "warn")

    fx.delete("b12")  # the as-of month
    assert fx.check("recurring_income").status == "fail"


def test_recurring_income_accepts_a_paycheck_dated_the_second_of_the_next_month(fx: Fixture) -> None:
    fx.delete("b07")
    fx.insert(_paycheck("b07-late", "2026-08-02"))
    assert fx.check("recurring_income").status == "pass"


def test_recurring_income_one_paycheck_cannot_satisfy_two_months(fx: Fixture) -> None:
    fx.delete("b04", "b07")
    fx.insert(_paycheck("b-straddle", "2026-07-02"))  # inside both June's and July's windows
    check = fx.check("recurring_income")
    assert check.status == "warn"
    assert [d["month"] for d in check.details if d.get("stream") == "Jeff monthly paycheck"] == ["2026-07"]


def test_recurring_income_reports_label_drift_without_changing_status(fx: Fixture) -> None:
    fx.execute(
        "UPDATE transactions SET subcategory = 'check_deposit', household_role = 'joint' WHERE id = 'b12'"
    )
    check = fx.check("recurring_income")
    assert check.status == "pass"
    [drift] = [d for d in check.details if d.get("kind") == "label_drift"]
    assert drift["transaction_id"] == "b12"
    assert (drift["subcategory"], drift["household_role"]) == ("check_deposit", "joint")


def test_tail_gap_fires_on_a_truncated_monthly_export(fx: Fixture) -> None:
    fx.delete("c07", "c08", "c09")  # Chase August now ends 2026-08-15
    check = fx.check("tail_gap")
    assert check.status == "warn"
    [gap] = check.details
    assert (gap["file"], gap["gap_days"], gap["threshold_days"]) == ("2026-08_Chase_Credit_Card.csv", 16, 5)


def test_tail_gap_fires_when_an_account_has_no_as_of_rows(fx: Fixture) -> None:
    fx.execute("DELETE FROM transactions WHERE account_id = ? AND occurred_on >= '2026-08-01'", (WEBSTER,))
    check = fx.check("tail_gap")
    assert check.status == "warn"
    assert {"account_id": WEBSTER, "month": "2026-08", "problem": "no rows at all for the as-of month"} in check.details


def test_early_download_fires_for_a_file_saved_before_month_end(fx: Fixture) -> None:
    early = datetime(2026, 8, 30, 9, 0).timestamp()
    os.utime(fx.watch_root / "exports" / "2026-08_Chase_Credit_Card.csv", (early, early))
    check = fx.check("early_download")
    assert check.status == "warn"
    [hit] = [d for d in check.details if "problem" in d]
    assert (hit["file"], hit["modified_on"]) == ("2026-08_Chase_Credit_Card.csv", "2026-08-30")


def test_early_download_skips_an_archived_file_with_a_note(fx: Fixture) -> None:
    archive = fx.watch_root / "archive"
    archive.mkdir()
    shutil.move(fx.watch_root / "exports" / "2026-08_Webster_Checking.csv", archive)
    check = fx.check("early_download")
    assert check.status == "pass"
    assert any(d.get("file") == "2026-08_Webster_Checking.csv" and "not found" in d["note"] for d in check.details)


def test_unbalanced_cluster_surfaces_in_orphans_and_unpaired_card_payments(fx: Fixture) -> None:
    fx.delete("w05")  # drop one Webster leg of the 08-03/08-06 cluster
    result = fx.run(checks=["balanced_clusters", "orphan_transfers", "unpaired_card_payments"])
    by_key = {c.key: c for c in result.checks}

    assert len(by_key["balanced_clusters"].details) == 1
    assert by_key["orphan_transfers"].status == "warn"
    assert _details(by_key["orphan_transfers"]) == {"c05", "b09", "w06"}
    assert by_key["unpaired_card_payments"].status == "fail"
    assert _details(by_key["unpaired_card_payments"]) == {"c05"}


def test_unpaired_card_payment_on_the_checking_side(fx: Fixture) -> None:
    fx.insert(Row("b-epay", BEACON, "2026-07-20", -300.00, "transfer", "CHASE CREDIT CRD EPAY", "transfer"))
    check = fx.check("unpaired_card_payments")
    assert check.status == "fail"
    assert _details(check) == {"b-epay"}


# ---------------------------------------------------------------------------
# Balances
# ---------------------------------------------------------------------------


def test_self_anchor_chain_passes_including_the_reverse_chronological_account(fx: Fixture) -> None:
    check = fx.check("self_anchor_chain")
    assert check.status == "pass"
    assert "Webster" in check.summary
    assert not [d for d in check.details if d.get("account_id") == WEBSTER]


def test_self_anchor_chain_fires_on_the_month_end_truncation(fx: Fixture) -> None:
    fx.delete("b10", "b11", "b12")  # the last three rows of Beacon's 2026-08
    check = fx.check("self_anchor_chain")
    assert check.status == "fail"
    [brk] = check.details
    assert (brk["account_id"], brk["month"], brk["prior_month"]) == (BEACON, "2026-09", "2026-08")
    assert brk["delta"] == pytest.approx(2544.21)


def test_self_anchor_chain_checks_the_balances_toml_opening(fx: Fixture) -> None:
    fx.write_balances(BALANCES_TOML.replace(f'"{WEBSTER}" = 20000.00', f'"{WEBSTER}" = 19990.00'))
    check = fx.check("self_anchor_chain")
    assert check.status == "fail"
    [seed] = check.details
    assert (seed["account_id"], seed["month"], seed["delta"]) == (WEBSTER, "2026-06", 10.0)


def test_self_anchor_chain_warns_on_an_ambiguous_boundary_day(fx: Fixture) -> None:
    fx.delete("s08")  # July now ends on the net-zero ACCTVERIFY day
    check = fx.check("self_anchor_chain")
    assert check.status == "warn"
    assert [(d["account_id"], d["day"]) for d in check.details] == [(SELFHELP, "2026-07-15")]


def test_stored_reconciliation_baseline_suppresses_the_known_ally_variance(fx: Fixture) -> None:
    check = fx.check("stored_reconciliation")
    assert check.status == "pass"
    assert check.suppressed_by == ["ally-checkpoint-2026-06-02"]
    [card] = [d for d in check.details if d.get("kind") == "credit_card"]
    assert card["account_id"] == CHASE and card["level"] == "info"
    assert set(card) >= {"chain_close", "v_computed_balance"}


def test_stored_reconciliation_fires_on_a_self_anchoring_variance_or_stale_row(fx: Fixture) -> None:
    where = "account_id = ? AND period_start = '2026-08-01' AND period_end = '2026-08-31'"
    fx.execute(f"UPDATE reconciliation_periods SET variance_amount = 5.0 WHERE {where}", (BEACON,))
    fx.execute(
        f"UPDATE reconciliation_periods SET computed_closing_balance = computed_closing_balance - 100 WHERE {where}",
        (WEBSTER,),
    )
    check = fx.check("stored_reconciliation")
    assert check.status == "warn"
    problems = {d["account_id"]: d["problem"] for d in check.details if "problem" in d}
    assert "variance $5.00" in problems[BEACON]
    assert "stale" in problems[WEBSTER]


def test_stored_reconciliation_fires_on_a_later_checkpoint_disagreeing(fx: Fixture) -> None:
    fx.write_balances(BALANCES_TOML.replace('"2026-08-01" = 13363.00', '"2026-08-01" = 13400.00'))
    check = fx.check("stored_reconciliation")
    assert check.status == "warn"
    [finding] = [d for d in check.details if d.get("period_end") == "2026-08-01"]
    assert finding["variance"] == pytest.approx(37.0)
    assert finding["computed_balance"] == pytest.approx(13363.0)


# ---------------------------------------------------------------------------
# Transfers
# ---------------------------------------------------------------------------


def test_balanced_clusters_recognise_both_live_shapes(fx: Fixture) -> None:
    check = fx.check("balanced_clusters")
    assert check.status == "info"
    assert "2 balanced clusters, 8 legs" in check.summary
    shapes = [(c["first"], c["last"], c["accounts"], len(c["legs"])) for c in check.details]
    assert shapes == [
        ("2026-06-25", "2026-06-27", sorted([BEACON, WEBSTER]), 4),
        ("2026-08-03", "2026-08-06", sorted([BEACON, CHASE, WEBSTER]), 4),
    ]


def test_orphan_transfers_baseline_counts(fx: Fixture) -> None:
    check = fx.check("orphan_transfers")
    assert check.status == "pass"
    assert check.summary == "0 unexplained unpaired transfer rows (4 benign-suppressed, 8 in balanced clusters)"
    assert check.suppressed_by == ["acctverify-2026-07-15", "axos-2026-02-12"]


def test_orphan_transfers_catches_an_inflow_coded_transfer(fx: Fixture) -> None:
    """The January-bonus shape: pair_transfers only examines direction='transfer',
    so an inflow coded ``transfer`` was invisible to it for nine months."""
    fx.insert(Row("bonus", BEACON, "2026-08-20", 16500.00, "inflow", "MOBILE CHECK DEP", "transfer"))
    check = fx.check("orphan_transfers")
    assert check.status == "warn"
    assert _details(check) == {"bonus"}


def test_orphan_transfers_clears_when_the_row_is_in_a_balanced_cluster(fx: Fixture) -> None:
    fx.insert(Row("bonus", BEACON, "2026-08-20", 16500.00, "inflow", "MOBILE CHECK DEP", "transfer"))
    fx.insert(Row("bonus-out", WEBSTER, "2026-08-21", -16500.00, "transfer", "ONLINE TRANSFER", "transfer"))
    check = fx.check("orphan_transfers")
    assert check.status == "pass"
    assert "10 in balanced clusters" in check.summary


def test_orphan_transfers_clears_when_a_benign_entry_matches(fx: Fixture) -> None:
    fx.insert(Row("bonus", BEACON, "2026-08-20", 16500.00, "inflow", "MOBILE CHECK DEP", "transfer"))
    fx.write_config(
        TEMPLATE_PATH.read_text(encoding="utf-8")
        + f"""
[[benign]]
id = "bonus-2026-08-20"
check = "orphan_transfers"
account_id = "{BEACON}"
occurred_on = "2026-08-20"
description_pattern = "(?i)mobile check dep"
amount = 16500.00
"""
    )
    check = fx.check("orphan_transfers")
    assert check.status == "pass"
    assert "bonus-2026-08-20" in check.suppressed_by


def test_pair_integrity(fx: Fixture) -> None:
    assert fx.check("pair_integrity").status == "pass"
    fx.execute("UPDATE transactions SET transfer_group_key = 'grp-lonely' WHERE id = 'c05'")
    check = fx.check("pair_integrity")
    assert check.status == "fail"
    assert _details(check, "transfer_group_key") == {"grp-lonely"}


def test_pair_integrity_hints_to_run_pair_transfers_after_an_ingest(fx: Fixture) -> None:
    fx.execute("UPDATE transactions SET transfer_group_key = NULL")
    check = fx.check("pair_integrity")
    assert check.status == "fail"
    assert "run pair_transfers first" in check.summary


# ---------------------------------------------------------------------------
# Mortgage
# ---------------------------------------------------------------------------


def test_mortgage_schedule_miss(fx: Fixture) -> None:
    assert fx.check("mortgage_schedule_miss").status == "pass"
    fx.execute("UPDATE transactions SET amount = -1000.00 WHERE id = 'b05'")  # below the floor
    check = fx.check("mortgage_schedule_miss")
    assert check.status == "warn"
    assert _details(check, "month") == {"2026-07"}


def _balances_with_mortgage(block: str) -> str:
    return BALANCES_TOML.replace(MORTGAGE_BLOCK, block)


def test_mortgage_anchor_stale_fires_when_rows_outrun_the_anchors(fx: Fixture) -> None:
    stale = MORTGAGE_BLOCK.split('[[mortgage.ion.anchor]]\nstatement_date = "2026-08-18"')[0]
    fx.write_balances(_balances_with_mortgage(stale))
    check = fx.check("mortgage_anchor_stale")
    assert check.status == "warn"
    assert check.details[0]["latest_anchor_month"] == "2026-07"
    assert check.details[0]["latest_mortgage_month"] == "2026-09"


def test_mortgage_anchor_stale_uses_the_flat_anchor_fallback(fx: Fixture) -> None:
    flat = """
[mortgage.ion]
merchant_match         = "IonBank Mortgage"
scheduled_payment      = 4044.66
principal_and_interest = 2611.83
escrow                 = 1432.83
annual_rate            = 0.04625
anchor_date            = "{anchor}"
anchor_balance         = 475016.69
"""
    fx.write_balances(_balances_with_mortgage(flat.format(anchor="2026-09-17")))
    check = fx.check("mortgage_anchor_stale")
    assert check.status == "pass"
    assert check.details[0]["anchor_source"] == "flat anchor_date"

    fx.write_balances(_balances_with_mortgage(flat.format(anchor="2026-06-17")))
    assert fx.check("mortgage_anchor_stale").status == "warn"


def test_mortgage_anchor_stale_fires_without_a_mortgage_block(fx: Fixture) -> None:
    fx.write_balances(_balances_with_mortgage(""))
    check = fx.check("mortgage_anchor_stale")
    assert check.status == "warn"
    assert "no [mortgage.ion] block" in check.summary


# ---------------------------------------------------------------------------
# Summaries
# ---------------------------------------------------------------------------


def test_monthly_summary_parser_round_trips_the_renderer(fx: Fixture) -> None:
    balances = load_balances(fx.watch_root)
    summary = compute_monthly_summary(
        fx.database, balances.wealth_bridge, 2026, 8, mortgage=balances.mortgage
    )
    parsed = vs.parse_monthly_summary(render_markdown(summary, "\nnotes\n"))
    assert parsed == {
        "inflows": summary["fcf_transactions"]["inflows"],
        "fixed_obligations": summary["fcf_transactions"]["fixed_obligations"],
        "discretionary": summary["fcf_transactions"]["discretionary"],
        "net_fcf": summary["fcf_transactions"]["net_fcf"],
        "net_consumption": summary["consumption"]["net_consumption"],
        "mortgage_principal_scheduled": summary["consumption"]["mortgage_principal_scheduled"],
        "debt_paydown_nonscheduled": summary["consumption"]["debt_paydown_nonscheduled"],
    }
    assert parsed["mortgage_principal_scheduled"] > 0


def test_annual_summary_parser_round_trips_the_renderer(fx: Fixture) -> None:
    balances = load_balances(fx.watch_root)
    summary = compute_annual_summary(fx.database, balances.wealth_bridge, 2026, mortgage=balances.mortgage)
    parsed = vs.parse_annual_summary(render_annual_markdown(summary, "\nnotes\n"))
    assert parsed["totals"] == {
        **summary["totals"],
        "net_consumption": summary["consumption_totals"]["net_consumption"],
    }
    assert parsed["months"] == {
        row["month"]: {
            "income": row["income"],
            "fixed_obligations": row["fixed_obligations"],
            "discretionary": row["discretionary"],
            "net_fcf": row["net_fcf"],
            "net_consumption": row["consumption"]["net_consumption"],
        }
        for row in summary["months"]
    }
    assert any(row["net_fcf"] < 0 for row in summary["months"])  # exercises the U+2212 sign


@pytest.mark.parametrize(
    ("text", "value"),
    [("$1,234.56", 1234.56), ("**$-1,234.56**", -1234.56), ("−$50.00", -50.0), ("+$0.00", 0.0)],
)
def test_summary_money_formats(text: str, value: float) -> None:
    assert vs._parse_money(text) == value


def _summary_file(fx: Fixture, name: str) -> Path:
    return fx.watch_root / "monthly_summaries" / name


def test_summary_drift_fires_when_a_stored_figure_differs(fx: Fixture) -> None:
    path = _summary_file(fx, "2026-08_Monthly_Cashflow_Summary.md")
    text = path.read_text(encoding="utf-8")
    [line] = [l for l in text.splitlines() if l.startswith("| Inflows this month |")]
    path.write_text(text.replace(line, "| Inflows this month | $1.00 |"), encoding="utf-8")

    check = fx.check("summary_drift")
    assert check.status == "warn"
    assert "DL-2026-08-03-C" in check.summary
    [drifted] = [d for d in check.details if d.get("in_sync") is False]
    assert [delta["field"] for delta in drifted["deltas"]] == ["inflows"]


def test_summary_drift_after_an_ingest_moves_the_month_and_the_annual(fx: Fixture) -> None:
    fx.insert(Row("c-late", CHASE, "2026-07-30", -44.00, "outflow", "LATE POSTING", "variable_lifestyle",
                  "dining_out", merchant="Late"))
    check = fx.check("summary_drift")
    assert check.status == "warn"
    drifted = {d["file"]: d["deltas"] for d in check.details if d.get("in_sync") is False}
    assert set(drifted) == {
        "monthly_summaries/2026-07_Monthly_Cashflow_Summary.md",
        "monthly_summaries/2026_Annual_Cashflow_Summary.md",
    }
    july = {d["field"]: d["delta"] for d in drifted["monthly_summaries/2026-07_Monthly_Cashflow_Summary.md"]}
    assert july["discretionary"] == 44.0 and july["net_fcf"] == -44.0


def test_summary_drift_warns_on_an_unparseable_file(fx: Fixture) -> None:
    _summary_file(fx, "2026-07_Monthly_Cashflow_Summary.md").write_text("garbage\n", encoding="utf-8")
    check = fx.check("summary_drift")
    assert check.status == "warn"
    assert "unparseable: monthly_summaries/2026-07_Monthly_Cashflow_Summary.md" in check.summary


def test_summary_drift_lists_months_without_a_stored_file_as_info(fx: Fixture) -> None:
    _summary_file(fx, "2026-06_Monthly_Cashflow_Summary.md").unlink()
    check = fx.check("summary_drift")
    assert check.status == "info"
    assert [d["month"] for d in check.details if "month" in d] == ["2026-06"]


def test_status_freshness(fx: Fixture) -> None:
    assert fx.check("status_freshness").status == "pass"
    status = fx.watch_root / "STATUS.md"

    status.write_text(f"_Last verified: {(date.today() - timedelta(days=40)).isoformat()}_\n", encoding="utf-8")
    check = fx.check("status_freshness")
    assert check.status == "warn" and "40 days ago" in check.summary

    status.write_text(f"_Last verified: {(date.today() - timedelta(days=3)).isoformat()}_\n", encoding="utf-8")
    check = fx.check("status_freshness")
    assert check.status == "warn" and "before the latest import" in check.summary

    status.unlink()
    assert fx.check("status_freshness").status == "warn"


# ---------------------------------------------------------------------------
# Labels
# ---------------------------------------------------------------------------


def test_taxonomy_reports_aliases_and_non_canonical_subcategories(fx: Fixture) -> None:
    fx.execute("UPDATE transactions SET subcategory = 'dining' WHERE id IN ('c07', 'c08')")
    fx.execute("UPDATE transactions SET subcategory = NULL WHERE id = 'c09'")
    check = fx.check("taxonomy")
    assert check.status == "warn"
    assert {"kind": "alias", "primary_category": "variable_lifestyle", "subcategory": "dining",
            "canonical": "dining_out", "rows": 2} in check.details
    assert {"kind": "not_canonical", "primary_category": "variable_lifestyle", "subcategory": None,
            "rows": 1} in check.details


def test_taxonomy_lists_multi_category_subcategories_as_info(fx: Fixture) -> None:
    fx.execute("UPDATE transactions SET subcategory = 'refund' WHERE id = 'w02'")  # income/refund
    fx.execute("UPDATE transactions SET subcategory = 'refund' WHERE id = 'c04'")  # variable_lifestyle/refund
    check = fx.check("taxonomy")
    assert check.status == "pass"
    [multi] = [d for d in check.details if d["kind"] == "multi_category_subcategory"]
    assert multi["primary_categories"] == ["income", "variable_lifestyle"]


def test_null_merchant_spend(fx: Fixture) -> None:
    assert fx.check("null_merchant_spend").status == "pass"
    fx.execute("UPDATE transactions SET merchant_normalized = NULL WHERE id = 'c09'")
    check = fx.check("null_merchant_spend")
    assert check.status == "info"
    assert check.summary == "1 spend rows / $30.00 with NULL merchant_normalized"


# ---------------------------------------------------------------------------
# Meta: benign entries, config, as-of month
# ---------------------------------------------------------------------------


def test_benign_entry_stops_suppressing_when_its_amount_no_longer_matches(fx: Fixture) -> None:
    fx.execute("UPDATE transactions SET amount = -150.00 WHERE id = 'a01'")
    result = fx.run(checks=["benign_stale", "orphan_transfers"])
    orphans, stale = result.checks  # registry order
    assert stale.status == "warn"
    [entry] = stale.details
    assert entry["id"] == "axos-2026-02-12" and "amount" in entry["problem"]
    assert orphans.status == "warn" and _details(orphans) == {"a01"}
    assert orphans.suppressed_by == ["acctverify-2026-07-15"]


def test_benign_entry_stops_suppressing_when_its_expected_count_no_longer_matches(fx: Fixture) -> None:
    fx.insert(Row("a-axos-2", ALLY, "2026-03-01", -100.00, "transfer", "AXOS BANK TRANSFER", "transfer"))
    result = fx.run(checks=["benign_stale", "orphan_transfers"])
    orphans, stale = result.checks
    assert stale.status == "warn"
    assert "expected_count 1, found 2" in stale.details[0]["problem"]
    assert _details(orphans) == {"a01", "a-axos-2"}


def test_benign_entry_that_suppresses_nothing_is_stale(fx: Fixture) -> None:
    fx.execute("UPDATE transactions SET transfer_group_key = 'grp-axos' WHERE id = 'a01'")
    check = fx.check("benign_stale")
    assert check.status == "warn"
    assert check.details[0]["problem"].startswith("suppressed nothing")


def test_period_benign_entry_is_falsified_by_a_changed_variance(fx: Fixture) -> None:
    fx.execute(
        "UPDATE reconciliation_periods SET variance_amount = 2500.0 "
        "WHERE account_id = ? AND period_end = '2026-06-02'",
        (ALLY,),
    )
    result = fx.run(checks=["stored_reconciliation", "benign_stale"])
    recon, stale = result.checks
    assert recon.status == "warn" and recon.suppressed_by == []
    assert stale.status == "warn" and "variance $2,400.00, found $2,500.00" in stale.details[0]["problem"]


def test_config_source_template_and_watch_root(fx: Fixture) -> None:
    check = fx.check("config_source")
    assert check.status == "info" and check.details[0]["source"] == "template"
    path = fx.write_config(TEMPLATE_PATH.read_text(encoding="utf-8"))
    check = fx.check("config_source")
    assert check.status == "info"
    assert check.details[0] == {"source": "watch_root", "path": str(path)}


@pytest.mark.parametrize(
    ("text", "message"),
    [
        ("[completeness\nanchor_accounts = []\n", "verify_state.toml"),
        ('[completeness]\ncard_payment_patterns = ["(unclosed"]\n', "invalid regex"),
        ('[[benign]]\ncheck = "orphan_transfers"\n', "benign[0].id: required string"),
    ],
)
def test_malformed_config_fails_loudly(fx: Fixture, text: str, message: str) -> None:
    fx.write_config(text)
    result = fx.run()
    assert result.status == "error"
    assert result.checks == []
    assert message in result.error
    assert result.error.startswith("verify_state.toml is malformed:")


def test_as_of_month_names_the_limiting_account(fx: Fixture) -> None:
    check = fx.check("as_of_month")
    assert check.status == "info"
    assert "limited by acct-chase-credit-card" in check.summary
    assert f"{BEACON} reaches 2026-09" in check.summary


def test_explicit_month_later_than_the_latest_complete_month_warns(fx: Fixture) -> None:
    result = fx.run(month="2026-09", checks=["as_of_month", "recurring_income"])
    as_of, income = result.checks[1], result.checks[0]
    assert result.as_of_month == "2026-09"
    assert as_of.status == "warn" and "incomplete" in as_of.summary
    assert income.status == "fail"  # September has no paycheck yet


# ---------------------------------------------------------------------------
# Read-only + write mode
# ---------------------------------------------------------------------------


def test_database_is_byte_identical_after_a_run_in_both_modes(fx: Fixture) -> None:
    db = fx.database.database_path
    before = _sha(db)
    fx.run(write=False)
    assert _sha(db) == before
    fx.run(write=True)
    assert _sha(db) == before


def test_write_touches_only_status_data_and_the_exports(fx: Fixture) -> None:
    before = _snapshot(fx.watch_root)
    result = fx.run(write=True)
    after = _snapshot(fx.watch_root)

    changed = {path for path in after if before.get(path) != after[path]}
    removed = set(before) - set(after)
    today = date.today().isoformat()
    assert removed == set()
    assert changed == {
        "STATUS_DATA.md",
        "_state_exports/transaction_overrides.csv",
        "_state_exports/classification_rules.csv",
        f"_state_exports/verify_state_{today}.md",
    }
    assert result.written is not None and result.written.errors == []
    assert result.written.status_data == str(fx.watch_root / "STATUS_DATA.md")

    status_data = (fx.watch_root / "STATUS_DATA.md").read_bytes()
    assert status_data.startswith(b"<!-- Generated by verify_state at ")
    assert status_data.splitlines()[0].endswith(b"Do not hand-edit; rerun verify_state. -->")
    for path in (fx.watch_root / "_state_exports").iterdir():
        assert b"\r\n" not in path.read_bytes()
    assert b"\r\n" not in status_data

    # Same-day reruns overwrite rather than accumulate.
    fx.run(write=True)
    assert set(_snapshot(fx.watch_root)) == set(after)


def test_state_export_csvs_carry_every_column_in_created_order(fx: Fixture) -> None:
    fx.run(write=True)
    exports = fx.watch_root / "_state_exports"
    for table, name in (
        ("transaction_overrides", "transaction_overrides.csv"),
        ("classification_rules", "classification_rules.csv"),
    ):
        lines = (exports / name).read_text(encoding="utf-8").splitlines()
        columns = [row["name"] for row in fx.query(f"PRAGMA table_info({table})")]
        assert lines[0].split(",") == columns
        ids = [row["id"] for row in fx.query(f"SELECT id FROM {table} ORDER BY created_at, id")]
        assert len(lines) == len(ids) + 1
        assert [line.split(",")[0] for line in lines[1:]] == ids
    assert "\\N" in (exports / "classification_rules.csv").read_text(encoding="utf-8")


def test_status_data_carries_the_data_block(fx: Fixture) -> None:
    result = fx.run(write=True)
    text = (fx.watch_root / "STATUS_DATA.md").read_text(encoding="utf-8")
    for heading in (
        "## Transactions by account",
        "## primary_category counts",
        "## Overrides",
        "## Classification rules",
        "## Transfers",
        "## Latest reconciliation per account",
        "## Ally HYSA gate",
        "## Stored summaries",
        "## Database",
        "## Checks",
    ):
        assert heading in text
    transfers = result.metrics["transfers"]
    assert transfers["groups"] == 2
    assert transfers["unpaired_by_disposition"] == {
        "benign:acctverify-2026-07-15": 3,
        "benign:axos-2026-02-12": 1,
        "balanced_cluster": 8,
        "orphan": 0,
    }
    assert result.metrics["overrides"] == {"count": 1, "rows_matching": 1, "rows_stamped": 1}
    assert result.metrics["errors"] == []


def test_unwritable_outputs_are_reported_not_raised(fx: Fixture) -> None:
    (fx.watch_root / "STATUS_DATA.md").mkdir()  # a directory where the file should go
    (fx.watch_root / "_state_exports").write_text("in the way", encoding="utf-8")
    result = fx.run(write=True)
    assert result.status == "pass"
    assert result.written is not None
    assert result.written.status_data is None
    assert len(result.written.errors) == 2


def test_generated_files_are_never_assigned_to_a_tracker_row(fx: Fixture) -> None:
    fx.write_config(TEMPLATE_PATH.read_text(encoding="utf-8"))
    fx.run(write=True)
    result = read_document_metadata(fx.settings, fx.database, _NoWatcher())
    matched = [m.relative_path for item in result.items for m in item.matched_files]
    generated = ("verify_state.toml", "STATUS_DATA.md", "_state_exports/")
    assert not [p for p in matched if p.startswith(generated)]


def test_generated_file_names_score_below_the_tracker_threshold() -> None:
    rows = load_tracker_rows(TEMPLATE_PATH.parents[2] / "docs" / "Spreadsheet_checklist_for_document_tracking.csv")
    best = {
        name: max(score_candidate(row, Path(name)) for row in rows)
        for name in ("verify_state.toml", "STATUS_DATA.md")
    }
    assert round(best["verify_state.toml"], 3) == 0.144
    assert round(best["STATUS_DATA.md"], 3) == 0.185
    assert all(score < MATCH_THRESHOLD for score in best.values())


class _NoWatcher:
    def recent_events(self) -> list:
        return []


def test_checkpoint_on_the_first_of_the_seed_month_verifies_against_the_seed(fx: Fixture) -> None:
    # 2026-01-01 checkpoint: the period ending the day before is the Dec-2025
    # seed row, which has a statement closing but no computed side.
    fx.write_balances(
        BALANCES_TOML.replace('"2026-06-02" = 12320.00', '"2026-01-01" = 10000.00\n"2026-06-02" = 12320.00')
    )
    from liquidity_gate_mcp.computed_balance import seed_balance_anchors

    seed_balance_anchors(fx.database, load_balances(fx.watch_root))
    assert fx.check("stored_reconciliation").status == "pass"

    fx.write_balances(
        BALANCES_TOML.replace('"2026-06-02" = 12320.00', '"2026-01-01" = 10005.00\n"2026-06-02" = 12320.00')
    )
    check = fx.check("stored_reconciliation")
    assert check.status == "warn"
    [finding] = [d for d in check.details if d.get("period_end") == "2026-01-01"]
    assert finding["variance"] == pytest.approx(5.0)
