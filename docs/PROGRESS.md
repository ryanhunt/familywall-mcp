# P0 working-plan progress

Purpose: make the P0 discovery resumable across sessions. Update the status column
as each phase lands. See [implementation plan](implementation-plan.md) and
[task cards](tasks.md).

Branch: `claude/p0-implementation-plan`

| Phase | Objective | Output | Status |
| --- | --- | --- | --- |
| A | Reconnaissance of sibling repos | scratch recon notes (untracked) | done |
| B | MCP SDK + self-hosted OAuth decision | `docs/decisions/0001-auth-and-sdk.md`, `docs/compatibility.md` | done |
| C | FamilyWall wire contracts | `docs/contracts/familywall.md`, `docs/contracts/calendar.md` | done |
| D | Concrete implementation plan rewrite | `docs/implementation-plan.md`, `docs/tasks.md` | done |
| E | Diff review and handoff | `docs/handoffs/p0-contracts.md` | done |

## Resume instructions

Read this table, then read the outputs of the last completed phase before
continuing. Each phase is self-contained: a new session can start at the first
row that is not `done` without re-reading the sibling repositories.

## Open questions requiring live access

Recorded here as they are found; each stays `pending-live` until a real
FamilyWall test account and an HTTPS endpoint are available.

All P0 phases are complete. The next action is task **02P** (read-only live
probe) in [tasks.md](tasks.md), which needs a FamilyWall test account. Until it
runs, P4 (calendar) stays blocked and the `pending-live` question lists in the
two contract files are the authoritative record of what is still unknown.
