"""Orchestration test: process_note happy path, no-op, partial Linear failure.

We mock the Extractor and LinearClient at the boundary. This validates the
orchestration logic (markers, sidecar JSON, partial-failure shape) without
hitting the network. The eval suite covers the LLM side; respx covers the
Linear-client side. This file glues them.
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from schemas.ticket import Ticket
from src.linear_client import CreatedIssue, LinearError
from src.main import process_note


def _make_extractor(tickets: list[Ticket]) -> MagicMock:
    ex = MagicMock()
    ex.extract.return_value = tickets
    return ex


def _make_linear(side_effects: list) -> MagicMock:
    """side_effects: list of CreatedIssue or Exception, one per create_issue call."""
    client = MagicMock()
    client.create_issue.side_effect = side_effects
    return client


def _issue(identifier: str) -> CreatedIssue:
    return CreatedIssue(id=f"id-{identifier}", identifier=identifier, url=f"https://linear.app/x/{identifier}")


def test_happy_path_writes_processed_marker(tmp_path: Path) -> None:
    note = tmp_path / "notes.md"
    note.write_text("real content")

    extractor = _make_extractor([Ticket(title="A"), Ticket(title="B")])
    linear = _make_linear([_issue("TES-1"), _issue("TES-2")])

    process_note(note, extractor, linear)

    assert (tmp_path / "notes.md.processed").exists()
    assert not (tmp_path / "notes.md.failed").exists()


def test_no_op_extraction_still_marks_processed(tmp_path: Path) -> None:
    note = tmp_path / "chitchat.md"
    note.write_text("thanks for the chat")

    extractor = _make_extractor([])  # no tickets
    linear = _make_linear([])

    process_note(note, extractor, linear)

    assert (tmp_path / "chitchat.md.processed").exists()
    linear.create_issue.assert_not_called()


def test_partial_failure_writes_sidecar_with_succeeded_and_failed(tmp_path: Path) -> None:
    note = tmp_path / "notes.md"
    note.write_text("c")

    extractor = _make_extractor([Ticket(title="A"), Ticket(title="B"), Ticket(title="C")])
    linear = _make_linear(
        [
            _issue("TES-10"),
            LinearError("HTTP 422: invalid"),
            _issue("TES-11"),
        ]
    )

    process_note(note, extractor, linear)

    # Failed marker present, processed marker NOT present.
    assert (tmp_path / "notes.md.failed").exists()
    assert not (tmp_path / "notes.md.processed").exists()

    sidecar = tmp_path / "notes.md.failed.json"
    assert sidecar.exists()
    payload = json.loads(sidecar.read_text())
    assert len(payload["succeeded"]) == 2
    assert len(payload["failed"]) == 1
    assert payload["failed"][0]["title"] == "B"
    succeeded_titles = {row["title"] for row in payload["succeeded"]}
    assert succeeded_titles == {"A", "C"}


def test_extractor_failure_writes_failed_marker_no_linear_calls(tmp_path: Path) -> None:
    note = tmp_path / "notes.md"
    note.write_text("c")

    from src.extractor import ExtractorError

    extractor = MagicMock()
    extractor.extract.side_effect = ExtractorError("API down")
    linear = _make_linear([])

    process_note(note, extractor, linear)

    assert (tmp_path / "notes.md.failed").exists()
    sidecar = tmp_path / "notes.md.failed.json"
    payload = json.loads(sidecar.read_text())
    assert payload["succeeded"] == []
    assert "(extractor)" in payload["failed"][0]["title"]
    linear.create_issue.assert_not_called()


def test_unreadable_file_writes_failed_marker(tmp_path: Path) -> None:
    """File deleted between watcher firing and process_note running."""
    note = tmp_path / "ghost.md"
    # Don't create the file — read will fail.

    extractor = _make_extractor([])
    linear = _make_linear([])

    process_note(note, extractor, linear)

    assert (tmp_path / "ghost.md.failed").exists()
    payload = json.loads((tmp_path / "ghost.md.failed.json").read_text())
    assert "(file read)" in payload["failed"][0]["title"]
