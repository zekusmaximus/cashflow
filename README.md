# Liquidity Gate

Liquidity Gate is a local-first desktop workspace for reconstructing household cash flow from raw financial documents, modeling liquidity constraints, and exposing a Python MCP server that LLM agents can use without sending data to the cloud.

## Stack

- Tauri v2 + React + TypeScript
- Tailwind CSS with hand-wired shadcn/ui-compatible components
- TanStack Query for local data orchestration
- SQLite via `@tauri-apps/plugin-sql`
- Python MCP server with `mcp`, `pydantic`, and `watchdog`

## Repository Layout

- `docs/` holds the primary planning references: the master index and the extracted tracker CSV.
- `src/` contains the React UI, including Document Intake and Cash Flow dashboard views.
- `src-tauri/` contains the Tauri v2 shell and plugin permissions.
- `server/` contains the Python MCP server, its schema, and supporting models.
- `.claudecowork/` contains a Financial Detective persona file plus MCP connection metadata.

## Local-First Rules

- No cloud sync is implemented.
- The tracker CSV in `docs/` is the intake source of truth for document coverage.
- The canonical schema lives in `server/sql/schema.sql` and is shared by the MCP server and the desktop app.

## Getting Started

### 1. Install prerequisites

- Node.js 20+
- Rust toolchain for Tauri desktop builds
- Python 3.11+

### 2. Install frontend dependencies

```powershell
npm install
```

### 3. Create a Python environment for the MCP server

```powershell
python -m venv server/.venv
server/.venv/Scripts/Activate.ps1
python -m pip install --upgrade pip
python -m pip install -e ./server
```

### 4. Start the MCP server

From the repository root, after activating the virtual environment:

```powershell
python -m liquidity_gate_mcp.server
```

The server runs over stdio and exposes:

- `read_document_metadata`
- `reconcile_transactions`
- `query_cashflow_data`

It also exposes the master index and tracker CSV as MCP resources and registers a `financial_detective` prompt.

### 5. Start the desktop app

In a separate terminal:

```powershell
npm run tauri dev
```

If you only want the web UI during early iteration, run:

```powershell
npm run dev
```

## Claude Cowork / MCP Client Setup

- Generic MCP client config lives in `.mcp.json`.
- Claude Cowork-oriented metadata lives in `.claudecowork/config.json` and `.claudecowork/mcp-server.json`.
- The persona source is `.claudecowork/agent.md`.

## Current Scaffold Status

- The intake view renders the full 86-item checklist from `docs/Spreadsheet_checklist_for_document_tracking.csv`.
- The dashboard shows placeholder inflow/outflow and liquidity-gate visualizations.
- The Tauri shell is configured for local SQLite and file-system plugins.
- The Python server includes a folder watcher, schema-enforced transaction ingestion, and read-only SQL querying.
