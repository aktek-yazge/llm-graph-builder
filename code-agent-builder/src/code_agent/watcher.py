"""File system watcher for automatic graph updates on code changes."""

from __future__ import annotations

import logging
import threading
import time
from pathlib import Path
from typing import Callable

from watchdog.events import FileSystemEventHandler, FileModifiedEvent, FileCreatedEvent, FileDeletedEvent
from watchdog.observers import Observer

logger = logging.getLogger(__name__)

WATCHED_EXTENSIONS = {".py", ".ts", ".tsx"}
DEBOUNCE_SECONDS = 1.0


class _DebouncedHandler(FileSystemEventHandler):
    """Debounced file change handler that batches rapid saves."""

    def __init__(self, on_change: Callable[[str], None]):
        super().__init__()
        self._on_change = on_change
        self._pending: dict[str, float] = {}
        self._lock = threading.Lock()
        self._timer: threading.Timer | None = None

    def on_modified(self, event: FileModifiedEvent) -> None:
        if event.is_directory:
            return
        self._schedule(event.src_path)

    def on_created(self, event: FileCreatedEvent) -> None:
        if event.is_directory:
            return
        self._schedule(event.src_path)

    def on_deleted(self, event: FileDeletedEvent) -> None:
        if event.is_directory:
            return
        self._schedule(event.src_path)

    def _schedule(self, path: str) -> None:
        ext = Path(path).suffix
        if ext not in WATCHED_EXTENSIONS:
            return
        with self._lock:
            self._pending[path] = time.time()
            if self._timer:
                self._timer.cancel()
            self._timer = threading.Timer(DEBOUNCE_SECONDS, self._flush)
            self._timer.daemon = True
            self._timer.start()

    def _flush(self) -> None:
        with self._lock:
            paths = list(self._pending.keys())
            self._pending.clear()

        for path in paths:
            try:
                self._on_change(path)
            except Exception as e:
                logger.error("Watcher callback error for %s: %s", path, e)


class FileWatcher:
    """Background file system watcher that triggers re-indexing on changes."""

    def __init__(self, on_file_changed: Callable[[str], None]):
        self._on_change = on_file_changed
        self._observer: Observer | None = None
        self._watched_paths: list[str] = []
        self._change_log: list[dict] = []

    def watch(self, directory: str) -> None:
        if directory in self._watched_paths:
            return

        if not self._observer:
            self._observer = Observer()
            self._observer.daemon = True

        handler = _DebouncedHandler(self._handle_change)
        self._observer.schedule(handler, directory, recursive=True)
        self._watched_paths.append(directory)
        logger.info("Watching directory: %s", directory)

        if not self._observer.is_alive():
            self._observer.start()

    def _handle_change(self, path: str) -> None:
        logger.info("File changed: %s", path)
        self._change_log.append({"path": path, "time": time.time()})
        if len(self._change_log) > 100:
            self._change_log = self._change_log[-100:]
        self._on_change(path)

    def get_status(self) -> dict:
        return {
            "watching": self._watched_paths,
            "is_running": self._observer.is_alive() if self._observer else False,
            "recent_changes": self._change_log[-10:],
        }

    def stop(self) -> None:
        if self._observer and self._observer.is_alive():
            self._observer.stop()
            self._observer.join(timeout=5)
            logger.info("File watcher stopped")
