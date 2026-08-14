from __future__ import annotations

import unittest
from unittest.mock import patch
from urllib.error import HTTPError

from mergewave.linear_adapter import LinearGraphQLAdapter, UrllibLinearTransport


class FakeLinearTransport:
    def __init__(self, responses: list[object]) -> None:
        self.responses = iter(responses)
        self.calls: list[tuple[str, dict[str, object]]] = []

    def execute(self, query: str, variables: dict[str, object]) -> object:
        self.calls.append((query, variables))
        return next(self.responses)


class LinearGraphQLAdapterTests(unittest.TestCase):
    def test_personal_api_key_is_sent_without_bearer_prefix(self) -> None:
        class FakeResponse:
            def __enter__(self):
                return self

            def __exit__(self, *args: object) -> None:
                pass

            def read(self) -> bytes:
                return b'{"data": {}}'

        with patch("mergewave.linear_adapter.urlopen", return_value=FakeResponse()) as open_url:
            UrllibLinearTransport("lin_api_key").execute("query { viewer { id } }", {})

        request = open_url.call_args.args[0]
        self.assertEqual(request.get_header("Authorization"), "lin_api_key")

    def test_fetch_candidates_maps_issue_contract_and_inverse_blockers(self) -> None:
        transport = FakeLinearTransport(
            [
                {
                    "data": {
                        "team": {
                            "issues": {
                                "nodes": [
                                    {
                                        "id": "issue-1",
                                        "identifier": "CTRL-1",
                                        "title": "Ship adapter",
                                        "description": "Complete the adapter",
                                        "url": "https://linear.app/acme/issue/CTRL-1",
                                        "state": {"id": "state-todo", "name": "Todo"},
                                        "inverseRelations": {
                                            "nodes": [
                                                {
                                                    "type": "blocks",
                                                    "issue": {"identifier": "CTRL-0"},
                                                }
                                            ]
                                        },
                                    }
                                ]
                            }
                        }
                    }
                }
            ]
        )
        adapter = LinearGraphQLAdapter(transport, team_id="team-1")

        candidates = adapter.fetch_candidates()

        self.assertEqual(candidates[0]["id"], "CTRL-1")
        self.assertEqual(candidates[0]["blocked_by"], ["CTRL-0"])
        self.assertEqual(candidates[0]["linear_id"], "issue-1")
        self.assertEqual(transport.calls[0][1], {"team_id": "team-1", "after": None})

    def test_fetch_candidates_follows_linear_connection_pages(self) -> None:
        transport = FakeLinearTransport(
            [
                {"data": {"team": {"issues": {"nodes": [{"identifier": "CTRL-1"}], "pageInfo": {"hasNextPage": True, "endCursor": "cursor-1"}}}}},
                {"data": {"team": {"issues": {"nodes": [{"identifier": "CTRL-2"}], "pageInfo": {"hasNextPage": False, "endCursor": "cursor-2"}}}}},
            ]
        )
        adapter = LinearGraphQLAdapter(transport, team_id="team-1")

        candidates = adapter.fetch_candidates()

        self.assertEqual([candidate["id"] for candidate in candidates], ["CTRL-1", "CTRL-2"])
        self.assertEqual(transport.calls[1][1]["after"], "cursor-1")

    def test_dependencies_link_and_acceptance_signal_are_explicit_tracker_operations(self) -> None:
        transport = FakeLinearTransport(
            [
                {"data": {"issue": {"inverseRelations": {"nodes": [{"type": "blocks", "issue": {"identifier": "CTRL-0"}}]}}}},
                {"data": {"attachmentsForURL": {"nodes": [{"issue": {"identifier": "CTRL-1"}}]}}},
                {"data": {"issue": {"description": "- [x] First\n- [ ] Second"}}},
            ]
        )
        adapter = LinearGraphQLAdapter(transport, team_id="team-1")

        self.assertEqual(adapter.fetch_dependencies("CTRL-1"), ["CTRL-0"])
        self.assertTrue(adapter.pull_request_linked("CTRL-1", "https://github.com/acme/demo/pull/1"))
        self.assertEqual(adapter.acceptance_criteria_signal("CTRL-1"), "partial")

    def test_transport_retries_rate_limit_and_server_failures(self) -> None:
        class FakeResponse:
            def __enter__(self):
                return self

            def __exit__(self, *args: object) -> None:
                pass

            def read(self) -> bytes:
                return b'{"data": {}}'

        error = HTTPError("https://api.linear.app/graphql", 429, "rate limited", {"Retry-After": "0"}, None)
        with patch("mergewave.linear_adapter.urlopen", side_effect=[error, FakeResponse()]), patch("mergewave.linear_adapter.time.sleep") as sleep:
            UrllibLinearTransport("key", max_retries=1).execute("query { viewer { id } }", {})

        sleep.assert_called_once()

    def test_tracker_mutations_transition_link_and_comment(self) -> None:
        transport = FakeLinearTransport(
            [
                {"data": {"issueUpdate": {"success": True}}},
                {"data": {"attachmentCreate": {"success": True}}},
                {"data": {"commentCreate": {"success": True}}},
            ]
        )
        adapter = LinearGraphQLAdapter(transport, team_id="team-1")

        adapter.transition_state("CTRL-1", "state-progress")
        adapter.link_pull_request("CTRL-1", "https://github.com/acme/demo/pull/1")
        adapter.post_comment("CTRL-1", "CI passed")

        self.assertEqual(len(transport.calls), 3)
        self.assertIn("issueUpdate", transport.calls[0][0])
        self.assertEqual(transport.calls[0][1], {"issue_id": "CTRL-1", "state_id": "state-progress"})
        self.assertIn("attachmentCreate", transport.calls[1][0])
        self.assertEqual(transport.calls[1][1]["issue_id"], "CTRL-1")
        self.assertIn("commentCreate", transport.calls[2][0])

    def test_graphql_errors_are_not_silently_treated_as_empty_data(self) -> None:
        transport = FakeLinearTransport([{"errors": [{"message": "forbidden"}]}])
        adapter = LinearGraphQLAdapter(transport, team_id="team-1")

        with self.assertRaisesRegex(RuntimeError, "forbidden"):
            adapter.fetch_candidates()


if __name__ == "__main__":
    unittest.main()
