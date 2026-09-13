# AI development contract

This is the shared operating contract for Codex, Claude Code, and any agents
working in this repository. `CLAUDE.md` imports this file so both tools use the
same policy.

## Repository context

- This repository is the foundation for a Python async client and MCP server
  that will port the useful FamilyWall API surface from the sibling
  `familywall-api` TypeScript project.
- The first hosted version is intended for a small group of invited family
  members. Each person will have their own login, with a self-hosted OAuth
  identity mapped to that person's FamilyWall credentials.
- `halaxy-mcp` is a local design reference for a small Python MCP server and
  its operational documentation. Do not copy its domain assumptions or
  sensitive practice-management guidance into this project.
- The current repository is documentation-only aside from `LICENSE` (`README.md`
  is documentation). Do not describe the Python client, MCP tools, OAuth flow, or
  validation commands as implemented until the corresponding foundation lands.

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
   and run the project-defined checks. At the current foundation stage there
   is no runtime, test, lint, type-check, or package-install command to run;
   report that fact instead of claiming a passing command. A plan may propose
   future commands before implementation defines them, but they must be marked
   proposed or unavailable until the foundation provides them.

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
