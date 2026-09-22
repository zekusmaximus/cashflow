# Liquidity Gate MCP Server

The server is a local-only bridge between the Cash Flow folder, the shared SQLite database, and MCP-compatible LLM clients.

## Tools

- `read_document_metadata(folder_path?)`: scans the local document folder, compares discovered files against the tracker CSV, and persists the latest document coverage snapshot.
- `reconcile_transactions(request)`: validates an LLM-parsed transaction payload with Pydantic and upserts normalized rows into SQLite.
- `query_cashflow_data(request)`: runs read-only SQL queries for summaries, anomaly checks, and downstream dashboard work.
- `verify_state(request?)`: runs every post-ingest check in one call (integrity, rules, overrides,
  completeness, balances, transfers, mortgage, summaries, labels) and returns pass / info / warn /
  fail per check. Read-only against the database. Run it after `ingest_documents` →
  `pair_transfers` → `reconcile_periods`. `{"write": true}` also writes `<watch_root>/STATUS_DATA.md`
  and `<watch_root>/_state_exports/` (the overrides and rules CSVs, restorable with
  `scripts/restore_state_from_export.py`); nothing else. Configured by `<watch_root>/verify_state.toml`
  (template: `server/templates/verify_state.template.toml`).

## Ingestion behavior

- **Scan scope.** Both `read_document_metadata` and `ingest_documents` scan
  the watch root recursively via the same `iter_candidate_files` helper, so
  they always agree on which files are in scope. Set-aside directories are
  skipped: any `archive/` segment, and any directory whose name begins with
  `.` or `_` (e.g. `_superseded_inputs`, `_probe_hold`). Move a retired file
  into one of those (or out of the watch root) and it will not be re-ingested.
- **Dedup identity.** Transactions are de-duplicated per **account**, not per
  source filename: `(account_id, source_record_key)`. Parsers derive
  `source_record_key` from stable, format-independent content (date + amount +
  normalized description + a deterministic occurrence ordinal for genuine
  same-day/same-amount duplicates). The same transaction therefore collapses
  to one row whether it arrives in a YTD export or a per-month file, so
  re-importing an overlapping file is idempotent. Existing databases are moved
  to the matching DB-level `UNIQUE(account_id, source_record_key)` constraint
  by `scripts/migrations/2026-05-31_transactions_account_scoped_unique.py`.

## Resources

- `docs://master-index`
- `docs://tracker`
- `watch://recent-events`

## Prompt

- `financial_detective`

## Development

```powershell
python -m venv .venv
.venv/Scripts/Activate.ps1
python -m pip install --upgrade pip
python -m pip install -e .
python -m liquidity_gate_mcp.server
```
