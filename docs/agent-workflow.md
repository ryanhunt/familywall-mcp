# Agent workflow

This workflow keeps Claude Code and Codex aligned while the repository grows
from a documentation-only foundation into a small hosted MCP service.

## 1. Define one bounded task

Start with [`templates/task-brief.md`](./templates/task-brief.md). State the
user-visible objective, the files in scope, acceptance criteria, and the
evidence that will establish completion. A task should be small enough that a
reviewer can understand its full effect from one diff.

If the request is broad, split it into sequential briefs. Good early tasks
include one API mapping, one MCP tool contract, one authentication boundary,
or one validation slice. Keep the first hosted release's invited-family scope
visible when making identity and data-handling decisions.

## 2. Gather only the context needed

Read `AGENTS.md` first, inspect the current worktree, and then read the
smallest set of source, documentation, and sibling-project files that answer
the brief. Record sources and unresolved assumptions in the brief. Avoid
loading whole repositories or copying sensitive examples from other domains.

When delegation is useful, give the agent a single bounded objective, an
explicit file boundary, and an acceptance criterion. Cheap agents are
appropriate for reconnaissance, documentation lookup, bounded implementation
phases, and summarising checks. After the relevant contract is defined, a
cheaper agent may implement that phase within its boundary. The primary agent
retains architecture and security decisions, integration across phases, and
final review.

## 3. Implement within the brief

Keep the diff limited to the stated files and acceptance criteria. Preserve
unrelated work in a dirty worktree. Use dummy identities and synthetic data in
fixtures and examples. Never place FamilyWall credentials, OAuth secrets,
cookies, tokens, or real family records in source, prompts, logs, screenshots,
or generated artifacts.

If implementation reveals a material change in scope, update the brief before
continuing. Do not create a permission gate for a routine, reversible step
already covered by the user's request. Request direction only when a missing
choice changes the result or requires a new external authority.

## 4. Verify the result

Review the diff and worktree status for accidental files, secrets, personal
data, generated output, and unsupported claims. Run the repository's defined
tests, lint, type checks, and other checks when they exist, and attach the
command and result to the handoff.

The project-defined checks are listed in `AGENTS.md` (working agreement, item
6); `scripts/check` runs them together. Report each command and its result, and
never claim a check ran when it did not. Live tests stay opt-in.

## 5. Hand off clearly

Use [`templates/handoff.md`](./templates/handoff.md) to report the changed
files, acceptance evidence, validation, known limitations, and the next
bounded task. A handoff should let another agent continue without rereading
the entire conversation. Normal implementation work stays on its fresh branch;
delivery actions such as commit, push, PR, or merge happen only when the user
asks for them.
