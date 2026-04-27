"""LLM extractor.

Calls Claude with a forced tool that mirrors the Ticket schema. Parses the tool
input (which Anthropic guarantees against the schema) and returns a list of
Ticket models. Pydantic does the final validation, so anything malformed raises
ExtractorError before it ever reaches the Linear client.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Optional

from anthropic import Anthropic
from anthropic import APIError, APITimeoutError
from pydantic import ValidationError

from schemas.ticket import Ticket, TicketBatch

log = logging.getLogger(__name__)

# JSON Schema for the tool. Mirrors schemas.ticket.Ticket exactly. Kept inline
# (rather than generated from Pydantic) so the LLM sees clean docs and so a
# schema change is a deliberate two-line edit, not an accidental drift.
_TICKET_TOOL = {
    "name": "submit_tickets",
    "description": (
        "Submit the list of Linear issues extracted from the notes. "
        "Pass an empty array if the notes have no actionable items."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "tickets": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "title": {
                            "type": "string",
                            "minLength": 1,
                            "maxLength": 256,
                            "description": "Short imperative. Starts with a verb.",
                        },
                        "description": {
                            "type": "string",
                            "description": "Markdown body. Context + acceptance criteria.",
                        },
                        "priority": {
                            "type": "integer",
                            "enum": [0, 1, 2, 3, 4],
                            "description": "0=No priority, 1=Urgent, 2=High, 3=Medium, 4=Low.",
                        },
                        "labels": {
                            "type": "array",
                            "items": {"type": "string"},
                            "description": "1-3 short lowercase tags.",
                        },
                        "estimate": {
                            "type": ["number", "null"],
                            "minimum": 0,
                            "description": "Story points. Null if uncertain.",
                        },
                        "project": {
                            "type": ["string", "null"],
                            "description": "Project name. Null if not specified in notes.",
                        },
                    },
                    "required": ["title", "description", "priority", "labels"],
                    "additionalProperties": False,
                },
            }
        },
        "required": ["tickets"],
        "additionalProperties": False,
    },
}


class ExtractorError(Exception):
    """Raised when the LLM call fails or returns unparseable output."""


class Extractor:
    def __init__(
        self,
        *,
        api_key: str,
        model: str = "claude-sonnet-4-6",
        prompt_path: Optional[Path] = None,
        max_tokens: int = 4096,
    ) -> None:
        if not api_key:
            raise ExtractorError("ANTHROPIC_API_KEY is empty")
        self._client = Anthropic(api_key=api_key)
        self._model = model
        self._max_tokens = max_tokens

        if prompt_path is None:
            prompt_path = Path(__file__).resolve().parent.parent / "prompts" / "extract_tickets.md"
        if not prompt_path.exists():
            raise ExtractorError(f"prompt file not found: {prompt_path}")
        self._system_prompt = prompt_path.read_text(encoding="utf-8")

    def extract(self, notes_text: str) -> list[Ticket]:
        """Run the LLM on notes_text. Returns a list of validated Ticket models.

        Empty list is a valid, expected outcome (notes had no actionable items).
        """
        if not notes_text.strip():
            log.info("extractor: notes are empty/whitespace, returning []")
            return []

        try:
            response = self._client.messages.create(
                model=self._model,
                max_tokens=self._max_tokens,
                system=self._system_prompt,
                tools=[_TICKET_TOOL],
                tool_choice={"type": "tool", "name": "submit_tickets"},
                messages=[{"role": "user", "content": notes_text}],
            )
        except APITimeoutError as exc:
            raise ExtractorError(f"Anthropic timeout: {exc}") from exc
        except APIError as exc:
            raise ExtractorError(f"Anthropic API error: {exc}") from exc

        # Forced tool_choice means the response MUST contain a tool_use block.
        tool_blocks = [b for b in response.content if getattr(b, "type", None) == "tool_use"]
        if not tool_blocks:
            raise ExtractorError(
                f"LLM returned no tool_use block despite forced tool_choice. "
                f"Stop reason: {response.stop_reason}. Content: {response.content!r}"
            )
        if len(tool_blocks) > 1:
            log.warning("LLM returned %d tool_use blocks, using the first", len(tool_blocks))

        raw_input = tool_blocks[0].input

        try:
            batch = TicketBatch.model_validate(raw_input)
        except ValidationError as exc:
            raise ExtractorError(
                f"LLM tool input failed schema validation: {exc}. Raw input: {raw_input!r}"
            ) from exc

        log.info("extractor: produced %d ticket(s)", len(batch.tickets))
        return batch.tickets
