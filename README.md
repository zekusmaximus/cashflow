# Liquidity Gate

Liquidity Gate is a local-first desktop workspace for reconstructing household spending from core transaction exports, storing that data in SQLite, and exposing a Python MCP server that LLM agents can use for useful monthly and annual cash-flow discussions without sending data to the cloud.

Advanced planning inputs such as payroll, tax, insurance, debt, and capital-plan documents can be layered in later, but they are not the baseline requirement for using this repo.

## Stack

- Tauri v2 + React + TypeScript
- Tailwind CSS with hand-wired shadcn/ui-compatible components
- TanStack Query for local data orchestration
- SQLite via `@tauri-apps/plugin-sql`
- Python MCP server using `mcp`, `pydantic`, and `watchdog`

## Repository layout

- [docs/](docs/) — canonical operating references: the master index, the tracker CSV that drives the intake view, and [PROJECT_STATUS.md](docs/PROJECT_STATUS.md) (living roadmap of what's built and what's next).
- [src/](src/) — React UI: Document Intake and Cash Flow Dashboard.
- [src-tauri/](src-tauri/) — Tauri v2 shell, Rust commands, plugin permissions.
- [server/](server/) — Python MCP server, schema, and supporting models.
- [.claudecowork/](.claudecowork/) — Financial Detective persona and MCP launch descriptor for Claude Cowork.
- [.mcp.json](.mcp.json) — Generic MCP client config (used by Claude Code and other MCP-aware clients).

## Local-first rules

- No cloud sync is implemented. Everything runs against local SQLite and the local filesystem.
- The tracker CSV in [docs/](docs/) is the single source of truth for tracked sources and optional enrichments, but the baseline required inputs are the core transaction exports that reconstruct household spending.
- The canonical schema lives in [server/sql/schema.sql](server/sql/schema.sql) and is shared by the MCP server and the desktop app.
- **Real financial documents are never tracked in this repository.** They live under a watch root outside the repo (configured via `LIQUIDITY_GATE_WATCH_ROOT`).
- **The SQLite database lives in the OS app-config directory** to match what Tauri's `plugin-sql` resolves `sqlite:liquidity-gate.db` against — the desktop app and the MCP server share one file. Defaults: `%APPDATA%\com.jeff.liquiditygate\liquidity-gate.db` on Windows, `~/Library/Application Support/com.jeff.liquiditygate/liquidity-gate.db` on macOS, `~/.config/com.jeff.liquiditygate/liquidity-gate.db` on Linux. Override with `LIQUIDITY_GATE_DB_PATH`.

## Prerequisites

- Node.js 20 or newer
- Rust toolchain (required for Tauri desktop builds)
- Python 3.11 or newer

## First-time setup

### 1. Install frontend dependencies

```bash
npm install
```

### 2. Create the Python virtual environment

The server's venv lives at `server/.venv`. Do not create venvs at the repo root — they pollute the workspace and Vite's file watcher.

**macOS / Linux:**

```bash
python3 -m venv server/.venv
source server/.venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -e ./server
```

**Windows (PowerShell):**

```powershell
python -m venv server/.venv
server/.venv/Scripts/Activate.ps1
python -m pip install --upgrade pip
python -m pip install -e ./server
```

### 3. Set the watch root

The MCP server and the Tauri app both read `LIQUIDITY_GATE_WATCH_ROOT` to find your real financial documents. This must point at a directory **outside the repo**.

**Windows (PowerShell, persistent at User scope):**

```powershell
[Environment]::SetEnvironmentVariable("LIQUIDITY_GATE_WATCH_ROOT", "C:\Users\YourName\Documents\Cashflow", "User")
```

After running this, **open a new PowerShell window** so the variable is inherited by `npm` and `python` subprocesses.

**macOS / Linux:**

```bash
export LIQUIDITY_GATE_WATCH_ROOT="$HOME/Documents/Cashflow"
```

Add the export to `~/.bashrc` or `~/.zshrc` to persist across sessions.

If you skip this step, both the desktop app and the MCP server fall back to `~/Documents/CashFlow`.

### 4. Create the watch root folder

```powershell
New-Item -ItemType Directory -Force "C:\Users\YourName\Documents\Cashflow"
```

```bash
mkdir -p ~/Documents/Cashflow
```

Avoid putting the folder under OneDrive, iCloud, or Dropbox — cloud-sync placeholders interfere with the file watcher, and syncing real financial documents to a third-party cloud contradicts the local-first rule.

## Running the desktop app

From the repo root:

```bash
npm run tauri dev
```

This compiles the Rust shell (~30 seconds on first run), starts the Vite dev server on port 1420, and opens the Tauri window. You should see:

- The **Document Intake** view, listing tracked financial sources and optional enrichments.
- A status banner showing which folder is being watched and how many files are indexed.
- Any file you drop in the watch root appears under its best-matching tracker row within 5 seconds, badged "Auto-matched."

To stop the app, **close the window** (clean shutdown). Pressing Ctrl+C in the terminal also works but produces benign cleanup warnings from the embedded WebView.

For UI-only iteration without recompiling Rust, use `npm run dev` (web view, port 1420). The watch-root scan is disabled in that mode because there is no Tauri runtime.

## Naming files for auto-matching

Filenames are scored against tracker rows by token overlap, string similarity, and an extension bonus. A few conventions make matching reliable:

- **Use the noun tokens from the tracker's Document column.** For doc-001 that's `Chase Credit Card`. For doc-003 that's `Ally HYSA`. Don't paraphrase.
- **Underscore-separate every word.** `Chase_Credit_Card` matches cleanly; `ChaseCreditCard` and `Chase-CardActivity` are weaker.
- **The extension matters.** `.csv` for transaction exports, `.pdf` for statements, `.png` or `.jpg` for screenshots. The matcher gives a +0.15 score bonus when the extension matches the tracker's Format column.
- **Stopwords are stripped from both sides.** `2026`, `2027`, `all`, `and`, `annual`, `every`, `current`, `export`, `history`, `pdf`, `screenshot`, `the`, `ytd` don't help or hurt — include them for human readability if you like.

Examples that match cleanly:

```text
2026-05-12_Chase_Credit_Card.csv             -> doc-001 Chase credit-card
2026-05-12_Beacon_Checking.csv               -> doc-002 Beacon checking
2026-05-12_Ally_HYSA_Savings.csv             -> doc-003 Ally HYSA
2026-05-12_Jeff_Regular_Paystub.pdf          -> doc-011 Jeff regular paystubs
2026-05-12_Ashley_RSU_Vesting_Paystub.pdf    -> doc-015 Ashley RSU vesting
2026-05-12_Primary_Mortgage_Statement.pdf    -> doc-021 Primary mortgage
```

Each file is assigned to its single highest-scoring row, so a file never appears under multiple rows. The intake row's "Auto-matched" badge shows the matched filename and the score.

## Connecting Claude Cowork

The MCP server is **launched by an MCP client**, not run by hand. Claude Cowork spawns it as a subprocess over stdio when it opens a session in this project.

Cowork reads two files from this repo:

- [.claudecowork/config.json](.claudecowork/config.json) — agent name, persona path, MCP descriptor path, and the startup sequence (read the master index first, then the tracker, then inspect core transaction sources before asking for more inputs).
- [.claudecowork/mcp-server.json](.claudecowork/mcp-server.json) — how to launch the server: `python -m liquidity_gate_mcp.server` from the `server/` directory, with `PYTHONPATH=src` and `LIQUIDITY_GATE_ROOT=..`.

To use it:

1. Complete first-time setup steps 1–4 above. The Python venv at `server/.venv` must exist and have `liquidity-gate-mcp` installed.
2. Make sure the `python` Cowork invokes can find the `liquidity_gate_mcp` package. The descriptor uses bare `python`, which resolves to whatever is on `PATH` when Cowork starts the subprocess. If your `PATH` does not include the venv, edit `.claudecowork/mcp-server.json` to use the full venv interpreter path: `server/.venv/Scripts/python.exe` (Windows) or `server/.venv/bin/python` (macOS/Linux).
3. Make sure `LIQUIDITY_GATE_WATCH_ROOT` is set in the environment Cowork inherits.
4. Open this project directory in Cowork. The Financial Detective persona auto-loads, and three MCP tools become available: `read_document_metadata`, `reconcile_transactions`, `query_cashflow_data`. The master index and tracker CSV are exposed as MCP resources.

To verify the MCP server starts without Cowork, run it once from an activated venv:

```bash
python -m liquidity_gate_mcp.server
```

It will print nothing and block, waiting for JSON-RPC on stdin. **That is correct** — the server uses stdio. Press Ctrl+C to exit; the traceback you see is the standard cancel cleanup, not an error.

## Connecting other MCP clients

For any MCP client that reads an `mcp.json`-style config (Claude Code, etc.), [.mcp.json](.mcp.json) declares the same server. The client launches it the same way Cowork does.

## Tracker spreadsheet format

The active tracker is [docs/Spreadsheet_checklist_for_document_tracking.csv](docs/Spreadsheet_checklist_for_document_tracking.csv). Earlier revisions also tracked an `.xlsx` mirror; that file has been removed. The CSV is the single canonical source — edit it directly and commit.

For the spending-first scope, interpret the tracker in three tiers:

- required core transaction sources that make household spending reconstruction possible
- optional enrichment sources that improve categorization and context
- deferred planning inputs that may matter later but should not block baseline spending analysis

Missing optional or deferred items should not prevent useful monthly and annual spending discussions once the core transaction feeds are present.

The intake view marks a row as "Obtained" if either the CSV's `Obtained ✓` column says so (manual override), or a file in the watch root auto-matches the row.

## Testing

### Python

```bash
source server/.venv/bin/activate    # PowerShell: server/.venv/Scripts/Activate.ps1
python -m pytest server/tests
```

### Frontend

```bash
npm test
```

### Type-check and production build

```bash
npx tsc --noEmit
npm run build
```

CI runs all four on every push and pull request — see [.github/workflows/ci.yml](.github/workflows/ci.yml).
