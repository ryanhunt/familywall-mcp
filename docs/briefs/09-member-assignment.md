# Task 09 — Member assignment for tasks and calendar events

## Objective

Let an MCP caller select FamilyWall family members by name when assigning a list
task or an ordinary calendar event. The server resolves names locally to members
of the authenticated family and sends only opaque FamilyWall account IDs upstream.

This is an implementation plan only. It does not add runtime behaviour.

## Scope

- In scope: the evidence, safety gates, implementation slices and tests needed
  for individual/all-member task assignments and calendar attendees.
- Files or components anticipated in implementation: `familywall/discovery.py`,
  `familywall/lists.py`, `familywall/calendar.py`, `services/lists.py`,
  `services/calendar.py`, a new `services/members.py`, `tools/registry.py`,
  receipt storage, contracts, and their unit tests.
- Out of scope: recurrence edits, special calendars and birthdays, arbitrary
  calendar editing, fuzzy name matching, caller-supplied FamilyWall account IDs,
  and any implementation in this documentation PR.

## Context and evidence

### Established evidence

- Discovery already provides each authenticated family's member `account_id`,
  `display_name`, and `first_name`.
- A controlled browser probe on 2026-09-16 created one disposable event. Its
  sanitized `evtcreate` form used `isToAll="false"` plus
  `attendee.0.accountId=<opaque account ID>` for one attendee. No attendee
  display name appeared on the wire. This is live-verified for one-member
  calendar creation only.
- Calendar read payloads contain `attendeeIds`, `attendees`, and `toAll`, but
  the current `CalendarEvent` model does not retain them.
- List read payloads contain `assignee`, `assigneeIds`, and `toAll`, but the
  current `ListItem` model does not retain them.
- The public web bundle declares `taskcreate(text, dueDate, assignee, reminder)`
  and `taskupdate(taskId, text, dueDate, assignee, reminder)`. The exact list
  `assignee` form encoding and update semantics remain source-only.
- Calendar writes are not implemented. Existing calendar contracts deliberately
  exclude them because timezone, recurrence and update semantics are unsafe to
  infer. List add is a non-atomic create-then-move flow with durable receipts.

### Open questions that block writes

- List `assignee`: exact wire field/value shape; one versus multiple assignees;
  all-member encoding; whether update replaces or patches; whether moving a task
  preserves assignment.
- Calendar: multiple-attendee indexed encoding; all-member encoding; required
  create fields; `evtupdate` replacement/series semantics; response identity;
  and proven timezone/all-day encodings.

No implementation may guess at these questions.

## Implementation plan

### 1. Freeze the wire contracts with controlled live probes

Lead-owned, opt-in probes will use only disposable data, retain only sanitized
key names and structural value types, and never log names, account IDs, event
contents, credentials, cookies or tokens.

- For tasks, inspect create and update with all, one, and (where the UI permits)
  two members; reread `assigneeIds` and `toAll`; test whether a move retains
  assignment. Delete only the exact disposable task IDs created by the probe.
- For events, inspect create and update with all, one and two attendees. Confirm
  indexed `attendee.N.accountId` fields, required fields, timezone, response ID
  and non-recurring update behaviour. Use only a disposable ordinary,
  non-recurring event and clean up its exact ID.
- Update `docs/contracts/familywall.md`, `docs/contracts/calendar.md`, and
  `docs/compatibility.md` after observation. Recurring series/occurrences,
  special calendars, and unverified all-day writes remain excluded.

### 2. Add fail-closed local member resolution

Create `services/members.py` with an immutable resolved-assignment model holding
`to_all`, account IDs and safe display names.

- Tools accept optional `assigned_to: list[str]`. Omission means all members for
  backwards compatibility; an empty list is invalid.
- Never accept an upstream account ID from a caller. Resolve names only against
  the authenticated family's discovered members.
- Normalize Unicode, case and repeated whitespace. Prefer an exact full display
  name; otherwise allow an exact unique first name. De-duplicate by account ID
  while preserving order. Unknown and ambiguous names fail before any write; no
  fuzzy or substring matching.
- Add a read-only `list_family_members` tool returning safe names only, not
  account IDs, so callers can select an exact value.
- Add `tests/unit/test_member_selection.py` plus discovery/tool coverage for
  normalization, full-name precedence, unique first names, duplicates, unknown,
  ambiguous and empty input, and tenant isolation.

### 3. Preserve assignment data in read models

- Extend `ListItem` with assignment account IDs and an all-members flag; parse
  `assigneeIds` and `toAll` according to the existing malformed-item policy.
- Extend `CalendarEvent` with attendee account IDs, an all-members flag, and
  `editable` for update safety.
- Update list/calendar fixtures and adapter tests using synthetic IDs. Tool
  reads map known IDs back to names and report unresolved-member counts without
  exposing account IDs. Confirmation comparisons use IDs internally.

### 4. Implement list assignment only after its evidence gate

- Extend exact field builders for `taskcreate` and `taskupdate`; assert complete
  form dictionaries and assert that display names never reach the wire.
- Add `assigned_to` to `add_list_item`. Resolve before the receipt/write,
  include canonical assignment intent in the payload hash, retain the existing
  create-then-move behaviour, and require correct destination *and* assignment
  for `confirmed`.
- Add narrow `set_list_item_assignees`, not a broad edit tool. Verify membership,
  preserve every field that the verified endpoint requires, write once, reread,
  and return confirmed/acknowledged/unknown. Never retry a lost write.
- Update `services/lists.py`, `tools/registry.py`, list adapter/service/tool
  tests. Assert zero calls for disabled writes or invalid/ambiguous names,
  endpoint order, readback mismatch, receipt replay, and payload conflicts.

### 5. Generalize durable receipts before calendar mutation

Calendar creation is non-idempotent and must not depend on list-shaped receipt
state. Generalize receipt resource/action fields and provide a tested SQLite
migration that preserves existing rows and `(subject, operation_id)` isolation.
Update `models.py`, `interfaces.py`, memory/SQLite stores, the list service, and
receipt tests. Hash action, resource, resolved IDs/all-members state, dates,
text and destination as applicable.

### 6. Implement calendar attendees only after its evidence gate

Initial scope is ordinary, editable, non-recurring events in the family calendar.
Refuse birthdays, special calendars, recurring events/occurrences and exceptions
before mutation. Do not hard-code a timezone.

- Add verified create/update builders to `familywall/calendar.py`: all-members
  mode uses verified `isToAll` behaviour; named members use verified indexed
  `attendee.N.accountId` entries. Names never go upstream.
- Add `create_event` and narrow `set_calendar_event_attendees` service methods.
  For updates, fetch and verify the event in a bounded supplied range, enforce
  family-calendar/editable/non-recurring constraints, preserve required fields
  if update is replacement-style, write once and reread for exact confirmation.
- Add `create_calendar_event` and `set_calendar_event_attendees` tools behind
  the current write gate and durable receipts. Confirm only from exact readback;
  use acknowledged when success cannot be confirmed and unknown for a lost
  response. Never auto-retry.
- Cover all/specific fields, resolver behavior, timezone/DST, unsafe-event
  refusal, readback mismatch, replay and zero-call write-gate behavior in
  adapter, service and tool suites.

### 7. Roll out in focused pull requests

1. Evidence probes and contract updates.
2. Resolver and assignment read models.
3. List create/update assignment.
4. Receipt generalization and migration.
5. Calendar create/update attendees.
6. Controlled live verification and user documentation.

Update `README.md`, `docs/PROGRESS.md`, `docs/architecture.md`, contracts,
compatibility and tool counts only in the corresponding implementation PRs.
Writes remain disabled by default.

## Acceptance criteria

- [ ] This PR adds no runtime code and labels evidence as live-verified,
  source-only or pending-live accurately.
- [ ] Future tools accept names but send only IDs discovered for the current
  authenticated family.
- [ ] Unknown/ambiguous names and unsafe calendar targets fail before mutation.
- [ ] All-members and named-member modes are explicit; empty assignment is never
  guessed.
- [ ] Write-gate, account isolation, durable replay protection, no automatic
  retries and readback-based outcomes remain mandatory.
- [ ] Multiple assignees, duplicate names, recurring/special-event updates and
  unverified all-day creation are documented limitations until proven.

## Validation

This PR is documentation-only. Review links and evidence statements against the
current contracts, adapters, services and tests. Each implementation slice must
run:

```text
uv sync --frozen --group dev
uv run ruff check .
uv run ruff format --check .
uv run mypy src
uv run pytest -m 'not live'
uv build
scripts/check
```

## Security and privacy

- Use synthetic fixtures and dummy identifiers only.
- Keep credentials, cookies, tokens, real account IDs, names and event content
  out of Git, logs, fixtures and PR text.
- Review name-resolution ambiguity, session-family isolation, receipt migration,
  calendar-event scope and write retry behaviour before each implementation PR.

## Handoff target

Evidence probe for list-task assignment wire semantics, followed by the
corresponding contract update.
