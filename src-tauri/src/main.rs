#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

use std::fs;
use std::path::{Path, PathBuf};

use serde::{Deserialize, Serialize};

#[derive(Debug, Deserialize)]
#[serde(rename_all = "camelCase")]
struct LeakageTimeRange {
    start: String, // "YYYY-MM"
    end: String,   // "YYYY-MM"
}

#[derive(Debug, Serialize)]
#[serde(rename_all = "camelCase")]
struct LeakageTransaction {
    id: String,
    vendor: String,
    amount: f64,
    date: String,
    raw_description: String,
    match_score: Option<f64>,
}

#[derive(Serialize)]
#[serde(rename_all = "camelCase")]
struct CandidateFile {
    relative_path: String,
    filename: String,
    stem: String,
    extension: String,
    parent_name: String,
    modified_ms: Option<u64>,
}

#[derive(Serialize)]
#[serde(rename_all = "camelCase")]
struct WatchRootSnapshot {
    root: String,
    exists: bool,
    files: Vec<CandidateFile>,
}

const IGNORED_DIRECTORIES: &[&str] = &[
    ".git",
    "node_modules",
    "dist",
    "docs",
    "src",
    "src-tauri",
    "server",
    ".claudecowork",
    ".venv",
    "__pycache__",
];

fn default_watch_root() -> PathBuf {
    let home = std::env::var("USERPROFILE")
        .or_else(|_| std::env::var("HOME"))
        .map(PathBuf::from)
        .unwrap_or_else(|_| PathBuf::from("."));
    home.join("Documents").join("Cashflow")
}

fn walk_root(dir: &Path, base: &Path, out: &mut Vec<CandidateFile>) {
    let Ok(entries) = fs::read_dir(dir) else {
        return;
    };
    for entry in entries.flatten() {
        let name = entry.file_name().to_string_lossy().to_string();
        // Skip ignored directories at any depth, and also venv-1, venv-2, etc.
        if IGNORED_DIRECTORIES.contains(&name.as_str()) || name.starts_with(".venv-") {
            continue;
        }
        let path = entry.path();
        let Ok(meta) = entry.metadata() else { continue };
        if meta.is_dir() {
            walk_root(&path, base, out);
        } else if meta.is_file() {
            let relative = path
                .strip_prefix(base)
                .unwrap_or(&path)
                .to_string_lossy()
                .replace('\\', "/");
            let stem = path
                .file_stem()
                .map(|s| s.to_string_lossy().to_string())
                .unwrap_or_default();
            let extension = path
                .extension()
                .map(|s| s.to_string_lossy().to_lowercase())
                .unwrap_or_default();
            let parent_name = path
                .parent()
                .and_then(|p| p.file_name())
                .map(|s| s.to_string_lossy().to_string())
                .unwrap_or_default();
            let modified_ms = meta
                .modified()
                .ok()
                .and_then(|t| t.duration_since(std::time::UNIX_EPOCH).ok())
                .map(|d| d.as_millis() as u64);
            out.push(CandidateFile {
                relative_path: relative,
                filename: name,
                stem,
                extension,
                parent_name,
                modified_ms,
            });
        }
    }
}

fn resolve_watch_root() -> PathBuf {
    std::env::var("LIQUIDITY_GATE_WATCH_ROOT")
        .map(PathBuf::from)
        .unwrap_or_else(|_| default_watch_root())
}

#[tauri::command]
fn list_watch_root_files() -> WatchRootSnapshot {
    let root = resolve_watch_root();
    let exists = root.exists();
    let mut files = Vec::new();
    if exists {
        walk_root(&root, &root, &mut files);
    }
    WatchRootSnapshot {
        root: root.to_string_lossy().to_string(),
        exists,
        files,
    }
}

const ALLOWED_UPLOAD_EXTENSIONS: &[&str] = &["csv", "pdf"];

#[derive(Debug, Serialize)]
#[serde(rename_all = "camelCase")]
struct CopyToWatchRootResult {
    destination: String,
    filename: String,
}

fn copy_to_watch_root_impl(
    source: &Path,
    watch_root: &Path,
) -> Result<CopyToWatchRootResult, String> {
    let metadata = source
        .metadata()
        .map_err(|err| format!("Cannot read source file: {err}"))?;
    if !metadata.is_file() {
        return Err("Source path is not a regular file.".into());
    }

    let filename = source
        .file_name()
        .ok_or_else(|| "Source path has no filename.".to_string())?
        .to_string_lossy()
        .to_string();

    let extension = source
        .extension()
        .map(|s| s.to_string_lossy().to_lowercase())
        .unwrap_or_default();
    if !ALLOWED_UPLOAD_EXTENSIONS.contains(&extension.as_str()) {
        return Err(format!(
            "Unsupported file type '.{extension}'. Allowed: {}.",
            ALLOWED_UPLOAD_EXTENSIONS.join(", ")
        ));
    }

    if !watch_root.exists() {
        fs::create_dir_all(watch_root)
            .map_err(|err| format!("Cannot create watch root: {err}"))?;
    }

    let destination = watch_root.join(&filename);
    if destination.exists() {
        return Err(format!(
            "A file named '{filename}' already exists in the watch root."
        ));
    }

    fs::copy(source, &destination).map_err(|err| format!("Copy failed: {err}"))?;

    Ok(CopyToWatchRootResult {
        destination: destination.to_string_lossy().to_string(),
        filename,
    })
}

#[tauri::command]
fn copy_to_watch_root(source_path: String) -> Result<CopyToWatchRootResult, String> {
    let source = PathBuf::from(&source_path);
    let watch_root = resolve_watch_root();
    copy_to_watch_root_impl(&source, &watch_root)
}

/// Opens the OS file manager with `path` revealed/selected in its parent.
///
/// macOS `open -R` and Windows `explorer /select,` highlight the target inside
/// its parent folder. Linux has no universal "select" verb, so `xdg-open`
/// opens the folder itself (the closest equivalent).
#[tauri::command]
fn reveal_in_file_manager(path: String) -> Result<(), String> {
    use std::process::Command;

    let target = PathBuf::from(&path);
    if !target.exists() {
        return Err(format!("Path does not exist: {path}"));
    }

    #[cfg(target_os = "macos")]
    let result = Command::new("open").args(["-R", &path]).spawn();

    #[cfg(target_os = "windows")]
    let result = Command::new("explorer")
        .arg(format!("/select,{path}"))
        .spawn();

    #[cfg(target_os = "linux")]
    let result = Command::new("xdg-open").arg(&path).spawn();

    result.map(|_| ()).map_err(|err| err.to_string())
}

#[tauri::command]
fn list_leakage_transactions(
    app: tauri::AppHandle,
    category_id: String,
    range: LeakageTimeRange,
) -> Result<Vec<LeakageTransaction>, String> {
    use tauri::Manager;
    let db_path = app
        .path()
        .app_data_dir()
        .map_err(|e| e.to_string())?
        .join("liquidity-gate.db");

    if !db_path.exists() {
        return Ok(vec![]);
    }

    let conn = rusqlite::Connection::open_with_flags(
        &db_path,
        rusqlite::OpenFlags::SQLITE_OPEN_READ_ONLY,
    )
    .map_err(|e| e.to_string())?;

    let start_date = format!("{}-01", range.start);
    let end_date = format!("{}-31", range.end);

    let mut stmt = conn
        .prepare(
            "SELECT id,
                    COALESCE(NULLIF(merchant_normalized, ''), description_raw) AS vendor,
                    ABS(amount) AS amount,
                    occurred_on AS date,
                    description_raw
               FROM transactions
              WHERE direction = 'outflow'
                AND primary_category = ?1
                AND occurred_on >= ?2
                AND occurred_on <= ?3
              ORDER BY occurred_on DESC
              LIMIT 200",
        )
        .map_err(|e| e.to_string())?;

    let rows = stmt
        .query_map(
            rusqlite::params![category_id, start_date, end_date],
            |row| {
                Ok(LeakageTransaction {
                    id: row.get(0)?,
                    vendor: row.get(1)?,
                    amount: row.get(2)?,
                    date: row.get(3)?,
                    raw_description: row.get(4)?,
                    match_score: None,
                })
            },
        )
        .map_err(|e| e.to_string())?;

    let mut result = Vec::new();
    for row in rows {
        result.push(row.map_err(|e| e.to_string())?);
    }

    Ok(result)
}

// ── Classification-rule sidecar ──────────────────────────────────────────────
//
// The classifier engine is Python (server/src/liquidity_gate_mcp), unreachable
// in-process from this Rust/React app. To keep a single classification engine we
// shell out to `python -m liquidity_gate_mcp.cli_classify`, replicating the
// `.mcp.json` launch contract exactly: the venv interpreter, `cwd = server`, and
// `LIQUIDITY_GATE_ROOT=..`. The interpreter path is overridable via
// `LIQUIDITY_GATE_PYTHON`; the project root via `LIQUIDITY_GATE_ROOT`.

/// Hard ceiling on a single classify run. ~1,300 rows complete in well under a
/// second; this only guards against a hung interpreter so the UI never waits
/// forever.
const CLASSIFIER_TIMEOUT_SECS: u64 = 120;

/// Locate the project root (the directory that contains `server/pyproject.toml`).
/// Honours `LIQUIDITY_GATE_ROOT`, then walks up from the current dir and the
/// executable's dir so it resolves in both `tauri dev` and a run-from-root shell.
fn resolve_project_root() -> Option<PathBuf> {
    fn has_marker(dir: &Path) -> bool {
        dir.join("server").join("pyproject.toml").exists()
    }

    if let Ok(root) = std::env::var("LIQUIDITY_GATE_ROOT") {
        let candidate = PathBuf::from(&root);
        // `.mcp.json` uses a relative ".." against cwd=server; resolve it.
        let resolved = if candidate.is_absolute() {
            candidate
        } else {
            std::env::current_dir().ok()?.join(candidate)
        };
        if let Ok(canon) = resolved.canonicalize() {
            return Some(canon);
        }
        return Some(resolved);
    }

    let mut starts: Vec<PathBuf> = Vec::new();
    if let Ok(cwd) = std::env::current_dir() {
        starts.push(cwd);
    }
    if let Ok(exe) = std::env::current_exe() {
        if let Some(dir) = exe.parent() {
            starts.push(dir.to_path_buf());
        }
    }

    for start in starts {
        let mut dir: Option<&Path> = Some(start.as_path());
        while let Some(current) = dir {
            if has_marker(current) {
                return Some(current.to_path_buf());
            }
            dir = current.parent();
        }
    }
    None
}

/// Default sidecar interpreter inside the project's venv, matching `.mcp.json`.
fn default_python_interpreter(project_root: &Path) -> PathBuf {
    let venv = project_root.join("server").join(".venv");
    if cfg!(target_os = "windows") {
        venv.join("Scripts").join("python.exe")
    } else {
        venv.join("bin").join("python")
    }
}

#[derive(Debug, Deserialize)]
#[serde(rename_all = "camelCase")]
struct ApplyClassificationRuleArgs {
    /// UpsertClassificationRuleRequest fields (snake_case), passed straight
    /// through to the Python CLI as `--rule <json>`. `None` re-runs existing rules.
    rule: Option<serde_json::Value>,
    #[serde(default = "default_true")]
    reclassify_all: bool,
    #[serde(default)]
    dry_run: bool,
    #[serde(default)]
    account_filter: Option<String>,
}

fn default_true() -> bool {
    true
}

/// Run the Python classifier sidecar. Returns the parsed JSON the CLI prints on
/// stdout; surfaces a missing interpreter or a non-zero exit as a clear error.
#[tauri::command]
fn apply_classification_rule(
    args: ApplyClassificationRuleArgs,
) -> Result<serde_json::Value, String> {
    use std::process::{Command, Stdio};
    use std::sync::mpsc;
    use std::time::Duration;

    let project_root = resolve_project_root().ok_or_else(|| {
        "Classifier runtime not found — could not locate the project root \
         (server/pyproject.toml). Start the MCP/Python env or set LIQUIDITY_GATE_ROOT."
            .to_string()
    })?;

    let interpreter = std::env::var("LIQUIDITY_GATE_PYTHON")
        .map(PathBuf::from)
        .unwrap_or_else(|_| default_python_interpreter(&project_root));

    // Pre-flight: only assert existence for a concrete path (not a bare PATH
    // command supplied via LIQUIDITY_GATE_PYTHON).
    if interpreter.is_absolute() && !interpreter.exists() {
        return Err(format!(
            "Classifier runtime not found — Python interpreter missing at {}. \
             Start the MCP/Python env or set LIQUIDITY_GATE_PYTHON.",
            interpreter.display()
        ));
    }

    let server_dir = project_root.join("server");

    let mut command = Command::new(&interpreter);
    command
        .arg("-m")
        .arg("liquidity_gate_mcp.cli_classify")
        .current_dir(&server_dir)
        .env("LIQUIDITY_GATE_ROOT", &project_root)
        .stdout(Stdio::piped())
        .stderr(Stdio::piped());

    if let Some(rule) = &args.rule {
        command.arg("--rule").arg(rule.to_string());
    }
    if args.reclassify_all {
        command.arg("--reclassify-all");
    } else {
        command.arg("--no-reclassify-all");
    }
    if args.dry_run {
        command.arg("--dry-run");
    }
    if let Some(account_filter) = &args.account_filter {
        command.arg("--account-filter").arg(account_filter);
    }

    // Run on a worker thread with a timeout so a hung interpreter can't block
    // the command indefinitely. Output is a single small JSON line.
    let (tx, rx) = mpsc::channel();
    std::thread::spawn(move || {
        let _ = tx.send(command.output());
    });

    let output = match rx.recv_timeout(Duration::from_secs(CLASSIFIER_TIMEOUT_SECS)) {
        Ok(Ok(output)) => output,
        Ok(Err(err)) => {
            if err.kind() == std::io::ErrorKind::NotFound {
                return Err(format!(
                    "Classifier runtime not found — could not launch {}. \
                     Start the MCP/Python env or set LIQUIDITY_GATE_PYTHON.",
                    interpreter.display()
                ));
            }
            return Err(format!("Failed to launch classifier: {err}"));
        }
        Err(_) => {
            return Err(format!(
                "Classifier timed out after {CLASSIFIER_TIMEOUT_SECS}s."
            ));
        }
    };

    let stdout = String::from_utf8_lossy(&output.stdout);
    let stderr = String::from_utf8_lossy(&output.stderr);

    if !output.status.success() {
        // The CLI prints `{"ok": false, "error": ...}` on handled failures;
        // prefer that, then stderr, for a useful message.
        let detail = stdout.trim();
        let message = if detail.is_empty() {
            stderr.trim().to_string()
        } else {
            detail.to_string()
        };
        return Err(format!("Classifier failed: {message}"));
    }

    serde_json::from_str::<serde_json::Value>(stdout.trim())
        .map_err(|err| format!("Could not parse classifier output ({err}): {}", stdout.trim()))
}

fn main() {
    tauri::Builder::default()
        .plugin(tauri_plugin_dialog::init())
        .plugin(tauri_plugin_fs::init())
        .plugin(tauri_plugin_sql::Builder::default().build())
        .invoke_handler(tauri::generate_handler![
            list_watch_root_files,
            copy_to_watch_root,
            reveal_in_file_manager,
            list_leakage_transactions,
            apply_classification_rule
        ])
        .run(tauri::generate_context!())
        .expect("error while running Liquidity Gate");
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::io::Write;
    use std::sync::atomic::{AtomicU64, Ordering};

    static COUNTER: AtomicU64 = AtomicU64::new(0);

    fn tempdir(name: &str) -> PathBuf {
        let mut dir = std::env::temp_dir();
        let n = COUNTER.fetch_add(1, Ordering::SeqCst);
        dir.push(format!(
            "liquidity-gate-test-{}-{}-{}-{}",
            name,
            std::process::id(),
            std::time::SystemTime::now()
                .duration_since(std::time::UNIX_EPOCH)
                .map(|d| d.as_nanos())
                .unwrap_or(0),
            n
        ));
        fs::create_dir_all(&dir).expect("create tempdir");
        dir
    }

    fn write_file(path: &Path, contents: &[u8]) {
        let mut f = fs::File::create(path).expect("create file");
        f.write_all(contents).expect("write file");
    }

    #[test]
    fn copies_csv_into_existing_watch_root() {
        let src_dir = tempdir("src-csv");
        let watch_root = tempdir("watch-csv");
        let source = src_dir.join("Chase_April.csv");
        write_file(&source, b"a,b\n1,2\n");

        let result = copy_to_watch_root_impl(&source, &watch_root).expect("copy ok");

        let dest = watch_root.join("Chase_April.csv");
        assert!(dest.exists(), "destination file should exist");
        assert_eq!(result.filename, "Chase_April.csv");
        assert_eq!(result.destination, dest.to_string_lossy().to_string());

        fs::remove_dir_all(&src_dir).ok();
        fs::remove_dir_all(&watch_root).ok();
    }

    #[test]
    fn creates_watch_root_when_missing() {
        let src_dir = tempdir("src-mkdir");
        let parent = tempdir("watch-mkdir-parent");
        let watch_root = parent.join("nested");
        let source = src_dir.join("statement.pdf");
        write_file(&source, b"%PDF-fake");

        assert!(!watch_root.exists());
        copy_to_watch_root_impl(&source, &watch_root).expect("copy ok");
        assert!(watch_root.join("statement.pdf").exists());

        fs::remove_dir_all(&src_dir).ok();
        fs::remove_dir_all(&parent).ok();
    }

    #[test]
    fn rejects_unsupported_extension() {
        let src_dir = tempdir("src-bad-ext");
        let watch_root = tempdir("watch-bad-ext");
        let source = src_dir.join("notes.txt");
        write_file(&source, b"hi");

        let err = copy_to_watch_root_impl(&source, &watch_root).expect_err("should reject");
        assert!(err.contains("Unsupported file type"), "got: {err}");
        assert!(!watch_root.join("notes.txt").exists());

        fs::remove_dir_all(&src_dir).ok();
        fs::remove_dir_all(&watch_root).ok();
    }

    #[test]
    fn rejects_when_destination_already_exists() {
        let src_dir = tempdir("src-dup");
        let watch_root = tempdir("watch-dup");
        let source = src_dir.join("Beacon.csv");
        write_file(&source, b"a,b\n");
        write_file(&watch_root.join("Beacon.csv"), b"existing");

        let err = copy_to_watch_root_impl(&source, &watch_root).expect_err("should reject");
        assert!(err.contains("already exists"), "got: {err}");

        fs::remove_dir_all(&src_dir).ok();
        fs::remove_dir_all(&watch_root).ok();
    }

    #[test]
    fn rejects_non_file_source() {
        let src_dir = tempdir("src-dir");
        let watch_root = tempdir("watch-dir-src");

        let err = copy_to_watch_root_impl(&src_dir, &watch_root).expect_err("should reject");
        assert!(err.contains("not a regular file"), "got: {err}");

        fs::remove_dir_all(&src_dir).ok();
        fs::remove_dir_all(&watch_root).ok();
    }
}
