from __future__ import annotations

from collections import deque
from datetime import datetime, timezone
from pathlib import Path
from typing import Deque

from watchdog.events import FileSystemEvent, FileSystemEventHandler
from watchdog.observers.polling import PollingObserver

from .models import FileEventSnapshot


IGNORED_PARTS = {
    ".git",
    "node_modules",
    "dist",
    "docs",
    "src",
    "src-tauri",
    "server",
    ".claudecowork",
    # Generated monthly cashflow summaries land here; they are an output of
    # the watch root, not an ingest input — don't echo their writes as events.
    "monthly_summaries",
}


class CashFlowEventHandler(FileSystemEventHandler):
    def __init__(self, root: Path, event_store: Deque[FileEventSnapshot]) -> None:
        self.root = root
        self.event_store = event_store

    def on_any_event(self, event: FileSystemEvent) -> None:
        path = Path(event.src_path)
        if any(part in IGNORED_PARTS for part in path.parts):
            return

        try:
            relative_path = path.resolve().relative_to(self.root.resolve()).as_posix()
        except ValueError:
            relative_path = path.name

        self.event_store.appendleft(
            FileEventSnapshot(
                event_type=event.event_type,
                path=relative_path,
                is_directory=event.is_directory,
                occurred_at=datetime.now(timezone.utc).isoformat(),
            )
        )


class CashFlowWatcher:
    def __init__(self, root: Path) -> None:
        self.root = root
        self._events: Deque[FileEventSnapshot] = deque(maxlen=100)
        self._observer = PollingObserver(timeout=1.0)
        self._started = False

    def start(self) -> None:
        if self._started or not self.root.exists():
            return

        handler = CashFlowEventHandler(self.root, self._events)
        self._observer.schedule(handler, str(self.root), recursive=True)
        self._observer.start()
        self._started = True

    def stop(self) -> None:
        if not self._started:
            return

        self._observer.stop()
        self._observer.join(timeout=2.0)
        self._started = False

    def recent_events(self, limit: int = 25) -> list[FileEventSnapshot]:
        return list(self._events)[:limit]
