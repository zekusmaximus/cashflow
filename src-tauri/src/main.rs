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
    home.join("Documents").join("CashFlow")
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

#[tauri::command]
fn list_watch_root_files() -> WatchRootSnapshot {
    let root = std::env::var("LIQUIDITY_GATE_WATCH_ROOT")
        .map(PathBuf::from)
        .unwrap_or_else(|_| default_watch_root());
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

fn main() {
    tauri::Builder::default()
        .plugin(tauri_plugin_fs::init())
        .plugin(tauri_plugin_sql::Builder::default().build())
        .invoke_handler(tauri::generate_handler![list_watch_root_files])
        .run(tauri::generate_context!())
        .expect("error while running Liquidity Gate");
}
