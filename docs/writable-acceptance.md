# Writable acceptance runbook

This gate is intentionally absent from normal CI because it changes a Linear
issue and relies on a real GitHub pull request. Use only a disposable issue,
repository branch, and local checkout. It never creates, approves, merges, or
closes a pull request.

Required environment:

```console
MERGEWAVE_ACCEPT_WRITES=DISPOSABLE_RESOURCES_ONLY
MERGEWAVE_ACCEPTANCE_REPOSITORY_PATH=/path/to/disposable/checkout
MERGEWAVE_ACCEPTANCE_WORKSPACE_ROOT=/path/to/disposable/worktrees
MERGEWAVE_GITHUB_TOKEN=...
MERGEWAVE_GITHUB_REPOSITORY=owner/disposable-repository
MERGEWAVE_GITHUB_BRANCH=agent/existing-test-pr
MERGEWAVE_GITHUB_BASE_REVISION=<exact-commit-sha>
MERGEWAVE_GITHUB_SCOPE_PATHS=src/,tests/
MERGEWAVE_LINEAR_API_KEY=...
MERGEWAVE_LINEAR_TEAM_ID=...
MERGEWAVE_LINEAR_ISSUE_ID=TEST-1
MERGEWAVE_LINEAR_RESTORE_STATE=Todo
PYTHONPATH=src python -m mergewave --writable-acceptance
```

The run fails closed before writes if a variable is missing. It then:

1. creates and validates a real worktree at the exact base SHA;
2. observes an existing PR, head check run, reviews, changed-file scope, and
   base ancestry through the GitHub adapter;
3. transitions the disposable Linear issue to `In Progress`;
4. links the PR and posts one acceptance audit comment;
5. transitions the issue to `In Review` and verifies the attachment;
6. restores the configured original Linear state; and
7. removes the temporary worktree and local acceptance branch.

Optional state-name overrides are
`MERGEWAVE_LINEAR_IN_PROGRESS_STATE` and
`MERGEWAVE_LINEAR_IN_REVIEW_STATE`. The JSON result contains normalized IDs,
SHAs, and signals, never tokens.

This gate proves adapter interoperability in the selected sandbox. It is not
authorization for auto-merge: `DeliveryController` rejects every merge
authority other than `human_only`.
