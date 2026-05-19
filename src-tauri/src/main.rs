#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

use std::fs;
use std::path::{Path, PathBuf};

use serde::Serialize;

#[derive(Serialize)]
#[serde(rename_all = "camelCase")]
struct CandidateFile {
    relative_path: String,
    filename: String,
    stem: String,
    extension: String,
    parent_name: String,
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
            out.push(CandidateFile {
                relative_path: relative,
                filename: name,
                stem,
                extension,
                parent_name,
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

fn main() {
    tauri::Builder::default()
        .plugin(tauri_plugin_dialog::init())
        .plugin(tauri_plugin_fs::init())
        .plugin(tauri_plugin_sql::Builder::default().build())
        .invoke_handler(tauri::generate_handler![
            list_watch_root_files,
            copy_to_watch_root
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
