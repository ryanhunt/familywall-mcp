# Compatibility record

What has actually been verified, and by what means. Keep this table honest: a
row moves to a positive result only when the stated check was executed.

Last updated 2026-09-13 by task 02P (read-only live probe).

## Server-side

| Item | Verified value | How | Date |
| --- | --- | --- | --- |
| Python | 3.12 and 3.14.7 | Spike virtualenvs; `mcp==2.2.0` installs and imports on both | 2026-09-13 |
| `mcp` | 2.2.0 | Installed from PyPI, imported, server constructed | 2026-09-13 |
| Modern server API | `mcp.server.MCPServer` | `list_tools()` returned a registered tool | 2026-09-13 |
| Structured output | `output_schema` generated from a pydantic return model | Spike output | 2026-09-13 |
| Streamable HTTP app | `streamable_http_app()` mounts `/mcp` | Route inspection | 2026-09-13 |
| Protected resource metadata | `/.well-known/oauth-protected-resource/mcp` | Route inspection | 2026-09-13 |
| Authorization server routes | `/.well-known/oauth-authorization-server`, `/authorize`, `/token`, `/register`, `/revoke` | Route inspection with a provider instance | 2026-09-13 |
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

No client has connected to this server. Every row is outstanding.

| Client | Requirement (documented) | Status |
| --- | --- | --- |
| Claude custom connector | HTTPS Streamable HTTP; OAuth with PKCE; DCR supported; callback `https://claude.ai/api/mcp/auth_callback` | **pending** — P6 |
| Claude — CIMD registration | Preferred by Anthropic; not exercised in the spike | **pending** — P6 |
| ChatGPT developer mode | HTTPS Streamable HTTP only; OAuth 2.1 mandatory; DCR supported; bare bearer tokens rejected; no localhost | **pending** — P6 |
| Two distinct users, concurrent | — | **pending** — P5/P6 |
| Token refresh and reconnect after expiry | — | **pending** — P6 |

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
| Family/calendar discovery for a real account | **pass** — 2026-09-13; single family object, `family_id` + `metaId` + `members[]` with IANA `timeZone` |
| Derived `calendar/{family_id}` accepted | **pass with a caveat** — 2026-09-13; it is the server's own calendar ID, but the request parameter is **ignored** and does not scope or filter anything |
| List read shapes | **pass** — 2026-09-13; bare arrays, `taskList/<id>` and `task/<id>` identities, types `SHOPPING_LIST`/`TODOS`/`OTHER` |
| Item `quantity` readable | **fail** — 2026-09-13; no quantity key on any of 85 items across four lists |
| Calendar recurrence expansion | **pass** — 2026-09-13; server expands occurrences; no local expansion needed |
| Calendar all-day encoding | **pass** — 2026-09-13; `allDay:"true"` with a UTC-stamped date that must not be timezone-converted |
| Calendar boundary semantics | **pass** — 2026-09-13; half-open overlap, `start < to && end > from` |
| Multi-day all-day event | **pending** — needs a deliberately created test event |
| Multi-family account discovery | **pending** — the probe account has one family |
| Shopping add/check/uncheck round trip | **pending** — P3, a write, needs a disposable test list |
| `taskcreate` / `taskmark` response shapes | **pending** — P3; both are writes and were excluded from the read-only probe |
| Known test week matches the FamilyWall UI | **pending** — P4 |
