# Repository and compatibility research

Reviewed 2026-09-13 by the primary agent with Luna repository reviewers. This is
source research, not a live FamilyWall integration test. Both reference repositories
were checked against remote `main`; the sibling local checkouts matched and were clean.

## Pinned evidence

| Repository | Inspected revision | Role |
| --- | --- | --- |
| [halaxy-mcp](https://github.com/ryanhunt/halaxy-mcp/tree/205c6cebe4ed8a1f437786b9f90dd26747caa1bf) | `205c6cebe4ed8a1f437786b9f90dd26747caa1bf` | Python MCP, self-hosted OAuth and deployment reference |
| [familywall-api](https://github.com/ryanhunt/familywall-api/tree/c85bb152115d3c41ab90322ef9dce732122ff4c0) | `c85bb152115d3c41ab90322ef9dce732122ff4c0` | TypeScript protocol implementation and offline contract tests |
| familywall-mcp | Initial `8fb7767e7e988326bb58b7185b6d796e3957e122` | Only README and MIT license existed before this planning work |

## What to reuse from Halaxy

The [single Python module](https://github.com/ryanhunt/halaxy-mcp/blob/205c6cebe4ed8a1f437786b9f90dd26747caa1bf/halaxy_mcp.py)
implements five read-only tools, stdio, Streamable HTTP, a health route, and a
custom OAuth provider. It loads environment configuration relative to the script.
Upstream errors become explicit errors instead of misleading empty results.

Its OAuth model has one MCP username/password and one Halaxy client-credentials pair.
It does **not** implement per-person account mapping. OAuth state survives restart
through an atomic, permission-restricted JSON file; refresh tokens rotate. Its
history includes fixes for consent phishing, reflected XSS, expiring login state,
throttling, and an SDK version incompatibility. These are useful regression cases,
not evidence that copying its provider produces a complete multi-user service.

[Docker/Caddy documentation](https://github.com/ryanhunt/halaxy-mcp/blob/205c6cebe4ed8a1f437786b9f90dd26747caa1bf/README.md)
is a good operational model: non-root process, persistent state, TLS proxy and smoke
checks. Replace its shared JSON token store with transactional per-user storage.
Do not copy FHIR logic, clinical-data filtering, indefinite global caches, shared
identity, Sydney-only constants or broad dependency ranges.

At the inspected revision Halaxy has no AGENTS.md, CLAUDE.md, tests or CI. The shared
AI setup pattern comes from FamilyWall API and is newly adapted here.

## FamilyWall protocol evidence

Primary sources:
[client.ts](https://github.com/ryanhunt/familywall-api/blob/c85bb152115d3c41ab90322ef9dce732122ff4c0/src/client.ts),
[types.ts](https://github.com/ryanhunt/familywall-api/blob/c85bb152115d3c41ab90322ef9dce732122ff4c0/src/types.ts),
[family.ts](https://github.com/ryanhunt/familywall-api/blob/c85bb152115d3c41ab90322ef9dce732122ff4c0/src/family.ts),
and [tests](https://github.com/ryanhunt/familywall-api/tree/c85bb152115d3c41ab90322ef9dce732122ff4c0/test).

Requests generally POST form-urlencoded fields to `https://api.familywall.com/api/<endpoint>`
with `partnerScope=Family`. New helpers decode `a00.r.r`, distinguish `a00.ex`, HTTP,
JSON and shape failures. The TypeScript transport injects `tokencsrf` from JSESSIONID
and uses browser-style headers. Validate which headers are actually necessary;
never copy its static analytics-cookie values.

| Operation | Source-backed wire contract | Evidence location / qualification |
| --- | --- | --- |
| Login | `log2in`: `a00identifier`, `a00password`, `a00generateAutologinToken=true`, `a01call=log2get`, `transactional=true`; capture JSESSIONID, then `webset`/`webget` | client.ts 651–684; source retries missing cookie then logs/returns; Python must raise a typed failure |
| Family discovery | `accgetallfamily` plus batched profile/family/settings/invite/thread/account calls | client.ts 1181–1205; batch parsing and multi-family selection require verification |
| Calendar sync | `evtsync`, `calendarId`, plus `evtcallist` batch options | client.ts 770–786; returns sync metadata, not guaranteed complete bounded agenda |
| Calendar interval | `evtlistinterval`: `calendarId`, `a00from`, `a00to` | client.ts 824–848; array or supported wrapped event collection |
| Calendar create/update | `evtcreate` / `evtupdate`; update adds `metaId` | client.ts 718–757; old defaults include London timezone and recurrence NONE; do not copy |
| Calendar delete | `evtdelete`: `option=All`, `eventId.0` | client.ts 759–768; series-wide behavior unsuitable for first release |
| List summaries | `taskgettasklists` | client.ts 850–870; no verified family selector or pagination |
| List detail | `tasklist`: `a00listId` | client.ts 872–942; list metadata + items or bare item collection |
| Create list | `taskcreatelist`: `a00name`, `a00taskListType` | client.ts 944–966; friendly type maps to SHOPPING/TODO/OTHER |
| Add item | `taskcreate`: `a00taskListId`, `a00text`, optional `a00quantity` | client.ts 968–997; normalized object or ID-only acknowledgement |
| Mark item | `taskmark`: `a00taskId`, `a00complete=true/false` | client.ts 999–1018; acknowledgement discarded in TS; Python needs readback |
| Threads | `imthreadlist`: `a00isLoggedFamily=false` | client.ts 1020–1034; normalized participant metadata |
| Messages | `immessagelist2`: `a00threadId`, `a00limit` | client.ts 1036–1058; one bounded page, default 20 |
| Send text | `imsend`: `a00threadId`, `a00text` | client.ts 1060–1080; object or ID response; optional future scope |
| Attachment | HTTPS binary GET to attachment URL | client.ts 1082–1179; size limits and credential isolation across redirects; optional future scope |

List IDs, item IDs, text and completion fields have several observed aliases. Port
the normalized behavior with synthetic fixtures. Preserve unknown returned list types;
validate supported input types. Do not mistake an ID-only mutation acknowledgement
for a fully verified post-write state.

## Important gaps and deliberate changes

1. **Live evidence:** the tests use injected fetch and synthetic envelopes. They
   verify request construction and parsing, not current FamilyWall server behavior.
   Several newer operations derive from a public third-party reference, explicitly
   not an official API specification. Do not label them live-verified.
2. **Family scoping:** list calls have no verified family ID field, despite the Family
   facade. Active-family session behavior and family-to-calendar ID mapping are P0
   blockers for multi-family tools. Never invent a form field. Until verified, reject
   unsupported family switching and do not claim cross-family support.
3. **Sessions:** expiry/HTML login detection, reauthentication, password changes and
   concurrent sessions need new behavior. Confirm the minimum cookie/CSRF handshake.
4. **Calendar:** interval inclusivity, overlapping events, all-day representation,
   recurring occurrences, cancelled exceptions and external calendars are unresolved.
   The TS date-only end uses 23:59:59.999 and `days` uses fixed 24-hour increments.
   Python's public contract will use local calendar arithmetic and half-open ranges,
   with an explicitly tested adapter to actual upstream inclusivity.
5. **Pagination:** no proven list continuation or complete multi-page calendar contract.
   Message `size/count/start` fields do not establish a usable continuation request.
   Never manufacture cursors or silently claim complete results after truncation.
6. **Writes:** no automatic retry after unknown outcomes. Recurrence edits, item edits
   and deletion need separate endpoint evidence and acceptance criteria.
7. **Coverage:** meal, recipe, category and ingredient-transfer endpoint names in the
   [remaining-support plan](https://github.com/ryanhunt/familywall-api/blob/c85bb152115d3c41ab90322ef9dce732122ff4c0/docs/plans/remaining-api-support.md)
   are discovery leads, not contracts. Exclude them from v1.

## AI workflow and attribution

[FamilyWall API's AGENTS.md](https://github.com/ryanhunt/familywall-api/blob/c85bb152115d3c41ab90322ef9dce732122ff4c0/AGENTS.md)
is the shared policy source for its AI tools. Its contributor files emphasize focused
branches, offline tests, secret protection and fork attribution. This repo improves
that pattern with an actual Claude `@AGENTS.md` import, task/handoff templates and
bounded implementation tasks for cheaper agents, with architecture and final review
kept with the lead agent. Claude documents imports in its
[memory guide](https://code.claude.com/docs/en/memory).

FamilyWall API credits Tomsoz and CodingButter; package metadata declares MIT but no
root LICENSE file was present in the inspected checkout. Preserve known attribution
and recover applicable original notices before copying substantial source/fixtures in
P1. Keep this repository's existing Ryan Hunt MIT license intact.

## Client compatibility, checked 2026-09-13

- [ChatGPT developer mode](https://developers.openai.com/api/docs/guides/developer-mode)
  supports remote MCP reads and writes, Streamable HTTP/SSE, and OAuth. Availability
  and workspace controls must be checked in the intended user's account at rollout.
- [Claude remote connectors](https://claude.com/docs/connectors/custom/remote-mcp)
  accept an HTTPS MCP URL and support per-user OAuth. Shared request-header credentials
  are a different deployment model and do not solve individual account mapping.
- [Python SDK](https://py.sdk.modelcontextprotocol.io/) now documents stable v2;
  lock an installable release and inspect its tests instead of copying Halaxy's v1
  import paths. [MCP authorization](https://modelcontextprotocol.io/specification/2026-07-28/basic/authorization)
  has also evolved; store negotiated/verified versions in P0's compatibility record.
- [FamilyWall's product page](https://www.familywall.com/en/index.html) advertises
  shared calendars, shopping lists, tasks and other family features. Product features
  do not establish API availability or account entitlements.

No FamilyWall login, live read/write, OAuth round trip in ChatGPT/Claude, container
deployment, or source-repository test suite was executed during this planning pass.
