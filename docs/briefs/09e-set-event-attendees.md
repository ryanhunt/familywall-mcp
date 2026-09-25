# Task 09E — `set_calendar_event_attendees`

Slice E of the [brief 09 implementation plan](09-implementation-plan.md). It builds on
slices B (resolver), D (receipts) and C (encodings, refresh-once resolution). Offline
only: synthetic fixtures, no live network, no credentials.

## Objective

A narrow write tool that changes **only** who an existing ordinary, one-off,
timed family-calendar event is assigned to. Blank `assigned_to` means everyone. It
refuses anything unsafe before writing, writes once, and confirms from an exact
readback that the attendees changed **and nothing else did**.

## Scope

- Files allowed to change:
  - `src/familywall_mcp/familywall/calendar.py` (a new `build_update_attendees_fields`)
  - `src/familywall_mcp/services/calendar.py` (a new `set_event_attendees` plus helpers)
  - `src/familywall_mcp/models.py` (add `"calendar.set_attendees"` to the `action` Literal only)
  - `src/familywall_mcp/tools/registry.py` (the new tool and its response model; extract the
    refresh-once resolution already in `_create_calendar_event` into a shared helper so both
    tools use it)
  - tests: `tests/unit/test_calendar_adapter.py`, a new `tests/unit/test_event_attendees.py`,
    `tests/unit/test_tools.py`, `tests/support/*` (synthetic only)
  - docs: `README.md` (tool table, count and a behaviour note), `AGENTS.md` (the tool-count
    sentence), `docs/architecture.md` (one table row), `docs/PROGRESS.md` (one row),
    `docs/briefs/09-implementation-plan.md` (mark E done), new
    `docs/handoffs/09e-set-event-attendees.md`
- Out of scope: editing any other event field, all-day or recurring events, deleting events,
  list assignment (slice F), and live verification (the lead does it).

## Evidence (live-verified 2026-09-25; `docs/contracts/calendar.md#mutations`)

- **`evtupdate` patches.** A request carrying only `partnerScope`, `option=All`, `calendarId`,
  `metaId`, `isToAll` and `attendee.N.accountId` changed the attendees and left every other
  field (title, instants, zone, reminder, privacy, recurrence) unchanged. The attendee set is
  replaced, not merged.
- **Response:** the full updated event object.
- **Readback encodings:** everyone reads back as `toAll:"true"` with no `attendeeIds`; named
  members as `toAll:"false"` with those IDs.
- **Not observed:** whether an unsent non-empty `where`/`description` survives (the readback
  comparison below covers it), and any all-day or recurring update. Refuse those.

## Binding decisions

1. **Tool.** `set_calendar_event_attendees(event_id: str, date: str, assigned_to: list[str] | None = None, idempotency_key: str | None = None)`.
   - `event_id` is the `occurrence_id` that `get_week_overview` returns.
   - `date` is the event's local date (`YYYY-MM-DD`) in the member's timezone.
   - Blank `assigned_to` means everyone.
   - Annotations: `read_only_hint=False`, `destructive_hint=False`. Behind the write gate:
     zero upstream calls when writes are disabled.
2. **Resolution.** Use the same refresh-once-on-`unknown_member` resolution as
   `create_calendar_event`, via one shared helper in `registry.py`, **before** any lookup,
   receipt or write.
3. **Lookup.** Fetch the local day with `evtlistinterval` over
   `resolve_days(date, 1, member_tz)` and find `occurrence_id == event_id`. Not found gives an
   `event_not_found` error with zero writes. An invalid `date` gives an `invalid_request` error
   with zero calls.
4. **Refuse before writing** with `unsupported_event` (a static message) if the event:
   - has a `calendar_id` other than the family calendar;
   - has `editable` other than exactly `True` (so `None` is refused too);
   - has `is_recurring` or `is_series_exception`;
   - has an `event_type` other than `"UNKNOWN"`;
   - has an `AllDaySpan`.
   Refusals make zero writes and write no receipt.
5. **Wire builder.** `build_update_attendees_fields(*, event_id, calendar_id, to_all, attendee_account_ids)`
   returns **exactly** `partnerScope=Family`, `option=All`, `calendarId`, `metaId=event_id`,
   `isToAll` (`"true"`/`"false"`) and `attendee.N.accountId` in the given order. It rejects an
   empty ID list. Nothing else is sent; no other field is rebuilt. For everyone, pass every
   member's ID in discovery order, as in slice C.
6. **Write once.** Send `evtupdate` at most once, never retried. Indeterminate errors (the
   `_INDETERMINATE_ERRORS` set) give `unknown`. `UpstreamRejectedError`/`AuthenticationError`
   are recorded as `rejected` with the error JSON, then re-raised, exactly as `create_event`
   does.
7. **Receipts.** `resource_id=event_id` and `action="calendar.set_attendees"`. The payload hash
   uses `_hash_payload("evtupdate", calendar_id, fields)`. Pending, acknowledged and final
   receipts follow `create_event`'s pattern, including the action check and `rejected` replay.
8. **Confirmation.** Re-read the same day window. With `before` the event from the lookup and
   `after` the event with the same ID:
   - `after` missing gives `acknowledged`;
   - `"attendees"` mismatch uses slice C's rule;
   - **every other comparable field must equal `before`**: title, span, timezone, location,
     description, recurrence flags, calendar and reminders. Each difference is named in
     `mismatched_fields`, and any mismatch gives `mismatched`;
   - otherwise `confirmed`.
9. **Response.** `family_name`, `outcome`, `event_id`, `assigned_to` (display names; every member
   for everyone), `assigned_to_everyone`, and `mismatched_fields` (empty unless `mismatched`).
   No account IDs.

## Required tests (each must exist and pass; put the E-number in the name or docstring)

- **E1** Builder: the complete everyone and named forms, exactly the six field kinds, plus the
  empty-list rejection.
- **E2** Tool: writes disabled gives zero calls.
- **E3** An invalid date gives zero calls; an unknown event ID gives `event_not_found` after
  exactly one read and no write.
- **E4** Each refusal case (not the family calendar, `editable` false, `editable` `None`,
  recurring, series exception, `eventType` `BIRTHDAY_ACCOUNT`, all-day) gives
  `unsupported_event`, only the lookup read, no write and no receipt.
- **E5** Confirmed: the attendees changed as requested and every other field is unchanged. The
  endpoint order is `evtlistinterval`, then `evtupdate`, then `evtlistinterval`.
- **E6** A non-attendee field changing on readback (for example `description` cleared or
  `reminderList` dropped) gives `mismatched`, naming that field.
- **E7** An attendee mismatch (missing ID; everyone requested but `toAll:"false"`) gives
  `mismatched` with `"attendees"`.
- **E8** A lost write gives `unknown`, with exactly one `evtupdate` and no retry.
- **E9** A refused write records `rejected`, and a replay raises the same error code with zero
  calls.
- **E10** The receipt carries `resource_id=event_id` and `action="calendar.set_attendees"`. A
  replay returns the stored result with zero calls. The same key with a different
  `assigned_to`, or a key used by another action, is `operation_id_conflict`.
- **E11** An unknown name refreshes discovery once, then succeeds or fails; an ambiguous name
  gives no refresh and zero calls.
- **E12** The lookup window is the member's local day and is DST-correct (for example
  `Australia/Sydney` on 2026-10-04 is a 23-hour day).
- **E13** The serialised response contains no synthetic account ID.

## Validation

Run from this worktree: `uv sync --frozen --group dev`, `uv run ruff check .`,
`uv run ruff format --check .`, `uv run mypy src`, `uv run pytest -m 'not live'`, `uv build`.
`scripts/check` may fail only at its detect-secrets step, on pre-existing findings (fixed
separately in PR #8); report that rather than editing `.secrets.baseline`.

## Security and privacy

Use **neutral synthetic names only** (for example Alex, Robin, Sam, Jordan), never a real
person's name. Never read `.env*` files or credential stores, and never make network calls.
Names never reach the wire.

## Handoff target

The lead's live check (slice G), then merge.
