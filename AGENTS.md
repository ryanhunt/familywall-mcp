# AI development contract

This is the shared operating contract for Codex, Claude Code, and any agents
working in this repository. `CLAUDE.md` imports this file so both tools use the
same policy.

## Repository context

- This repository holds a Python async client and MCP server that ports the
  useful FamilyWall API surface from the sibling `familywall-api` TypeScript
  project. The local **stdio** server is implemented and live-verified against
  a real FamilyWall account: see [docs/PROGRESS.md](docs/PROGRESS.md) for the
  phase table and current status.
- The first hosted version is for a small group of invited family members,
  each with their own login: a self-hosted OAuth identity mapped to that
  person's FamilyWall credentials. Hosted mode is implemented (P5: OAuth with a
  static user list configured through `FAMILYWALL_USER_<N>_*` variables; P6:
  Docker and HTTPS deployment files) but not yet verified end-to-end against a
  real Claude or ChatGPT connector. Self-service invitations and multi-family
  support are not implemented.
- `halaxy-mcp` is a local design reference for a small Python MCP server and
  its operational documentation. Do not copy its domain assumptions or
  sensitive practice-management guidance into this project.
- Do not describe self-service invitations or multi-family support as
  implemented, or hosted mode as verified against real clients, until
  `docs/PROGRESS.md`'s phase table says so — check it rather than assuming.

## Working agreement

1. Read this file before making a change. Read only the repository files needed
   for the task, then record the relevant evidence in the task brief or
   handoff.
2. Keep each change focused on one objective. Preserve unrelated user edits,
   inspect the worktree before editing, and review the resulting diff.
3. Use a fresh `codex/<short-slug>` or `claude/<short-slug>` branch for normal
   implementation work. Do not commit, push, open a PR, or merge unless the
   user explicitly asks for that delivery action.
4. Prefer small, bounded, cheap-agent tasks for repository reconnaissance,
   documentation lookup, implementation phases, and output summarisation. Give
   every delegated task a narrow objective, a file boundary, and an evidence or
   acceptance criterion. A cheaper agent may implement a bounded phase once
   the relevant contract and acceptance criteria are defined. Keep architecture
   and security decisions, integration across phases, and final review with
   the primary agent.
5. Do not add new permission gates for routine work already authorised by the
   user. Ask only when a missing choice would materially change the requested
   result or an external action requires new authority.
6. For a change that affects runtime behaviour, add or update meaningful tests
   and run the project-defined checks: `uv sync --frozen --group dev`,
   `uv run ruff check .`, `uv run ruff format --check .`, `uv run mypy src`,
   `uv run pytest -m 'not live'`, `uv build`, and `scripts/check`. Live tests
   remain opt-in. All ten MCP tools are implemented and live-verified against
   a real FamilyWall account (see `docs/compatibility.md`); the OAuth
   provider and hosted mode are not live-verified.

## Security and data handling

- Never put passwords, FamilyWall credentials, OAuth client secrets, access
  tokens, session cookies, private keys, real account data, or `.env` contents
  in Git, prompts, logs, fixtures, screenshots, or PR text.
- Examples may use dummy placeholders such as `email@example.com` and
  `yourpassword`. Keep local `.env` files and runtime credential/state stores
  untracked; `.env.example` may contain placeholders only.
- Never print environment variables or credential stores. Never change Git
  identity, signing configuration, remotes, or provider credentials. Do not add
  AI usernames, co-author trailers, or claims of original authorship.
- Treat FamilyWall calendar and family information as private user data. Keep
  fixtures synthetic and minimise any data copied into diagnostics.

## Review and handoff

Before handing work back, check the diff for scope creep, secrets, personal
data, generated files, and unsupported claims about the current implementation.
Include the changed files, acceptance evidence, validation performed (or why it
is unavailable), known limitations, and the next bounded task in the handoff.
Use the templates in `docs/templates/` for repeatable task briefs and
handoffs.
