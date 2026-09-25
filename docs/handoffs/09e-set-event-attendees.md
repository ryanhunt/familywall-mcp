# Handoff — 09E `set_calendar_event_attendees`

## Outcome

`set_calendar_event_attendees(event_id, date, assigned_to=None,
idempotency_key=None)` is a new write tool that changes **only** who an
existing, ordinary, one-off, timed family-calendar event is assigned to.
`event_id` is the `occurrence_id` `get_week_overview` returns; `date` is that
event's local date (`YYYY-MM-DD`) in the authenticated member's FamilyWall
timezone. `assigned_to` is resolved the same way, and at the same point
(before any lookup, receipt or write), as `create_calendar_event`'s: blank
means everyone, an unknown name triggers exactly one discovery refresh, and an
ambiguous or otherwise invalid name never refreshes and makes zero upstream
calls. The refresh-once resolution used to be duplicated in
`_create_calendar_event`; it is now a shared `ToolRegistry._resolve_assignment`
helper both tools call.

The tool fetches the event from the member's local day window
(`resolve_days(date, 1, member_tz)` over `evtlistinterval`) and matches it by
`occurrence_id`. Not found is `event_not_found`, with zero writes. Before any
write or receipt, the found event is checked against five safety gates
(decision 4): it must be on the family calendar, have `editable` **exactly**
`True` (so `None` is refused too, not just `False`), be non-recurring, not a
series exception, an ordinary `"UNKNOWN"` event, and timed rather than
all-day. Any failure is `unsupported_event`, after only the lookup read, with
zero writes and no receipt. Only then is a `"pending"` receipt recorded and
one `evtupdate` sent, carrying **exactly** six field kinds
(`build_update_attendees_fields`: `partnerScope`, `option=All`, `calendarId`,
`metaId`, `isToAll`, `attendee.N.accountId`) — nothing else is sent, and no
other field is rebuilt from the event just read. The write is sent at most
once and never retried; an indeterminate error (`_INDETERMINATE_ERRORS`) gives
`unknown`, and `UpstreamRejectedError`/`AuthenticationError` are recorded as
`rejected` with the error JSON and re-raised, exactly as `create_event` does.

The outcome is `confirmed` only when a re-read of the *same* day window shows
the requested attendees **and** every other comparable field (title, span,
timezone, location, description, recurrence flags, calendar and reminders)
unchanged from the event this method first read (not from the create-time
request, since there is none) — any difference is named in
`mismatched_fields` and the outcome is `mismatched`. Receipts carry
`resource_id=event_id` and `action="calendar.set_attendees"`; the payload
hash (`_hash_payload("evtupdate", calendar_id, fields)`) depends only on the
event ID, calendar ID and resolved assignment, so the idempotency-key replay
check runs *before* the lookup — a replay of a known key makes **zero**
upstream calls, even for a request that would otherwise fail the lookup or a
safety gate.

This is brief 09 slice E, done offline (synthetic fixtures only, no live
calls). It builds on slice B's resolver/read-models, slice C's readback and
refresh-once patterns, and slice D's generalized receipts. Next: the lead's
live check (slice G), then merge.

## Changed files

- `src/familywall_mcp/familywall/calendar.py`:
  - New `build_update_attendees_fields(*, event_id, calendar_id, to_all,
    attendee_account_ids)`: returns exactly `partnerScope=Family`,
    `option=All`, `calendarId`, `metaId=event_id`, `isToAll`
    (`"true"`/`"false"`) and `attendee.N.accountId` per ID, in that order.
    Rejects an empty `attendee_account_ids`. Only ever receives resolved
    account IDs, never member names.
- `src/familywall_mcp/services/calendar.py`:
  - New `_EVENT_NOT_FOUND`/`_UNSUPPORTED_EVENT` `ErrorInfo` constants and
    `EventNotFoundError`/`UnsupportedEventError` (`FamilyWallError`
    subclasses, following `services/members.py`'s `MemberSelectionError`
    precedent — the adapter/errors module boundary in this file's scope does
    not include `errors.py`).
  - New `SetAttendeesResult(DomainModel)`: `outcome`, `event_id`,
    `mismatched_fields` (populated iff `mismatched`).
  - New `CalendarService.set_event_attendees(principal, event_id, local_day,
    timezone, assignment, operation_id, receipt_repository,
    family_context)`: the full lookup → safety-gate → write-once → confirm
    flow described above.
  - New `CalendarService._confirm_attendees`: re-reads the same
    `LocalRange` day window and delegates to `_readback_attendee_mismatches`.
  - New module-level `_replay_set_attendees` (mirrors `_replay_create`, but
    always has an `event_id` to fall back to, since the replay check runs
    before any lookup), `_is_unsafe_to_update` (decision 4's five gates), and
    `_readback_attendee_mismatches` (compares the reread event against the
    *first-read* event for every field except attendees, which are compared
    against `assignment` using slice C's rule).
  - `LocalRange` added to the `services.ranges` import for the
    `_confirm_attendees` day-window parameter's type.
- `src/familywall_mcp/models.py`: `OperationReceipt.action` gains
  `"calendar.set_attendees"`.
- `src/familywall_mcp/tools/registry.py`:
  - New `SetCalendarEventAttendeesResponse(DomainModel)`: `family_name`,
    `outcome`, `event_id`, `assigned_to` (display names),
    `assigned_to_everyone`, `mismatched_fields`. No account ID field.
  - New `_set_calendar_event_attendees` tool method: write gate, then parses
    `date` (an unparseable date is `invalid_request` with zero calls, checked
    before member resolution so a bad date never triggers a refresh call
    either), then the shared `_resolve_assignment`, then
    `CalendarService.set_event_attendees`.
  - New `ToolRegistry._resolve_assignment(principal, ctx, assigned_to)`:
    the refresh-once-on-`unknown_member` resolution extracted out of
    `_create_calendar_event` (which now calls it too, with no behaviour
    change — same two calls to `resolve_members`, same refresh condition).
  - `date` (the parameter) shadows the module-level `datetime.date` import
    inside `_set_calendar_event_attendees`; resolved by adding a plain
    `import datetime` alongside the existing `from datetime import date`, and
    calling `datetime.date.fromisoformat(date)` in that one method, so
    `_get_week_overview`'s existing `date.fromisoformat`/`date.today()` calls
    stay untouched.
  - New tool registration: `read_only_hint=False`, `destructive_hint=False`,
    matching decision 1.
- `tests/unit/test_calendar_adapter.py`: new `TestBuildUpdateAttendeesFields`
  (E1: everyone, named, empty-list rejection).
- `tests/unit/test_event_attendees.py` (new): the full service-layer suite —
  `ScriptedTransport` here is a strict **ordered script** of
  `(endpoint, response)` pairs (not a per-endpoint dict, since
  `evtlistinterval` is called twice with two different payloads — before and
  after the write) that raises on any extra or out-of-order call, so a bug
  that adds a retry or an extra write fails the test immediately.
- `tests/unit/test_tools.py`:
  - `FakeSessionPool.call` gains an `evtupdate` branch: mutates the matching
    `created_events` form's `isToAll`/`attendee.N.accountId` keys in place
    (dropping any stale higher-index keys first), so the next
    `evtlistinterval` reads back the update through the existing
    `_stored_event` machinery.
  - `_stored_event` gains `"editable": "true"` (previously absent, which
    would have made every `FakeSessionPool`-created event fail decision 4's
    `editable is True` gate; `_readback_mismatches`, used by
    `create_calendar_event`'s own confirmation, never checked `editable`, so
    this addition doesn't affect any existing assertion).
  - New `_existing_event_form()` helper (an evtcreate-shaped form
    pre-appended to `pool.created_events` as `"event/1"`, for tests that
    exercise `set_calendar_event_attendees` without first creating an event
    through the tool).
  - New tests: `test_e2_set_calendar_event_attendees_is_blocked_by_the_write_gate`,
    `test_e3_invalid_date_gives_zero_calls`,
    `test_set_calendar_event_attendees_confirms_and_sends_no_account_id` (a
    happy-path sanity test, unnumbered),
    `test_e13_set_calendar_event_attendees_response_has_no_account_id`,
    `test_e11_unknown_member_found_after_one_refresh_for_set_attendees`,
    `test_e11_ambiguous_name_never_refreshes_for_set_attendees`,
    `test_set_calendar_event_attendees_is_registered_as_a_write_tool`
    (unnumbered, mirrors the existing `create_calendar_event` registration
    test).
- `README.md`: tool count (eight → nine), new table row, a rewritten writes-off
  bullet listing all four write tools, and a new bullet describing
  `set_calendar_event_attendees`'s scope and safety gates.
- `AGENTS.md`: tool-count sentence updated (nine tools; `list_family_members`
  and `set_calendar_event_attendees` are unit-tested but not yet
  live-verified).
- `docs/architecture.md`: one new table row for `set_calendar_event_attendees`
  in the initial tool contract table.
- `docs/PROGRESS.md`: one new row for slice E.
- `docs/briefs/09-implementation-plan.md`: slice E marked done in the "where
  brief 09 stands" table, the slice-order table, and its own section
  (checkboxes ticked, with one wording correction — see Deviation below).
- `docs/handoffs/09e-set-event-attendees.md`: this file.

## Deviation from the brief

None from `docs/briefs/09e-set-event-attendees.md` itself — all nine binding
decisions and all thirteen required tests (E1–E13) are implemented as
specified.

One pre-existing imprecision, corrected in place in
`docs/briefs/09-implementation-plan.md` (the higher-level planning doc, not
the binding `09e` brief), the same way slice C corrected its own planning
text: that document's original Slice E acceptance bullet said "one zero-call
refusal test for each unsafe target." Binding decision 4 (and required test
E4) are explicit that a refusal happens *after* the lookup read — it is
zero-**write**, not zero-**call**. The bullet now says so, with a note
pointing at this handoff.

## Acceptance evidence (E1–E13)

- **E1** Builder: the complete everyone and named forms (exactly the six
  field kinds), plus the empty-list rejection:
  `tests/unit/test_calendar_adapter.py::TestBuildUpdateAttendeesFields`
  (`test_e1_complete_form_for_everyone`,
  `test_e1_complete_form_for_named_members`,
  `test_e1_builder_rejects_empty_attendees`); the sent-form shape is also
  asserted at the service layer in
  `tests/unit/test_event_attendees.py::TestConfirmation::test_e1_write_sends_exactly_the_six_field_kinds`.
- **E2** Tool: writes disabled gives zero calls:
  `tests/unit/test_tools.py::test_e2_set_calendar_event_attendees_is_blocked_by_the_write_gate`.
- **E3** Invalid date gives zero calls; unknown event ID gives
  `event_not_found` after exactly one read and no write:
  `tests/unit/test_tools.py::test_e3_invalid_date_gives_zero_calls`;
  `tests/unit/test_event_attendees.py::TestLookupAndRefusal::test_e3_unknown_event_id_gives_event_not_found`.
- **E4** Each refusal case (not the family calendar, `editable` false,
  `editable` `None`, recurring, series exception, `eventType`
  `BIRTHDAY_ACCOUNT`, all-day) gives `unsupported_event`, only the lookup
  read, no write and no receipt (parametrized, seven cases):
  `TestLookupAndRefusal::test_e4_each_refusal_case_makes_zero_writes_and_no_receipt`.
- **E5** Confirmed: attendees changed as requested, everything else
  unchanged; endpoint order `evtlistinterval`, `evtupdate`,
  `evtlistinterval`:
  `TestConfirmation::test_e5_confirmed_when_only_attendees_change_in_the_right_order`.
- **E6** A non-attendee field changing on readback (`text`/title, `where`,
  `description`, or an emptied `reminderList`) gives `mismatched`, naming
  that field (parametrized, four cases):
  `TestConfirmation::test_e6_a_non_attendee_field_changing_is_mismatched`.
- **E7** An attendee mismatch (missing ID; everyone requested but
  `toAll:"false"`) gives `mismatched` with `"attendees"` (parametrized, two
  cases): `TestConfirmation::test_e7_attendee_mismatch_cases`.
- **E8** A lost write gives `unknown`, with exactly one `evtupdate` and no
  retry:
  `tests/unit/test_event_attendees.py::TestAcknowledgedAndUnknown::test_e8_lost_write_is_unknown_and_never_retried`.
- **E9** A refused write records `rejected`; a replay raises the same error
  code with zero calls:
  `tests/unit/test_event_attendees.py::TestReceipts::test_e9_refused_write_records_rejected_and_replays_the_same_error`.
- **E10** The receipt carries `resource_id=event_id` and
  `action="calendar.set_attendees"`; a replay returns the stored result with
  zero calls; the same key with a different `assigned_to`, or a key used by
  another action, is `operation_id_conflict`:
  `TestReceipts::test_e10_receipt_fields_and_zero_call_replay`,
  `test_e10_same_key_different_assigned_to_conflicts`,
  `test_e10_key_used_by_another_action_conflicts`.
- **E11** An unknown name refreshes discovery once, then succeeds; an
  ambiguous name gives no refresh and zero calls:
  `tests/unit/test_tools.py::test_e11_unknown_member_found_after_one_refresh_for_set_attendees`,
  `test_e11_ambiguous_name_never_refreshes_for_set_attendees`.
- **E12** The lookup window is the member's local day and is DST-correct
  (`Australia/Sydney` on 2026-10-04 is a 23-hour day):
  `tests/unit/test_event_attendees.py::TestLookupAndRefusal::test_e12_lookup_window_is_the_local_day_and_dst_correct`.
- **E13** The serialised response contains no synthetic account ID:
  `tests/unit/test_tools.py::test_e13_set_calendar_event_attendees_response_has_no_account_id`.

## Everyone-mode and named-mode `evtupdate` forms (from E1, synthetic IDs)

Everyone (`to_all=True`, IDs
`("acct-synthetic-1", "acct-synthetic-2", "acct-synthetic-3")`):

```python
{
    "partnerScope": "Family",
    "option": "All",
    "calendarId": "calendar/family-123",
    "metaId": "event/existing-1",
    "isToAll": "true",
    "attendee.0.accountId": "acct-synthetic-1",
    "attendee.1.accountId": "acct-synthetic-2",
    "attendee.2.accountId": "acct-synthetic-3",
}
```

Named (`to_all=False`, IDs `("acct-synthetic-2", "acct-synthetic-1")`, in that order):

```python
{
    "partnerScope": "Family",
    "option": "All",
    "calendarId": "calendar/family-123",
    "metaId": "event/existing-1",
    "isToAll": "false",
    "attendee.0.accountId": "acct-synthetic-2",
    "attendee.1.accountId": "acct-synthetic-1",
}
```

## Validation

- `uv sync --frozen --group dev`: ok (already synced in this worktree).
- `uv run ruff check .`: All checks passed.
- `uv run ruff format --check .`: 116 files already formatted.
- `uv run mypy src`: Success, no issues found in 30 source files.
- `uv run pytest -m 'not live'`: **524 passed** (488 at the end of slice C;
  36 net new test cases: 3 builder, 26 service-layer, 7 tool-layer).
- `uv build`: both sdist and wheel built successfully.
- `scripts/check`: every step above passes; the final detect-secrets step
  fails, but only on pre-existing drift unrelated to this slice — `git status`
  and `git diff --stat .secrets.baseline` both show no change to the baseline
  file before or after running the script. This matches the brief's
  expectation and PR #8's separate tracking; the baseline was left untouched.

## Security and privacy review

- All test data is synthetic: neutral placeholder names (`Alex`, `Robin`,
  `Sam`, `Jordan`, `Jordan Lee`, plus the pre-existing `Test Member`/`Other
  Member`), fake account IDs (`acct-synthetic-*`, `acct-self`, `acct-robin`,
  `acct-alex`, `acct/1`, etc.), and fake family/calendar/event IDs. No real
  person's name is used anywhere in this diff.
- No `.env*` file or credential store was read. No network calls were made;
  every test uses in-memory fakes (the new `ScriptedTransport` in
  `test_event_attendees.py`, `FakeSessionPool` and its subclasses in
  `test_tools.py`) or synthetic wire payloads.
- No environment variables were printed.
- Names never reach the wire: `build_update_attendees_fields` only accepts
  already-resolved account IDs, and `SetCalendarEventAttendeesResponse`
  reports names only, never an account ID — asserted directly in E1 (builder)
  and E13 (tool response serialisation).

## Known limitations

- Not yet live-verified. This slice is offline only (synthetic fixtures, no
  live network), per the brief; the lead's live check is slice G, next.
- All-day event *updates* stay unsupported, as decided (probe A1 found no
  write-side `allDay` field to safely target); an all-day event is refused
  by the same safety gate that refuses a recurring or special-calendar event.
- List assignment (`set_list_item_assignees`, slice F) is still not
  implemented; this slice only covers the calendar event attendees tool.
- The refresh-on-`unknown_member` path (shared with `create_calendar_event`
  via the new `_resolve_assignment` helper) is exercised only against
  synthetic discovery payloads, as before.
- The confirmation compares the reread event against the event this method
  first read (`before`), not against a caller-supplied "expected" event —
  this is what the brief specifies (decision 8), but it does mean a
  concurrent edit to a non-attendee field by someone else, landing between
  the lookup and the confirmation reread, would correctly report
  `mismatched` even though this tool's own write only touched attendees.

## Next bounded task

The lead's live check (slice G): one `set_calendar_event_attendees` call
against a real, disposable event in both everyone and named modes, followed
by the documented updates slice G's own acceptance criteria call for (README,
PROGRESS, architecture, compatibility, `AGENTS.md`'s tool count) — most of
which this handoff already updated for slice E specifically. Then slice F
(list assignment).
