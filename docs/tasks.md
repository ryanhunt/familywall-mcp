# Delegable implementation tasks

Status: P0 tasks 00A/00B/00C are **done** (contracts, calendar contract and
ADR 0001); Phase 1 tasks 01A and 01B are **done** on PR #1. The next ready task
is **02P**, the read-only live probe. All other runtime tasks are not started.
Update this file with task status, commit/PR and acceptance evidence as work
lands. See [phase plan](implementation-plan.md) and [PROGRESS](PROGRESS.md).

## Copyable delegation prompt

```text
Implement task <ID> from docs/tasks.md in this repository.
Read AGENTS.md, the task card, and only its linked contracts/source files.
Confirm dependencies have landed and inspect the current worktree.
Follow the task's file boundaries; preserve unrelated edits.
Do not invent FamilyWall endpoints or change shared contracts silently.
Use synthetic fixtures; no live network unless this task explicitly includes an
authorized test-family check with credentials supplied outside chat and Git.
Run the project-defined relevant checks and inspect the diff for secrets.
Complete docs/templates/handoff.md with evidence, limitations, and next task.
Do not mark a live check passed from mock tests. Do not commit/push unless requested.
```

For a cheap-worker subagent, the lead adds the precise source revision and files, the
allowed edit set, and where to return findings. "Cheap worker" means the lowest-cost
agent tier that can do the job; which models that maps to lives in each machine's
agent configuration, not in these cards, so the cards do not go stale when models
change. For an independent Claude Code/Codex task,
the same prompt works without naming a model. Request a narrow follow-up when a
check fails instead of reopening the entire phase.

## Task map

| ID | Deliverable | Depends on | Suggested implementer |
| --- | --- | --- | --- |
| 00A | FamilyWall wire contract and family context | none | done: docs/contracts/familywall.md |
| 00B | Calendar semantics evidence | none | done: docs/contracts/calendar.md |
| 00C | SDK and self-hosted OAuth decision/spike | none | done: docs/decisions/0001-auth-and-sdk.md |
| 01A | Package/config/model foundation | 00A, 00C | done: PR #1, docs/handoffs/phase1-foundation.md |
| 01B | Offline harness and checks | 01A | done: PR #1, docs/handoffs/phase1-foundation.md |
| 02P | Read-only live probe | 00A, 00B, test account | **next**: lead only, live credentials |
| 02A | Transport, login and error handling | 01B | Cheap worker |
| 02B | Discovery, family context and sessions | 02A, 00A, 02P | Cheap worker + lead isolation review |
| 03A | List API adapters | 02B | Cheap worker |
| 03B | List selection and mutation service | 03A | Cheap worker + lead receipt review |
| 03C | Shopping MCP tools | 03B | Cheap worker |
| 04A | Calendar normalization and ranges | 02B, 00B | Cheap worker |
| 04B | Week service and calendar MCP tools | 04A | Cheap worker + lead recurrence review |
| 05A | SQLite migrations and encrypted storage | 01B | Cheap worker + lead storage review |
| 05B | Invitations and account-link pages | 05A, 02B | Cheap worker |
| 05C | OAuth provider and HTTP identity | 05A, 00C | Lead-directed cheap worker + lead auth review |
| 05D | Multi-user integration and negative tests | 05B, 05C, 03C, 04B | Lead integration; cheap-worker tests |
| 06A | Containers and HTTPS deployment | 05D | Cheap worker |
| 06B | Backup, key rotation and operator guide | 06A | Cheap worker + lead recovery review |
| 07A | ChatGPT/Claude acceptance and release docs | 06B | Lead + user/client interaction |

Do not concurrently edit shared models, dependency files or app wiring. Safe parallel
work after 02B: list adapters, calendar adapters, and storage in their own directories.
The lead integrates completed changes and reruns affected checks before downstream tasks.

## P0 cards — complete

| ID | Output | Key result |
| --- | --- | --- |
| 00A | [familywall.md](contracts/familywall.md) | Family scope is session-global; no family selector exists in any list or calendar request |
| 00B | [calendar.md](contracts/calendar.md) | Only six event fields proven; all-day, recurrence, occurrence identity and cancellation have no evidence |
| 00C | [ADR 0001](decisions/0001-auth-and-sdk.md), [compatibility.md](compatibility.md) | `mcp` 2.2.0 mounts the full OAuth endpoint surface from a provider; embed the authorization server, no Authlib |

Do not redo these from the TypeScript source. The remaining unknowns need a live
account, not another source read.

### 02P — Read-only live probe

**Lead only. Not delegable: handles live credentials and real family data.**

- Read: [familywall.md](contracts/familywall.md) and [calendar.md](contracts/calendar.md)
  open-question lists, and the P2a procedure in [the plan](implementation-plan.md).
- Own: updates to both contract files and [compatibility.md](compatibility.md).
  The probe script itself is throwaway and is never committed.
- Prerequisite: a FamilyWall test family whose week has been populated **in the
  UI first** with a normal event, an all-day event, a multi-day all-day event, a
  recurring series with a cancelled or modified occurrence, and an event starting
  before the query window and ending inside it. Without a known expected answer
  the probe proves nothing.
- Deliver: answers to the eight `pending-live` questions in each contract,
  promoted with evidence level `live-verified` and the date; and the P4 branch
  decision — consume server-expanded occurrences, or implement local expansion.
- Accept: read-only, no write endpoint called; no credentials, cookies, real
  names, event titles or IDs written to the repository or any diagnostic; every
  answered question dated; unanswered questions still listed as `pending-live`
  rather than quietly dropped.

## Foundation/client cards

### 01A — Package, config and contracts

- Own: pyproject/lockfile, `src/familywall_mcp/config.py`, errors/models/interfaces,
  CLI skeleton, dummy `.env.example`, original-license notices.
- Deliver: typed `Principal`, credential provider, `FamilyContext`, client interface,
  result/error envelope and receipt repository interface; no production tools yet.
- Accept: clean installation/build; hosted settings fail closed; no env dump; explicit
  stdio principal; unknown family context cannot masquerade as validated context.

### 01B — Test harness and checks

- Own: tests/support, initial config tests, lint/type/test/build configuration,
  `scripts/check`, update contribution check instructions.
- Deliver: unexpected-request rejection, fake time, synthetic envelopes and identities,
  network-disabled default tests, secret scanning and a locked frozen install.
- Accept: real baseline tests detect bad config and malformed envelopes; checks run on
  a fresh checkout; no “live tests” in the default run; no placeholder green test
  suite.

### 02A — HTTP and session handshake

- Own: `familywall/client.py`, `familywall/errors.py`, login/envelope tests.
- Deliver: form encoding, JSESSIONID/CSRF, webset/webget, explicit login failure,
  bounded timeouts and safe errors. Remove static analytics cookies.
- Accept: missing/multiple cookies, Unicode, HTTP 200 errors/login HTML, malformed JSON,
  401/403/429 and transport timeout tested; no upstream write retry path.

### 02B — Discovery and session lifecycle

- Own: family discovery/context/session modules and focused tests.
- Deliver: family/calendar mapping from `accgetallfamily`, the **single-family
  rule** (more than one accessible family is an explicit unsupported-configuration
  error, never a silent pick), per-user pools/locks, bounded read reauth, eviction
  and credential-generation invalidation.
- Accept: A/B cookie isolation, concurrent login collapse, password update racing
  an old login, and 02P discovery evidence (or recorded pending evidence). Foreign
  family IDs rejected before a target operation. No family-switching code path is
  written: there is no evidence any such request exists.

## Shopping/calendar cards

### 03A — List API adapters

- Own: list wire models/adapters/tests under familywall/ and tests/unit/.
- Deliver: summary/detail/add/mark exact fields, object/ID acknowledgements and
  supported aliases; unknown returned list types retained.
- Accept: port relevant TS test behavior with Python fixtures; explicit false and
  omitted quantity correct; validation occurs before request; malformed results error.

### 03B — Selection and mutation behavior

- Own: services/lists, receipt service interface implementation using test repository,
  tests; do not edit SQLite migrations in parallel with 05A.
- Deliver: defaults/ambiguity/ownership, explicit checked state, the three-state
  receipt machine (`confirmed` / `acknowledged` / `unknown`) and the
  acknowledgement-versus-readback distinction. `taskmark` sends no list ID, so
  item membership must be verified before the call — a security property.
- Accept: same ID/payload returns prior result, mismatched payload rejects, lost
  response does not resend, pending receipt becomes unknown after crash, duplicate
  titles are not a global dedupe key. Verify list membership before item actions.

### 03C — Shopping MCP tool surface

- Own: tools/connection, tools/lists and MCP schema/handler tests.
- Deliver: initial connection/family/list/add/mark tool contracts and annotations.
- Accept: no credential/user-switch input; scoped writes; meaningful safe errors;
  structured output works with the pinned SDK; “add bread” resolves deterministically
  or returns actionable ambiguity. Live test-list add/check/uncheck is recorded separately.

### 04A — Calendar ranges and normalization

- Own: familywall/calendar adapters, services/ranges, tests.
- **Blocked until 02P answers the all-day and boundary questions.**
- Deliver: `calendar/{family_id}` usage confirmed by 02P, date/instant parsing,
  the boundary adapter matched to observed inclusivity, occurrence identity and
  overlap filtering. Preserve the raw start/end representation alongside the
  parsed form until all-day encoding is settled.
- Accept: DST days/weeks, midnight overlap, malformed events, unrecognised object
  types preserved, and truncation represented as partial. Do not copy the
  `Europe/London` / `recurrency=NONE` mutation defaults.

### 04B — Weekly overview and calendar tools

- Own: services/calendar, tools/calendar, behavior tests.
- **Blocked until 02P settles the recurrence branch.** If local expansion is
  required, that becomes its own reviewed subtask with a maintained recurrence
  library, and the limitation is stated until it lands.
- Deliver: saved timezone/week-start handling, sorted/deduplicated occurrences,
  completeness/cursor warnings and calendar-only scope in descriptions.
- Accept: known weekly calendar including exceptions matches FamilyWall; recurring
  occurrences are not collapsed by series ID; empty data differs from unavailable
  data. Any required expansion becomes its own evidence-backed reviewed subtask.

## Identity/storage cards

### 05A — Persistent repositories and encryption

- Own: storage/, migration tests, secret/receipt repositories; dependency changes
  coordinated through lead.
- Deliver: schema in architecture.md, AEAD user/field binding, versioned keys,
  hashed tokens, atomic consume/rotate, consistent backup primitive.
- Accept: wrong key/subject/tampering fail; no plaintext secrets in DB/logs; permissions,
  migration/restart, concurrent receipt uniqueness and pending recovery tested. Key
  absence never triggers automatic regeneration. Export stable interfaces to 05B/05C.

### 05B — Invites, recovery and FamilyWall linking

- Own: accounts/, templates, account CLI commands, web flow tests.
- Deliver: operator-created bearer invitation, password setup, self-service link/update,
  defaults, disable/revoke and operator-mediated recovery. No public registration.
- Accept: single-use/expired/replayed invites, username collisions, secure session
  rotation, CSRF/XSS, non-enumerating errors and concurrent credential replacement
  tested. Operator has a documented first-user bootstrap without a default password.
  OAuth/login integration uses the agreed 05C interface, not a second authentication system.

### 05C — Self-hosted OAuth

- Own: auth/, protocol tests; app integration only coordinated with lead.
- Deliver: selected provider, discovery, exact redirect validation, PKCE, opaque grants,
  refresh rotation/revocation, issuer/resource/scope checks and request-local principal.
- Accept: actual HTTP negative tests for missing/expired token, forged subject,
  scope escalation, code reuse, wrong client/resource/redirect/verifier, refresh reuse,
  disabled user, invalid Origin and restart. Keep client ID separate from user ID.
  Lead reviews before this component can serve real credentials remotely.

### 05D — Integration and adversarial user separation

- Own: app wiring, end-to-end HTTP tests; coordinate changes across owned modules.
- Deliver: tools use real per-user repositories, receipt persistence and session
  lifecycle; all hosted paths authenticated with no stdio fallback.
- Accept: two users, different test families, concurrent calls, token/session swapping,
  arbitrary IDs and expired memberships cannot read/write another context. Updating
  one user's credentials cannot replace another's pool. Check authorization over HTTP,
  not solely an in-memory client that bypasses middleware.

## Operations/release cards

### 06A — Docker and HTTPS

- Own: Dockerfile, `.dockerignore`, Compose/Caddy templates, deployment guide.
- Deliver: non-root one-worker service, persistent data/key wiring, TLS proxy,
  health checks and tested architecture targets.
- Accept: secret-free build context/layers, only proxy published in production,
  restart retains accounts/grants; invalid public URL/key/Origin fails correctly;
  build and local fake-data smoke checks recorded. Actual host/domain setup is a
  deployment action with concrete configuration, not assumed from docs.

### 06B — Recovery and operations

- Own: operator CLI backup/key-rotation commands and runbook tests/docs.
- Deliver: consistent DB backup, separate key backup, restore, revoke/reset, retention,
  key rotation and image/schema rollback procedure.
- Accept: disposable restore succeeds; wrong key and interrupted migration fail
  recoverably; logs contain no secret/record content; recovery does not re-enable
  disabled users or replay unknown mutations.

### 07A — Real-client acceptance

- Own: docs/compatibility.md, acceptance evidence, README/operator runbooks.
- Deliver: P7 matrix run in real ChatGPT and Claude, at least two users, supported
  tools/scopes and truthful remaining limitations.
- Accept: all mandatory rows pass; no mock-only row labelled live; a clean local
  `scripts/check` run; no secret-bearing screenshots/logs in commits. Report release readiness and
  any explicit blocker. Commit, push, deployment and publication require the user's
  requested delivery scope; do not infer completion from an available container image.

## Handoff and progress record

Each task handoff must contain changed files, commands/results, source evidence,
remaining live checks, any contract changes and the next ready task. Use
[handoff template](templates/handoff.md). Update statuses as `not started`, `in progress`,
`blocked: <specific evidence needed>`, or `done: <acceptance reference>`.

After P1, replace proposed command examples with the actual commands the foundation
provides. After each phase, update the README's current-status statement so the next
agent does not confuse this original planning snapshot with the implemented product.
