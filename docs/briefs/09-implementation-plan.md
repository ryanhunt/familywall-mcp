# Task 09 — Implementation plan for member assignment

This turns [brief 09](09-member-assignment.md) into ordered, independently
reviewable slices. It adds no runtime behaviour. Each slice lists its evidence
gate, file boundary, acceptance criteria and who should implement it under the
[AGENTS.md](../../AGENTS.md) working agreement.

## Where brief 09 stands

| Brief step | State | Where |
| --- | --- | --- |
| 1. Wire contracts | **Partly done.** Calendar create with one attendee (`isToAll=false`, `attendee.0.accountId`) is live-verified, and so is `evtdelete` of a non-recurring event. Nothing about list `assignee`, `evtupdate`, all-members or multiple attendees has been observed | [calendar contract](../contracts/calendar.md#mutations) |
| 2. Member resolver | Not started | — |
| 3. Read models | Not started. Live reads already show `attendeeIds`, `attendees`, `toAll` and `editable` on events, and `assignee`, `assigneeIds` and `toAll` on tasks | contracts |
| 4. List assignment | Not started | — |
| 5. Generalized receipts | Not started. `create_calendar_event` reuses `OperationReceipt` with the calendar ID in `list_id`, and tags its payload hash with the endpoint so a key reused across tools conflicts | [handoff 10](../handoffs/10-create-calendar-event.md) |
| 6. Calendar attendees | **Create, self-only, done** (`create_calendar_event`). Attendee selection and `set_calendar_event_attendees` are not started | PR #9 |

## Decisions (settled by the account owner, 2026-09-25)

1. **A blank `assigned_to` means everyone.** Every assigning tool takes
   `assigned_to: list[str] | None`. Omitted, `null` and `[]` all mean every
   family member, sent with the everyone encoding A1/A2 verify. Named members
   are sent as the resolved account IDs. To assign only the signed-in member,
   the caller names them; `list_family_members` shows which member is "you".
   There is no separate flag and no magic name such as `"everyone"`.
   - **This changes `create_calendar_event`.** It shipped (PR #9) assigning
     omitted events to the signed-in member only, because that was the only
     encoding verified. Slice C switches its default to everyone, but only
     once A1 has verified the everyone encoding. Until then the self-only
     default stays, since sending an unverified encoding is worse than a
     narrower default.
   - For tasks, if A2 shows `taskcreate` without `assignee` already gives
     everyone, omitting it is enough. Otherwise the verified everyone
     encoding is sent explicitly.
2. **The receipt migration is accepted.** Deployment is Docker, and an older
   image not reading a migrated database is acceptable. Slice D still records
   a `PRAGMA user_version` and adds a one-line "back up the data volume before
   upgrading" note to the deploy docs.
3. **Live probe sessions.** The lead tells the account owner when A1 or A2 is
   ready, and they sign in to the FamilyWall web app in the in-app browser.
   Agents never type the password. Both probes write only disposable events and
   tasks, and delete them by exact ID afterwards.

## Slice order

| Slice | Delivers | Needs | Can start |
| --- | --- | --- | --- |
| A1 | Calendar attendee and `evtupdate` evidence | an owner sign-in | when the owner is available |
| A2 | List `assignee` and `taskupdate` evidence | an owner sign-in | when the owner is available |
| B | Member resolver, `list_family_members`, assignment read models | — | now (offline) |
| D | Generalized receipts and migration | — | now (offline) |
| C | Attendees on `create_calendar_event`, default everyone | A1, B | after A1 and B |
| E | `set_calendar_event_attendees` | A1, B, D | after those |
| F | List assignment | A2, B (D if `taskupdate` is used) | after those |
| G | Live acceptance and docs | each slice | per slice |

Each slice is one PR. B and D can run in parallel with the probes.

---

## Slice A1 — Calendar attendee and update probe

**Implementer:** lead only. Live, run when the owner signs in (decision 3).

**Objective:** record the exact wire encodings that C and E will send, before
any code depends on them.

Method: capture the FamilyWall web app's own `evtcreate` and `evtupdate` form
fields in the browser's network log, then replay each form with a one-off
script (not committed) and read back. Record key names, value shapes and counts
only. Never record names, account IDs, event IDs or content.

Questions, each with a pass/fail entry in `docs/compatibility.md`:

1. Everyone: is it `isToAll=true` alone, or `isToAll=true` plus every
   `attendee.N.accountId`? What do the readback's `toAll` and `attendeeIds`
   show?
2. Two named members: are the attendees indexed `attendee.0` and `attendee.1`?
   Does the order hold on readback?
3. `evtupdate` on a non-recurring event, changing attendees only: does the web
   form resend every field? If `where` or `description` is omitted, is it
   cleared (replace) or kept (patch)? Is `metaId` the occurrence ID?
4. A daylight-saving instant (`+11:00`) through `create_calendar_event`. This
   closes the only open item from handoff 10.
5. `evtupdate` on an all-day event: is there a write-side `allDay` field? If
   not, E refuses all-day events.

Clean up by `evtdelete` of each exact non-recurring ID; that call is already
verified. Recurring events are never created.

**Acceptance:**
- [ ] `docs/contracts/calendar.md` records each answer as live-verified or
  pending.
- [ ] Every encoding that C and E rely on is live-verified, or that capability
  is marked out of scope.
- [ ] The probe account is left with no disposable events.

## Slice A2 — List assignment probe

**Implementer:** lead only. Live and opt-in.

Same method, for `taskcreate` and `taskupdate`:

1. What does `taskcreate` without `assignee` produce (`toAll`, `assigneeIds`)?
   This is the default that omission preserves.
2. How is `assignee` encoded for one member, two members and everyone? The web
   bundle declares a bare `assignee` parameter.
3. Does `taskmove` keep the assignment? This decides whether F can assign at
   create time or must update after the move.
4. **`taskupdate` semantics, the highest-risk question.** It declares
   `text, dueDate, assignee, reminder` but not `description`. Reads return
   `reminder` but no `dueDate`. If `taskupdate` replaces rather than patches,
   changing an assignee could silently clear a due date or description that
   this server cannot even see. Test on a disposable task with a due date,
   reminder and description set in the web app.

Clean up by `taskdelete`, which is already verified.

**Acceptance:**
- [ ] `docs/contracts/familywall.md` records each answer.
- [ ] A go/no-go is recorded for `set_list_item_assignees`. If `taskupdate`
  clears fields that cannot be read back, the tool is **not built**, and F ships
  create-time assignment only.

## Slice B — Member resolver, `list_family_members`, assignment read models

**Implementer:** cheap agent. Lead reviews name resolution and tenant isolation.
**Offline** (synthetic fixtures only).

Files: new `services/members.py`; `familywall/calendar.py` and
`familywall/lists.py` (parsing only); `tools/registry.py`; their tests.

- `ResolvedAssignment` (frozen): `to_all: bool`, `account_ids: tuple[str, ...]`,
  `display_names: tuple[str, ...]`.
- `resolve_members(names, family)`:
  - `None` or `[]` resolves to `to_all=True` with every member's account ID
    (decision 1). The wire builder decides which of those fields the verified
    everyone encoding actually sends.
  - Normalise with NFKC, casefold and collapse whitespace.
  - An exact full display name wins; otherwise accept an exact first name only
    if it is unique.
  - De-duplicate by account ID, preserving order.
  - An unknown or ambiguous name raises a safe error naming the candidates by
    display name only.
  - No fuzzy or substring matching.
- In hosted mode, discovery is cached per subject, so a new member would look
  unknown. On an unknown name, refresh discovery once (a read) before failing.
- A new read-only tool, `list_family_members`, returns display names, first
  names and which one is you. It never returns account IDs.
- Read models:
  - `CalendarEvent` gains `attendee_ids`, `to_all` and `editable`.
  - `ListItem` gains `assignee_ids` and `to_all`.
  - Both follow the existing malformed-item policy.
  - `get_week_overview` and `get_list_items` map IDs back to names and report
    `unresolved_members: int`, never raw IDs.

**Acceptance:**
- [ ] Tests cover normalisation, full-name precedence, unique and ambiguous
  first names, duplicates, unknown names, `None` and `[]` meaning everyone, and
  a name from another family never resolving.
- [ ] No tool schema accepts or returns an account ID.
- [ ] Existing read outputs gain fields without losing any.

## Slice C — Attendees on `create_calendar_event`

**Implementer:** cheap agent from this card after A1 and B land. Lead reviews
the confirmation logic.

Files: `familywall/calendar.py` (builder), `services/calendar.py`,
`tools/registry.py`, tests.

- The tool gains `assigned_to` (decision 1). Blank means everyone; names mean
  exactly those members. Names are resolved before any receipt or write.
  Invalid input makes zero upstream calls.
- **The default changes from the signed-in member to everyone.** The tool
  description and README say so. If A1 did not verify an everyone encoding,
  this slice keeps the self-only default and goes back to the owner rather
  than guessing.
- `build_create_event_fields` takes a `ResolvedAssignment` and emits exactly the
  A1-verified encoding. Everyone mode is built only if A1 verified it.
- Confirmation extends to the attendee set (order-insensitive) and `toAll`. A
  difference is `mismatched`, with an `attendees` field name.
- The payload hash already covers the attendee fields, so replay and conflict
  behaviour carries over unchanged.
- The response's `assigned_to` becomes a list of display names, and it gains
  `assigned_to_everyone: bool`.

**Acceptance:**
- [ ] Full-form tests for self, two named members and everyone. Display names
  never appear in the form.
- [ ] Readback with a missing or extra attendee is `mismatched`.
- [ ] Omitting `assigned_to`, or passing `[]`, sends the A1-verified everyone
  form.
- [ ] Naming only the signed-in member sends the single-attendee form that
  PR #9 verified.

## Slice D — Generalized receipts and migration

**Implementer:** cheap agent. **Lead reviews the migration** (decision 2).

Files: `models.py`, `interfaces.py`, `storage/memory.py`, `storage/sqlite.py`,
`services/lists.py`, `services/calendar.py`, and the receipt tests.

- `OperationReceipt`:
  - `list_id` becomes `resource_id`.
  - Add `action`, e.g. `list.add_item`, `list.set_checked` or
    `calendar.create_event`, with `legacy` for migrated rows.
  - Add the status `rejected`, a definite refusal that replays as the original
    error instead of `unknown`.
- SQLite migration:
  - It runs once, keyed on `PRAGMA user_version`.
  - The CHECK constraint changes, so this is a table rebuild in one transaction:
    create the new table, copy the rows, drop the old table, rename.
  - Idempotent on re-run; every existing row survives, and
    `(subject, operation_id)` isolation is kept.
- Hashing: calendar hashes keep their endpoint tag. **List hashes do not
  change**, because re-hashing would make receipts from the last 24 hours
  conflict on replay. `action` is what separates tools from now on.
- `create_calendar_event` writes `resource_id = calendar_id` and records
  `rejected` on a refusal.

**Acceptance:**
- [ ] A migration test from a fixture database in the current schema preserves
  every row and field. A second `initialise()` is a no-op.
- [ ] Replaying a legacy list receipt still returns its stored outcome.
- [ ] A refused create replays as the refusal, with no upstream call.
- [ ] README and NAS docs say to back up before upgrading.

## Slice E — `set_calendar_event_attendees`

**Implementer:** lead-directed cheap agent after A1, B and D. **Lead reviews the
safety gates.**

Files: `familywall/calendar.py` (update builder), `services/calendar.py`,
`tools/registry.py`, tests.

- Inputs:
  - `event_id`, plus the event's local date. The event is looked up in a
    bounded window around that date, never by an unbounded scan.
  - `assigned_to` (blank means everyone) and `idempotency_key`.
- Refuse before any write if the event:
  - is not found in the window,
  - is not in `calendar/{family_id}`,
  - has `editable` other than `"true"`,
  - is recurring or a series exception,
  - is not an ordinary event (`eventType` other than `UNKNOWN`), or
  - is all-day and A1 found no write-side `allDay` field.
- If A1 shows `evtupdate` replaces the event, rebuild every field from the event
  just read and change only the attendees. The readback must show every
  non-attendee field unchanged **and** the new attendees before the outcome is
  `confirmed`.
- One write, no retry. `resource_id` is the event ID.

**Acceptance:**
- [ ] One zero-call refusal test for each unsafe target.
- [ ] If any non-attendee field changed on readback, the outcome is
  `mismatched`, never `confirmed`.
- [ ] Tests cover replay, conflict and cross-subject isolation.

## Slice F — List assignment

**Implementer:** cheap agent after A2 and B. Lead reviews.

Files: `familywall/lists.py`, `services/lists.py`, `tools/registry.py`, tests.

- `add_list_item` gains `assigned_to` (blank means everyone).
  - If A2 shows `taskmove` keeps the assignment, send `assignee` on
    `taskcreate`.
  - Otherwise assign after the move, and report a failed assignment as its own
    outcome (e.g. `unassigned`) rather than folding it into `misfiled`.
  - `confirmed` needs the right list *and* the right assignment.
- `set_list_item_assignees` is built **only on an A2 go**. It verifies list
  membership first (as `set_list_item_checked` does), preserves every field
  `taskupdate` requires, writes once and reads back.

**Acceptance:**
- [ ] The resolved assignment is part of the payload hash, and the existing
  create-then-move tests still pass.
- [ ] Zero calls with writes disabled or with an invalid or ambiguous name.
- [ ] Tests cover readback mismatch, replay and conflict.

## Slice G — Live acceptance and documentation

**Implementer:** lead.

Run every new tool once against the live account, the same way handoff 10 did:
disposable data, then an exact-ID cleanup. Then update README, PROGRESS,
architecture, compatibility and the tool count in `AGENTS.md`, and write a
handoff per slice.

## Limitations that stay documented

- There is no fuzzy matching, and no caller-supplied account or calendar IDs.
- Recurring series and occurrences, birthdays and special calendars are never
  mutated.
- Any encoding the probes did not observe stays unsupported, even when the web
  bundle suggests it exists.
- Writes stay disabled by default.

## Validation for every slice

```text
uv sync --frozen --group dev
uv run ruff check .
uv run ruff format --check .
uv run mypy src
uv run pytest -m 'not live'
uv build
scripts/check
```

`scripts/check` also needs PR #8's detect-secrets fix before it can pass.
