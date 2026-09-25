# FamilyWall calendar contract

Status: **live-verified for reads, for event create (timed and all-day, with
everyone, named-member and single-attendee encodings), for attendee-only
`evtupdate`, and for `evtdelete` of non-recurring events** (2026-09-25; see
[Mutations](#mutations)). Produced by P0 task 00B on 2026-09-13 from
`familywall-api@c85bb152115d3c41ab90322ef9dce732122ff4c0`, then settled by the
02P read-only live probe on 2026-09-13 against a real family calendar
(47 events in a one-month window, 1115 in a three-year window).

**The two P0 blockers are resolved.** The server expands recurring occurrences,
and all-day events have an explicit flag. The conditional local-expansion branch
of P4 is therefore **not needed** and must not be built. Evidence levels are
defined in [the main contract](familywall.md).

## Live-verified summary (2026-09-13)

| Question | Answer |
| --- | --- |
| Does `evtlistinterval` expand recurrence? | **Yes.** Each occurrence is a separate object with its own `eventId`, plus `eventMasterId`, `occurenceIndex` and an iCal `rrule` |
| How is an occurrence identified? | `eventId` (== `metaId`) per occurrence; `eventMasterId` groups the series; `occurenceIndex` is its integer position |
| How is a cancelled occurrence represented? | It is **absent** from the expansion. The surviving occurrences carry `exdate` (excluded instants) and `recurrencyDeletedOccurence` (excluded indexes) for reference |
| How is all-day encoded? | `allDay` is the string `"true"`; `startDate` is `<date>T00:00:00.000Z` and `endDate` is `<date>T23:59:59.000Z` |
| Are interval bounds inclusive? | Half-open and overlap-based: an event is returned iff `startDate < a00to` **and** `endDate > a00from` |
| Are overlapping events returned? | **Yes** — a window strictly inside a longer event returns that event |
| Do non-event objects appear? | Yes. `eventType` was `UNKNOWN` (ordinary events) and `BIRTHDAY_ACCOUNT`, the latter from a different calendar, `calendarSpecialDays/<accountId>` |
| Is there a result cap? | None observed up to 1115 events / 1095 days. No continuation mechanism exists |
| Is `calendar/{family_id}` the real ID? | Yes — it is the `calendarId` the server stamps on ordinary events. But see the warning below |

### `calendarId` is not a filter — a correction with a security consequence

The probe sent `calendarId=calendar/000000`, a family that is not the caller's,
and also omitted `calendarId` entirely. **Both returned the caller's own events,
identical to the correct request.** The server ignores the parameter and scopes
the query to the session's family, exactly as the list endpoints do.

Two binding consequences:

1. `calendarId` must never be treated as an access boundary, and no tool may
   accept a caller-supplied calendar ID and imply it selects anything. It is sent
   for contract fidelity only.
2. A response may contain events from several of the family's calendars. If the
   port wants only the family calendar, it must filter **client-side** on each
   event's own `calendarId` field. V1 keeps everything, and reports each event's
   calendar, because a birthday is legitimately part of "what's on this week".

## Calendar identity

`calendar/{family_id}` is confirmed as the family calendar's own identifier: the
server stamps exactly that value on every ordinary event's `calendarId` field.
A second calendar, `calendarSpecialDays/<accountId>`, surfaced for a birthday
event. There is still no endpoint that enumerates a family's calendars — the set
is discovered only by observing what the interval query returns.

## `evtlistinterval` — bounded range query

The v1 read path. Evidence: `live-verified (2026-09-13)`.

| Field | Value | Note |
| --- | --- | --- |
| `partnerScope` | `Family` | required |
| `calendarId` | `calendar/{family_id}` | **ignored by the server**; sent for fidelity only |
| `a00from` | ISO instant with offset, or a bare `YYYY-MM-DD` | epoch milliseconds are rejected with `502 Cannot parse date` |
| `a00to` | as `a00from` | |

Response: `a00.r.r` is a **bare array**. The object-wrapped variants in the
reference client (`events`, `datas`, `updatedCreated`, `results`) were never
observed; keep tolerant parsing, but the array is the real shape.

### Event fields — observed

Union of keys across 47 events:

`accountId`, `allDay`, `attendeeIds`, `attendees`, `bestMoment`,
`birthDateNoYear`, `byDay`, `calendarId`, `clientOpId`, `color`, `colorInfo`,
`comments`, `description`, `editable`, `emoji`, `endDate`, `eventId`,
`eventMasterId`, `eventType`, `exdate`, `familyId`, `lastAction`,
`lastActionAuthor`, `lastActionDate`, `medias`, `metaId`, `modifDate`, `moodMap`,
`moodStarShortcut`, `occurenceIndex`, `private`, `recurrency`,
`recurrencyDeletedOccurence`, `recurrencyEndDate`, `recurrencyExceptionOfId`,
`recurrencyInterval`, `refPersonId`, `refPersonName`, `reminder`, `reminderList`,
`rrule`, `startDate`, `text`, `timeZone`, `toAll`, `where`.

The fields v1 depends on:

| Field | Type | Meaning |
| --- | --- | --- |
| `eventId` / `metaId` | string | **Occurrence** identity. Distinct per occurrence of a series |
| `eventMasterId` | string | Series identity. Equals `eventId` for non-recurring events |
| `occurenceIndex` | string integer | Position within the series; `"0"` for non-recurring |
| `text` | string | Title |
| `startDate` / `endDate` | `YYYY-MM-DDTHH:MM:SS.sssZ` | Always UTC-stamped. See all-day below |
| `allDay` | `"true"` / `"false"` | String, not boolean |
| `timeZone` | IANA string | The event's own zone. **Absent on some events** (e.g. birthdays) |
| `recurrency` | `NONE` / `WEEKLY` / … | Series frequency |
| `rrule` | iCal RRULE | e.g. `FREQ=WEEKLY;UNTIL=20261021;INTERVAL=1;BYDAY=WE` |
| `exdate` | array of instants | Excluded occurrence start instants |
| `recurrencyDeletedOccurence` | array of string indexes | Excluded occurrence indexes |
| `recurrencyExceptionOfId` | string | Present when this occurrence overrides a series entry |
| `eventType` | `UNKNOWN` / `BIRTHDAY_ACCOUNT` / … | Treat as an open vocabulary; preserve unknown values |
| `calendarId` | string | Source calendar of this event |
| `where`, `description`, `color`, `attendeeIds` | — | As source-derived |

Booleans are strings throughout, matching the list endpoints.

### All-day encoding — settled, and the trap

An all-day event is `allDay: "true"` with
`startDate = <date>T00:00:00.000Z` and `endDate = <date>T23:59:59.000Z`.

Those `Z` suffixes are **a lie about the timezone**. The event belongs to the
local date in the date component; the instant is a carrier, not a moment. For a
`Australia/Sydney` family, converting `2026-09-12T00:00:00.000Z` to local time
yields 12 September 10:00, and converting the end yields 13 September 09:59 —
placing a one-day event across two days.

The normalisation rule is therefore mandatory and is the single most important
line in this contract:

> When `allDay` is `"true"`, take the **date component of the UTC timestamp
> verbatim** as the local calendar date. Never convert an all-day instant through
> a timezone.

When `allDay` is `"false"`, `startDate` and `endDate` are genuine UTC instants
and must be converted through the user's timezone normally.

Multi-day all-day is the same encoding across two dates, **live-verified
2026-09-25**: an event created for 1–2 October read back as
`2026-10-01T00:00:00.000Z` to `2026-10-02T23:59:59.000Z` with `allDay:"true"`.

### Boundary semantics — settled

Probed by querying windows around a known one-hour event:

| Window relative to the event | Returned |
| --- | --- |
| strictly inside the event | **yes** |
| ends exactly at the event's start | no |
| ends one second after the event's start | **yes** |
| starts exactly at the event's end | no |
| starts one second before the event's end | **yes** |

The rule is a clean half-open overlap:

> an event is returned iff `event.startDate < a00to` **and** `event.endDate > a00from`

This matches the Python public contract's half-open local range directly, so the
boundary adapter is an identity transform rather than a correction layer. It is
still a named, separately tested component, because it is the thing that would
have to change if the server's behaviour ever drifted.

The practical consequence for "this week": an event that started last Sunday
night and runs into Monday **is** returned, so a week view must render partial
overlaps rather than assume every event starts inside the window.

### Recurrence — settled

The server expands. A weekly series appears as one object per occurrence inside
the window, each with its own `eventId` and an incrementing `occurenceIndex`,
all sharing one `eventMasterId` and one `rrule`. A cancelled occurrence is simply
not present; its index appears in the siblings' `recurrencyDeletedOccurence` and
its start instant in their `exdate`.

Binding consequences for P4:

1. **Do not implement local recurrence expansion.** No recurrence library is
   needed and none may be added.
2. De-duplicate on `eventId`, never on `eventMasterId` — collapsing by series ID
   would reduce a week's three soccer trainings to one.
3. `exdate` and `recurrencyDeletedOccurence` are informational. Do not subtract
   them again; the server has already applied them, and re-applying would delete
   a legitimate occurrence.
4. `recurrencyExceptionOfId` marks a modified occurrence, which arrives already
   modified. Preserve the field; no special handling is required for a read.

### Result size

No cap was observed: 41 events over 30 days, 109 over 90, 471 over 365 and 1115
over 1095. There is no continuation mechanism, so a cap — if one exists — would
be silent. The port therefore bounds its own request window (proposed 31 days per
call) and its own output size, and reports a reached bound as partial.

### Timezone handling

The port must use `zoneinfo` local calendar arithmetic. Resolving a week by
adding seven 24-hour periods is wrong across a DST transition; Australia/Sydney
has both a 23-hour and a 25-hour day each year. The TypeScript `days` helper
does exactly this fixed-increment arithmetic and must not be ported.

Inputs accepted by our tools: an RFC 3339 instant, or a local date interpreted in
the user's configured timezone. A naive datetime with no zone is rejected rather
than assumed.

## `evtsync` — full sync

Evidence: `source-tested` (calendar-range.test.ts:160). Fields: `calendarId`,
`a01call=evtcallist`, `a01withtask=true`, `a01withmeal=true`,
`a01withfolder=true`, `a01withAllFamilies=true`, `a01withExternal=true`,
`a01withOnError=true`. Returns `updatedCreated`.

This is a sync feed, not a bounded agenda: it carries sync metadata and no
proven completeness or continuation semantics. **Not** the v1 read path. Its
flags are, however, the only evidence that tasks, meals, external calendars and
other families can appear in calendar data at all — which means the port must
assume `evtlistinterval` may return objects that are not plain events, and
preserve rather than discard what it does not recognise.

## Superseded sections

The P0 draft carried two sections headed "the open blocker" — recurrence and
all-day encoding. Both were answered by the 02P probe and have been removed
rather than left to contradict the live findings above. The write-path
recurrence vocabulary they recorded (`recurrency`, `recurrencyInterval`,
`byDay`, `byMonthDay`, `recurrencyEndDate`) is still accurate and still belongs
to P8; the live `rrule` field is the read-path equivalent.

## Mutations

### `evtcreate` — live-verified 2026-09-25, narrow scope

Implemented by `create_calendar_event` for one timed, non-recurring event whose
only attendee is the authenticated member. A controlled live check on
2026-09-25 created one disposable event through the real tool, confirmed it by
readback, and deleted it by exact ID.

| Field | Value sent | Evidence |
| --- | --- | --- |
| `partnerScope` | `Family` | live-verified |
| `text` | title | live-verified |
| `startDate` / `endDate` | local wall-clock time with the event zone's own offset, e.g. `2026-09-30T10:00:00+10:00` | live-verified |
| `timeZone` | the event's IANA zone (the member's discovered zone by default) | live-verified; **never** the reference client's `Europe/London` |
| `where`, `description` | caller value or empty string | live-verified (empty) |
| `isToAll` | `false` | live-verified |
| `attendee.0.accountId` | the authenticated member's account ID from discovery | live-verified (browser probe 2026-09-16, tool 2026-09-25) |
| `picture`, `recurrencyEndDate`, `reminderList` | `$empty` | accepted |
| `private`, `byDay`, `byMonthDay` | empty string | accepted |
| `recurrency` / `recurrencyInterval` | `NONE` / `1` | accepted |
| `color` | **omitted** | accepted; the created event carries no `color` key |

Observed:

- The event was stored at exactly the requested instant
  (`10:00+10:00` → `startDate 2026-09-30T00:00:00.000Z`), with `timeZone`
  echoed. The check does not tell whether the server honours the offset or reads
  the wall clock in `timeZone`; the encoding is chosen so both give the same
  instant, and the readback comparison would report any shift as `mismatched`.
- The response `a00.r.r` is the full event object, with
  `eventId == metaId == eventMasterId`, `calendarId == calendar/{family_id}`,
  one `attendeeIds` entry, an empty `attendees`, `toAll:"false"`,
  `editable:"true"` and `recurrency:"NONE"`.
- `evtlistinterval` returned the same ID with identical fields, so the tool's
  outcome was `confirmed`.

A daylight-saving instant is also verified: `2026-10-07T09:00` in
`Australia/Sydney` (`+11:00`) was stored as `2026-10-06T22:00:00.000Z`, and the
tool returned `confirmed` (2026-09-25).

**Attendees and reminder (brief 09 slice C, offline, 2026-09-25).** The table
above is what the tool sent before this slice; it now sends the encodings
probe A1 verified for the web app itself, below. `assigned_to` (a list of
member names, or `None`/`[]` for everyone) is resolved to account IDs against
the cached family discovery before any write. Everyone is sent as
`isToAll=true` **plus** an `attendee.N.accountId` for every discovered member
(not `isToAll=true` alone); named members are sent as `isToAll=false` with
`attendee.0..N-1.accountId` in the given order. `reminderList=$empty` is
replaced by `reminderList.0.reminderType=SNOOZE`,
`reminderList.0.reminderUnit=MINUTE` and `reminderList.0.reminderValue=30` —
the web app's own default — for every created event. The readback
confirmation was extended to match: the `attendees` field name covers a
`toAll`/`attendeeIds` mismatch, and `reminder` covers a missing or different
`reminderList`. This is unit-tested only (synthetic fixtures); the lead's live
check of both attendee modes is the next step.

### Probe A1 — the web app's own forms (2026-09-25)

Captured from the FamilyWall web app's `evtcreate`, `evtupdate` and `evtdelete`
requests, with the account owner signed in. Only key names, value shapes and
masked IDs were recorded. Five disposable events were created and all were
deleted by exact ID.

**Timed create.** The web form sends the same fields `create_calendar_event`
sends, with these differences:

| Field | Web app | `create_calendar_event` |
| --- | --- | --- |
| `startDate` / `endDate` | UTC instant, `2026-09-29T23:00:00.000Z` | local time with offset; both store the same instant |
| `color` | `""` | omitted; both accepted |
| `reminderList` | indexed fields, `reminderList.0.reminderType=SNOOZE`, `reminderList.0.reminderUnit=MINUTE`, `reminderList.0.reminderValue=30` by default | `$empty` (no reminder) |

**Attendee encodings.**

| Selection | Sent | Read back |
| --- | --- | --- |
| Everyone (the web form's default, "Attendees: All") | `isToAll=true` **and** `attendee.N.accountId` for every member (all five) | `toAll:"true"`, `attendeeIds: []` |
| Two named members | `isToAll=false`, `attendee.0.accountId`, `attendee.1.accountId`, in selection order | `toAll:"false"`, `attendeeIds` equal to those two, in order |
| One member (earlier checks) | `isToAll=false`, `attendee.0.accountId` | one `attendeeIds` entry |

The creator is not added as an attendee unless selected.

**All-day create.** `allDay=true`, `startDate=<first day>T00:00:00.000Z`,
`endDate=<last day>T23:59:59.000Z`, and **no `timeZone` field**. The web
default reminder is `reminderValue=0`, which the app shows as "On day of event
at 9:00 am".

**Reads.** An empty `where` or `color` is omitted from the event object. The
reminder reads back as `reminderList: [{localId, reminderType, reminderUnit,
reminderValue}]`, plus `reminder` (the first entry), which maps directly onto
the indexed write fields.

### `evtupdate` — live-verified as a patch, not exposed yet

The web form sends `partnerScope=Family`, `option=All`,
`calendarId=calendar/{family_id}`, `metaId=<eventId>`, then the complete create
field set again. The response is the full updated event object.

**The server patches.** A scripted `evtupdate` carrying only `partnerScope`,
`option=All`, `calendarId`, `metaId`, `isToAll=false` and two
`attendee.N.accountId` entries, on a non-recurring event, left `text`, both
instants, `timeZone`, `allDay`, the reminder, `private` and `recurrency`
unchanged. The attendee list became **exactly** the entries sent: the attendee
set is replaced, not merged (2026-09-25).

A non-empty `where` and `description` also survive an update that omits them:
in slice G (2026-09-25) `set_calendar_event_attendees` changed the attendees of
an event carrying both, twice, and each readback was `confirmed` with neither
changed. Not observed: `evtupdate` on an all-day event, and any recurring event. On a recurring event `option=All` presumably updates the
whole series. `$empty` appears to be a sentinel for an omitted value; its exact
meaning is `source-only`.

### `evtdelete` — live-verified for non-recurring events, not exposed

The web app sends `partnerScope=Family`, `option=All`, `eventId=<eventId>`. The
reference client's `eventId.0=<eventId>` is also accepted. Both returned
`"true"`, and the event was absent on readback (2026-09-25; four events deleted
this way). No tool exposes it. On a recurring occurrence `option=All` remains
unobserved and plausibly deletes the whole series.

## Test cases the port must satisfy

Case 12 is now representable and cases 5 and 6 have a settled encoding.

| # | Case | Status |
| --- | --- | --- |
| 1 | Sydney spring-forward and fall-back weeks resolve to 7 local days | writable now |
| 2 | A second timezone produces different absolute bounds for the same local week | writable now |
| 3 | Sunday-start and Monday-start week configurations | writable now |
| 4 | An event crossing midnight appears on both days | writable now |
| 5 | An event crossing the week boundary appears in both weeks | writable now — the server returns overlaps |
| 6 | A multi-day all-day event appears on every day it covers | writable now — encoding live-verified 2026-09-25 |
| 7 | An explicit UTC offset is honoured; a naive datetime is rejected | writable now |
| 8 | An unparseable event does not discard the whole response | writable now |
| 9 | An unrecognised `eventType` or object is preserved, not dropped | writable now — `BIRTHDAY_ACCOUNT` is a real example |
| 10 | A reached fetch bound is reported as partial, never as a complete week | writable now |
| 11 | Empty-but-successful is distinguishable from failed-to-fetch | writable now |
| 12 | A cancelled occurrence of a series is not shown | writable now — the server omits it; the test asserts we do **not** re-subtract `exdate` |
| 13 | All-day dates are taken verbatim and never timezone-converted | **new, and the highest-value test in the suite** |
| 14 | Occurrences of one series are not collapsed by `eventMasterId` | **new** |

## Open questions

Answered by the 02P probe on 2026-09-13 and moved into the live-verified
sections above: calendar ID validity, boundary inclusivity, overlap, all-day
encoding, recurrence expansion, occurrence identity, cancellation, foreign
object types, and result caps.

Answered by probe A1 on 2026-09-25: multi-day all-day encoding, the everyone
and named-member attendee encodings, the all-day write form, `evtupdate`
patch semantics for attendees, and `evtdelete` for non-recurring events.

Still `pending-live`:

1. **Whether `eventType` has values beyond `UNKNOWN` and `BIRTHDAY_ACCOUNT`,**
   and whether tasks or meals appear in interval results. `evtsync`'s flags say
   they can appear somewhere; they did not appear here. Unknown types are
   preserved, so this is a presentation question, not a correctness one.
2. **Whether a silent result cap exists** above 1115 events. The port bounds its
   own window regardless.
3. **Remaining write questions** — `evtupdate` on an all-day event, and
   any write to a recurring series or occurrence.
