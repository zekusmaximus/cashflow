# Changelog

Notable behavior changes to the Liquidity Gate MCP server and its generated
documents. Design rationale lives in [docs/DECISION_LOG.md](docs/DECISION_LOG.md).

## 2026-09-23

### Removed

- **Monthly summary: the `hysa_delta_below_floor` auto-flag.** On 2026-09-23
  Jeff retired the household's month-end Beacon→Ally sweep rule. Money now moves
  into the Ally HYSA only when he chooses to move it, so a month with a small or
  zero HYSA delta is expected, not a "leak", and the flag
  (`"HYSA delta $X — below $2,500 floor. Investigate source of leak."`) would
  fire as a recurring false alarm. `compute_monthly_summary` /
  `generate_monthly_summary` no longer emit it under `flags.auto`; their
  signatures and output shape are unchanged. The `hysa` block (balance, $80,000
  target, gap, monthly delta, trailing 3-month average, projected hit date,
  months remaining) is still reported as information, and the
  `savings_rate_below_floor`, `discretionary_over_ceiling`, `abnormal_outflow`
  and `implied_withholding_negative` flags are unchanged.
- **`[wealth_bridge].hysa_floor_monthly_delta` (the $2,500 floor).** It fed
  only that flag, so it is gone from `server/templates/balances.template.toml`
  and from `WealthBridgeConfig`. An older `balances.toml` that still carries the
  key loads normally; the loader ignores keys it does not read.
- Stored `monthly_summaries/*.md` files rendered before this change still show
  the old flag line in their auto-flags block until they are regenerated with
  `generate_monthly_summary`. `verify_state`'s `summary_drift` check compares
  dollar figures only, so it does not report those files as drifted.
