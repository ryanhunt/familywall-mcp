# Task 09C — Attendees and the default reminder on `create_calendar_event`

Slice C of the [brief 09 implementation plan](09-implementation-plan.md). It builds on
slice B (resolver, read models) and slice D (receipts). Offline only: synthetic
fixtures, no live network, no credentials.

## Objective

`create_calendar_event` takes `assigned_to` (member names). Blank means everyone;
names mean exactly those members. Events get the web app's default 30-minute
reminder. Both are confirmed by the existing exact readback.

## Scope

- Files allowed to change:
  - `src/familywall_mcp/familywall/calendar.py` (`build_create_event_fields`; a reminder
    field on `CalendarEvent` and its parser)
  - `src/familywall_mcp/services/calendar.py` (`create_event`, `_readback_mismatches`)
  - `src/familywall_mcp/services/principal_context.py` (a `refresh()` on the resolvers)
  - `src/familywall_mcp/server.py` (only to pass the session pool to `FixedContextResolver`)
  - `src/familywall_mcp/tools/registry.py` (`_create_calendar_event`, its response model and
    description)
  - tests: `tests/unit/test_calendar_adapter.py`, `tests/unit/test_calendar_create.py`,
    `tests/unit/test_tools.py`, a new `tests/unit/test_principal_context.py` if useful,
    `tests/support/*` (synthetic only)
  - docs: `README.md` (the `create_calendar_event` text), `docs/contracts/calendar.md`
    (one paragraph saying which encodings the tool now sends), `docs/PROGRESS.md` (one row),
    `docs/briefs/09-implementation-plan.md` (mark C done), new
    `docs/handoffs/09c-calendar-attendees.md`
- Out of scope: `set_calendar_event_attendees` (slice E), list assignment (slice F),
  all-day or recurring events, a caller-chosen reminder, and live verification (the lead
  runs that separately).

## Evidence (live-verified 2026-09-25; `docs/contracts/calendar.md#mutations`)

- **Everyone:** the web app sends `isToAll=true` **plus** `attendee.N.accountId` for every
  member. Readback: `toAll:"true"` and `attendeeIds: []`.
- **Named members:** `isToAll=false` plus `attendee.0..N-1.accountId`. Readback: those IDs,
  in order.
- **Default reminder (timed events):** the web app sends `reminderList.0.reminderType=SNOOZE`,
  `reminderList.0.reminderUnit=MINUTE` and `reminderList.0.reminderValue=30`. It reads back
  as `reminderList: [{localId, reminderType, reminderUnit, reminderValue}]`.

## Binding decisions

1. **Tool argument.** `assigned_to: list[str] | None = None`. `None` or `[]` means everyone
   (owner decision 1). **This changes today's default**, which assigns only the signed-in
   member; say so in the tool description and README. Resolve with
   `services.members.resolve_members` against `ctx.discovered_family` **before** any receipt
   or write. A resolution error returns an `ErrorResponse` carrying the resolver's code, and
   makes no write.
2. **Refresh on an unknown name, once.** Add `async def refresh(self) -> tuple[Principal, PrincipalContext]`
   to the `ContextResolver` protocol.
   - `HostedContextResolver.refresh()`: `invalidate(subject)`, then `resolve()`.
   - `FixedContextResolver` takes an optional `session_pool: SessionPool | None = None`
     (and `server.py` passes it). Its `refresh()` rebuilds the context with
     `build_principal_context(principal, session_pool)`, stores it and returns it. Without a
     pool, it returns the current pair.
   - The tool calls `refresh()` **only** when resolution fails with `unknown_member`, then
     resolves exactly once more. Never refresh on `ambiguous_member` or `invalid_member_name`.
     At most one refresh per call.
3. **Wire builder.** `build_create_event_fields` replaces `attendee_account_id: str` with
   `to_all: bool` and `attendee_account_ids: Sequence[str]` (non-empty; the adapter layer must
   not import `services`). It emits `isToAll` (`"true"`/`"false"`) and `attendee.N.accountId`
   for each ID in the given order. For everyone, the service passes `to_all=True` with **every**
   member's ID in discovery order, matching the web app. It also replaces `reminderList=$empty`
   with the three `reminderList.0.*` fields above (owner decision 4). Every other field is
   unchanged. The full-form tests assert the complete dictionary.
4. **Service.** `create_event(..., assignment: ResolvedAssignment)` passes the assignment
   through. The payload hash is unchanged in construction and now naturally covers attendees
   and the reminder. Receipts and replay (slice D) are unchanged.
5. **Reminder read model.** `CalendarEvent` gains
   `reminders: tuple[EventReminder, ...] | None = None`, where
   `EventReminder(type: str, unit: str, value: str)` holds the verbatim strings from
   `reminderType`/`reminderUnit`/`reminderValue`. An absent `reminderList` gives `None`, and
   `[]` gives `()`. A **malformed** `reminderList` gives `None` and does **not** skip the event:
   reminders are not needed for reads, and dropping events over them would be a regression.
6. **Readback confirmation.** `_readback_mismatches` adds:
   - `"attendees"`: for everyone, the event's `to_all` must be `True`. For named members,
     `to_all` must be `False` **and** the set of `attendee_ids` must equal the requested set
     (order-insensitive).
   - `"reminder"`: the event's `reminders` must equal exactly one `EventReminder("SNOOZE", "MINUTE", "30")`.
   A difference is `mismatched`, like the existing fields.
7. **Response.** `CreateCalendarEventResponse.assigned_to` becomes `tuple[str, ...]` of display
   names (every member's name for everyone), and it gains `assigned_to_everyone: bool`. No
   account ID appears in any response. Update the tool description to explain `assigned_to`
   ("names as `list_family_members` shows them; omit for everyone") and the default reminder.

## Required tests (each must exist and pass; put the C-number in the name or docstring)

- **C1** Builder, everyone: the complete form dictionary, with `isToAll="true"`,
  `attendee.0..N-1` in order, and the three reminder fields.
- **C2** Builder, named: the complete form, with `isToAll="false"` and the given IDs in order.
- **C3** Builder: rejects an empty `attendee_account_ids`, and no display name ever appears in
  the form.
- **C4** Service, everyone: the everyone form is sent, and a readback with `toAll:"true"`, empty
  `attendeeIds` and the reminder is `confirmed`.
- **C5** Service, named: the named form is sent, and a readback with the same IDs in a
  **different order** is `confirmed`.
- **C6** Readback `attendees` mismatch in three cases: a missing ID, an extra ID, and everyone
  requested but `toAll:"false"`.
- **C7** A readback with no reminder, or a different one, is `mismatched` with `"reminder"`.
- **C8** Tool, unknown name with no refresh available (stdio, no pool): `unknown_member`,
  with zero upstream calls.
- **C9** Tool, an unknown name that the refresh does find: one refresh, then success; the
  second lookup uses the refreshed family.
- **C10** Tool, ambiguous or invalid name: no refresh, zero calls.
- **C11** The same idempotency key with a different `assigned_to` is `operation_id_conflict`,
  with no second write.
- **C12** Tool response: `assigned_to` names and `assigned_to_everyone` are correct for both
  modes, and no synthetic account ID appears in the serialised response.
- **C13** `HostedContextResolver.refresh()` invalidates, then rebuilds (a fake discovery is
  called twice). `FixedContextResolver.refresh()` with and without a pool.
- **C14** `CalendarEvent.reminders`: a present list parses verbatim; absent gives `None`; `[]`
  gives `()`; a malformed list gives `None` and the event is **not** skipped.
- **C15** Tests written for the old self-only default are updated to the new default, and one
  test proves that naming only the signed-in member still sends the single-attendee form that
  PR #9 verified live.

## Validation

Run from this worktree: `uv sync --frozen --group dev`, `uv run ruff check .`,
`uv run ruff format --check .`, `uv run mypy src`, `uv run pytest -m 'not live'`, `uv build`.
`scripts/check` may fail only at its detect-secrets step, on findings that exist before this
slice (fixed separately in PR #8); report that rather than editing `.secrets.baseline`.

## Security and privacy

Use **neutral synthetic names only** (for example Alex, Robin, Sam, Jordan). Never use a real
person's name. Never read `.env*` files or credential stores; never make network calls. Names
never reach the wire; only resolved account IDs do.

## Handoff target

The lead's live check of both modes, then slice E (`set_calendar_event_attendees`).
