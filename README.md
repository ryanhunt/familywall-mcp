# FamilyWall MCP

A planned Python MCP server for using FamilyWall from ChatGPT and Claude.
Not affiliated with FamilyWall.

The first release will support shopping-list additions and completion, plus a
timezone-aware view of the family's week. A few invited family members will each
have a self-hosted MCP login linked to their own encrypted FamilyWall credentials.

**Current status:** the local **stdio** MCP server runs and has been
live-verified end to end against a real FamilyWall account. It exposes six
tools — `get_connection_status`, `list_shopping_lists`, `get_list_items`,
`get_week_overview`, `add_list_item` and `set_list_item_checked` — and both
reads and writes (add, move, delete, check/uncheck) have been confirmed
live, with durable SQLite receipts recording outcomes. The write gate is off
by default; tools stay listed but refuse before touching the upstream API
until it is turned on deliberately. Hosted OAuth, containers/HTTPS and
multi-user invites are not implemented yet — see
[docs/PROGRESS.md](docs/PROGRESS.md) for the full phase table.

- [Build progress](docs/PROGRESS.md): phase-by-phase status, live-verification notes and how to run the server locally.
- [Implementation plan](docs/implementation-plan.md): phases, dependencies and release acceptance.
- [Delegable task cards](docs/tasks.md): bounded work and copyable prompts for cheaper agents.
- [Architecture](docs/architecture.md): account mapping, credentials, tools and deployment.
- [Research](docs/research.md): pinned repository evidence and unresolved API behavior.
- [Wire contracts](docs/contracts/familywall.md) and [calendar contracts](docs/contracts/calendar.md): endpoint-level evidence levels, including live-verified sections.
- [ADR 0001](docs/decisions/0001-auth-and-sdk.md): the MCP SDK and self-hosted OAuth decision.
- [Compatibility](docs/compatibility.md): what has actually been verified, and how.
- [AI workflow](docs/agent-workflow.md): shared Claude Code/Codex conventions.
- [Contributing](CONTRIBUTING.md): workflow and validation commands.

### Local development

Install the locked development environment with `uv sync --frozen --group dev`.
Run `scripts/check` for lint, format, type, offline-test, build, and secret-scan
checks. Configure via environment variables (`FAMILYWALL_MODE=stdio`,
`FAMILYWALL_BASE_URL`, `FAMILYWALL_EMAIL`, `FAMILYWALL_PASSWORD`,
`FAMILYWALL_LOCAL_SUBJECT`, `FAMILYWALL_ENABLE_WRITES`; see
[docs/PROGRESS.md](docs/PROGRESS.md#how-to-run-it-locally)), then run
`uv run familywall-mcp serve` to start the MCP server on stdio.

### Hosted deployment

To run a multi-user hosted MCP server with OAuth and HTTPS (backed by Caddy and
Let's Encrypt):

1. **Prerequisites:** A domain pointing at your host, with ports 80 and 443
   reachable from the internet.

2. **Configure users:** Copy `.env.example` to `.env` and fill in the hosted-mode
   section with at least one user:
   ```
   FAMILYWALL_MODE=hosted
   FAMILYWALL_PUBLIC_URL=https://your-domain.com
   FAMILYWALL_AUTH_SECRET_KEY=<random-32+-character-string>
   FAMILYWALL_USER_1_MCP_USERNAME=alice
   FAMILYWALL_USER_1_MCP_PASSWORD=<mcp-password>
   FAMILYWALL_USER_1_FW_EMAIL=alice@familywall.account
   FAMILYWALL_USER_1_FW_PASSWORD=<familywall-password>
   FAMILYWALL_ALLOWED_REDIRECT_URI_HOSTS=claude.ai,chatgpt.com
   ```
   See `.env.example` for additional users, all variables, and security notes.

3. **Configure Caddy:** Copy `Caddyfile.example` to `Caddyfile` and replace
   `your-domain.example.com` with your actual domain. Caddy will automatically
   provision and renew Let's Encrypt certificates.

4. **Deploy:** Run `docker compose -f docker-compose.prod.yml up -d --build`.
   The MCP server listens internally on port 8000; Caddy handles public HTTPS
   on port 443.

5. **Add to Claude or ChatGPT:** Navigate to `https://your-domain.com/mcp`,
   follow the OAuth login prompt with an MCP username and password from `.env`,
   and authorize the connector. The AI can now call your FamilyWall tools.

Inspired by [ryanhunt/halaxy-mcp](https://github.com/ryanhunt/halaxy-mcp) and based on
protocol research in [ryanhunt/familywall-api](https://github.com/ryanhunt/familywall-api),
which credits [Tomsoz](https://github.com/Tomsoz/familywall-api) and
[CodingButter](https://github.com/CodingButter/familywall-api).
Licensed under [MIT](LICENSE).
