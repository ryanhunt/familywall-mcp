# FamilyWall MCP

A [Model Context Protocol](https://modelcontextprotocol.io) server that gives
Claude, ChatGPT and other MCP clients access to a [FamilyWall](https://familywall.com)
account: shopping lists and a timezone-aware view of the family calendar. Not
affiliated with FamilyWall.

It ports the useful parts of the [FamilyWall web API](https://github.com/ryanhunt/familywall-api)
(itself reverse-engineered protocol research) into a typed async Python client
and an MCP server, so an AI assistant can read a family's shopping lists and
week, and add or check off items, without ever seeing raw FamilyWall
credentials in a prompt.

**Current status:** the local **stdio** server is implemented and
live-verified end to end against a real FamilyWall account, including writes.
Hosted OAuth and Docker/HTTPS deployment are implemented and unit-tested, but
have **not** yet been verified against a real Claude or ChatGPT connector over
HTTPS — see [Known limitations](#known-limitations) and
[docs/PROGRESS.md](docs/PROGRESS.md) for the authoritative phase table.

## What it does

Six MCP tools are exposed today:

| Tool | Type | Description |
| --- | --- | --- |
| `get_connection_status` | read | Connection state, family name, member count, resolved timezone, whether writes are enabled |
| `list_shopping_lists` | read | Every shopping/todo list accessible to the authenticated member |
| `get_list_items` | read | Items in a specific list, with checked state |
| `get_week_overview` | read | A timezone-aware week of calendar events (Monday- or Sunday-start), with recurrence already expanded |
| `add_list_item` | write | Add an item to a list, with an idempotency key so retries don't create duplicates |
| `set_list_item_checked` | write | Mark a list item checked or unchecked (explicit target state, not a toggle) |

A few things worth knowing about how these behave:

- **Writes are off by default.** `add_list_item` and `set_list_item_checked`
  stay listed but refuse before making any upstream request until
  `FAMILYWALL_ENABLE_WRITES=true` is set deliberately.
- **Mutations are idempotent.** Every write takes (or generates) an
  `idempotency_key`; a durable SQLite receipt records the outcome so a retried
  call returns the original result instead of creating a duplicate.
- **`get_week_overview` covers calendar events only** — not meals, budgets or
  undated tasks.
- There is no delete or move tool yet, and item `quantity` cannot be read back
  (FamilyWall's API doesn't return it).

See [docs/architecture.md](docs/architecture.md) for the full tool contract
and the service rules (list selection, receipt semantics, timezone handling)
behind them.

## Prerequisites

- Python 3.12+
- [`uv`](https://docs.astral.sh/uv/) for dependency management
- A FamilyWall account (email + password) for each person who will use it

## Install and run locally (stdio)

This is the recommended way to use it today — one person, running the server
as a local subprocess of their own MCP client, talking to their own FamilyWall
account.

```bash
git clone https://github.com/ryanhunt/familywall-mcp.git
cd familywall-mcp
uv sync --frozen
```

Configure it with environment variables — either export them, or create a
`.env` and export it into your shell (the app itself does not load `.env`
files; `env_file:` in Docker Compose is what does that in the hosted path):

```bash
FAMILYWALL_MODE=stdio
FAMILYWALL_LOCAL_SUBJECT=<any stable local id, e.g. your name>
FAMILYWALL_BASE_URL=https://api.familywall.com
FAMILYWALL_EMAIL=<your FamilyWall account email>
FAMILYWALL_PASSWORD=<your FamilyWall account password>
FAMILYWALL_ENABLE_WRITES=false
```

Then run it directly to confirm it starts and speaks MCP over stdio:

```bash
uv run familywall-mcp serve
```

Once that works, point an MCP client at it instead of running it by hand —
see the next section. Set `FAMILYWALL_ENABLE_WRITES=true` only once you're
ready for the assistant to actually modify your lists.

### Configuring MCP clients

Every stdio-capable client wants roughly the same three things: a command to
run (`uv`), arguments that run `familywall-mcp serve` from this checkout, and
the environment variables above. Replace `/path/to/familywall-mcp` with your
actual clone path.

**Claude Desktop** — edit `claude_desktop_config.json` ([config file location](https://modelcontextprotocol.io/quickstart/user)):

```json
{
  "mcpServers": {
    "familywall": {
      "command": "uv",
      "args": [
        "run", "--project", "/path/to/familywall-mcp",
        "familywall-mcp", "serve"
      ],
      "env": {
        "FAMILYWALL_MODE": "stdio",
        "FAMILYWALL_LOCAL_SUBJECT": "your-name",
        "FAMILYWALL_BASE_URL": "https://api.familywall.com",
        "FAMILYWALL_EMAIL": "you@example.com",
        "FAMILYWALL_PASSWORD": "yourpassword",
        "FAMILYWALL_ENABLE_WRITES": "false"
      }
    }
  }
}
```

**Claude Code** — from the repo, or anywhere with `--project`:

```bash
claude mcp add familywall \
  --env FAMILYWALL_MODE=stdio \
  --env FAMILYWALL_LOCAL_SUBJECT=your-name \
  --env FAMILYWALL_BASE_URL=https://api.familywall.com \
  --env FAMILYWALL_EMAIL=you@example.com \
  --env FAMILYWALL_PASSWORD=yourpassword \
  --env FAMILYWALL_ENABLE_WRITES=false \
  -- uv run --project /path/to/familywall-mcp familywall-mcp serve
```

**Other stdio clients** (Cursor, Windsurf, Zed, etc.) — these generally read
the same `mcpServers` shape shown above from their own settings file; consult
the client's MCP documentation for the exact file location.

**ChatGPT** does not support local stdio MCP servers — it only connects over
HTTPS. To use this server from ChatGPT, deploy the hosted mode below and add
it there as a custom connector.

## Hosted deployment (Docker + Caddy)

Hosted mode runs the server over HTTPS with per-user OAuth, so several
invited family members can each log in with their own MCP username/password,
which is mapped to their own FamilyWall credentials — one process, one
container, no per-user infrastructure. Users are configured statically by the
operator via environment variables; there is no self-service signup or
invitation flow (see [ADR 0002](docs/decisions/0002-simplified-hosted-auth.md)
for why).

**This path is implemented and unit-tested against the ASGI app directly, but
has not yet been verified against a real Claude or ChatGPT connector over a
live HTTPS deployment.** Treat it as ready to try, not as a proven integration.

### 1. Prerequisites

- A domain name pointing at your host, with ports 80 and 443 reachable from
  the internet (Caddy needs both to provision Let's Encrypt certificates).
- Docker and Docker Compose.

### 2. Get the code

Clone the repository onto the host that will run the containers:

```bash
git clone https://github.com/ryanhunt/familywall-mcp.git
cd familywall-mcp
```

If you already have a clone from an earlier install, update it to the latest
`main` before redeploying:

```bash
git checkout main
git fetch origin
git merge origin/main
```

(`git merge origin/main` after a `fetch` is equivalent to `git pull` while
being explicit that it's a fast-forward/merge sync, not a rebase — if you have
local edits to tracked files like `Caddyfile` or `.env`, commit or stash them
first so the merge doesn't conflict.)

### 3. Configure users and secrets

Copy `.env.example` to `.env` and fill in the hosted-mode section with at
least one user:

```bash
FAMILYWALL_MODE=hosted
FAMILYWALL_PUBLIC_URL=https://your-domain.com
FAMILYWALL_AUTH_SECRET_KEY=<random-32+-character-string>
FAMILYWALL_ALLOWED_REDIRECT_URI_HOSTS=claude.ai,chatgpt.com
FAMILYWALL_BASE_URL=https://api.familywall.com

FAMILYWALL_USER_1_MCP_USERNAME=alice
FAMILYWALL_USER_1_MCP_PASSWORD=<mcp-password-alice-will-log-in-with>
FAMILYWALL_USER_1_FW_EMAIL=alice@familywall.account
FAMILYWALL_USER_1_FW_PASSWORD=<alice's real FamilyWall password>
```

Add `FAMILYWALL_USER_2_*`, `FAMILYWALL_USER_3_*`, etc. for additional family
members (up to 20; the MCP username/password is separate from and need not
match their FamilyWall login). See the comments in `.env.example` for every
variable and its constraints, and generate the secret key with something like:

```bash
openssl rand -base64 32
```

### 4. Configure Caddy

Copy `Caddyfile.example` to `Caddyfile` and replace the placeholder domain
with your own:

```bash
cp Caddyfile.example Caddyfile
```

```
your-domain.example.com {
    reverse_proxy familywall-mcp:8000
}
```

Caddy automatically provisions and renews a Let's Encrypt certificate for
that domain — no manual certificate management.

### 5. Deploy

```bash
docker compose -f docker-compose.prod.yml up -d --build
```

This starts two containers: `familywall-mcp` (the server, listening
internally on port 8000, not exposed to the host) and `caddy` (the reverse
proxy, bound to ports 80/443, handling TLS termination). Both persist state
in named Docker volumes (`familywall_data`, `caddy_data`, `caddy_config`).

`docker-compose.yml` (without `.prod`) is a local-only variant that runs just
the app container bound to `127.0.0.1:8000`, for testing without Caddy or a
real domain. If you already have your own HTTPS reverse proxy (e.g. a
Synology NAS's built-in one), see
[docs/synology-nas.md](docs/synology-nas.md) and
`docker-compose.nas.yml` for a Caddy-free variant.

Check it's up:

```bash
curl https://your-domain.com/health
```

### 6. Add it to Claude or ChatGPT

In Claude or ChatGPT's custom-connector / MCP settings, add
`https://your-domain.com/mcp`. The client will redirect to this server's
login page; sign in with one of the `FAMILYWALL_USER_<N>_MCP_USERNAME` /
`MCP_PASSWORD` pairs from `.env` and authorize the connector. The assistant
can now call the FamilyWall tools as that person.

## Environment variables

See `.env.example` for the full, commented list. In short:

| Variable | Mode | Purpose |
| --- | --- | --- |
| `FAMILYWALL_MODE` | both | `stdio` or `hosted` |
| `FAMILYWALL_LOCAL_SUBJECT` | stdio | Any stable local identifier for the single stdio user |
| `FAMILYWALL_BASE_URL` | both | FamilyWall API base URL. Set it to `https://api.familywall.com` (do not append `/api`). |
| `FAMILYWALL_EMAIL` / `FAMILYWALL_PASSWORD` | stdio | The FamilyWall account this stdio server acts as |
| `FAMILYWALL_ENABLE_WRITES` | both | `true`/`false` — write tools refuse until this is `true` |
| `FAMILYWALL_DATABASE_PATH` | both | SQLite path for write receipts (and OAuth state in hosted mode) |
| `FAMILYWALL_PUBLIC_URL` | hosted | Public HTTPS URL of the deployment, e.g. `https://mcp.example.com` |
| `FAMILYWALL_PORT` | hosted | Internal port the server listens on (default `8000`) |
| `FAMILYWALL_AUTH_SECRET_KEY` | hosted | Random ≥32-character string for signing cookies/CSRF tokens |
| `FAMILYWALL_ALLOWED_REDIRECT_URI_HOSTS` | hosted | Comma-separated hosts an OAuth client may redirect to (`claude.ai,chatgpt.com`) |
| `FAMILYWALL_USER_<N>_MCP_USERNAME` / `_MCP_PASSWORD` / `_FW_EMAIL` / `_FW_PASSWORD` | hosted | One block per invited family member (up to 20) |

## Known limitations

- **Hosted OAuth is unverified against real clients.** The provider is
  implemented and unit-tested against the ASGI app directly, but no real
  Claude or ChatGPT connector has completed a live login yet.
- **No delete or move tool.** The underlying FamilyWall endpoints for
  deleting and moving items are wire-verified, but not yet exposed as MCP
  tools.
- **Item `quantity` cannot be read.** FamilyWall's API never returns it, so a
  tool that writes a quantity can't verify it took effect.
- **Calendar only, no meals/budgets/tasks.** `get_week_overview` covers
  calendar events; other FamilyWall modules aren't ported.
- **Single-family accounts only are exercised.** Multi-family discovery is
  implemented defensively but not live-tested (the verification account has
  one family).
- **No self-service invitations or account pages.** Hosted users are
  configured statically by the operator via `.env`; onboarding a new family
  member means editing and redeploying `.env`.
- **All-day calendar dates are read verbatim, never timezone-converted** —
  by design, matching how FamilyWall itself stores them, but worth knowing if
  you build on top of the raw dates.

See [docs/compatibility.md](docs/compatibility.md) for the full, itemized
verification record, and [docs/PROGRESS.md](docs/PROGRESS.md) for current
phase status.

## Documentation

- [Build progress](docs/PROGRESS.md) — phase-by-phase status and live-verification notes
- [Architecture](docs/architecture.md) — account mapping, credentials, tool contract, deployment
- [Compatibility](docs/compatibility.md) — what has actually been verified, and how
- [Implementation plan](docs/implementation-plan.md) — phases, dependencies, release acceptance
- [Wire contracts](docs/contracts/familywall.md) and [calendar contracts](docs/contracts/calendar.md) — endpoint-level evidence
- [ADR 0001](docs/decisions/0001-auth-and-sdk.md) — MCP SDK and auth approach
- [ADR 0002](docs/decisions/0002-simplified-hosted-auth.md) — simplified hosted auth rationale
- [Contributing](CONTRIBUTING.md) — workflow and validation commands

## Credits

Inspired by [ryanhunt/halaxy-mcp](https://github.com/ryanhunt/halaxy-mcp) and
based on protocol research in
[ryanhunt/familywall-api](https://github.com/ryanhunt/familywall-api), which
credits [Tomsoz](https://github.com/Tomsoz/familywall-api) and
[CodingButter](https://github.com/CodingButter/familywall-api).

Licensed under [MIT](LICENSE).
