"""Markdown rendering for the monthly cashflow summary.

Kept separate from the compute pass so layout edits never touch the numbers.
No Jinja dependency — the document is a single fixed layout, so f-strings are
both lighter and more readable.

Re-run safety: the flag section is split into an auto-generated block and a
manual-notes block. ``write_monthly_summary`` reads any existing file first,
lifts the manual block out verbatim, and re-emits it inside the freshly
rendered document so hand-written review notes survive regeneration.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

# Markers delimiting the two halves of Section 4. The manual block between
# MANUAL NOTES markers is preserved verbatim across regeneration.
_AUTO_OPEN = "<!-- AUTO-FLAGS -->"
_AUTO_CLOSE = "<!-- /AUTO-FLAGS -->"
_MANUAL_OPEN = "<!-- MANUAL NOTES -->"
_MANUAL_CLOSE = "<!-- /MANUAL NOTES -->"

_MANUAL_BLOCK_RE = re.compile(
    re.escape(_MANUAL_OPEN) + r"(.*?)" + re.escape(_MANUAL_CLOSE),
    re.DOTALL,
)

_DEFAULT_MANUAL_BODY = (
    "\n_Add manual review notes below this line. They are preserved verbatim "
    "when this summary is regenerated._\n\n"
)


def extract_manual_block(path: Path) -> str | None:
    """Return the inner text of an existing file's manual-notes block.

    ``None`` when the file does not exist or carries no manual block — the
    caller then falls back to the default placeholder body.
    """
    if not path.exists():
        return None
    match = _MANUAL_BLOCK_RE.search(path.read_text(encoding="utf-8"))
    return match.group(1) if match else None


def _money(value: float) -> str:
    return f"${value:,.2f}"


def _pct(value: float) -> str:
    return f"{value:,.2f}%"


def _signed_money(value: float) -> str:
    sign = "+" if value >= 0 else "−"
    return f"{sign}{_money(abs(value))}"


def _render_projection(hysa: dict[str, Any]) -> str:
    if hysa["projected_hit_date"] == "indefinite":
        return "**indefinite** — trailing 3-month net HYSA delta is ≤ $0"
    months = hysa["months_remaining"]
    suffix = "month" if months == 1 else "months"
    return f"**{hysa['projected_hit_date']}** ({months} {suffix} out)"


def _render_movers(movers: list[dict[str, Any]]) -> str:
    if not movers:
        return "_No qualifying merchant-level movers this month._"
    lines = []
    for mover in movers:
        if mover["is_new"]:
            lines.append(
                f"- {mover['merchant']}: {_money(mover['this_month'])} (new this month)"
            )
        elif mover["this_month"] == 0 and mover["prior_month"] > 0:
            # A merchant that spent last month and nothing this month reads
            # better as "stopped" than as "$0.00 (Δ −$X)".
            lines.append(
                f"- {mover['merchant']}: stopped this month "
                f"(was {_money(mover['prior_month'])})"
            )
        else:
            lines.append(
                f"- {mover['merchant']}: {_money(mover['this_month'])} "
                f"(Δ {_signed_money(mover['delta'])} from prior month)"
            )
    return "\n".join(lines)


def _render_auto_flags(flags: list[dict[str, Any]]) -> str:
    if not flags:
        return "No flags this month."
    return "\n".join(
        f"- [{flag['severity'].upper()}] {flag['message']}" for flag in flags
    )


def render_markdown(summary: dict[str, Any], manual_body: str) -> str:
    """Render the full summary document. ``manual_body`` is re-emitted as-is."""
    period = summary["period"]
    hysa = summary["hysa"]
    fcf_t = summary["fcf_transactions"]
    fcf_h = summary["fcf_theoretical"]
    spend = summary["spend"]
    methodology = summary["methodology"]
    auto_flags = summary["flags"]["auto"]

    mom_pct = spend["mom_delta_pct"]

    # Presentational guard: while 401(k) is unconfigured the theoretical rate
    # understates real savings — warn readers not to act on it.
    if fcf_h.get("config_incomplete"):
        theoretical_banner = (
            "\n> ⚠️ **Theoretical view incomplete:** 401(k) contributions are not "
            "yet configured.\n"
            "> The values below understate the actual savings rate. Update "
            "`[wealth_bridge]` in\n"
            "> `balances.toml` after the next Novartis paystub.\n"
        )
    else:
        theoretical_banner = ""

    return f"""# {period['label']} Monthly Cashflow Summary

_Generated {summary['generated_at']}. Bridge document for the wealth-tracker
project — both this file and the structured JSON come from one compute pass._

## 1. HYSA Status

- **Current HYSA balance:** {_money(hysa['balance'])}
- **Monthly net delta:** {_signed_money(hysa['monthly_delta'])}
- **Gap to {_money(hysa['target'])} target:** {_money(hysa['gap'])}
- **Trailing 3-month avg net delta:** {_signed_money(hysa['trailing_3mo_avg_delta'])}
- **Projected $80K hit date:** {_render_projection(hysa)}

## 2. Net Free Cash Flow + Savings Rate

### Transactions view — reproducible from the transaction database

| Metric | Amount |
| --- | ---: |
| Inflows this month | {_money(fcf_t['inflows'])} |
| Fixed obligations | {_money(fcf_t['fixed_obligations'])} |
| Discretionary outflows | {_money(fcf_t['discretionary'])} |
| **Net free cash flow** | **{_money(fcf_t['net_fcf'])}** |
| **Effective savings rate** | **{_pct(fcf_t['savings_rate_pct'])}** |

### Theoretical view — config-sourced

> These figures are config-sourced from `[wealth_bridge]` in `balances.toml`,
> not derived from transactions. They are only as accurate as that config.
{theoretical_banner}
| Metric | Amount |
| --- | ---: |
| Gross monthly income | {_money(fcf_h['gross_monthly'])} |
| Tax-advantaged contributions (401k + HSA) | {_money(fcf_h['tax_advantaged_contributions'])} |
| Implied withholding | {_money(fcf_h['implied_withholding'])} |
| Theoretical savings | {_money(fcf_h['theoretical_savings'])} |
| **Effective savings rate (theoretical)** | **{_pct(fcf_h['savings_rate_pct'])}** |
| Target savings rate | {_pct(fcf_h['target_pct'])} |
| **Delta vs. target** | **{fcf_h['delta_vs_target_pct']:+,.2f} pts** |

Implied withholding is implied from the gap between the gross config and
actual payroll deposits — `gross − tax_advantaged − payroll_inflows`.

## 3. Spend Narrative

- **This month's discretionary spend:** {_money(spend['this_month_discretionary'])}
- **Prior month's discretionary spend:** {_money(spend['prior_month_discretionary'])}
- **Month-over-month change:** {_signed_money(spend['mom_delta'])} ({mom_pct:+,.2f}%)
- **YTD discretionary total:** {_money(spend['ytd_discretionary'])}
- **YTD annualized (×12 / elapsed months):** {_money(spend['ytd_annualized'])}

**Top 3 movers — merchant level:**

{_render_movers(spend['top_movers'])}

## 4. Flag for Review

{_AUTO_OPEN}
{_render_auto_flags(auto_flags)}
{_AUTO_CLOSE}

{_MANUAL_OPEN}{manual_body}{_MANUAL_CLOSE}

---

### Methodology

- **Discretionary scope:** outflows where `primary_category` ∈
  {methodology['discretionary_categories']}. Transfers, taxes,
  fixed_obligation, investment, rental, business_expense and income are
  excluded by definition.
- **HYSA monthly delta:** net change in the HYSA account balance from
  transactions in the target month, all categories included (account scope is
  the filter — inbound transfers count positively as the savings mechanism,
  outbound drawdowns negatively, interest counts).
- **HYSA projection basis:** {methodology['hysa_projection_basis']} — the
  trailing 3-month average net delta extrapolated forward. A trailing average
  of ≤ $0 yields "indefinite" rather than a date.
- **Month boundary:** {methodology['month_boundary']}.
- **Merchant normalization:** {methodology['merchant_normalization']}
- **Schedule:** generated by a Cowork scheduled task on the 1st of each month
  (shifted to the second business day when the 1st is a weekend or US federal
  holiday), always targeting the prior calendar month.
"""


def write_monthly_summary(
    summary: dict[str, Any],
    output_root: Path,
    output_dir_name: str,
) -> tuple[Path, bool]:
    """Render and write the summary markdown under ``output_root``.

    Returns ``(path, manual_block_preserved)``. The output directory is created
    if missing. When a file already exists at the target path its manual-notes
    block is lifted out and re-emitted so hand-written notes survive the re-run.
    """
    output_dir = Path(output_root) / output_dir_name
    output_dir.mkdir(parents=True, exist_ok=True)

    path = output_dir / f"{summary['period']['label']}_Monthly_Cashflow_Summary.md"

    existing_manual = extract_manual_block(path)
    manual_preserved = existing_manual is not None
    manual_body = existing_manual if manual_preserved else _DEFAULT_MANUAL_BODY

    path.write_text(render_markdown(summary, manual_body), encoding="utf-8")
    return path, manual_preserved
