# Compatibility record

What has actually been verified, and by what means. Keep this table honest: a
row moves to a positive result only when the stated check was executed.

Last updated 2026-09-14 by live write check and transport audit.

## Server-side

| Item | Verified value | How | Date |
| --- | --- | --- | --- |
| Python | 3.12 and 3.14.7 | Spike virtualenvs; `mcp==2.2.0` installs and imports on both | 2026-09-13 |
| `mcp` | 2.2.0 | Installed from PyPI, imported, server constructed | 2026-09-13 |
| Modern server API | `mcp.server.MCPServer` | `list_tools()` returned a registered tool | 2026-09-13 |
| Structured output | `output_schema` generated from a pydantic return model | Spike output | 2026-09-13 |
| Streamable HTTP app | `streamable_http_app()` mounts `/mcp` | Route inspection | 2026-09-13 |
| Protected resource metadata | `/.well-known/oauth-protected-resource/mcp` | Route inspection | 2026-09-13 |
| Authorization server routes | `/.well-known/oauth-authorization-server`, `/authorize`, `/token`, `/register`, `/revoke` | Implemented in `auth/provider.py` backed by `OAuthSqliteStore`; unit-tested | 2026-09-14 |
| Authenticated subject in handler | `get_access_token().subject` | Spike tool with a fake verifier | 2026-09-13 |

## Protocol

| Item | Value | Evidence level |
| --- | --- | --- |
| MCP authorization spec revision | `2026-07-28` | Documentation |
| PKCE | Required | Documentation |
| Resource indicator (RFC 8707) | Required | Documentation |
| Version negotiation | `MCP-Protocol-Version` request header | Documentation |
| Version this server negotiates in practice | **not measured** | — |

## Clients

No real client (Claude, ChatGPT, etc.) has connected to this server yet. Every row
requires a real HTTPS deployment and live connector testing.

**Implementation notes:** The OAuth provider is implemented and unit-tested with
static `.env`-configured multi-user support (per [ADR 0002](decisions/0002-simplified-hosted-auth.md)).
No invitations or account pages exist; users are configured via `FAMILYWALL_USER_<N>_*`
environment variables. Token refresh, rotation, reuse detection, and revocation are
implemented. The proof that this works with Claude and ChatGPT remains pending.

| Client | Requirement (documented) | Verified | Date |
| --- | --- | --- | --- |
| Claude custom connector | HTTPS Streamable HTTP; OAuth with PKCE; DCR supported; callback `https://claude.ai/api/mcp/auth_callback` | **not verified** — requires real HTTPS domain and live test | — |
| Claude — CIMD registration | Preferred by Anthropic; DCR is the working assumption | **not verified** — requires real HTTPS domain and live test | — |
| ChatGPT developer mode | HTTPS Streamable HTTP only; OAuth 2.1 mandatory; DCR supported; bare bearer tokens rejected; no localhost | **not verified** — requires real HTTPS domain and live test | — |
| Two distinct users, concurrent | MCP logins isolated; no credential cross-contamination | **unit-tested** — provider under ASGI app, not live | 2026-09-14 |
| Token refresh and reconnect after expiry | Refresh-token rotation, reuse detection, revocation | **unit-tested** — provider under ASGI app, not live | 2026-09-14 |

## FamilyWall upstream

A read-only live probe (task 02P) ran on 2026-09-13 against
`https://api.familywall.com/api` with a real account supplied outside Git. No
write endpoint was called. Findings are recorded in
[contracts](contracts/familywall.md) and [calendar](contracts/calendar.md).

| Check | Status |
| --- | --- |
| Login handshake against live service | **pass** — 2026-09-13; `JSESSIONID` cookie plus a mandatory `tokencsrf` header; `webset`/`webget` unnecessary |
| Login failure is distinguishable | **pass** — 2026-09-13; `a00.ex.ex` / `FiZClassId 3` / `bad password` |
| Expired-session presentation | **pass** — 2026-09-13; HTTP 200 with `a00.un.un` / `501` / `NOAUTHENT`, not 401 and not HTML |
| Transient load balancer errors (502) | **pass** — 2026-09-14; intermittent HTTP 502 Bad Gateway with non-JSON body; recoverable with retry |
| Family/calendar discovery for a real account | **pass** — 2026-09-13; single family object, `family_id` + `metaId` + `members[]` with IANA `timeZone` |
| Derived `calendar/{family_id}` accepted | **pass with a caveat** — 2026-09-13; it is the server's own calendar ID, but the request parameter is **ignored** and does not scope or filter anything |
| List read shapes | **pass** — 2026-09-13; bare arrays, `taskList/<id>` and `task/<id>` identities, types `SHOPPING_LIST`/`TODOS`/`OTHER` |
| Item `quantity` readable | **fail** — 2026-09-13; no quantity key on any of 85 items across four lists |
| Calendar recurrence expansion | **pass** — 2026-09-13; server expands occurrences; no local expansion needed |
| Calendar all-day encoding | **pass** — 2026-09-13; `allDay:"true"` with a UTC-stamped date that must not be timezone-converted |
| Calendar boundary semantics | **pass** — 2026-09-13; half-open overlap, `start < to && end > from` |
| Multi-day all-day event | **pass** — 2026-09-25; `<first>T00:00:00.000Z` to `<last>T23:59:59.000Z` with `allDay:"true"` |
| Multi-family account discovery | **pending** — the probe account has one family |
| `taskcreate` response shape | **pass** — 2026-09-14; returns full task object with `taskListId` showing actual destination |
| `taskcreate` ignores list identifier | **pass** — 2026-09-14; confirmed, item lands in default list only |
| `taskmove` endpoint exists and works | **pass** — 2026-09-14; moves item to target list; create-then-move flow verified |
| Create-then-move flow (add to chosen list) | **pass** — 2026-09-14; `taskcreate` then `taskmove` places item in target list |
| Replay protection via idempotency key | **pass** — 2026-09-14; same idempotency key produces no duplicate (confirmed by re-read) |
| `set_list_item_checked` true and false | **pass** — 2026-09-14; both directions work; explicit false is honoured |
| `confirmed` outcome fields (null for misfiled) | **pass** — 2026-09-14; `actual_list_id` and `requested_list_id` null when outcome is `confirmed` |
| `taskdelete` endpoint exists and works | **pass** — 2026-09-14; deletes items immediately; 14 items tested across two runs |
| `evtcreate`, one timed single-attendee event | **pass** — 2026-09-25; offset-form local times plus the event's `timeZone` stored the exact requested instant; `color` omitted is accepted; response is the full event object |
| `create_calendar_event` readback confirmation | **pass** — 2026-09-25; `evtlistinterval` returned the created ID with identical title, instants, zone and calendar, so the outcome was `confirmed` |
| `evtdelete` of non-recurring events | **pass** — 2026-09-25; `eventId` (web app) and `eventId.0` both accepted, `"true"`, absent on readback; no tool |
| `evtcreate` for everyone | **pass** — 2026-09-25; `isToAll=true` plus `attendee.N.accountId` for every member; reads back `toAll:"true"`, `attendeeIds: []` |
| `evtcreate` for two named members | **pass** — 2026-09-25; `isToAll=false`, `attendee.0/1.accountId`; reads back those two IDs in order |
| `evtcreate` all-day | **pass** — 2026-09-25; `allDay=true`, date-carrier instants, no `timeZone` field |
| `evtupdate` attendee-only | **pass** — 2026-09-25; patch semantics: other fields unchanged, attendee set replaced by exactly the entries sent |
| `create_calendar_event` daylight-saving instant | **pass** — 2026-09-25; `+11:00` stored correctly, `confirmed` |
| `create_calendar_event` for everyone and for named members, with the default reminder | **pass** — 2026-09-25; both `confirmed` through the tool; readback `toAll` and attendees as sent, `SNOOZE`/`MINUTE`/`30` reminder |
| `evtupdate` keeps an unsent non-empty `where`/`description`; all-day update | **pending** |
| Task assignment: named and everyone (`taskupdate2`) | **pass** — 2026-09-25; `toAll` plus `assignee.N`; everyone reads back as every member |
| `taskupdate2` partial update | **pass** — 2026-09-25; patch semantics: only the assignment changed; `description`, `dueDate` and reminder kept |
| `taskmove` keeps assignment | **pass** — 2026-09-25; assignment, description, due date and reminder kept |
| `taskcreate2` into a chosen list with an assignee | **pass** — 2026-09-25; single call, lands in the requested non-default list |
| `taskcreate` default assignment | **pass** — 2026-09-25; `a00text` alone assigns every member (`toAll:"true"`) |
| `taskmark` response shape | **pending** — 2026-09-14; effect verified by readback, response shape still unobserved |
| Known test week matches the FamilyWall UI | **pending** — P4 |
