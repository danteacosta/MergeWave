from __future__ import annotations

import unittest

from mergewave.linear_adapter import LinearGraphQLAdapter


class FakeLinearTransport:
    def __init__(self, responses: list[object]) -> None:
        self.responses = iter(responses)
        self.calls: list[tuple[str, dict[str, object]]] = []

    def execute(self, query: str, variables: dict[str, object]) -> object:
        self.calls.append((query, variables))
        return next(self.responses)


class LinearGraphQLAdapterTests(unittest.TestCase):
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
        self.assertEqual(transport.calls[0][1], {"team_id": "team-1"})

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
