"""Wire watcher → extractor → linear_client.

End-to-end flow for one note file:

    notes.md
       │
       ▼
    extractor.extract(notes_text) → list[Ticket]
       │
       │  empty? ─yes─► write notes.md.processed (no-op succeeded)
       │  no
       ▼
    for each ticket: linear_client.create_issue(ticket)
       │       ├─ success → record CreatedIssue
       │       ├─ transient → already retried 3x inside client; treat as permanent
       │       └─ permanent → record failure
       ▼
    all succeeded? ─yes─► write notes.md.processed
                  no  ─► write notes.md.failed + notes.md.failed.json
                          {"succeeded": [{"title", "id", "identifier", "url"}],
                           "failed":    [{"title", "error"}]}

The .processed and .failed sibling markers are the idempotency mechanism — the
watcher's `is_target_file()` skips any .md that has either marker.
"""

from __future__ import annotations

import json
import logging
import os
import signal
import sys
import threading
import time
from pathlib import Path
from typing import Optional

from dotenv import load_dotenv

from src.extractor import Extractor, ExtractorError
from src.linear_client import (
    CreatedIssue,
    LinearClient,
    LinearError,
    LinearTransientError,
)
from src.watcher import run_watcher

log = logging.getLogger(__name__)


def _setup_logging() -> None:
    logging.basicConfig(
        level=os.environ.get("LOG_LEVEL", "INFO").upper(),
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )


def _write_processed_marker(note_path: Path) -> None:
    marker = note_path.with_name(note_path.name + ".processed")
    marker.write_text(time.strftime("%Y-%m-%dT%H:%M:%S%z"), encoding="utf-8")


def _write_failed_marker(
    note_path: Path,
    succeeded: list[tuple[str, CreatedIssue]],
    failed: list[tuple[str, str]],
) -> None:
    marker = note_path.with_name(note_path.name + ".failed")
    marker.write_text(time.strftime("%Y-%m-%dT%H:%M:%S%z"), encoding="utf-8")
    sidecar = note_path.with_name(note_path.name + ".failed.json")
    payload = {
        "note": str(note_path),
        "timestamp": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "succeeded": [
            {"title": title, "id": issue.id, "identifier": issue.identifier, "url": issue.url}
            for title, issue in succeeded
        ],
        "failed": [{"title": title, "error": err} for title, err in failed],
    }
    sidecar.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def process_note(
    note_path: Path,
    extractor: Extractor,
    linear: LinearClient,
) -> None:
    """Single-file pipeline. Catches all exceptions; never raises to the watcher."""
    log.info("processing %s", note_path)
    try:
        text = note_path.read_text(encoding="utf-8")
    except OSError as exc:
        log.error("could not read %s: %s", note_path, exc)
        _write_failed_marker(note_path, [], [("(file read)", str(exc))])
        return

    try:
        tickets = extractor.extract(text)
    except ExtractorError as exc:
        log.error("extractor failed for %s: %s", note_path, exc)
        _write_failed_marker(note_path, [], [("(extractor)", str(exc))])
        return

    if not tickets:
        log.info("no actionable items in %s; marking processed", note_path)
        _write_processed_marker(note_path)
        return

    succeeded: list[tuple[str, CreatedIssue]] = []
    failed: list[tuple[str, str]] = []

    for ticket in tickets:
        try:
            issue = linear.create_issue(ticket)
            succeeded.append((ticket.title, issue))
            log.info("created %s — %s", issue.identifier, ticket.title)
        except (LinearError, LinearTransientError) as exc:
            # LinearTransientError here means the client already exhausted its
            # internal retries — treat as permanent for this batch.
            log.error("linear failed for %r: %s", ticket.title, exc)
            failed.append((ticket.title, str(exc)))

    if failed:
        _write_failed_marker(note_path, succeeded, failed)
        log.warning(
            "%s: %d/%d tickets created, %d failed (see %s.failed.json)",
            note_path.name,
            len(succeeded),
            len(tickets),
            len(failed),
            note_path.name,
        )
    else:
        _write_processed_marker(note_path)
        log.info("%s: all %d tickets created", note_path.name, len(succeeded))


def main(argv: Optional[list[str]] = None) -> int:
    _setup_logging()
    load_dotenv()

    inbox = os.environ.get("INBOX_DIR", "").strip()
    api_key_anthropic = os.environ.get("ANTHROPIC_API_KEY", "").strip()
    api_key_linear = os.environ.get("LINEAR_API_KEY", "").strip()
    team_id = os.environ.get("LINEAR_TEAM_ID", "").strip()
    model = os.environ.get("EXTRACTOR_MODEL", "claude-sonnet-4-6").strip()

    missing = [
        name
        for name, val in [
            ("INBOX_DIR", inbox),
            ("ANTHROPIC_API_KEY", api_key_anthropic),
            ("LINEAR_API_KEY", api_key_linear),
            ("LINEAR_TEAM_ID", team_id),
        ]
        if not val
    ]
    if missing:
        log.error("missing required env vars: %s. See .env.example.", ", ".join(missing))
        return 2

    inbox_path = Path(inbox).expanduser().resolve()
    extractor = Extractor(api_key=api_key_anthropic, model=model)
    linear = LinearClient(api_key=api_key_linear, team_identifier=team_id)
    # Resolve the team UUID once at startup so first-file latency doesn't include it.
    try:
        linear.resolve_team()
    except LinearError as exc:
        log.error("could not resolve Linear team: %s", exc)
        return 2

    def callback(path: Path) -> None:
        process_note(path, extractor, linear)

    observer = run_watcher(inbox_path, callback)

    stopping = threading.Event()

    def _stop(*_args) -> None:
        log.info("shutting down")
        stopping.set()

    signal.signal(signal.SIGINT, _stop)
    signal.signal(signal.SIGTERM, _stop)

    try:
        while not stopping.is_set():
            stopping.wait(timeout=1.0)
    finally:
        observer.stop()
        observer.join(timeout=5)
        linear.close()

    return 0


if __name__ == "__main__":
    sys.exit(main())
