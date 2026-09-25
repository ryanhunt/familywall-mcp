# Build progress

Resumable state for this project. A new session should read this file, then the
outputs of the last completed row, and continue at the first row that is not
`done`. See [implementation plan](implementation-plan.md) and
[task cards](tasks.md).

Current branch: `claude/hosted-oauth-multiuser`.

## Delivery target for this pass

Local **stdio** MCP server plus hosted multi-user OAuth (P5) and Docker/HTTPS (P6).
The stdio server is live-verified (2026-09-14). Hosted mode is complete and tested
against the ASGI app directly but not yet verified against a real Claude/ChatGPT
custom connector over HTTPS; see compatibility table below.

## Phase status

| Phase | Task | Output | Status |
| --- | --- | --- | --- |
| P0 | 00A/00B/00C contracts, calendar, ADR | `docs/contracts/`, `docs/decisions/0001-auth-and-sdk.md` | done |
| P1 | 01A/01B foundation and offline harness | PR #1 | done |
| P2a | **02P read-only live probe** | contracts + `docs/compatibility.md` updated; commit `2323362` | **done 2026-09-13** |
| P2b | 02A transport, envelope, login | `familywall/wire.py`, `familywall/client.py` | **done**: `01e6ba8` |
| P2b | 02B discovery, family context, session lifecycle | `familywall/discovery.py`, `services/session.py` | **done**: `4ce6815` |
| P3 | 03A list wire adapters | `familywall/lists.py` | **done**: `7a655d5` |
| P3 | 03B list selection and mutation service | `services/lists.py`, `storage/memory.py` | **done**: `1382d8f` |
| P3 | 03C shopping MCP tools | `tools/` | not started |
| P4 | 04A calendar adapters and local ranges | `familywall/calendar.py`, `services/ranges.py` | **done**: `e4170c2` |
| P4 | 04B weekly overview service | `services/calendar.py` | **done**: `3effb9a` |
| — | 05 adapter, MCP tools, stdio server | `services/transport.py`, `tools/`, `server.py` | **done**: `69a0951` |
| — | **Live stdio check, read-only** | verified 2026-09-14 | **done** |
| P3 | **Live write check (create, move, delete, check)** | `taskcreate`/`taskmove`/`taskdelete`/`set_list_item_checked` all live-verified; see contracts/familywall.md | **done 2026-09-14** |
| — | stdio server entry point and local smoke test | `server.py`, `cli.py` | not started |
| P5 | OAuth + static multi-user config | `auth/provider.py`, `OAuthSqliteStore`, `FAMILYWALL_USER_<N>_*` env vars | **done**: `config.py`, `auth/provider.py` implemented and unit-tested; live-verified via ASGI tests with fake credentials |
| P6 | Containers and HTTPS | `Dockerfile`, `docker-compose.prod.yml`, `Caddyfile.example` | **done**: `uv sync --frozen --no-dev` (the Dockerfile's install step) and the resulting `familywall-mcp serve` process were run directly and answer `/health`; `docker build`/`docker compose` itself was not run (no Docker available in this environment) — verify a real image build before relying on it in production; not yet verified against real Claude/ChatGPT over HTTPS |
| — | **`create_calendar_event`** (timed, non-recurring, assigned to the signed-in member) | `familywall/calendar.py`, `services/calendar.py`, `services/ranges.py`, `tools/registry.py`; [handoff](handoffs/10-create-calendar-event.md) | **done**: live-verified 2026-09-25 (create, readback `confirmed`, cleanup delete) |
| P7 | Acceptance and release | — | not started |

## What 02P settled, and what it changed

The read-only live probe on 2026-09-13 answered every blocking question and
**corrected four source-derived claims**. Full detail is in
[familywall.md](contracts/familywall.md#live-verification--02p-probe-2026-09-13)
and [calendar.md](contracts/calendar.md#live-verified-summary-2026-09-13).

Decisions now binding on implementation:

1. **P4 takes the "consume expanded occurrences" branch.** The server expands
   recurring occurrences. No recurrence library, no local expansion. The
   conditional sub-task in the original plan is cancelled, not deferred.
2. **All-day dates are read verbatim, never timezone-converted.** This is the
   single highest-risk normalisation rule in the project.
3. **Interval bounds are a half-open overlap** (`start < to && end > from`), so
   the boundary adapter is an identity match rather than a correction layer.
4. **`calendarId` is ignored by the server** and is not an access boundary. No
   tool may accept a caller-supplied calendar ID and imply it selects anything.
5. **Auth is `JSESSIONID` cookie + mandatory `tokencsrf` header.** No
   `webset`/`webget`, no analytics cookies, no `deviceId`, no `User-Agent`
   requirement.
6. **Failure envelopes are `aNN.un.un` and `aNN.ex.ex`**, both at HTTP 200.
   Session expiry is `un`/`501`/`NOAUTHENT`, not 401 and not HTML.
7. **`quantity` is not readable.** A tool that writes one cannot verify it, and
   must say so rather than imply success.

## Review findings worth carrying forward

Delegated implementation was reviewed rather than accepted on report. Every task
needed at least one correction round, and three defect classes recurred:

1. **Silently skipped requirements.** 02B shipped `_reauth_and_retry` as dead
   code that nothing called, so the bounded-read-reauth and never-retry-writes
   rules did not exist; 03B deferred four receipt-state-machine criteria to
   "integration testing" that were plain unit tests. Both reported high passing
   test counts truthfully. Numbering the required tests in the brief is what
   made the absences checkable.
2. **Mock-shaped correctness.** 03B called a `taskgetlist` endpoint that does
   not exist; its fake transport accepted it and all 234 tests passed. A green
   mock suite cannot tell you whether the real API would answer. **Every
   endpoint string must be checked against the contract, and the local stdio
   smoke test is the real gate for this phase.**
3. **Exception types that do not match the codebase.** 03B caught the Python
   builtins `TimeoutError`/`ConnectionError`/`OSError`, but the transport raises
   the project's own `TransportError`, which subclasses none of them — so a lost
   write would have propagated instead of yielding `unknown`. Caught only by
   checking the class hierarchy directly.

## How to run it locally

```
FAMILYWALL_MODE=stdio
FAMILYWALL_LOCAL_SUBJECT=<any stable local id>
FAMILYWALL_BASE_URL=https://api.familywall.com
FAMILYWALL_EMAIL=<account email>
FAMILYWALL_PASSWORD=<account password>
FAMILYWALL_ENABLE_WRITES=false
```

Then `uv run familywall-mcp serve`, which speaks MCP over stdio. Seven tools are
exposed: `get_connection_status`, `list_shopping_lists`, `get_list_items`,
`get_week_overview`, `add_list_item`, `set_list_item_checked` and
`create_calendar_event`.

**The write gate is off by default and must stay off until a live write check
has been run deliberately.** With it off, the three write tools are still listed
but refuse before any upstream request with `writes_disabled`.

### Live verification, 2026-09-14

A real MCP client drove the server over stdio against a live account. Observed:
initialize succeeded; six tools listed; no tool schema accepts a credential,
subject, principal or family parameter; connection status, lists, list items and
a week overview all returned real data; the resolved timezone was
`Australia/Sydney` from discovery, not a hard-coded default; recurrence arrived
expanded (11 occurrences across 9 series ids in one week). Both write tools
refused with `writes_disabled`, and an endpoint audit of the whole run recorded
only `taskgettasklists`, `tasklist` and `evtlistinterval` — no write endpoint was
reached.

### Live verification, 2026-09-25 — `create_calendar_event`

Authorised by the account owner. The real tool created one disposable timed
event assigned to the authenticated member: `evtcreate` then `evtlistinterval`,
outcome `confirmed`, stored at exactly the requested instant in
`Australia/Sydney`. The event was then deleted by exact ID with `evtdelete`
(`"true"`, absent on readback). Details are in the
[calendar contract](contracts/calendar.md#mutations).

### Probe A1, 2026-09-25 — calendar attendees and updates

With the owner signed in to the web app, its own `evtcreate`, `evtupdate` and
`evtdelete` forms were captured (masked), and a partial `evtupdate` and a
daylight-saving create were scripted. Five disposable events were created and
all were deleted. Findings are in the
[calendar contract](contracts/calendar.md#probe-a1--the-web-apps-own-forms-2026-09-25).

### Probe A2, 2026-09-25 — list assignment

The web app's own `taskcreate2`/`taskupdate2` forms were captured (masked), and
partial-update, move and single-call-create checks were scripted. Four
disposable items were created and all were deleted. Findings are in the
[FamilyWall contract](contracts/familywall.md#probe-a2--list-assignment-and-the-2-endpoints-2026-09-25).

## Still `pending-live`

Recorded so they are not quietly dropped:

- **`taskmark` response shape** — its *effect* is live-verified (2026-09-14:
  check and uncheck both took effect, confirmed by readback, with an explicit
  `false` honoured), but its returned payload was never inspected. Low value to
  close: the service re-reads the list to confirm state either way.
- Calendar writes: whether `evtupdate` keeps a non-empty `where`/`description`
  it was not sent, `evtupdate` on an all-day event, and anything recurring.
- Multi-family discovery shape — the probe account has one family, so the
  single-family refusal must fail closed on anything unexpected.

## Resume instructions

1. Read this table and the two contract files' live-verified sections.
2. Check `git log --oneline` for the last landed commit.
3. Continue at the first row that is not `done`. Each delegated task has a
   self-contained brief in `docs/briefs/` and a handoff in `docs/handoffs/`.
