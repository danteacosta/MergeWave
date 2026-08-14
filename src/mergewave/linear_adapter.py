"""Linear GraphQL tracker adapter with an injectable transport boundary."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
import json
from typing import Protocol
from urllib.request import Request, urlopen


class LinearGraphQLTransport(Protocol):
    def execute(self, query: str, variables: dict[str, object]) -> object: ...


class UrllibLinearTransport:
    def __init__(
        self,
        token: str,
        *,
        api_url: str = "https://api.linear.app/graphql",
        authorization_scheme: str = "Bearer",
    ) -> None:
        self._token = token
        self._api_url = api_url
        self._authorization = f"{authorization_scheme} {token}" if authorization_scheme else token

    def execute(self, query: str, variables: dict[str, object]) -> object:
        request = Request(
            self._api_url,
            data=json.dumps({"query": query, "variables": variables}).encode("utf-8"),
            method="POST",
            headers={
                "Accept": "application/json",
                "Content-Type": "application/json",
                "Authorization": self._authorization,
            },
        )
        with urlopen(request) as response:
            return json.loads(response.read().decode("utf-8"))


class LinearGraphQLAdapter:
    """Implement the tracker port without coupling core scheduling to Linear."""

    def __init__(self, transport: LinearGraphQLTransport, *, team_id: str) -> None:
        self._transport = transport
        self._team_id = team_id

    def fetch_candidates(self) -> Sequence[dict[str, object]]:
        response = self._execute(
            """
            query MergeWaveTeamIssues($team_id: String!) {
              team(id: $team_id) {
                issues {
                  nodes {
                    id identifier title description url
                    state { id name }
                    inverseRelations {
                      nodes { type issue { identifier } }
                    }
                  }
                }
              }
            }
            """,
            {"team_id": self._team_id},
        )
        team = response.get("team") if isinstance(response, Mapping) else None
        issues = team.get("issues", {}) if isinstance(team, Mapping) else {}
        nodes = issues.get("nodes", []) if isinstance(issues, Mapping) else []
        return tuple(self._normalize_issue(node) for node in nodes if isinstance(node, Mapping))

    def transition_state(self, item_id: str, state: str) -> None:
        response = self._execute(
            """
            mutation MergeWaveIssueUpdate($issue_id: String!, $state_id: String!) {
              issueUpdate(id: $issue_id, input: { stateId: $state_id }) { success }
            }
            """,
            {"issue_id": item_id, "state_id": state},
        )
        self._require_success(response, "issueUpdate")

    def link_pull_request(self, item_id: str, url: str) -> None:
        response = self._execute(
            """
            mutation MergeWaveAttachment($issue_id: String!, $url: String!) {
              attachmentCreate(
                input: {
                  issueId: $issue_id
                  title: "MergeWave pull request"
                  subtitle: "Delivery review"
                  url: $url
                }
              ) { success }
            }
            """,
            {"issue_id": item_id, "url": url},
        )
        self._require_success(response, "attachmentCreate")

    def post_comment(self, item_id: str, body: str) -> None:
        response = self._execute(
            """
            mutation MergeWaveComment($issue_id: String!, $body: String!) {
              commentCreate(input: { issueId: $issue_id, body: $body }) { success }
            }
            """,
            {"issue_id": item_id, "body": body},
        )
        self._require_success(response, "commentCreate")

    def _execute(self, query: str, variables: dict[str, object]) -> Mapping[str, object]:
        response = self._transport.execute(query, variables)
        if not isinstance(response, Mapping):
            raise RuntimeError("Linear GraphQL response must be an object")
        errors = response.get("errors")
        if errors:
            message = "; ".join(
                str(error.get("message", error)) if isinstance(error, Mapping) else str(error)
                for error in errors
            )
            raise RuntimeError(f"Linear GraphQL request failed: {message}")
        data = response.get("data")
        if not isinstance(data, Mapping):
            raise RuntimeError("Linear GraphQL response did not contain data")
        return data

    @staticmethod
    def _normalize_issue(issue: Mapping[str, object]) -> dict[str, object]:
        relations = issue.get("inverseRelations", {})
        relation_nodes = relations.get("nodes", []) if isinstance(relations, Mapping) else []
        blocked_by = []
        for relation in relation_nodes:
            if not isinstance(relation, Mapping) or relation.get("type") != "blocks":
                continue
            blocker = relation.get("issue")
            if isinstance(blocker, Mapping) and isinstance(blocker.get("identifier"), str):
                blocked_by.append(blocker["identifier"])
        state = issue.get("state")
        state_name = state.get("name") if isinstance(state, Mapping) else None
        return {
            "id": issue.get("identifier", issue.get("id", "")),
            "linear_id": issue.get("id", ""),
            "title": issue.get("title", ""),
            "description": issue.get("description") or "",
            "url": issue.get("url", ""),
            "state": state_name or "",
            "blocked_by": blocked_by,
        }

    @staticmethod
    def _require_success(data: Mapping[str, object], operation: str) -> None:
        result = data.get(operation)
        if not isinstance(result, Mapping) or result.get("success") is not True:
            raise RuntimeError(f"Linear mutation failed: {operation}")


__all__ = ["LinearGraphQLAdapter", "LinearGraphQLTransport", "UrllibLinearTransport"]
