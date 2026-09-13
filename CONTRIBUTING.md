# Contributing

The repository now contains the Phase 1 package and offline harness. The MCP
server, FamilyWall client/tools, and login flow remain unimplemented. Do not
imply that those planned capabilities are available.

Read [`AGENTS.md`](./AGENTS.md) before working, then use the bounded workflow
in [`docs/agent-workflow.md`](./docs/agent-workflow.md). Start with a focused
task brief, inspect only the context needed for that task, and finish with the
handoff template. Keep changes on a fresh `codex/<short-slug>` or
`claude/<short-slug>` branch. Commits, pushes, pull requests, and merges are
delivery actions that require the user's request.

Install from the committed lockfile with `uv sync --frozen --group dev`, then run
`scripts/check`. Individual checks are `uv run ruff check .`, `uv run ruff format
--check .`, `uv run mypy src`, `uv run pytest -m 'not live'`, and `uv build`.
The suite blocks external network access by default; live tests are excluded
from the default run and require explicit authorization.

There is no continuous-integration workflow in this repository. `scripts/check`
is run locally and its result recorded in the pull request; nothing enforces it
automatically.

Keep credentials and private family data out of the repository and its
diagnostics. Use synthetic fixtures and dummy values in examples. Existing
Git identity, signing, remotes, and authentication belong to the contributor
and must not be changed by automation.
