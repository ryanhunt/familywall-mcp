# FamilyWall MCP implementation plan

Status: P0 discovery complete; P1 foundation implemented; P2a live probe
complete and its findings binding; runtime under construction.
Updated 2026-09-13.

**Current delivery target: a local stdio server the user can run against their
own account.** P5 (OAuth), P6 (containers, HTTPS) and multi-user hosting are
deferred until that works end to end. This is a sequencing change, not a scope
change.

## Outcome and agreed scope

Build a Python MCP server that a few invited family members can connect to from
ChatGPT and Claude. Each person signs in with their own self-hosted MCP login,
which maps to encrypted FamilyWall credentials on the server. V1 must support
"add bread to the shopping list", checking an item off, and "what have we got on
this week at home?" against real FamilyWall data, with account isolation,
reconnects, persistence and operational recovery.

V1 is not a clone of FamilyWall. Calendar writes, messaging, attachments, meals
and recipes are later extensions.

Read [research](research.md) for the evidence base, [contracts](contracts/familywall.md)
and [calendar contracts](contracts/calendar.md) for the wire surface,
[ADR 0001](decisions/0001-auth-and-sdk.md) for the SDK and OAuth decision,
[architecture](architecture.md) for interfaces, [compatibility](compatibility.md)
for what is actually verified, and [task cards](tasks.md) to delegate one change
at a time. [PROGRESS](PROGRESS.md) tracks resumable state.

## What P0 settled

P0 ran as source reconnaissance plus an executed SDK spike. Four findings change
the shape of the work and are now binding:

1. **Family scope is a property of the session, not a request parameter.** No
   list or calendar call carries a family ID. There is no evidenced way to select
   a family. V1 therefore serves exactly one family per account, names that family
   in its output, and **refuses** — rather than guesses — when discovery returns
   more than one. Multi-family support is out of scope, not merely unimplemented.
2. **The MCP SDK supplies the OAuth endpoint surface.** `mcp` 2.2.0 mounts
   `/authorize`, `/token`, `/register`, `/revoke` and both metadata documents from
   an `OAuthAuthorizationServerProvider` implementation. P5 is storage and policy
   work against a ten-method protocol, not protocol implementation. No Authlib.
3. **The authenticated subject is available per request** via
   `get_access_token().subject`, which is the key into encrypted credentials.
   Multi-user isolation has a concrete mechanism.
4. **Calendar reads are far less proven than lists.** Six event fields are
   evidenced. All-day encoding, recurrence, occurrence identity and cancellation
   have none. P4 cannot be planned as a port.

## The reordering this implies

The original plan front-loaded offline work. P0 shows that a large share of the
remaining risk collapses to questions only a live account can answer, and they
are concentrated in **one read-only probe**:

- is `JSESSIONID` alone enough to authenticate?
- what does the session's family resolve to?
- is `calendar/{family_id}` accepted?
- does an interval query return expanded recurring occurrences?
- how is an all-day event encoded?
- are overlapping events returned?

Answering these is perhaps an hour of work against a test family, and every one
of them can invalidate weeks of downstream implementation if guessed wrong. The
probe is therefore promoted to **P2a, the next action after this plan**, ahead of
all feature implementation. It reads only; it writes nothing.

If no account is available, P3 (lists) may proceed offline because its contracts
are `source-tested`. **P4 (calendar) must not start**, because its two central
questions are unanswered and the two possible answers have opposite designs.
Building the wrong one is the most expensive mistake available here.

## Phases and dependencies

| Phase | Deliverable | Depends on | Exit evidence |
| --- | --- | --- | --- |
| P0 | Contract and SDK reconnaissance | — | **done**: contracts, ADR 0001, compatibility record |
| P1 | Python package and offline harness | P0 | **done**: PR #1, `scripts/check` green on a clean checkout |
| P2a | Read-only live probe | P1, test account | **done 2026-09-13**: all blocking questions answered; four source-derived claims corrected |
| P2b | FamilyWall client, session and family context | P2a | Synthetic auth/error cases; discovery validated |
| P3 | Shopping-list services and tools | P2b | Exact request tests, readback, ambiguity, unknown-write handling |
| P4 | Calendar services and tools | P2b | DST/recurrence/overlap tests and a calendar comparison. **Unblocked**: consume server-expanded occurrences |
| P5 | Per-user storage, login and OAuth | P1, ADR 0001 | Two-user HTTP isolation; login/refresh/revoke/restart |
| P6 | Hosted service and operations | P3, P4, P5 | HTTPS deployment, recovery drill, both clients connected |
| P7 | Acceptance and v1 release preparation | P6 | Acceptance matrix passes; limitations documented |
| P8 | Optional broader features | P7 + per-feature discovery | Independent scoped acceptance per feature |

P3, P4 and P5 run in parallel after P2b freezes domain contracts; P5's storage
work can start immediately after P1 against a synthetic principal. Integration is
serial. An incomplete OAuth phase must never expose an unauthenticated service to
the internet.

## P2a — Read-only live probe — **complete, 2026-09-13**

The probe ran read-only against a real account supplied outside Git. No write
endpoint was called; only key names, structural shapes, enum values and date
semantics were retained, with every free-text value and identifier hashed before
display. The script was discarded, as planned.

It did not require the pre-constructed test week the original procedure assumed:
the account's own calendar already contained a recurring series with cancelled
occurrences, all-day events, and a birthday from a second calendar, and the
boundary questions were answered by querying synthetic windows around a known
event rather than by creating one.

**Findings and their consequences** are recorded in
[familywall.md](contracts/familywall.md#live-verification--02p-probe-2026-09-13),
[calendar.md](contracts/calendar.md#live-verified-summary-2026-09-13), and
[compatibility.md](compatibility.md). The seven binding decisions are listed in
[PROGRESS](PROGRESS.md#what-02p-settled-and-what-it-changed).

**The P4 branch decision: consume server-expanded occurrences.** The local
expansion sub-task is cancelled. No recurrence library enters this project.

Four questions remain `pending-live` and are listed in
[PROGRESS](PROGRESS.md#still-pending-live). Three of them are write-path
questions that only a controlled P3 live check can answer; one needs a
deliberately created multi-day all-day event.

## P2b — Client, sessions and family context

**Objective:** a Python client authenticates and discovers its own data.

- Port form encoding and the `a00.r.r` success / `aNN.un.un` / `aNN.ex.ex`
  failure envelopes with async HTTPX, a per-session cookie jar and the mandatory
  `tokencsrf` header taken from the login response. P2a proved the static
  analytics cookies, the constant browser `deviceId`, a browser `User-Agent` and
  `webset`/`webget` are all unnecessary: none of them are ported.
- Login failure raises a typed error. There is no upstream logout, so a session
  is valid until it demonstrably fails.
- Separate six failure modes: non-2xx HTTP, HTML body, invalid JSON, missing
  `a00`, a failure envelope (`un` or `ex`), and unexpected `a00.r.r` shape. Emit
  safe domain errors with endpoint labels, never raw bodies or the upstream
  `message`. A `un`/`501`/`NOAUTHENT` envelope is the session-expiry signal.
- Implement discovery, and enforce the single-family rule: more than one family
  is an explicit unsupported-configuration error, never a silent pick.
- Keep FamilyWall account ID, family ID, calendar ID and MCP user ID distinct in
  the type system, not merely by convention.
- Per-user client pool, one login lock per credential generation, bounded idle
  eviction, bounded read-only reauth, no write retry, no global cookie jar.

Tests: Unicode form payload, multiple `Set-Cookie` headers, missing cookie,
malformed success and error envelopes, HTML login page on HTTP 200, 401/403/429,
transport timeout, one read reauth, bounded retries, concurrent users A/B,
same-user parallel requests, concurrent login collapse, and password change
racing an old session.

**Gate:** discovery validated against the P2a evidence, or the gap recorded as a
release blocker. No tool ships for an unresolved family context.

## P3 — Shopping workflow

**Objective:** "add bread to the shopping list" resolves the right list, writes
once, and reports what actually happened.

- Port `taskgettasklists`, `tasklist`, `taskcreate`, `taskmark`. Reads return
  bare arrays; identity is `taskList/<id>` and `task/<id>`; read types are
  `SHOPPING_LIST`/`TODOS`/`OTHER`. Preserve unknown list types verbatim.
  Quantity is passed through verbatim as a string — no unit parsing,
  normalisation or conversion — and because **no quantity is readable back**,
  a tool that sets one must say the value could not be verified rather than
  imply it was stored.
- List selection: a validated saved default, or a single eligible list, or an
  ambiguity response offering choices. Never an arbitrary first list. A deleted
  or inaccessible default does not silently redirect the write.
- `taskmark` sends no list ID, so item ownership **must** be verified against an
  accessible list before the call. This is a security property, not a nicety.
- Represent the three write outcomes distinctly: `confirmed` (acknowledged and
  read back), `acknowledged` (upstream success, readback did not confirm),
  `unknown` (lost, timed out, unparseable). `unknown` is never auto-retried —
  `taskcreate` is not idempotent.
- Operation receipts behind a storage interface (SQLite lands in P5): same
  ID and payload returns the prior result, a mismatched payload rejects, and
  unknown stays unknown across restart. Receipts are scoped to user and list.
- Tools: `list_lists`, `get_list`, `add_list_item`, `set_list_item_checked`,
  plus the connection/family tools clients need. Thin handlers over services.

Tests: several shopping lists, duplicate list names, invalid/default/foreign
IDs, already-completed items, Unicode titles, omitted quantity, explicit
`false`, ID-only acknowledgements, malformed responses, lost response, crash
after send, receipt replay and mismatch, and scope enforcement. Adding bread
twice on purpose must work — no global de-duplication by item text.

**Gate:** offline tool tests pass, and a controlled live add/check/uncheck in a
test list matches the FamilyWall UI. Test items are cleaned up through the UI;
no item-delete endpoint exists.

## P4 — Calendar and weekly overview

**Unblocked by P2a.** The server expands recurring occurrences and flags
all-day events, so this is now a normalisation and presentation phase, not a
research one.

**Objective:** a household week shows the correct events and signals gaps
honestly.

- Normalise the live-verified fields, and preserve the raw start/end strings
  alongside the parsed form as a durable audit trail.
- **All-day dates are taken verbatim from the UTC date component and are never
  timezone-converted.** This is the highest-risk rule in the phase.
- Accept RFC 3339 instants or local dates in the user's timezone; reject naive
  datetimes. Resolve weeks with `zoneinfo` calendar arithmetic, never seven
  fixed 24-hour additions. Timezone and week start are per-user preferences.
- The boundary adapter implements the observed rule
  `start < to && end > from`, which includes events overlapping the window. It
  stays a named, separately tested component even though it currently matches
  the server exactly, because it is the one place a server drift would be fixed.
- Recurrence consumes the server's expansion. **No local expansion and no
  recurrence library.** De-duplicate on `eventId`, never on `eventMasterId`.
  `exdate` and `recurrencyDeletedOccurence` have already been applied upstream
  and must not be re-applied.
- Stable ordering, occurrence-aware de-duplication, and explicit range and
  completeness metadata. Bound fetch windows (proposed 31 days per call) and
  output size; a reached cap reports partial status rather than a clean-looking
  week.
- Preserve unrecognised objects — `evtsync` flags show tasks, meals and external
  items can appear in calendar data.

Tests: the twelve cases in [calendar contracts](contracts/calendar.md).

**Gate:** the known test week matches FamilyWall, including a recurring series
with a cancelled occurrence and an all-day event on the correct local day.

## P5 — Invited users, encrypted links and self-hosted OAuth

**Objective:** each client token authorizes exactly one invited user's account.

- SQLite migrations and repositories for the records in
  [architecture](architecture.md#identity-and-storage-contract): users,
  invitations, preferences, credentials, web and OAuth state, rotation families
  and operation receipts. Atomic consumption, unique constraints, foreign keys,
  WAL. Test upgrade, restart and concurrent access.
- AES-GCM envelope encryption with key versions and AEAD data binding ciphertext
  to user ID and field; Argon2id for MCP login passwords. Plaintext FamilyWall
  credentials exist only for the duration of an API call. Opaque token values are
  hashed at rest. A missing key fails closed and is never regenerated.
- Implement the SDK's ten provider methods over that storage: `get_client`,
  `register_client`, `authorize`, `load_authorization_code`,
  `exchange_authorization_code`, `load_refresh_token`, `exchange_refresh_token`,
  `load_access_token`, `revoke_token`, `exchange_identity_assertion`. Enable
  dynamic client registration and revocation. `subject` is a random stable user
  ID; `client_id` is never treated as identity.
- Login, consent, invite acceptance, account-link and defaults pages are ours.
  FamilyWall credentials are validated before replacing a working link. The
  Halaxy reference's regression history — consent phishing, reflected XSS,
  expiring login state, login throttling — is the test list for these pages.
- Operator CLI: create invites, disable users, revoke sessions, reset access,
  rotate keys. Non-echo password input, never shell arguments. No SMTP; the
  operator shares invite URLs privately.
- User status is checked per request, so local disable or revoke takes effect on
  the next call even with an unexpired token.

Tests, over real HTTP rather than an in-memory client that bypasses middleware:
user A's token with user B's IDs, transport session reuse with another token,
parallel different-family calls, forged subject or client ID, a read-scoped token
on a write tool, authorization-code and refresh replay, redirect substitution,
XSS and CSRF, invite reuse, wrong AEAD subject, missing or wrong key, link
replacement, and pending mutations surviving restart.

**Gate:** lead review of auth and storage; all negative isolation cases pass. MCP
credentials, FamilyWall credentials and OAuth client credentials stay distinct.
No tool accepts a user-switching parameter.

## P6 — Hosted integration and operational recovery

- Wire tools, account pages and OAuth into the ASGI lifespan; manage client pools
  and DB connections. One worker until shared locking is designed.
- Dockerfile, `.dockerignore`, local and production Compose, Caddy example.
  Non-root; only the proxy's 80/443 published in production; developer HTTP bound
  to loopback. Mount `/data`, the encryption key and Caddy state correctly; keep
  secrets out of the build context and image layers. Build amd64 and arm64.
- Shallow `/health` that neither logs into FamilyWall nor reveals users or
  config. Resource, timeout and retention limits; Origin and Host validation;
  proxy buffering and timeouts appropriate to Streamable HTTP.
- Runbooks: setup, invite/link/reconnect, update/rollback, backup/restore,
  disable/revoke, key rotation, upstream drift. Back up SQLite with its backup
  API; store the key separately; restore into a disposable instance.
- Connect real ChatGPT and Claude accounts over HTTPS. Record callback URIs, the
  negotiated `MCP-Protocol-Version`, registration mode (DCR, or Claude's CIMD if
  DCR proves awkward), tool metadata, login, expiry, refresh and reconnect.
  Confirm two invited users work independently and concurrently.

**Gate:** restart and rebuild retain links and refresh state; restoring DB plus
key recovers service; revocation and unknown mutation state survive restart; a
lost key fails clearly. Client checks recorded as pass, fail or pending, with no
secrets. This plan performs no hosting purchase or production deployment.

## P7 — Acceptance and release preparation

Run in both ChatGPT and Claude, with at least two distinct test accounts and a
second test family for negative boundaries.

| Scenario | Required observed result |
| --- | --- |
| Invite and connect | User sets their own MCP login, links their own FamilyWall account, grants OAuth; no password in chat |
| Add bread | Correct family and list, one new item, verified ID and state in FamilyWall |
| Several shopping lists | Clarification or a saved default; never an arbitrary first list |
| Check and uncheck | Correct item, requested explicit state, repeat-safe |
| Multiple families on one account | Explicit unsupported-configuration error naming the limitation |
| This week at home | Correct local interval; matches normal, all-day, recurring and cancelled test events |
| Another user's IDs | Safe denial before any foreign data or write reaches upstream |
| Write with read scope | Rejected; an annotation alone cannot bypass policy |
| Expired FamilyWall session | Bounded read recovery or clear reconnect guidance; never a false empty result |
| Lost mutation response | Unknown outcome reported; no automatic duplicate |
| Upstream error or rate limit | Safe actionable error; no hidden retries |
| Restart and update | Links, grants, defaults and receipts persist |
| Disable and revoke | Old credentials and tokens stop authorizing the next request |
| Backup and key rotation | Restore succeeds; wrong or missing key fails clearly; old state recoverable during rotation |
| Sensitive output review | Logs, image layers, Git diff, fixtures and diagnostics contain no live secrets or family data |

Deliverables: a user README with tested connection instructions, an operator
runbook, a supported tool and scope table, known limitations, and the
compatibility record. Run the full local check suite and the acceptance suite
once; repeat only what a fix affects. V1 is ready only when every required row
passes. Commit, push and release follow the user's explicit delivery request.

## P8 — Independent extensions

Prioritised by actual household use after v1:

1. Create lists (`taskcreatelist`, types `SHOPPING`/`TODO`/`OTHER`) and single
   non-recurring calendar events, with explicit timezone — never the reference
   client's hard-coded `Europe/London` — duplicate protection and post-write
   verification.
2. Calendar update and delete only after occurrence-versus-series semantics are
   proven. The reference client's `evtdelete` with `option=All` is not a default;
   deleting a series when one occurrence was meant is unacceptable.
3. Item edit and delete: no endpoint evidence exists. Discovery first.
4. Message threads and bounded history; verify ordering and read-state effects
   before promising passive reads. Sending is its own explicitly enabled scope.
5. Attachment metadata and download, with size limits, URL validation, redirect
   restrictions and credential isolation. No tokenized URLs returned by default.
6. Meals, recipes, categories and ingredient transfer — endpoint names only, no
   contracts. Discovery yields complete request, response and failure contracts
   before any implementation.
7. Multi-family support, which requires evidence that family selection is even
   possible. `evtsync`'s `withAllFamilies` flag is the only lead.

Each extension gets a contract, narrow tool schemas, offline tests, scoped live
acceptance and an updated compatibility record. No general-purpose raw API tool.

## Execution economics

Budget in small deliverables, not token estimates. Most task cards are one
PR-sized change. Use cheap agents for evidence collection, parsers, fixtures,
handlers, documentation and bounded implementation against a defined contract.
Keep with the lead: the P2a probe interpretation, auth and tenant isolation, the
recurrence branch decision, and milestone review.

Give an agent only AGENTS.md, its task card, the relevant contract and model
files, and the source snippets it needs. If a task depends on an unproven
protocol detail, stop that part, record a discovery ticket with a concrete
question, and finish the independent work. No agent rereads both repositories on
every ticket.

The first tangible milestone is local shopping plus calendar tools after P3 and
P4. The requested hosted product is complete only after P7.
