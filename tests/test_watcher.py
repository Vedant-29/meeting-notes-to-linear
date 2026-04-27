"""Watcher tests — debounce coalescing and is_target_file rules.

We don't actually run the watchdog Observer here; we drive the handler
directly with synthetic events. That tests the debounce logic without the
flakiness of real filesystem timing.
"""

from __future__ import annotations

import threading
import time
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from src.watcher import DebouncedHandler, is_target_file


def _evt(path: Path, *, kind: str = "modified") -> Any:
    """Build a stand-in for a watchdog FileSystemEvent."""
    return SimpleNamespace(
        src_path=str(path),
        dest_path=str(path),
        is_directory=False,
        event_type=kind,
    )


# -------------------------------------------------------------- is_target_file


def test_target_file_true_for_plain_md(tmp_path: Path) -> None:
    p = tmp_path / "notes.md"
    p.write_text("hi")
    assert is_target_file(p) is True


def test_target_file_false_for_non_md(tmp_path: Path) -> None:
    p = tmp_path / "notes.txt"
    p.write_text("hi")
    assert is_target_file(p) is False


def test_target_file_false_when_processed_marker_exists(tmp_path: Path) -> None:
    p = tmp_path / "notes.md"
    p.write_text("hi")
    (tmp_path / "notes.md.processed").write_text("done")
    assert is_target_file(p) is False


def test_target_file_skips_marker_filenames(tmp_path: Path) -> None:
    assert is_target_file(tmp_path / "x.processed.md") is False
    assert is_target_file(tmp_path / "x.failed.md") is False


# ------------------------------------------------------------ debounce timing


def test_three_rapid_events_collapse_to_one_call(tmp_path: Path) -> None:
    p = tmp_path / "notes.md"
    p.write_text("hi")

    calls: list[Path] = []
    done = threading.Event()

    def cb(path: Path) -> None:
        calls.append(path)
        done.set()

    handler = DebouncedHandler(cb, quiet_period_s=0.05)
    # Simulate atomic-rename save: create + modify + move within 10ms.
    handler.on_created(_evt(p, kind="created"))
    handler.on_modified(_evt(p, kind="modified"))
    handler.on_moved(_evt(p, kind="moved"))

    assert done.wait(timeout=1.0), "callback never fired"
    assert calls == [p], f"expected single call for {p}, got {calls}"


def test_different_paths_fire_independently(tmp_path: Path) -> None:
    p1 = tmp_path / "a.md"
    p2 = tmp_path / "b.md"
    p1.write_text("a")
    p2.write_text("b")

    calls: list[Path] = []
    lock = threading.Lock()
    both_done = threading.Event()

    def cb(path: Path) -> None:
        with lock:
            calls.append(path)
            if len(calls) == 2:
                both_done.set()

    handler = DebouncedHandler(cb, quiet_period_s=0.05)
    handler.on_modified(_evt(p1))
    handler.on_modified(_evt(p2))

    assert both_done.wait(timeout=1.0), f"only got {calls}"
    assert sorted(calls) == sorted([p1, p2])


def test_event_during_quiet_period_resets_timer(tmp_path: Path) -> None:
    p = tmp_path / "notes.md"
    p.write_text("hi")

    calls: list[Path] = []
    done = threading.Event()

    def cb(path: Path) -> None:
        calls.append(path)
        done.set()

    handler = DebouncedHandler(cb, quiet_period_s=0.1)
    handler.on_modified(_evt(p))
    time.sleep(0.05)  # halfway through quiet period
    handler.on_modified(_evt(p))  # should reset timer
    time.sleep(0.05)  # still inside the new quiet period
    assert not calls, "callback fired before quiet period elapsed"

    assert done.wait(timeout=1.0)
    assert calls == [p]


def test_processed_file_event_is_ignored(tmp_path: Path) -> None:
    p = tmp_path / "notes.md"
    p.write_text("hi")
    (tmp_path / "notes.md.processed").write_text("done")

    calls: list[Path] = []

    def cb(path: Path) -> None:
        calls.append(path)

    handler = DebouncedHandler(cb, quiet_period_s=0.05)
    handler.on_modified(_evt(p))
    time.sleep(0.2)
    assert calls == []


def test_directory_events_are_ignored(tmp_path: Path) -> None:
    calls: list[Path] = []

    def cb(path: Path) -> None:
        calls.append(path)

    handler = DebouncedHandler(cb, quiet_period_s=0.05)
    dir_evt = SimpleNamespace(src_path=str(tmp_path), dest_path=str(tmp_path), is_directory=True, event_type="modified")
    handler.on_modified(dir_evt)
    time.sleep(0.15)
    assert calls == []


def test_callback_exception_does_not_crash_handler(tmp_path: Path) -> None:
    p = tmp_path / "notes.md"
    p.write_text("hi")

    fired = threading.Event()

    def cb(path: Path) -> None:
        fired.set()
        raise RuntimeError("boom")

    handler = DebouncedHandler(cb, quiet_period_s=0.05)
    handler.on_modified(_evt(p))
    assert fired.wait(timeout=1.0)
    # If we got here without an unhandled exception escaping, we pass.
