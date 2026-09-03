# Main-branch governance

The runtime contract assumes that `main` is a protected integration branch.
The repository cannot turn GitHub branch protection on from application code,
so the repository administrator must configure these settings for `main`:

- pull requests required;
- at least one human approval and dismissal of stale approvals after new
  commits;
- the `CI` and `arp-contract` checks required before merge;
- force pushes and branch deletion disabled;
- direct pushes limited to maintainers;
- auto-merge disabled for this control plane.

The code-level invariant is `merge_authority="human_only"`; GitHub branch
protection is the external enforcement layer for the repository itself.
