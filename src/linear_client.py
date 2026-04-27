"""Linear GraphQL client.

Responsibilities:
- Resolve team identifier (UUID or short key like "TES") to a UUID at startup.
- Map label NAMES to label IDs, creating missing labels lazily.
- Map project NAME to project ID if it exists (else attaches no project).
- Create one issue with retry on 5xx, raise on 4xx.

Batch logic and sidecar JSON live in main.py — keeping this client small.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from typing import Optional
from uuid import UUID

import httpx

from schemas.ticket import Ticket

LINEAR_API_URL = "https://api.linear.app/graphql"
log = logging.getLogger(__name__)


class LinearError(Exception):
    """Permanent Linear failure (4xx, schema mismatch, or auth). Don't retry."""


class LinearTransientError(Exception):
    """Recoverable Linear failure (5xx, network). Caller may retry."""


@dataclass
class CreatedIssue:
    id: str
    identifier: str  # e.g. "TES-42"
    url: str


def _is_uuid(value: str) -> bool:
    try:
        UUID(value)
        return True
    except (ValueError, AttributeError):
        return False


class LinearClient:
    def __init__(
        self,
        api_key: str,
        team_identifier: str,
        *,
        max_retries: int = 3,
        backoff_base: float = 0.5,
        timeout: float = 15.0,
    ) -> None:
        if not api_key:
            raise LinearError("LINEAR_API_KEY is empty")
        if not team_identifier:
            raise LinearError("LINEAR_TEAM_ID is empty")

        self._client = httpx.Client(
            base_url=LINEAR_API_URL,
            headers={"Authorization": api_key, "Content-Type": "application/json"},
            timeout=timeout,
        )
        self._max_retries = max_retries
        self._backoff_base = backoff_base

        # Resolved on first request.
        self._team_id: Optional[str] = team_identifier if _is_uuid(team_identifier) else None
        self._team_key: Optional[str] = None if _is_uuid(team_identifier) else team_identifier
        # Caches keyed by team_id.
        self._label_cache: dict[str, str] = {}  # name(lower) -> id
        self._project_cache: dict[str, str] = {}  # name(lower) -> id

    # ------------------------------------------------------------------ teams

    def list_teams(self) -> list[dict]:
        """Returns [{id, key, name}] for all teams the API key can see."""
        data = self._gql("query { teams { nodes { id key name } } }")
        return data["teams"]["nodes"]

    def resolve_team(self) -> str:
        """Returns the team UUID, resolving from short key if needed.

        Idempotent — caches the result.
        """
        if self._team_id:
            return self._team_id

        teams = self.list_teams()
        for team in teams:
            if team["key"].lower() == (self._team_key or "").lower():
                self._team_id = team["id"]
                log.info("resolved team key %s -> %s (%s)", team["key"], team["id"], team["name"])
                return self._team_id

        keys = ", ".join(t["key"] for t in teams) or "(none visible)"
        raise LinearError(
            f"team key {self._team_key!r} not found. Visible teams: {keys}. "
            "Either fix LINEAR_TEAM_ID or check that the API key has access."
        )

    # ----------------------------------------------------------------- labels

    def _load_labels(self, team_id: str) -> None:
        """Populate the label name->id cache for this team."""
        # Linear caps `first` at 250; we assume teams have <250 labels for v1.
        query = """
        query($teamId: String!) {
          team(id: $teamId) {
            labels(first: 250) { nodes { id name } }
          }
        }"""
        data = self._gql(query, {"teamId": team_id})
        nodes = data["team"]["labels"]["nodes"]
        self._label_cache = {n["name"].lower(): n["id"] for n in nodes}

    def _ensure_labels(self, names: list[str]) -> list[str]:
        """Resolve label names to IDs, creating missing labels in the team."""
        if not names:
            return []
        team_id = self.resolve_team()
        if not self._label_cache:
            self._load_labels(team_id)

        ids: list[str] = []
        for name in names:
            key = name.strip().lower()
            if not key:
                continue
            if key in self._label_cache:
                ids.append(self._label_cache[key])
                continue
            # Create the label.
            mutation = """
            mutation($input: IssueLabelCreateInput!) {
              issueLabelCreate(input: $input) {
                success
                issueLabel { id name }
              }
            }"""
            result = self._gql(
                mutation,
                {"input": {"name": name.strip(), "teamId": team_id}},
            )
            payload = result["issueLabelCreate"]
            if not payload["success"]:
                raise LinearError(f"failed to create label {name!r}")
            new_id = payload["issueLabel"]["id"]
            self._label_cache[key] = new_id
            ids.append(new_id)
        return ids

    # --------------------------------------------------------------- projects

    def _load_projects(self, team_id: str) -> None:
        query = """
        query($teamId: String!) {
          team(id: $teamId) {
            projects(first: 250) { nodes { id name } }
          }
        }"""
        data = self._gql(query, {"teamId": team_id})
        nodes = data["team"]["projects"]["nodes"]
        self._project_cache = {n["name"].lower(): n["id"] for n in nodes}

    def _resolve_project(self, name: Optional[str]) -> Optional[str]:
        if not name:
            return None
        team_id = self.resolve_team()
        if not self._project_cache:
            self._load_projects(team_id)
        return self._project_cache.get(name.strip().lower())

    # ----------------------------------------------------------------- issues

    def create_issue(self, ticket: Ticket) -> CreatedIssue:
        """Create one Linear issue. Retries 5xx with exponential backoff. Raises LinearError on 4xx or permanent failure."""
        team_id = self.resolve_team()
        label_ids = self._ensure_labels(ticket.labels)
        project_id = self._resolve_project(ticket.project)

        input_obj: dict = {
            "teamId": team_id,
            "title": ticket.title,
            "description": ticket.description or "",
            "priority": ticket.priority,
        }
        if label_ids:
            input_obj["labelIds"] = label_ids
        if project_id:
            input_obj["projectId"] = project_id
        if ticket.estimate is not None:
            input_obj["estimate"] = ticket.estimate

        mutation = """
        mutation($input: IssueCreateInput!) {
          issueCreate(input: $input) {
            success
            issue { id identifier url }
          }
        }"""
        data = self._gql(mutation, {"input": input_obj})
        payload = data["issueCreate"]
        if not payload["success"] or not payload["issue"]:
            raise LinearError(f"issueCreate returned success=false for title={ticket.title!r}")
        issue = payload["issue"]
        return CreatedIssue(id=issue["id"], identifier=issue["identifier"], url=issue["url"])

    # -------------------------------------------------------- transport core

    def _gql(self, query: str, variables: Optional[dict] = None) -> dict:
        """Send a GraphQL request. Retries 5xx and network errors; raises on 4xx and GraphQL errors."""
        body = {"query": query, "variables": variables or {}}
        last_exc: Optional[Exception] = None

        for attempt in range(self._max_retries):
            try:
                resp = self._client.post("", json=body)
            except (httpx.TimeoutException, httpx.NetworkError) as exc:
                last_exc = exc
                self._sleep_backoff(attempt)
                continue

            if 500 <= resp.status_code < 600:
                last_exc = LinearTransientError(f"HTTP {resp.status_code}: {resp.text[:200]}")
                self._sleep_backoff(attempt)
                continue
            if resp.status_code >= 400:
                # 4xx: don't retry. Auth, validation, rate-limit-permanent, etc.
                raise LinearError(f"HTTP {resp.status_code}: {resp.text[:500]}")

            payload = resp.json()
            if "errors" in payload:
                # GraphQL-level error. Treat as permanent — usually schema or auth.
                raise LinearError(f"GraphQL errors: {payload['errors']}")
            return payload["data"]

        # Exhausted retries.
        raise LinearTransientError(
            f"Linear request failed after {self._max_retries} attempts: {last_exc}"
        )

    def _sleep_backoff(self, attempt: int) -> None:
        delay = self._backoff_base * (2**attempt)
        log.warning("linear request retry in %.2fs (attempt %d)", delay, attempt + 1)
        time.sleep(delay)

    def close(self) -> None:
        self._client.close()


# --------------------------------------------------------- CLI helper for setup


def _cli_list_teams() -> None:
    """`python -m src.linear_client list-teams` — sanity-check your API key and find UUIDs."""
    import os

    from dotenv import load_dotenv

    load_dotenv()
    api_key = os.environ.get("LINEAR_API_KEY", "")
    if not api_key:
        print("LINEAR_API_KEY not set in environment or .env")
        raise SystemExit(2)
    client = LinearClient(api_key=api_key, team_identifier="placeholder")
    # Override team requirement just for listing.
    client._team_id = "ignored"
    teams = client.list_teams()
    if not teams:
        print("No teams visible to this API key.")
        return
    print(f"{'KEY':<10} {'UUID':<40} NAME")
    for t in teams:
        print(f"{t['key']:<10} {t['id']:<40} {t['name']}")


if __name__ == "__main__":
    import sys

    if len(sys.argv) > 1 and sys.argv[1] == "list-teams":
        _cli_list_teams()
    else:
        print("usage: python -m src.linear_client list-teams")
        raise SystemExit(2)
