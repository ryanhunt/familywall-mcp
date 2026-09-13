# Delegable implementation tasks

Status: Phase 1 tasks 01A and 01B are **done** on PR #1; P0 contract/SDK
decisions and all later runtime tasks remain not started. Update this file with
task status, commit/PR and acceptance evidence as work lands. See [phase
plan](implementation-plan.md).

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

For a Luna subagent, the lead adds the precise source revision and files, the allowed
edit set, and where to return findings. For an independent Claude Code/Codex task,
the same prompt works without naming a model. Request a narrow follow-up when a
check fails instead of reopening the entire phase.

## Task map

| ID | Deliverable | Depends on | Suggested implementer |
| --- | --- | --- | --- |
| 00A | FamilyWall wire contract and family context | none | Luna reconnaissance |
| 00B | Calendar live-semantics evidence | none | Luna evidence + lead review |
| 00C | SDK and self-hosted OAuth decision/spike | none | Lead-directed Luna + lead decision |
| 01A | Package/config/model foundation | 00A, 00C | done: PR #1, docs/handoffs/phase1-foundation.md |
| 01B | Offline harness and checks | 01A | done: PR #1, docs/handoffs/phase1-foundation.md |
| 02A | Transport, login and error handling | 01B | Luna |
| 02B | Discovery, family context and sessions | 02A, 00A | Luna + lead isolation review |
| 03A | List API adapters | 02B | Luna |
| 03B | List selection and mutation service | 03A | Luna + lead receipt review |
| 03C | Shopping MCP tools | 03B | Luna |
| 04A | Calendar normalization and ranges | 02B, 00B | Luna |
| 04B | Week service and calendar MCP tools | 04A | Luna + lead recurrence review |
| 05A | SQLite migrations and encrypted storage | 01B | Luna + lead storage review |
| 05B | Invitations and account-link pages | 05A, 02B | Luna |
| 05C | OAuth provider and HTTP identity | 05A, 00C | Lead-directed Luna + lead auth review |
| 05D | Multi-user integration and negative tests | 05B, 05C, 03C, 04B | Lead integration; Luna tests |
| 06A | Containers and HTTPS deployment | 05D | Luna |
| 06B | Backup, key rotation and operator guide | 06A | Luna + lead recovery review |
| 07A | ChatGPT/Claude acceptance and release docs | 06B | Lead + user/client interaction |

Do not concurrently edit shared models, dependency files or app wiring. Safe parallel
work after 02B: list adapters, calendar adapters, and storage in their own directories.
The lead integrates completed changes and reruns affected checks before downstream tasks.

## P0 cards

### 00A — Wire contracts and family context

- Read: research.md; pinned TS client/family/types and list tests.
- Own: `docs/contracts/familywall.md`, attribution notes under `docs/decisions/`.
- Deliver: exact login/discovery/list fields, variants/errors and evidence levels;
  distinguish account/family/calendar IDs and determine active-family selection.
- Accept: no guessed parameters; no “complete” pagination claims without evidence;
  unsupported family switching is explicitly blocked; original notices identified
  before copying substantial source. If live access is unavailable, report the exact
  missing observation and let independent offline tasks proceed.

### 00B — Calendar behavior evidence

- Read: pinned calendar-range tests, legacy create/update code, research.md.
- Own: calendar section of contract (coordinate with 00A) or a separate
  `docs/contracts/calendar.md`, synthetic case descriptions.
- Deliver: boundaries, timezone/all-day format, overlap, recurrence, exceptions,
  calendar IDs, external-calendar visibility and completeness behavior.
- Accept: each assertion labelled source-only/mock/live; one test-week comparison
  when authorized test-family access exists. No calendar mutation implementation.
  Missing recurring-event behavior remains a release blocker, not “not applicable”.

### 00C — SDK and OAuth compatibility decision

- Read: architecture.md auth sections, current official SDK/auth docs, Halaxy provider.
- Own: `docs/decisions/0001-auth-and-sdk.md`, `docs/compatibility.md`; disposable spike
  outside production source until selected.
- Deliver: installable SDK release, verified provider/server imports, self-hosted
  registration strategy, subject extraction, token metadata and actual HTTP handshake.
- Accept: PKCE/resource/subject behavior tested with fake users; client round trips
  pass or remain explicitly pending. Explain embedded-provider tradeoffs and fallback
  self-hosted component if the SDK cannot satisfy requirements. No external IdP signup.

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
- Deliver: accessible-family/calendar mapping, verified family selection, per-user
  pools/locks, bounded read reauth, eviction and credential-generation invalidation.
- Accept: A/B cookie isolation, concurrent login collapse, active-family switch race,
  password update racing old login, and read-only live discovery probe (or recorded
  pending evidence). Foreign family IDs rejected before a target operation.

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
- Deliver: defaults/ambiguity/ownership, explicit checked state, operation receipt
  state machine and acknowledgement/readback distinction.
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
- Deliver: verified calendar IDs, date/instant parsing, boundary adapter, occurrence
  identity and overlap filtering. Preserve local all-day dates.
- Accept: DST days/weeks, midnight overlap, malformed events, unknown recurrence and
  truncation are represented correctly. Do not copy London/NONE mutation defaults.

### 04B — Weekly overview and calendar tools

- Own: services/calendar, tools/calendar, behavior tests.
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
