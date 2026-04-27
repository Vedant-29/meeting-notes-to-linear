"""Linear client unit tests with respx mocking httpx.

Covers: happy path, 5xx retry exhaustion, 4xx no-retry, label name->id mapping
with auto-create, team key resolution.
"""

from __future__ import annotations

import httpx
import pytest
import respx

from schemas.ticket import Ticket
from src.linear_client import LinearClient, LinearError, LinearTransientError

# Match any path on the Linear host — httpx normalizes base_url + "" to a trailing slash,
# so an exact URL string can miss. Host-level matching is unambiguous since this is the only
# host our client talks to.
LINEAR_HOST_PATTERN = "https://api.linear.app/"
TEAM_UUID = "11111111-1111-1111-1111-111111111111"
LABEL_BUG_ID = "label-bug-id"
LABEL_FEATURE_ID = "label-feature-id"


def _client(team_id: str = TEAM_UUID) -> LinearClient:
    return LinearClient(api_key="test-key", team_identifier=team_id, max_retries=3, backoff_base=0.0)


def _gql_response(data: dict) -> httpx.Response:
    return httpx.Response(200, json={"data": data})


@respx.mock
def test_team_resolution_from_short_key() -> None:
    respx.post(url__startswith=LINEAR_HOST_PATTERN).mock(
        return_value=_gql_response(
            {"teams": {"nodes": [{"id": TEAM_UUID, "key": "TES", "name": "Test"}]}}
        )
    )
    client = _client(team_id="TES")
    assert client.resolve_team() == TEAM_UUID
    # Cached on second call.
    assert client.resolve_team() == TEAM_UUID
    assert respx.calls.call_count == 1


@respx.mock
def test_team_resolution_unknown_key_raises() -> None:
    respx.post(url__startswith=LINEAR_HOST_PATTERN).mock(
        return_value=_gql_response(
            {"teams": {"nodes": [{"id": "other", "key": "OTHER", "name": "Other"}]}}
        )
    )
    client = _client(team_id="MISSING")
    with pytest.raises(LinearError, match="not found"):
        client.resolve_team()


@respx.mock
def test_create_issue_happy_path_with_existing_labels() -> None:
    # 1) labels query  2) issueCreate mutation
    route = respx.post(url__startswith=LINEAR_HOST_PATTERN).mock(
        side_effect=[
            _gql_response(
                {"team": {"labels": {"nodes": [{"id": LABEL_BUG_ID, "name": "bug"}]}}}
            ),
            _gql_response(
                {
                    "issueCreate": {
                        "success": True,
                        "issue": {"id": "i1", "identifier": "TES-1", "url": "https://linear.app/x/issue/TES-1"},
                    }
                }
            ),
        ]
    )
    client = _client()
    ticket = Ticket(title="Fix auth", description="", priority=2, labels=["bug"])
    issue = client.create_issue(ticket)
    assert issue.id == "i1"
    assert issue.identifier == "TES-1"
    assert route.call_count == 2

    # Inspect the issueCreate call body.
    create_body = route.calls[1].request.read().decode()
    assert LABEL_BUG_ID in create_body
    assert '"priority": 2' in create_body or '"priority":2' in create_body


@respx.mock
def test_create_issue_creates_missing_label() -> None:
    route = respx.post(url__startswith=LINEAR_HOST_PATTERN).mock(
        side_effect=[
            _gql_response({"team": {"labels": {"nodes": []}}}),  # cache empty
            _gql_response(
                {
                    "issueLabelCreate": {
                        "success": True,
                        "issueLabel": {"id": LABEL_FEATURE_ID, "name": "feature"},
                    }
                }
            ),
            _gql_response(
                {
                    "issueCreate": {
                        "success": True,
                        "issue": {"id": "i2", "identifier": "TES-2", "url": "https://linear.app/x"},
                    }
                }
            ),
        ]
    )
    client = _client()
    issue = client.create_issue(Ticket(title="Ship feature", labels=["feature"]))
    assert issue.identifier == "TES-2"
    assert route.call_count == 3


@respx.mock
def test_5xx_retries_then_raises_transient() -> None:
    respx.post(url__startswith=LINEAR_HOST_PATTERN).mock(return_value=httpx.Response(503, text="upstream unavailable"))
    client = _client()
    with pytest.raises(LinearTransientError):
        client.list_teams()
    # max_retries=3 → 3 attempts total.
    assert respx.calls.call_count == 3


@respx.mock
def test_4xx_raises_immediately_no_retry() -> None:
    respx.post(url__startswith=LINEAR_HOST_PATTERN).mock(return_value=httpx.Response(401, text="unauthorized"))
    client = _client()
    with pytest.raises(LinearError, match="401"):
        client.list_teams()
    assert respx.calls.call_count == 1


@respx.mock
def test_graphql_errors_in_payload_raise_permanent() -> None:
    respx.post(url__startswith=LINEAR_HOST_PATTERN).mock(
        return_value=httpx.Response(200, json={"errors": [{"message": "bad query"}]})
    )
    client = _client()
    with pytest.raises(LinearError, match="GraphQL errors"):
        client.list_teams()


def test_empty_api_key_raises_at_construction() -> None:
    with pytest.raises(LinearError, match="LINEAR_API_KEY"):
        LinearClient(api_key="", team_identifier=TEAM_UUID)


def test_empty_team_id_raises_at_construction() -> None:
    with pytest.raises(LinearError, match="LINEAR_TEAM_ID"):
        LinearClient(api_key="k", team_identifier="")
