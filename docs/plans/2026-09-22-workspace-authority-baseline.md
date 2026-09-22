# Preserve workspace authority across pre-existing edits

## Acceptance contract

Given a captured workspace baseline, an unchanged dirty or staged file remains
acceptable in a read-only stage. Further content changes, removal, executable
mode changes, staging, or committing are workspace writes and must be rejected
unless their exact paths have write authority. Names containing spaces, newlines,
carriage returns, Unicode, or arrow text retain their original identity.

## Design and verification

Use the existing Git authority adapter: capture index identities and hashes of
tracked/non-ignored working files, compare the state at verification, and retain
the committed-head diff. Parse Git paths with NUL separators and compare symlink
links without dereferencing their targets. Bind each baseline to its workspace.
No new abstraction or design pattern is needed.

Real temporary Git repositories reproduce the contract through the public
capture/verify interface. Initial regressions demonstrated approval of further
edits to already dirty tracked/untracked files, plus wrong-path decisions caused
by stripping/parsing porcelain output. Verification includes unchanged staged
files and staged-only, deleted, committed, executable, and symlink changes.

Run `PYTHONPATH=src PYTHONWARNINGS=error python -m unittest discover -s tests -v`,
`python -m compileall -q src tests`, and `git diff --check`. The verifier remains
a before/after observation, not runtime isolation; see the integration guide.
