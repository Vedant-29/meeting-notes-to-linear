"""Pydantic Ticket model validation."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from schemas.ticket import Ticket, TicketBatch


def test_minimal_ticket_roundtrips() -> None:
    t = Ticket(title="Fix the auth bug", description="Stack trace in the slack thread.", priority=2, labels=["bug"])
    assert t.title == "Fix the auth bug"
    assert t.priority == 2
    assert t.labels == ["bug"]
    assert t.estimate is None
    assert t.project is None


def test_priority_must_be_in_enum() -> None:
    with pytest.raises(ValidationError):
        Ticket(title="x", priority=5)  # type: ignore[arg-type]
    with pytest.raises(ValidationError):
        Ticket(title="x", priority="high")  # type: ignore[arg-type]


def test_title_length_capped_at_256() -> None:
    long = "a" * 257
    with pytest.raises(ValidationError):
        Ticket(title=long)


def test_title_must_be_non_empty() -> None:
    with pytest.raises(ValidationError):
        Ticket(title="")
    with pytest.raises(ValidationError):
        Ticket(title="   ")


def test_empty_labels_allowed() -> None:
    t = Ticket(title="x", labels=[])
    assert t.labels == []


def test_estimate_must_be_non_negative() -> None:
    with pytest.raises(ValidationError):
        Ticket(title="x", estimate=-1)


def test_ticket_batch_empty_is_valid() -> None:
    batch = TicketBatch(tickets=[])
    assert batch.tickets == []


def test_ticket_batch_validates_each() -> None:
    batch = TicketBatch.model_validate(
        {
            "tickets": [
                {"title": "Ship the thing", "description": "", "priority": 3, "labels": ["feature"]},
            ]
        }
    )
    assert len(batch.tickets) == 1
    assert batch.tickets[0].title == "Ship the thing"


def test_ticket_batch_rejects_invalid_member() -> None:
    with pytest.raises(ValidationError):
        TicketBatch.model_validate(
            {"tickets": [{"title": "x", "priority": 99, "labels": []}]}
        )
