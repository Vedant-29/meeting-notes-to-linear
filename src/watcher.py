"""File watcher with per-path debouncing.

                   ┌──────────────┐
   .md file save → │  watchdog    │ on_created / on_modified / on_moved
                   │  Observer    │ (often all 3 fire on a single editor save)
                   └──────┬───────┘
                          │ each event resets a 500ms timer for that path
                          ▼
                   ┌──────────────┐
                   │ DebouncedHandler ──── after 500ms quiet ──► callback(path)
                   └──────────────┘

We only care about `.md` files. We ignore sibling `.processed` and `.failed`
markers and any path that already has a `.processed` sibling.
"""

from __future__ import annotations

import logging
import threading
from pathlib import Path
from typing import Callable

from watchdog.events import FileSystemEvent, FileSystemEventHandler
from watchdog.observers import Observer

log = logging.getLogger(__name__)


def is_target_file(path: Path) -> bool:
    """A path is a target if it's a .md file with no .processed sibling."""
    if path.suffix.lower() != ".md":
        return False
    # Skip our own marker files.
    if path.name.endswith(".processed.md") or path.name.endswith(".failed.md"):
        return False
    if path.with_name(path.name + ".processed").exists():
        return False
    return True


class DebouncedHandler(FileSystemEventHandler):
    """Coalesce rapid filesystem events for the same path into one callback."""

    def __init__(self, callback: Callable[[Path], None], *, quiet_period_s: float = 0.5) -> None:
        super().__init__()
        self._callback = callback
        self._quiet_period_s = quiet_period_s
        self._timers: dict[str, threading.Timer] = {}
        self._lock = threading.Lock()

    # All three events route through the same handler — editors atomic-rename
    # on save, producing create + modify + move in rapid succession.
    def on_created(self, event: FileSystemEvent) -> None:
        self._schedule(event)

    def on_modified(self, event: FileSystemEvent) -> None:
        self._schedule(event)

    def on_moved(self, event: FileSystemEvent) -> None:
        # For move events, the destination is what we want to process.
        dest = getattr(event, "dest_path", None) or event.src_path
        self._schedule_path(dest, is_directory=event.is_directory)

    def _schedule(self, event: FileSystemEvent) -> None:
        self._schedule_path(event.src_path, is_directory=event.is_directory)

    def _schedule_path(self, raw_path: str, *, is_directory: bool) -> None:
        if is_directory:
            return
        path = Path(raw_path)
        if not is_target_file(path):
            return

        with self._lock:
            existing = self._timers.get(str(path))
            if existing is not None:
                existing.cancel()
            timer = threading.Timer(self._quiet_period_s, self._fire, args=(path,))
            timer.daemon = True
            self._timers[str(path)] = timer
            timer.start()

    def _fire(self, path: Path) -> None:
        with self._lock:
            self._timers.pop(str(path), None)
        # Last-mile re-check: file may have been deleted or marked .processed
        # between scheduling and now.
        if not path.exists() or not is_target_file(path):
            log.debug("watcher: skipping %s (gone or already processed)", path)
            return
        try:
            self._callback(path)
        except Exception:  # noqa: BLE001 — keep watcher alive on any handler crash
            log.exception("watcher: callback raised for %s", path)


def run_watcher(inbox_dir: Path, callback: Callable[[Path], None], *, quiet_period_s: float = 0.5) -> Observer:
    """Start the observer in a background thread and return it. Caller blocks."""
    if not inbox_dir.exists():
        raise FileNotFoundError(f"inbox does not exist: {inbox_dir}")
    if not inbox_dir.is_dir():
        raise NotADirectoryError(f"inbox is not a directory: {inbox_dir}")

    handler = DebouncedHandler(callback, quiet_period_s=quiet_period_s)
    observer = Observer()
    observer.schedule(handler, str(inbox_dir), recursive=False)
    observer.start()
    log.info("watching %s for *.md changes", inbox_dir)
    return observer
