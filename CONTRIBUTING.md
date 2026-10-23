# Contributing

## Issues and sprints

- **Sprints are milestones:** Sprint 1 (core & demo readiness), Sprint 2 (beautiful & interactive UI), Sprint 3 (demo day).
- **Labels:** `type:*`, `area:*`, `priority:P0–P2`, `guardrail` (touches an AGENTS.md rule), `stretch`.
- **Dependencies:** each issue lists its blockers under **## Blocked by** and as GitHub "blocked by" links.
  - `status:blocked`: at least one blocker is still open. Don't start it.
  - `status:ready`: every blocker is closed. Pick it up.
  - The **Issue dependencies** workflow switches the label automatically when a blocker closes (or blocks again if one reopens). Run it manually from the Actions tab to resync.
- **Closing an issue:** reference it from the PR (`Closes #12`) so it closes on merge and unblocks its dependents.

## Before you push

See the commands in `AGENTS.md`. The **CI passed** check must be green.
