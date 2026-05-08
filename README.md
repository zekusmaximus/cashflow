# Liquidity Gate

Liquidity Gate is a local-first desktop workspace for reconstructing household cash flow from raw financial documents, modeling liquidity constraints, and exposing a Python MCP server that LLM agents can use without sending data to the cloud.

## Stack

- Tauri v2 + React + TypeScript
- Tailwind CSS with hand-wired shadcn/ui-compatible components
- TanStack Query for local data orchestration
- SQLite via `@tauri-apps/plugin-sql`
- Python MCP server with `mcp`, `pydantic`, and `watchdog`

## Repository Layout

- `docs/` holds the canonical planning references: `00_CASH_FLOW_MASTER_INDEX.md` and the extracted tracker CSV. The frontend and Python server both load these from `docs/`.
- `src/` contains the React UI, including Document Intake and Cash Flow dashboard views.
- `src-tauri/` contains the Tauri v2 shell and plugin permissions.
- `server/` contains the Python MCP server, its schema, and supporting models.
- `.claudecowork/` contains a Financial Detective persona file plus MCP connection metadata.

## Local-First Rules

- No cloud sync is implemented.
- The tracker CSV in `docs/` is the intake source of truth for document coverage.
- The canonical schema lives in `server/sql/schema.sql` and is shared by the MCP server and the desktop app.
- **Real financial documents are never tracked in this repository.** They live under `~/Documents/CashFlow` by default (override with `LIQUIDITY_GATE_WATCH_ROOT`). See "Document watch root" below.

## Tracker spreadsheet format

The tracker is maintained as `docs/Spreadsheet_checklist_for_document_tracking.csv`. Earlier revisions of this repository also tracked an `.xlsx` mirror; that file has been removed. The CSV is now the single canonical source — edit it directly and commit.

## Document watch root

The Python MCP server scans a directory for matched documents and watches it
for filesystem events. Configure it with `LIQUIDITY_GATE_WATCH_ROOT`:

| Scenario | Value |
| --- | --- |
| Default (no env var) | `~/Documents/CashFlow` (created on demand) |
| Custom location | Set `LIQUIDITY_GATE_WATCH_ROOT=/absolute/path/to/financial-docs` |

The watch root must point at a directory **outside the repository** so private
statements stay out of git history. The repo's own `docs/`, `src/`,
`src-tauri/`, `server/`, `node_modules/`, `dist/`, and `.git/` directories are
hard-coded into the ignore list, so even if you point the watch root at the
project root the scanner will still skip them; they will not be matched as
candidate documents.

## Getting Started

### 1. Install prerequisites

- Node.js 20+
- Rust toolchain for Tauri desktop builds
- Python 3.11+

### 2. Install frontend dependencies

```bash
npm install
```

### 3. Create a Python environment for the MCP server

#### macOS / Linux

```bash
python3 -m venv server/.venv
source server/.venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -e ./server
```

#### Windows (PowerShell)

```powershell
python -m venv server/.venv
server/.venv/Scripts/Activate.ps1
python -m pip install --upgrade pip
python -m pip install -e ./server
```

### 4. Start the MCP server

From the repository root, after activating the virtual environment:

```bash
python -m liquidity_gate_mcp.server
```

The server runs over stdio and exposes:

- `read_document_metadata`
- `reconcile_transactions`
- `query_cashflow_data`

It also exposes the master index and tracker CSV as MCP resources and registers a `financial_detective` prompt.

### 5. Start the desktop app

In a separate terminal:

```bash
npm run tauri dev
```

If you only want the web UI during early iteration, run:

```bash
npm run dev
```

## MCP client configuration

Two MCP config files live in this repository. They serve distinct purposes:

| File | Purpose |
| --- | --- |
| `.mcp.json` | **Canonical** generic MCP client config. Use this for any MCP-aware client (e.g. Claude Code). |
| `.claudecowork/mcp-server.json` | Claude Cowork-specific server descriptor (transport, command, persona prompt). Used together with `.claudecowork/config.json`. |

If you only need a generic MCP client, point at `.mcp.json`. If you are using
Claude Cowork, the persona and startup sequence in `.claudecowork/` apply on
top of that.

## Current Scaffold Status

- The intake view renders the full 86-item checklist by reading
  `docs/Spreadsheet_checklist_for_document_tracking.csv` **at runtime** (Tauri
  filesystem in the desktop app, fetched static asset on the web).
- The dashboard reads `monthly_cashflow_summary`, `liquidity_gates`, and
  `leakage_categories` from the local SQLite database. When the database is
  empty it renders an explicit demo fallback so the UI still shows expected
  composition during early iteration.
- The Tauri shell is configured for local SQLite and file-system plugins, with
  a restrictive Content Security Policy.
- The Python server includes a folder watcher, schema-enforced transaction
  ingestion, and read-only SQL querying. Read-only access is enforced at the
  SQLite connection level (URI `mode=ro` + `PRAGMA query_only=1`), so
  `WITH ... DELETE` style CTEs cannot mutate state.

## Testing

### Python

```bash
source server/.venv/bin/activate
python -m pytest server/tests
```

### Frontend

```bash
npm test
```

### Type-check / build

```bash
npx tsc --noEmit
npm run build
```

CI runs all four on every push / PR — see `.github/workflows/ci.yml`.
