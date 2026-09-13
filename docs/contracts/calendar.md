# FamilyWall calendar contract

Status: **source-derived, materially incomplete**. Produced by P0 task 00B on
2026-09-13 from `familywall-api@c85bb152115d3c41ab90322ef9dce732122ff4c0`.
Evidence levels are defined in [the main contract](familywall.md).

The headline finding: the reference client's *read* path proves only six event
fields. All-day encoding, recurrence and cancellation — the three things a
correct weekly overview depends on — have **no evidence at all**. This is the
largest single risk in the project and it is why P4 cannot be planned as a
straightforward port.

## Calendar identity

The only calendar reference used anywhere is the string `calendar/{family_id}`,
built locally from the discovery payload (family.ts:34,57). Evidence:
`source-only`. No endpoint in the reference client returns a list of calendars,
so:

- there is no proof this is the server's own identifier rather than a convention
  that happens to work;
- there is no way to enumerate a family's calendars;
- external/subscribed calendars cannot be discovered, only implied by the
  `a01withExternal=true` flag on `evtsync`.

P2 must confirm the derived ID is accepted before any calendar tool ships.

## `evtlistinterval` — bounded range query

The v1 read path. Evidence: `source-tested` (calendar-range.test.ts:10,30-34).

| Field | Value |
| --- | --- |
| `partnerScope` | `Family` |
| `calendarId` | `calendar/{family_id}` |
| `a00from` | ISO instant |
| `a00to` | ISO instant |

Response: a bare array, or an object under one of `events`, `datas`,
`updatedCreated`, `results` (client.ts:438-452).

Proven event fields (client.ts:830-848):

| Field | Aliases | Notes |
| --- | --- | --- |
| eventId | `eventId`, `metaId` | identity |
| text | `text` | title |
| startDate | `startDate` | ISO datetime |
| endDate | `endDate` | ISO datetime |
| where | `where` | location |
| description | `description` | free text |
| color | `color` | inferred from the write path, not asserted on read |

**Nothing else is proven.** There is no evidenced field for: all-day status,
timezone of the event, recurrence rule, recurrence parent/series ID, occurrence
identity, exception or cancellation status, participants, calendar of origin, or
owning family.

### Boundary semantics — unresolved

The TypeScript client resolves a date-only `a00to` to `23:59:59.999` local
(client.ts:397-436). That is a client-side convention chosen to approximate an
inclusive end; it says nothing about how the server treats the interval.

Specifically unknown (`pending-live`):

1. whether `a00from`/`a00to` are inclusive or exclusive at each end;
2. whether an event that *overlaps* the window but starts before `a00from` is
   returned, or only events starting inside it;
3. whether a multi-day or all-day event spanning the window is returned.

Point 2 decides whether a "this week" answer is correct or silently missing the
Monday-morning continuation of a Sunday-night event. The Python public contract
uses half-open local ranges and a **boundary adapter** whose job is to translate
that into whatever the server actually does — the adapter is a named, separately
tested component precisely because its input is currently a guess.

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

## Recurrence — the open blocker

The *write* path sends `recurrency` (default `NONE`), `recurrencyInterval`,
`byDay`, `byMonthDay`, `recurrencyEndDate` (client.ts:739-753). This proves the
server models recurrence, and gives the vocabulary. It does **not** establish:

- what a recurring series looks like when read back;
- whether `evtlistinterval` returns one master object or expanded occurrences
  inside the window;
- how an occurrence is identified distinctly from its series;
- how a modified or cancelled single occurrence is represented.

Two possible worlds, with opposite implementations:

| If the server… | Then the port… |
| --- | --- |
| expands occurrences inside the interval | consumes them directly; needs only occurrence-aware de-duplication |
| returns series masters | must expand locally with a maintained recurrence library, plus exception handling — a separate, separately reviewed piece of work |

Choosing between these from source is not possible. P4 must therefore be planned
with the expansion path as a *conditional* sub-task, and a weekly overview must
not be described as complete until the answer is known. A week that silently
omits a recurring school pickup is worse than one that says it is unsure.

## All-day events — the second open blocker

No evidenced field. The candidates are a boolean flag, a date-only `startDate`,
or a midnight-to-midnight instant pair, and they are not interchangeable: a
midnight-to-midnight instant pair converted through a timezone shifts an all-day
event onto the wrong day. Until a live payload settles this, the normaliser must
preserve the raw start/end representation alongside its parsed form so the
decision can be made from data rather than reversed later.

## Mutations — excluded from v1

| Endpoint | Fields | Why excluded |
| --- | --- | --- |
| `evtcreate` | `text`, `startDate`, `endDate`, `color`, `where`, `description`, `picture=$empty`, `timeZone=Europe/London`, `isToAll=false`, `private=`, `recurrencyInterval=1`, `recurrency=NONE`, `byDay=`, `byMonthDay=`, `recurrencyEndDate=$empty`, `reminderList=$empty` | `source-only`, no test. The hard-coded `Europe/London` timezone would create every event in the wrong zone for an Australian household |
| `evtupdate` | as create, plus `metaId` | `source-only`; unsafe without occurrence-vs-series semantics |
| `evtdelete` | `option=All`, `eventId.0` | `source-only`. `option=All` plausibly deletes an entire series. Deleting a whole recurring series when the user asked to cancel one soccer practice is unacceptable |

Calendar writes belong to P8 and only after recurrence semantics are proven.
`$empty` appears to be a sentinel for an omitted value; its meaning is
`source-only`.

## Test cases the port must satisfy

Derived from the above, independent of which world we land in:

1. Sydney spring-forward and fall-back weeks resolve to 7 local days.
2. A second timezone produces different absolute bounds for the same local week.
3. Sunday-start and Monday-start week configurations.
4. An event crossing midnight appears on both days.
5. An event crossing the week boundary appears in both weeks.
6. A multi-day all-day event appears on every day it covers.
7. An explicit UTC offset in input is honoured; a naive datetime is rejected.
8. An unparseable event does not discard the whole response.
9. An unrecognised object type is preserved, not dropped.
10. Truncation is reported as partial, never presented as a complete week.
11. Empty-but-successful is distinguishable from failed-to-fetch.
12. A cancelled occurrence of a series is not shown (once representable).

Cases 6 and 12 cannot be written as passing tests until the corresponding
`pending-live` questions are answered; they are recorded now so they are not
quietly dropped from the acceptance matrix.

## Open questions (`pending-live`)

1. Is `calendar/{family_id}` accepted, and how are other calendars enumerated?
2. Are interval bounds inclusive or exclusive?
3. Are overlapping events returned, or only events starting inside the window?
4. How is an all-day event encoded?
5. Does the interval call expand recurring occurrences?
6. How is an occurrence identified, and how is a cancelled one represented?
7. Do tasks, meals or external-calendar items appear in interval results?
8. Is there a result cap, and any continuation mechanism?
