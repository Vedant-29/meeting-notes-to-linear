from typing import Annotated, Literal, Optional

from pydantic import BaseModel, Field, StringConstraints

# Linear's IssueCreateInput priority enum.
# 0 = No priority, 1 = Urgent, 2 = High, 3 = Medium, 4 = Low
LinearPriority = Literal[0, 1, 2, 3, 4]

# Linear titles cap at 256 chars. We reject longer at parse time so the LLM can
# never produce something the API will refuse.
TicketTitle = Annotated[str, StringConstraints(min_length=1, max_length=256, strip_whitespace=True)]


class Ticket(BaseModel):
    title: TicketTitle
    description: str = Field(
        default="",
        description="Markdown body of the ticket. Can include context, links, and acceptance criteria.",
    )
    priority: LinearPriority = Field(
        default=0,
        description="0=No priority, 1=Urgent, 2=High, 3=Medium, 4=Low.",
    )
    labels: list[str] = Field(
        default_factory=list,
        description=(
            "Label NAMES (not IDs). The Linear client maps names to IDs and creates "
            "missing labels in the team. Keep labels short and reusable."
        ),
    )
    estimate: Optional[float] = Field(
        default=None,
        description="Story points / effort estimate. Use only if confident from the notes.",
        ge=0,
    )
    project: Optional[str] = Field(
        default=None,
        description="Project name (free-form). Linear client maps to project ID if it exists.",
    )


class TicketBatch(BaseModel):
    """Wrapper the LLM returns. An empty list is valid (note had no actionable items)."""

    tickets: list[Ticket] = Field(default_factory=list)
