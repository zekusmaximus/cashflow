# Liquidity Gate MCP Server

The server is a local-only bridge between the Cash Flow folder, the shared SQLite database, and MCP-compatible LLM clients.

## Tools

- `read_document_metadata(folder_path?)`: scans the local document folder, compares discovered files against the tracker CSV, and persists the latest document coverage snapshot.
- `reconcile_transactions(request)`: validates an LLM-parsed transaction payload with Pydantic and upserts normalized rows into SQLite.
- `query_cashflow_data(request)`: runs read-only SQL queries for summaries, anomaly checks, and downstream dashboard work.

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
