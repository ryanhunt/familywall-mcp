# Handoff — 09C attendees and the default reminder on `create_calendar_event`

## Outcome

`create_calendar_event` now takes `assigned_to: list[str] | None`. Blank
(`None` or `[]`) means everyone; names mean exactly those members, resolved
against the cached family discovery (`services.members.resolve_members`)
before any receipt or write. This **changes the previous default**, which
assigned only the signed-in member (PR #9) — the tool description and README
say so. An unknown name triggers exactly one discovery refresh (in case the
cached family list is stale) and a second resolution attempt; an ambiguous or
otherwise invalid name never refreshes.

Every created event also gets FamilyWall's own default reminder
(`reminderList.0.reminderType=SNOOZE`, `reminderUnit=MINUTE`,
`reminderValue=30`), replacing the previous `reminderList=$empty`. The
existing exact-readback confirmation was extended to cover both: an
`attendees` mismatch (wrong `toAll`/`attendeeIds`) and a `reminder` mismatch
(missing or different `reminderList`) are each reported by name, alongside
the existing fields.

Names never reach the wire in either direction: `build_create_event_fields`
only ever receives already-resolved account IDs (`to_all: bool` +
`attendee_account_ids: Sequence[str]`), and the tool's response reports
`assigned_to` as display names only (`assigned_to_everyone: bool` alongside
it), never an account ID.

This is brief 09 slice C, done offline (synthetic fixtures only, no live
calls). It builds on slice B's resolver/read-models and slice D's generalized
receipts. Next: the lead's live check of both attendee modes, then slice E
(`set_calendar_event_attendees`).

## Changed files

- `src/familywall_mcp/familywall/calendar.py`:
  - `build_create_event_fields`: `attendee_account_id: str` replaced by
    `to_all: bool` and `attendee_account_ids: Sequence[str]` (rejects empty).
    Emits `isToAll` (`"true"`/`"false"`) and `attendee.N.accountId` for each
    ID in the given order; replaces `reminderList=$empty` with the three
    `reminderList.0.*` default-reminder fields. Every other field unchanged.
  - New `EventReminder(DomainModel)` (`type`, `unit`, `value`, all verbatim
    strings) and `CalendarEvent.reminders: tuple[EventReminder, ...] | None`.
  - New `_parse_reminders`: absent `reminderList` gives `None`; `[]` gives
    `()`; a malformed value (not a list, or an entry missing/mistyping a
    reminder field) gives `None` **without** raising — unlike
    `attendeeIds`/`toAll`/`editable`, a malformed reminder never skips the
    event (reminders are not needed for reads).
- `src/familywall_mcp/services/calendar.py`:
  - `create_event` gains an `assignment: ResolvedAssignment` parameter
    (imported from `services.members`), passed through to
    `build_create_event_fields` as `to_all=assignment.to_all`,
    `attendee_account_ids=assignment.account_ids`. The payload hash is
    unchanged in construction and now naturally covers the attendee/reminder
    fields.
  - `_confirm_created`/`_readback_mismatches` both gain the same `assignment`
    parameter. `_readback_mismatches` adds two checks: `"attendees"` (everyone
    needs `event.to_all is True`; named needs `event.to_all is False` **and**
    `set(event.attendee_ids) == set(assignment.account_ids)`, order-
    insensitive) and `"reminder"` (`event.reminders` must equal exactly
    `(EventReminder("SNOOZE", "MINUTE", "30"),)`, the new module-level
    `DEFAULT_REMINDER` constant).
- `src/familywall_mcp/services/principal_context.py`:
  - `ContextResolver` protocol gains `async def refresh(self) -> tuple[Principal, PrincipalContext]`.
  - `HostedContextResolver.refresh()`: reads the current subject from
    `get_access_token()` (failing closed with `AuthenticationError` if
    missing, same guard as `resolve()`), calls `invalidate(subject)`, then
    `resolve()` (which rebuilds and re-caches since the entry was just
    dropped).
  - `FixedContextResolver` takes an optional `session_pool: SessionPool | None = None`.
    Its `refresh()` rebuilds via `build_principal_context(principal,
    session_pool)`, stores and returns the new context; without a pool it
    returns the current pair unchanged (nothing to refresh against).
- `src/familywall_mcp/server.py`: `_run_stdio` now passes `session_pool` to
  `FixedContextResolver(principal, context, session_pool)`, so the stdio
  server's `refresh()` is real (one more `accgetallfamily` call), not a no-op.
- `src/familywall_mcp/tools/registry.py`:
  - `_create_calendar_event` gains `assigned_to: list[str] | None = None`.
    Resolves it with `resolve_members` against `ctx.discovered_family` after
    validating the event's times but before building the transport/service or
    generating an operation ID. On `MemberSelectionError`: if the code is
    `unknown_member`, calls `self._context_resolver.refresh()` once and
    resolves exactly once more (any further failure propagates); any other
    code (`ambiguous_member`, `invalid_member_name`) re-raises immediately,
    which the existing outer `except FamilyWallError` turns into the matching
    `ErrorResponse` — no refresh, zero upstream calls.
  - `CreateCalendarEventResponse.assigned_to` is now `tuple[str, ...]` (every
    member's name for everyone); it gains `assigned_to_everyone: bool`. Both
    come straight from the `ResolvedAssignment`, never an account ID.
  - Tool description and method docstring updated to explain `assigned_to`
    and the default reminder.
- `tests/support/calendar_fixtures.py`: three new fixtures —
  `event_with_reminder`, `event_with_empty_reminder_list`,
  `event_with_malformed_reminder_list`.
- `tests/unit/test_calendar_adapter.py`: `TestBuildCreateEventFields` rewritten
  for the new builder signature (C1, C2, C3, plus the pre-existing
  London/absent-fields/naive-datetime tests updated to it); new
  `TestReminderField` (C14).
- `tests/unit/test_calendar_create.py`: `stored_event()` now defaults to the
  self-only assignment's attendee/reminder shape (`attendeeIds: ["acct-self"]`,
  `toAll: "false"`, the default `reminderList`), so existing "confirmed" tests
  keep passing without individually restating those fields; `create()` takes
  an optional `assignment` (defaults to the new `SELF_ONLY_ASSIGNMENT`
  constant); one direct `CalendarService(...).create_event(...)` call updated
  for the new positional `assignment` argument. New `TestAttendeesAndReminder`
  (C4, C5, C6, C7) and one new test in `TestReceipts` (C11); the existing
  single-attendee-form test renamed/marked as C15.
- `tests/unit/test_tools.py`: `_stored_event` generalized to mirror whatever
  `isToAll`/`attendee.N.accountId`/`reminderList.0.*` the sent form actually
  carried, instead of a fixed self-only shape, so every scenario (everyone,
  named, refreshed family) reads back consistently. New
  `create_family_with_ambiguous_members`, `_refreshed_family_payload`, and
  `FakeSessionPoolWithDiscoveryRefresh` (a `FakeSessionPool` subclass that
  answers `accgetallfamily`). New tests for C8, C9, C10, C12, C15 (two, one
  covering the new everyone default and one covering the named single-member
  form); one pre-existing test renamed/updated for the new default (see below).
- `tests/unit/test_principal_context.py` (new): C13 —
  `FixedContextResolver.refresh()` with and without a `session_pool`;
  `HostedContextResolver.refresh()` invalidating then rebuilding (a fake
  discovery counted twice: once by the initial `resolve()`, once more by
  `refresh()`), and failing closed with no access token.
- `README.md`: `create_calendar_event` row and its bullet rewritten for the
  new default (everyone), `assigned_to`, the refresh-on-unknown-name behaviour,
  and the default reminder.
- `docs/contracts/calendar.md`: one new paragraph under the `evtcreate`
  section recording which encodings the tool now sends (attendees, reminder),
  matching probe A1, and noting this is offline/unit-tested only so far.
- `docs/PROGRESS.md`: one new row for slice C.
- `docs/briefs/09-implementation-plan.md`: slice C marked done in the "where
  brief 09 stands" table, the slice-order table, and its own section
  (checkboxes ticked); one bullet corrected in place (see Deviation below)
  rather than silently rewritten, following the same pattern slice D used for
  its own decision E2.
- `docs/handoffs/09c-calendar-attendees.md`: this file.

## Deviation from the brief

`docs/briefs/09-implementation-plan.md`'s slice C section (written before the
more detailed `09c-calendar-attendees.md` brief) said
`build_create_event_fields` would "take a `ResolvedAssignment`". The actual
task brief's binding decision 3 is explicit that **the adapter layer must not
import `services`**, so `ResolvedAssignment` (defined in
`services/members.py`) cannot appear in `familywall/calendar.py`'s signature.
Implemented as specified in `09c-calendar-attendees.md`: the builder takes
`to_all: bool` and `attendee_account_ids: Sequence[str]` directly, and
`services/calendar.py`'s `create_event` is what accepts the
`ResolvedAssignment` and unpacks it for the builder. Recorded in place in
`docs/briefs/09-implementation-plan.md` (same pattern as slice D's decision
E2) rather than silently diverging from the older plan text.

No other deviations. All seven binding decisions and all fifteen required
tests (C1–C15) are implemented as specified.

## Acceptance evidence (C1–C15)

- **C1** Builder, everyone — complete form, `isToAll="true"`, ordered
  `attendee.0..N-1`, three reminder fields:
  `tests/unit/test_calendar_adapter.py::TestBuildCreateEventFields::test_c1_complete_form_for_everyone`.
- **C2** Builder, named — complete form, `isToAll="false"`, given IDs in
  order:
  `TestBuildCreateEventFields::test_c2_complete_form_for_named_members`.
- **C3** Builder rejects empty `attendee_account_ids` (both `to_all` values);
  no display name ever appears in the form:
  `TestBuildCreateEventFields::test_c3_builder_rejects_empty_attendees_and_never_emits_a_name`.
- **C4** Service, everyone — everyone form sent; readback with `toAll:"true"`,
  empty `attendeeIds`, the default reminder is `confirmed`:
  `tests/unit/test_calendar_create.py::TestAttendeesAndReminder::test_c4_everyone_sends_every_member_and_confirms`.
- **C5** Service, named — named form sent; readback with the same IDs in a
  different order is `confirmed`:
  `TestAttendeesAndReminder::test_c5_named_members_confirm_with_ids_in_a_different_order`.
- **C6** Readback `attendees` mismatch, three cases (missing ID, extra ID,
  everyone requested but `toAll:"false"`), parametrized:
  `TestAttendeesAndReminder::test_c6_attendees_mismatch_cases`.
- **C7** Readback with no reminder, or a different one, is `mismatched` with
  `"reminder"`:
  `TestAttendeesAndReminder::test_c7_missing_reminder_is_mismatched` and
  `test_c7_different_reminder_is_mismatched` (parametrized: empty list,
  different type/unit/value).
- **C8** Tool, unknown name with no refresh available (stdio, no pool):
  `unknown_member`, zero upstream calls:
  `tests/unit/test_tools.py::test_c8_unknown_member_with_no_refresh_available`.
- **C9** Tool, an unknown name the refresh does find: one refresh, then
  success; the second lookup uses the refreshed family (asserts the sent
  `attendee.0.accountId` is the newly-discovered member's ID):
  `test_c9_unknown_member_found_after_one_refresh`.
- **C10** Tool, ambiguous or invalid name: no refresh, zero calls (parametrized
  over both cases, using a pool that would record a call if refresh() were
  incorrectly attempted): `test_c10_ambiguous_or_invalid_name_never_refreshes`.
- **C11** Same idempotency key, different assignment: `operation_id_conflict`,
  no second write:
  `tests/unit/test_calendar_create.py::TestReceipts::test_c11_reused_key_with_a_different_assignment_conflicts`.
- **C12** Tool response: `assigned_to`/`assigned_to_everyone` correct for both
  modes; no account ID in the serialised response:
  `tests/unit/test_tools.py::test_c12_response_names_and_no_account_id_leak`.
- **C13** `HostedContextResolver.refresh()` invalidates then rebuilds (fake
  discovery called twice) and fails closed with no access token;
  `FixedContextResolver.refresh()` with and without a pool:
  `tests/unit/test_principal_context.py` (`TestFixedContextResolverRefresh`,
  `TestHostedContextResolverRefresh`).
- **C14** `CalendarEvent.reminders`: present parses verbatim; absent gives
  `None`; `[]` gives `()`; malformed gives `None` and does not skip the event:
  `tests/unit/test_calendar_adapter.py::TestReminderField` (four tests).
- **C15** Old self-only-default tests updated to the new default; naming only
  the signed-in member still sends the single-attendee form PR #9 verified,
  at both the service layer
  (`tests/unit/test_calendar_create.py::TestConfirmation::test_c15_create_sends_the_verified_single_attendee_form`)
  and the tool layer
  (`tests/unit/test_tools.py::test_c15_create_calendar_event_defaults_to_everyone`,
  `test_c15_naming_only_the_signed_in_member_sends_the_single_attendee_form`).

## Everyone-mode and named-mode forms (from C1/C2, synthetic IDs)

Everyone (`to_all=True`, IDs `("acct-synthetic-1", "acct-synthetic-2", "acct-synthetic-3")`):

```python
{
    "partnerScope": "Family",
    "text": "Family dinner",
    "startDate": "2026-10-06T18:00:00+11:00",
    "endDate": "2026-10-06T19:00:00+11:00",
    "timeZone": "Australia/Sydney",
    "where": "Main St",
    "description": "Roast night",
    "isToAll": "true",
    "attendee.0.accountId": "acct-synthetic-1",
    "attendee.1.accountId": "acct-synthetic-2",
    "attendee.2.accountId": "acct-synthetic-3",
    "picture": "$empty",
    "private": "",
    "recurrency": "NONE",
    "recurrencyInterval": "1",
    "byDay": "",
    "byMonthDay": "",
    "recurrencyEndDate": "$empty",
    "reminderList.0.reminderType": "SNOOZE",
    "reminderList.0.reminderUnit": "MINUTE",
    "reminderList.0.reminderValue": "30",
}
```

Named (`to_all=False`, IDs `("acct-synthetic-2", "acct-synthetic-1")`, in that order):

```python
{
    "partnerScope": "Family",
    "text": "Dentist",
    "startDate": "2026-10-06T10:00:00+11:00",
    "endDate": "2026-10-06T11:00:00+11:00",
    "timeZone": "Australia/Sydney",
    "where": "Main St",
    "description": "Checkup",
    "isToAll": "false",
    "attendee.0.accountId": "acct-synthetic-2",
    "attendee.1.accountId": "acct-synthetic-1",
    "picture": "$empty",
    "private": "",
    "recurrency": "NONE",
    "recurrencyInterval": "1",
    "byDay": "",
    "byMonthDay": "",
    "recurrencyEndDate": "$empty",
    "reminderList.0.reminderType": "SNOOZE",
    "reminderList.0.reminderUnit": "MINUTE",
    "reminderList.0.reminderValue": "30",
}
```

## Validation

- `uv sync --frozen --group dev`: ok (already synced in this worktree).
- `uv run ruff check .`: All checks passed.
- `uv run ruff format --check .`: 113 files already formatted.
- `uv run mypy src`: Success, no issues found in 30 source files.
- `uv run pytest -m 'not live'`: **488 passed** (437 at the end of slice D's
  lead review; 51 net new/changed test cases across the files above).
- `uv build`: both sdist and wheel built successfully.
- `scripts/check`: every step above passes; the final detect-secrets step
  fails, but only on pre-existing drift unrelated to this slice — a fresh
  `detect-secrets scan` reports a different finding set for `README.md` and
  `.env.local` than the committed `.secrets.baseline` records, with no
  changes anywhere in this slice's diff to either the scan behaviour or the
  baseline file itself (`git status` shows no diff to `.secrets.baseline`
  before or after running the script). This matches the brief's expectation
  and PR #8's separate tracking; the baseline was left untouched.

## Security and privacy review

- All test data is synthetic: neutral placeholder names (`Alex`, `Robin`,
  `Sam`, `Jordan`, and compounds like `Jordan Lee`/`Jordan Alex`/`Jordan
  Robin`, plus the pre-existing `Test Member`/`Other Member`), fake account
  IDs (`acct-synthetic-*`, `acct-alex`, `acct/1`, etc.), and fake family/
  calendar IDs. No real person's name is used anywhere in this diff.
- No `.env*` file or credential store was read. No network calls were made;
  every test uses in-memory fakes (`ScriptedTransport`, `FakeSessionPool` and
  its subclasses) or synthetic wire payloads.
- No environment variables were printed.
- Names never reach the wire: `build_create_event_fields` only accepts
  already-resolved account IDs, and every response model reports names only,
  never an account ID — asserted directly in C3 (builder) and C12 (tool
  response serialisation).

## Known limitations

- `docs/architecture.md`'s tool-contract table (line ~233) still describes
  `create_calendar_event` as "assigned to the authenticated member" — this
  file was not in this slice's file boundary (the brief lists README.md,
  `docs/contracts/calendar.md`, `docs/PROGRESS.md`, and the 09-implementation
  plan, not `architecture.md`), so it was left unedited rather than touched
  outside scope. It is now stale and should be corrected in the same pass
  that touches that file next (e.g. slice E or G).
- `set_calendar_event_attendees` (editing an existing event's attendees,
  slice E) and list assignment (slice F) are still not implemented; this
  slice only covers `create_calendar_event`.
- The refresh-on-`unknown_member` path is exercised only against synthetic
  discovery payloads; it has not been exercised against a real FamilyWall
  account whose family membership actually changed between calls.

## Next bounded task

The lead's live check of both attendee modes (everyone, then two named
members) through the real `create_calendar_event` tool, followed by slice E
(`set_calendar_event_attendees`) per `docs/briefs/09-implementation-plan.md`.

## Lead review and live verification (2026-09-25)

- Reviewed the builder, readback checks, resolver refresh and tool flow against
  the brief; C1–C15 present; 488 passed. A family-name audit of this worktree
  finds no real member names or IDs.
- Live check through the real tool, authorised by the account owner: one event
  with `assigned_to` blank and one naming two members. Both returned
  `confirmed` with no mismatched fields. Readback: everyone as `toAll:"true"`
  with no `attendeeIds`; named as `toAll:"false"` with exactly two; both carried
  the `SNOOZE`/`MINUTE`/`30` reminder. Both events were deleted by exact ID and
  none remain.
- Fixed the stale `create_calendar_event` row in `docs/architecture.md` noted
  above.
