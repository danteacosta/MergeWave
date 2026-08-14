"""Linear GraphQL tracker adapter with an injectable transport boundary."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
import json
import re
import time
from typing import Protocol
from urllib.error import HTTPError
from urllib.request import Request, urlopen


class LinearGraphQLTransport(Protocol):
    def execute(self, query: str, variables: dict[str, object]) -> object: ...


class UrllibLinearTransport:
    def __init__(
        self,
        token: str,
        *,
        api_url: str = "https://api.linear.app/graphql",
        authorization_scheme: str = "",
        max_retries: int = 2,
        backoff_seconds: float = 0.5,
    ) -> None:
        self._token = token
        self._api_url = api_url
        self._authorization = f"{authorization_scheme} {token}" if authorization_scheme else token
        if max_retries < 0 or backoff_seconds < 0:
            raise ValueError("retry configuration must be non-negative")
        self._max_retries = max_retries
        self._backoff_seconds = backoff_seconds

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
        for attempt in range(self._max_retries + 1):
            try:
                with urlopen(request) as response:
                    return json.loads(response.read().decode("utf-8"))
            except HTTPError as error:
                retryable = error.code == 429 or error.code >= 500
                if not retryable or attempt >= self._max_retries:
                    raise
                retry_after = error.headers.get("Retry-After") if error.headers else None
                delay = float(retry_after) if retry_after else self._backoff_seconds * (2**attempt)
                time.sleep(delay)
        raise RuntimeError("Linear request exhausted retries")


class LinearGraphQLAdapter:
    """Implement the tracker port without coupling core scheduling to Linear."""

    def __init__(self, transport: LinearGraphQLTransport, *, team_id: str) -> None:
        self._transport = transport
        self._team_id = team_id
        self._state_ids: dict[str, str] = {}

    def resolve_state_id(self, state: str) -> str:
        if state in self._state_ids.values():
            return state
        if not self._state_ids:
            response = self._execute(
                """
                query MergeWaveTeamWorkflowStates($team_id: String!) {
                  team(id: $team_id) {
                    states { nodes { id name type } }
                  }
                }
                """,
                {"team_id": self._team_id},
            )
            team = response.get("team")
            states = team.get("states", {}) if isinstance(team, Mapping) else {}
            nodes = states.get("nodes", []) if isinstance(states, Mapping) else []
            self._state_ids = {
                str(node["name"]): str(node["id"])
                for node in nodes
                if isinstance(node, Mapping) and isinstance(node.get("name"), str) and isinstance(node.get("id"), str)
            }
        try:
            return self._state_ids[state]
        except KeyError as error:
            raise ValueError(f"Linear workflow state was not found: {state}") from error

    def fetch_candidates(self) -> Sequence[dict[str, object]]:
        query = """
            query MergeWaveTeamIssues($team_id: String!, $after: String) {
              team(id: $team_id) {
                issues(after: $after) {
                  nodes {
                    id identifier title description url
                    state { id name }
                    inverseRelations {
                      nodes { type issue { identifier } }
                    }
                  }
                  pageInfo { hasNextPage endCursor }
                }
              }
            }
            """
        candidates: list[dict[str, object]] = []
        after: str | None = None
        seen_cursors: set[str] = set()
        while True:
            response = self._execute(
                query,
                {"team_id": self._team_id, "after": after},
            )
            team = response.get("team") if isinstance(response, Mapping) else None
            issues = team.get("issues", {}) if isinstance(team, Mapping) else {}
            nodes = issues.get("nodes", []) if isinstance(issues, Mapping) else []
            candidates.extend(self._normalize_issue(node) for node in nodes if isinstance(node, Mapping))
            page_info = issues.get("pageInfo", {}) if isinstance(issues, Mapping) else {}
            if not isinstance(page_info, Mapping) or not page_info.get("hasNextPage"):
                break
            next_cursor = page_info.get("endCursor")
            if not isinstance(next_cursor, str) or next_cursor in seen_cursors:
                raise RuntimeError("Linear pagination returned an invalid or repeated cursor")
            seen_cursors.add(next_cursor)
            after = next_cursor
        return tuple(candidates)

    def fetch_dependencies(self, item_id: str) -> Sequence[str]:
        response = self._execute(
            """
            query MergeWaveIssueDependencies($issue_id: String!) {
              issue(id: $issue_id) {
                inverseRelations { nodes { type issue { identifier } } }
              }
            }
            """,
            {"issue_id": item_id},
        )
        issue = response.get("issue")
        relations = issue.get("inverseRelations", {}) if isinstance(issue, Mapping) else {}
        nodes = relations.get("nodes", []) if isinstance(relations, Mapping) else []
        return [
            relation["issue"]["identifier"]
            for relation in nodes
            if isinstance(relation, Mapping)
            and relation.get("type") == "blocks"
            and isinstance(relation.get("issue"), Mapping)
            and isinstance(relation["issue"].get("identifier"), str)
        ]

    def pull_request_linked(self, item_id: str, url: str) -> bool:
        response = self._execute(
            """
            query MergeWaveAttachment($url: String!) {
              attachmentsForURL(url: $url) {
                nodes { issue { id identifier } }
              }
            }
            """,
            {"url": url},
        )
        attachments = response.get("attachmentsForURL", {})
        nodes = attachments.get("nodes", []) if isinstance(attachments, Mapping) else []
        return any(
            isinstance(node, Mapping)
            and isinstance(node.get("issue"), Mapping)
            and item_id in {node["issue"].get("id"), node["issue"].get("identifier")}
            for node in nodes
        )

    def acceptance_criteria_signal(self, item_id: str) -> str:
        response = self._execute(
            """
            query MergeWaveAcceptanceCriteria($issue_id: String!) {
              issue(id: $issue_id) { description }
            }
            """,
            {"issue_id": item_id},
        )
        issue = response.get("issue")
        description = issue.get("description", "") if isinstance(issue, Mapping) else ""
        if not isinstance(description, str):
            return "unknown"
        checks = re.findall(r"^\s*[-*]\s+\[([ xX])\]", description, flags=re.MULTILINE)
        if not checks:
            return "unknown"
        completed = sum(value.lower() == "x" for value in checks)
        return "complete" if completed == len(checks) else "partial"

    def transition_state(self, item_id: str, state: str) -> None:
        response = self._execute(
            """
            mutation MergeWaveIssueUpdate($issue_id: String!, $state_id: String!) {
              issueUpdate(id: $issue_id, input: { stateId: $state_id }) { success }
            }
            """,
            {"issue_id": item_id, "state_id": self.resolve_state_id(state)},
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
        state_id = state.get("id") if isinstance(state, Mapping) else None
        state_name = state.get("name") if isinstance(state, Mapping) else None
        return {
            "id": issue.get("identifier", issue.get("id", "")),
            "linear_id": issue.get("id", ""),
            "title": issue.get("title", ""),
            "description": issue.get("description") or "",
            "url": issue.get("url", ""),
            "state": state_name or "",
            "state_id": state_id or "",
            "blocked_by": blocked_by,
        }

    @staticmethod
    def _require_success(data: Mapping[str, object], operation: str) -> None:
        result = data.get(operation)
        if not isinstance(result, Mapping) or result.get("success") is not True:
            raise RuntimeError(f"Linear mutation failed: {operation}")


__all__ = ["LinearGraphQLAdapter", "LinearGraphQLTransport", "UrllibLinearTransport"]
