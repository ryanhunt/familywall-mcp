# Compatibility record

What has actually been verified, and by what means. Keep this table honest: a
row moves to a positive result only when the stated check was executed.

Last updated 2026-09-13 by P0.

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

No FamilyWall call has been made by this project. All wire knowledge is
source-derived from the pinned TypeScript client; see
[contracts](contracts/familywall.md) for per-endpoint evidence levels and the
list of questions that need a live account.

| Check | Status |
| --- | --- |
| Login handshake against live service | **pending** — P2 |
| Family/calendar discovery for a real account | **pending** — P2 |
| Derived `calendar/{family_id}` accepted | **pending** — P2 |
| Shopping add/check/uncheck round trip | **pending** — P3 |
| Known test week matches the FamilyWall UI | **pending** — P4 |
