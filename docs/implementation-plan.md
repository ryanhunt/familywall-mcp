# FamilyWall MCP implementation plan

Status: ready for phased implementation; runtime not built. Updated 2026-09-13.

## Outcome and agreed scope

Build a Python MCP server that a few invited family members can connect to from
ChatGPT and Claude. Each person signs in with their own self-hosted MCP login, which
maps to encrypted FamilyWall credentials on the server. The initial release must
support “add bread to the shopping list”, checking an item off, and “what have we got
on this week at home?” using actual FamilyWall data.

This plan does not equate “fully working” with cloning every FamilyWall feature.
V1 means those shopping/calendar workflows work for the invited users in both
clients, with account isolation, reconnects, persistence and operational recovery.
Calendar writes, messaging, attachments, meals and recipes are later extensions.

Read [research](research.md) for pinned source evidence, [architecture](architecture.md)
for interfaces and behavior, and [task cards](tasks.md) to delegate one change at a
time. The plan proposes future code, commands and test cases; none are currently runnable.

## Decisions already made

- Python, directly porting the necessary TypeScript wire contracts.
- A few invited users, each with a separate login and FamilyWall link.
- Entirely self-hosted login and credential storage, following Halaxy's operational model.
- Official MCP SDK, stdio for developer/local checks, authenticated Streamable HTTP
  over HTTPS for the shared service.
- Small modular package; SQLite; non-root Docker + Caddy; no public signup.
- No credentials in GitHub, commits, logs, fixtures or model-visible tool payloads.
- Cheap agents may implement bounded tickets. Lead agent reviews shared contracts,
  security boundaries and final integration.

## Phases and dependencies

| Phase | Deliverable | Depends on | Exit evidence |
| --- | --- | --- | --- |
| P0 | Contract and compatibility reconnaissance | This plan | Written evidence resolves the risky protocol/auth choices |
| P1 | Python package and offline harness | P0 SDK/contract decisions | Reproducible install and meaningful checks on a clean checkout |
| P2 | FamilyWall session and account context | P1 | Synthetic auth/error cases and opt-in read-only live probe |
| P3 | Shopping-list services and local MCP tools | P2 | Exact request tests, readback, ambiguity and unknown-write handling |
| P4 | Reliable week/calendar services and tools | P2 | DST/recurrence/overlap tests and calendar comparison |
| P5 | Per-user storage, self-hosted login and OAuth | P1, P0 auth decision | Two-user HTTP isolation, login/refresh/revoke/restart tests |
| P6 | Integrated hosted service and operations | P3, P4, P5 | HTTPS deployment, recovery drill, both clients connected |
| P7 | End-to-end acceptance and v1 release preparation | P6 | Full acceptance matrix passes, limitations documented |
| P8 | Optional broader FamilyWall features | P7 + per-feature discovery | Independent scoped acceptance for each new feature |

P3 and P4 can run independently after P2 freezes domain contracts. P5 storage work
can proceed after P1 while P2–P4 use a synthetic/local principal. Integration remains
serial. An incomplete OAuth phase must never expose a real unauthenticated service
to the internet. P0 tests may use a temporary endpoint with fake data only.

## P0 — Prove contracts before building on them

**Objective:** replace assumptions most likely to cause a rewrite with evidence.
Keep this phase bounded to discovery and a disposable compatibility spike.

Work:

1. Turn the research matrix into `docs/contracts/familywall.md`, recording endpoint,
   method, form fields, response variants, source SHA/line and evidence level
   (`source-only`, `offline-tested`, `live-verified`). Use the pinned TypeScript tests
   as examples; never treat permissive parser branches as observed live payloads.
2. Verify multi-family discovery, family-to-calendar mapping and active-family/list
   scoping. Determine whether family selection mutates session state. If so, lock
   select+operation together, or use isolated sessions per verified family context.
   Do not add a guessed `familyId` field to list calls. If unresolved, explicitly limit
   the account to its verified active family and block unsupported switching.
3. Determine interval boundary/overlap semantics, all-day encoding, recurring instance
   IDs, exclusions/cancellations and which calendars appear. Use a test family with
   known examples. No fabricated recurrence expansion. A week cannot be accepted
   as complete while known recurring events are silently missing.
4. Install an exact stable Python MCP SDK in a disposable environment; test the chosen
   v2 server/provider interfaces, request subject propagation, metadata, PKCE and
   resource validation. Record the release and minimum compatible protocol versions.
5. Prove a self-hosted OAuth round trip with a synthetic `whoami` tool using the chosen
   library/provider. Try real ChatGPT/Claude connections when access and a temporary
   HTTPS endpoint are available; otherwise mark those checks pending for P6.
6. Resolve source attribution before porting code. Record the original MIT notices
   for the Tomsoz/CodingButter lineage, not just the package's license label.

Artifacts: contract matrix, `docs/decisions/0001-auth-and-sdk.md`,
`docs/compatibility.md`, sanitized test-case descriptions. Discard experimental
runtime code unless deliberately adopted in P1 with tests.

**Gate:** lead reviews the selected OAuth component and exact family-context behavior.
Lack of a live test account does not stop offline work; dependent claims remain
unverified. A protocol gap becomes a small discovery ticket with a concrete question,
not an invented endpoint or a silent fallback. Provider incompatibility requires an
ADR revision before P5; owner requested self-hosting, so an external IdP is not the fallback.

## P1 — Reproducible Python foundation

**Objective:** every later agent has one package layout, test harness and check command.

Work:

- Add `pyproject.toml`, a locked dependency set, src-layout package, test extras and
  console entry points. Establish config, typed errors, `Principal`, domain models,
  transport injection and storage interfaces from the architecture.
- Define explicit `stdio` and hosted configurations. Hosted startup must reject
  missing key/auth/public URL settings. Environment values are never printed.
  Provide `.env.example` with dummy placeholders and a deterministic config path.
- Add pytest fixtures for JSON envelopes, fake cookies, a fixed clock, request capture
  and a fake upstream that rejects unexpected calls. Block external network in the
  default suite. Include a substantive config/error test rather than placeholder tests.
- Provide a single local check entry point covering locked installation, lint/format
  checks, typing, offline tests, build and secret detection, and inspect tracked build
  inputs. This project deliberately has no CI workflow; the checks are run locally and
  their results recorded in the pull request. Keep a future `.dockerignore` in scope
  before any container build.
- Update AGENTS/CONTRIBUTING with real commands only when they exist; preserve the
  shared Claude import. Keep inherited license notices with ported material.

Proposed command contract to implement: `uv sync --frozen --group dev`,
`uv run ruff check .`, `uv run ruff format --check .`, `uv run mypy src`,
`uv run pytest -m 'not live'`, and `uv build`. A small `scripts/check` should run the
project's agreed checks; these are unavailable until P1 defines them.

**Gate:** clean install/build and actual baseline tests pass with no FamilyWall account.
Both AI tools can find the same operating instructions. No real API tools advertised yet.

## P2 — FamilyWall client, sessions and family context

**Objective:** a Python client can authenticate and discover its own accessible data.

Work:

- Port exact form encoding and envelopes with async HTTP, cookie/CSRF handling and
  login → webset/webget. Use a cookie jar and necessary verified headers; remove
  static analytics cookies and hard-coded browser device identifiers unless required
  and explained by evidence. Never forward FamilyWall credentials to another origin.
- Unify error handling across new and legacy endpoints: invalid credentials,
  missing session, HTML response, 401/403, `a00.ex`, invalid JSON, rate limit, timeout
  and schema drift. Emit safe domain errors with endpoint labels, not raw bodies.
- Port discovery and implement P0's verified family selection strategy. Distinguish
  FamilyWall account ID, family ID, calendar ID and MCP user ID.
- Implement bounded client lifetime, concurrent-login suppression, read-only retry
  policy and explicit credential-generation invalidation. Login failure must raise.
- Implement local single-user injection for stdio and a test principal provider;
  no global account fallback in hosted mode.

Tests: Unicode form payload, multiple Set-Cookie headers, missing cookie, malformed
success/error envelope, logout/password change, one read reauth, bounded retries,
concurrent users A/B, same-user parallel requests and correct active-family context.

**Gate:** a read-only opt-in test-family probe validates login and family/calendar
discovery. If unavailable, record it as pending and continue offline P3/P4; it remains
a release blocker. Do not ship tools for unresolved family contexts.

## P3 — Shopping workflow

**Objective:** “add bread to the shopping list” resolves the right list, writes once,
and reports what actually happened.

Work:

- Port summary/detail/add/mark request and parser contracts. Support optional quantity
  only in the upstream's verified string/number form; do not invent unit conversion.
- Build list selection using validated account defaults, a single eligible list,
  or an ambiguity response with choices. A deleted/default-inaccessible list does
  not silently switch the destination. Validate IDs against the accessible collection.
- Add `list_lists`, `get_list`, `add_list_item`, `set_list_item_checked` thin handlers
  using shared service results. Include the connection/family tools needed by clients.
- Introduce `operation_id` receipt handling via a storage interface (SQLite persistence
  lands in P5). Same ID/payload returns the known result; mismatch rejects. Unknown
  results stay unknown after restart. Scope receipts to user and target list/family.
- Read back the created/marked item when possible; distinguish upstream acknowledgement
  from confirmed state. No blind write retries, bulk actions or deletion in this phase.

Tests: multiple shopping lists, duplicate names, invalid/default/foreign IDs, completed
items, Unicode title, quantity omission, explicit false, ID-only acknowledgements,
malformed response, lost response, crash after send, receipt replay/mismatch and
scope enforcement. Demonstrate that adding bread twice intentionally is possible;
do not globally deduplicate by item text.

**Gate:** offline MCP tool tests and a controlled live add/check/uncheck in a test list
match the FamilyWall UI. Cleanup only test items created for the run using a known
supported route/UI; item-delete endpoint discovery is not required for cleanup.

## P4 — Calendar and weekly overview

**Objective:** a household week shows the correct events and honestly signals gaps.

Work:

- Port interval requests and normalize event/occurrence IDs, title, start/end,
  all-day status, timezone, calendar/family context and permitted participant names.
  Fetch the user's relevant calendars using P0's mapping; do not assume family ID
  and calendar ID are interchangeable or merge unrelated families.
- Accept RFC3339 instants or documented local dates; reject ambiguous naive datetimes.
  Resolve weeks using local calendar arithmetic (`zoneinfo`), never seven fixed
  24-hour additions across DST. Configure timezone/default week start per user.
- Implement and test the boundary adapter for the observed upstream inclusivity.
  Include events overlapping the query window, not only those starting inside it.
- Use server-provided recurring instances when available. If expansion is required,
  give it a separately reviewed subtask with proven recurrence/exception fields and
  a maintained recurrence library. Avoid treating a series master as one occurrence.
- Return stable ordering, occurrence-aware deduplication and range/completeness
  metadata. Bound fetch windows (proposed 31 days per call) and output size; if a cap
  is reached, provide a supported follow-up mechanism or explicit partial status.

Tests: Sydney spring/fall DST, a second timezone, Sunday/Monday boundaries, events
crossing midnight/week boundaries, all-day multi-day events, explicit offsets,
cancelled/changed recurring instances, overlapping calendars, empty/error distinction
and output truncation. Verify the known test week against FamilyWall.

**Gate:** both calendar tools produce matching complete results for the supported
calendar semantics. If recurrence remains unresolved, label the beta limitation and
keep full v1 week acceptance open; do not quietly mark P7 complete.

## P5 — Invited users, encrypted links and self-hosted OAuth

**Objective:** each client token authorizes exactly one invited user's account.
Split this phase into the separate storage, account-page and OAuth tickets in tasks.md.

Work:

- Add SQLite migrations and repositories for users, invitations/recovery, preferences,
  credentials, web/OAuth state, rotation families and operation receipts. Use atomic
  consumption and unique constraints; test upgrade/restart and concurrent access.
- Implement AES-GCM envelope/key versions and Argon2id password hashing through
  maintained libraries. Store a random stable user ID as OAuth subject; hash opaque
  token values at rest. Keep plaintext credentials only for the short API call lifecycle.
- Add operator CLI workflows to create invites, disable users, revoke sessions, reset
  access and rotate keys. Passwords use non-echo input, never shell arguments. No
  outbound email/SMTP service required; the operator shares invite URLs privately.
- Add invite acceptance, login, consent, account-link and defaults pages. FamilyWall
  credentials are validated before replacing a working link. A concurrent old login
  cannot reinstall its old session after a credential update.
- Implement P0's OAuth provider contract: client registration strategy, metadata,
  code + PKCE, scopes/resource binding, per-request token verification, refresh,
  revocation, expiry, login rate limits, browser session/CSRF and secure redirects.
- Wire the verified subject to each tool's service context. Local disable/revoke
  takes effect on the next request, even when the token is otherwise unexpired.

Tests: user A token + user B IDs, transport session reuse with another token, parallel
different-family calls, forged username/client ID, read token on write tool, OAuth
code/refresh replay, redirect substitution, XSS/CSRF, invite reuse, wrong AEAD subject,
missing/wrong key, link replacement, and persisted pending mutations after restart.
Use actual HTTP requests for auth tests: in-memory MCP tests can bypass middleware.

**Gate:** lead reviews auth/storage changes and all negative isolation cases pass.
MCP credentials, FamilyWall credentials and OAuth client credentials remain distinct.
No arbitrary user-switching parameter exists in any tool.

## P6 — Hosted integration and operational recovery

**Objective:** a reproducible, persistent HTTPS service that survives routine updates.

Work:

- Integrate tools/account pages/OAuth in the ASGI lifespan; manage clients and DB
  connections. Enforce one worker until shared locking/session design is expanded.
- Add Dockerfile, `.dockerignore`, local/production Compose files and Caddy example.
  Run non-root; publish only proxy 80/443 in production. Bind developer HTTP to loopback.
  Mount `/data`, encryption keys and Caddy state correctly; keep secrets outside build
  context and image layers. Test amd64 and arm64; 32-bit Pi support is a separate choice.
- Add shallow `/health` and local operational readiness checks. Health probes must not
  log into FamilyWall or reveal users/config. Set resource/timeouts/retention limits,
  Origin/Host validation and proxy buffering/timeouts for the selected MCP transport.
- Write setup, invite/link/reconnect, update/rollback, backup/restore, disable/revoke,
  key-rotation and upstream-drift runbooks. Back up DB consistently with SQLite's
  backup mechanism; store the key separately. Restore into a disposable instance.
- Connect real ChatGPT and Claude accounts over HTTPS. Record callback URIs and
  negotiated transport/protocol, tool metadata, login, expiry/refresh and reconnect.
  Confirm both invited users work independently, including concurrent requests.

**Gate:** restart/rebuild retains links and OAuth refresh state; restoring DB plus
key recovers service; revocation and unknown mutation state survive restart. A lost
key fails clearly. Client checks are recorded as pass/fail/pending, with no secrets.
No provider/hosting purchase or production deployment is performed by this plan.

## P7 — Acceptance and release preparation

Use this matrix in both ChatGPT and Claude. For tests requiring multiple users,
use at least two distinct test accounts; use a second test family to exercise
negative boundaries where the accounts would otherwise share identical access.

| Scenario | Required observed result |
| --- | --- |
| Invite and connect | User sets own MCP login, links own FamilyWall account, grants OAuth; no password in chat |
| Add bread | Correct family/list, one new item, verified ID/state in FamilyWall |
| Several shopping lists | Clarification or saved default; never arbitrary first-list selection |
| Check and uncheck | Correct existing item, requested explicit state, repeat-safe behavior |
| This week at home | Correct resolved local interval; matches normal/all-day/recurring/cancelled test events |
| Another user's IDs | Safe denial before any foreign data/write reaches the upstream operation |
| Write with read scope | Rejected; annotation alone cannot bypass policy |
| Expired FamilyWall session | Bounded read recovery or clear reconnect guidance, no false empty response |
| Lost mutation response | Unknown outcome/reconciliation; no automatic duplicate addition |
| Upstream error/rate limit | Safe actionable error; no hidden retries beyond policy |
| Restart/update | Account links, grants, defaults and receipts persist |
| Disable/revoke | Old credentials/tokens stop authorizing next request |
| Backup/key rotation | Restore succeeds; wrong/missing key fails clearly; old state remains recoverable during rotation |
| Sensitive output review | Logs, image layers, Git diff, fixtures and diagnostics contain no live secrets/family data |

Release deliverables: concise user README with tested connection instructions,
operator runbook, supported-tool/scope table, known limitations and compatibility
record. Run the full local check suite and the controlled acceptance suite once;
repeat only checks affected by a fix. V1 is ready only when all required rows pass.
Commit/push/release publication follow the user's delivery request.

## P8 — Independent extensions

After v1, prioritize according to actual household use:

1. Create lists and single non-recurring calendar events, with explicit timezone,
   duplicate protection and post-write verification.
2. Calendar update/delete only after recurrence occurrence/series semantics are proven.
   Do not port the current TypeScript `option=All` deletion as a default.
3. Fresh thread reads and bounded message history; verify ordering/read-state effects
   before promising passive reads. Text sending is its own explicitly enabled scope.
4. Attachment metadata/download if clients need it; apply size limits, URL validation,
   redirect restrictions and credential isolation. Do not return tokenized URLs by default.
5. Meals/recipes/ingredients and list edit/delete only after endpoint discovery yields
   complete request/response/failure contracts. Endpoint names alone are insufficient.

Each extension gets a contract, narrow tool schemas, offline tests, scoped live
acceptance and updated client compatibility evidence. No general-purpose raw API tool.

## Cost-conscious execution

Budget in small deliverables rather than uncertain token/hour estimates. Most task
cards target one focused PR-sized change. Use Luna for evidence collection, parsers,
fixtures, handlers, documentation and bounded implementation. Use the lead for
P0 decisions, auth/tenant isolation, recurrence policy and milestone review.

Give an agent only AGENTS.md, its task card, the relevant contract/model files and
the pinned source snippets it needs. If a task requires an unproven protocol detail,
stop that dependent part, record a discovery ticket, and complete independent work.
No agent should reread both complete repositories on every ticket.

The first tangible milestone is local shopping plus calendar tools after P3/P4.
The requested hosted product is complete only after P7. Prioritize known risks
(family selection, recurrence, OAuth interoperability) early so inexpensive feature
work does not build on a mistaken contract.
