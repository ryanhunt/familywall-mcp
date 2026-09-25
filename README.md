# FamilyWall MCP

A [Model Context Protocol](https://modelcontextprotocol.io) server that gives
Claude, ChatGPT and other MCP clients access to a [FamilyWall](https://familywall.com)
account: shopping and to-do lists, a timezone-aware view of the family calendar,
calendar events, and who in the family each item or event is for. Not affiliated
with FamilyWall.

It ports the useful parts of the [FamilyWall web API](https://github.com/ryanhunt/familywall-api)
(itself reverse-engineered protocol research) into a typed async Python client
and an MCP server, so an AI assistant can read a family's lists and week, add
or check off items, create calendar events, and assign items and events to
family members by name, without ever seeing raw FamilyWall credentials (or
FamilyWall account IDs) in a prompt.

**Current status (version 0.2.0):** the local **stdio** server is implemented,
and all ten tools are live-verified end to end against a real FamilyWall
account, including every write.
Hosted OAuth and Docker/HTTPS deployment are implemented and unit-tested, but
have **not** yet been verified against a real Claude or ChatGPT connector over
HTTPS — see [Known limitations](#known-limitations) and
[docs/PROGRESS.md](docs/PROGRESS.md) for the authoritative phase table.

## What's new in 0.2.0

- **Assign items and events to family members.** `create_calendar_event` and
  `add_list_item` take `assigned_to`, a list of member names; the new
  `set_calendar_event_attendees` and `set_list_item_assignees` change who an
  existing event or item is for. Names are matched locally against your own
  family and only FamilyWall account IDs are sent, so no name ever leaves the
  server and no tool accepts or returns an account ID.
- **Blank means everyone.** Leaving `assigned_to` out (or passing `[]`) assigns
  the whole family. **This changes `create_calendar_event`**, which previously
  assigned only the signed-in member.
- **`list_family_members`** lists the exact names to use, and which member is
  you. The week overview and list items now show who each event or item is
  assigned to.
- **`add_list_item` is a single call.** It creates the item directly in the
  chosen list with its assignment, replacing the old create-then-move
  ([ADR 0003](docs/decisions/0003-single-call-add.md)).
- **Events get FamilyWall's default 30-minute reminder**, as the web app does.
- **Receipts migrate in place.** Idempotency receipts are no longer
  list-specific, and a definite refusal from FamilyWall now replays as the
  same error.

**Upgrading an existing deployment:** back up the data volume first. The
receipts table is migrated in place on start-up, and older images cannot read
a migrated database.

## What it does

Ten MCP tools are exposed today:

| Tool | Type | Description |
| --- | --- | --- |
| `get_connection_status` | read | Connection state, family name, member count, resolved timezone, whether writes are enabled |
| `list_shopping_lists` | read | Every shopping/todo list accessible to the authenticated member |
| `get_list_items` | read | Items in a specific list, with checked state and who it's assigned to |
| `get_week_overview` | read | A timezone-aware week of calendar events (Monday- or Sunday-start), with recurrence already expanded, and who each event is assigned to |
| `list_family_members` | read | The family's members by display name and first name, and which one is you (never account IDs) |
| `add_list_item` | write | Add an item to a list, assigned to everyone or to named members in the same call, with an idempotency key so retries don't create duplicates |
| `set_list_item_checked` | write | Mark a list item checked or unchecked (explicit target state, not a toggle) |
| `set_list_item_assignees` | write | Change only who an existing list item is assigned to; nothing else about the item changes |
| `create_calendar_event` | write | Add a timed, one-off event to the family calendar, assigned to everyone or to named members, and confirm it by reading it back |
| `set_calendar_event_attendees` | write | Change only who an existing, ordinary, one-off, timed family-calendar event is assigned to, and confirm nothing else about it changed |

A few things worth knowing about how these behave:

- **Writes are off by default.** `add_list_item`, `set_list_item_checked`,
  `set_list_item_assignees`, `create_calendar_event` and
  `set_calendar_event_attendees` stay listed but refuse before making any
  upstream request until `FAMILYWALL_ENABLE_WRITES=true` is set deliberately.
- **Mutations are idempotent.** Every write takes (or generates) an
  `idempotency_key`; a durable SQLite receipt records the outcome so a retried
  call returns the original result instead of creating a duplicate.
- **`get_week_overview` covers calendar events only** — not meals, budgets or
  undated tasks.
- **`add_list_item` creates the item directly in the chosen list, already
  assigned, in one call.** `assigned_to` takes member names exactly as
  `list_family_members` shows them; omit it, or pass an empty list, to assign
  everyone. An unknown name triggers one refresh of the cached family list
  before failing. There is no separate move step any more (see
  [ADR 0003](docs/decisions/0003-single-call-add.md)); the outcome is
  `confirmed` only when a readback of the requested list finds the item with
  exactly the requested assignment. `misfiled` (the item landed in a list
  other than the one requested) and `mismatched` (the assignment differs) are
  both reported, never silently retried or corrected.
- **`set_list_item_assignees` changes only the assignment.** It verifies the
  item belongs to an accessible list before writing (the underlying endpoint
  carries no list ID, so this server enforces that check itself), sends one
  partial update, and confirms by reading the item back: any field other than
  the assignment that changed is reported as `mismatched`, naming it.
- **`create_calendar_event` is deliberately narrow.** It creates one timed,
  non-recurring event. `assigned_to` takes member names exactly as
  `list_family_members` shows them; omit it, or pass an empty list, to assign
  everyone in the family — that is the default, a change from earlier
  versions that assigned only the signed-in member. An unknown name triggers
  one refresh of the cached family list before failing. The event gets
  FamilyWall's own default 30-minute reminder. Times are local to your
  FamilyWall timezone unless you pass another `timezone` or an explicit offset;
  a local time skipped or repeated by a daylight-saving change is refused.
  All-day and recurring events, and deleting events, are not supported yet.
  The outcome is `confirmed` only when a readback matches the request exactly
  (including the attendees and the reminder); `mismatched` means the event
  exists but differs.
- **`set_calendar_event_attendees` changes only who a timed, one-off event is
  assigned to.** `event_id` is the `occurrence_id` `get_week_overview` returns,
  and `date` is that event's local date in your FamilyWall timezone.
  `assigned_to` works exactly as it does for `create_calendar_event`; an
  unknown name triggers the same one-time discovery refresh. Before writing
  anything, it refuses an event that is on another calendar, not editable,
  recurring, a series exception, not an ordinary event, or all-day. It sends
  only the attendee fields — nothing else about the event is ever rebuilt —
  and is `confirmed` only when a readback shows the new attendees and every
  other field (title, time, zone, location, description, recurrence, calendar,
  reminder) unchanged; `mismatched` means something else also changed.
- There is no delete or move tool yet, and item `quantity` cannot be read back
  (FamilyWall's API doesn't return it).
- **Assignment is shown by name, never by account ID.** `get_week_overview` and
  `get_list_items` report `assigned_to` (names), `assigned_to_everyone`, and an
  `unresolved_members` count; `list_family_members` is how you learn the exact
  names to use. No tool accepts or returns a raw account ID.

### Example requests

You talk to your assistant normally; it picks the tools. With writes enabled:

- *"Add milk to the shopping list."* → `add_list_item`, assigned to everyone.
- *"Put 'book the plumber' on the to-do list for Alex."* → `list_family_members`
  to find the exact name if needed, then `add_list_item` with
  `assigned_to: ["Alex"]`.
- *"Add a dentist appointment for Robin and me next Tuesday at 10."* →
  `create_calendar_event` with both names; the assistant reports whether
  FamilyWall confirmed it.
- *"Actually, make Tuesday's dentist appointment just for Robin."* →
  `get_week_overview` to find the event, then `set_calendar_event_attendees`.
- *"Who's the soccer training on Thursday for?"* → `get_week_overview`, which
  shows each event's assignees by name.

Every write reports an outcome — `confirmed`, `acknowledged`, `mismatched`,
`misfiled` or `unknown` — so the assistant can tell you exactly what happened
rather than assuming success.

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
        "FAMILYWALL_PASSWORD": "<your FamilyWall account password>",
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

If you are upgrading an existing deployment, back up the data volume
(`familywall_data`) first: this release migrates the receipts table in place,
and an older image cannot read the migrated database.

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
- **Member names must match exactly.** Names are matched case- and
  spacing-insensitively against the family's display or first names, but there
  is no fuzzy or partial matching: an unknown or ambiguous name fails before
  anything is written. `list_family_members` shows the names to use.
- **All-day and recurring events can't be created or edited.** Only timed,
  one-off events are supported for writes; `set_calendar_event_attendees`
  refuses all-day, recurring and special-calendar events before writing.
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
- [ADR 0002 (hosted auth)](docs/decisions/0002-simplified-hosted-auth.md) — simplified hosted auth rationale
- [ADR 0003](docs/decisions/0003-single-call-add.md) — single-call `add_list_item` (supersedes [ADR 0002, non-atomic add](docs/decisions/0002-non-atomic-add.md))
- [Member assignment plan](docs/briefs/09-implementation-plan.md) — how assignment was probed, built and verified
- [Contributing](CONTRIBUTING.md) — workflow and validation commands

## Credits

Inspired by [ryanhunt/halaxy-mcp](https://github.com/ryanhunt/halaxy-mcp) and
based on protocol research in
[ryanhunt/familywall-api](https://github.com/ryanhunt/familywall-api), which
credits [Tomsoz](https://github.com/Tomsoz/familywall-api) and
[CodingButter](https://github.com/CodingButter/familywall-api).

Licensed under [MIT](LICENSE).
