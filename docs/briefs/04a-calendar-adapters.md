# Task 04A — Calendar wire adapters and local week ranges

Owner: delegated. Lead reviews before integration. Pure parsing and date
arithmetic only: this task performs **no I/O and no HTTP**.

## File boundary — do not edit anything else

Create:
- `src/familywall_mcp/familywall/calendar.py`
- `src/familywall_mcp/services/__init__.py`
- `src/familywall_mcp/services/ranges.py`
- `tests/unit/test_calendar_adapter.py`
- `tests/unit/test_ranges.py`
- `tests/support/calendar_fixtures.py`

**Do not edit** `pyproject.toml`, `uv.lock`, `models.py`, `config.py`,
`errors.py`, `interfaces.py`, `cli.py`, `tests/support/doubles.py`,
`tests/conftest.py`, any existing test, or any doc. Other agents are
concurrently creating `familywall/client.py`, `familywall/wire.py`,
`familywall/__init__.py` and `familywall/lists.py` — **do not create or import
any of those**; if you need a helper they own, write your own private copy.

## Facts (live-verified 2026-09-13 against a real family calendar)

`evtlistinterval` returns `a00.r.r` as a **bare JSON array** of event objects.
All booleans are the **strings** `"true"` / `"false"`.

Fields that matter:

| Field | Meaning |
| --- | --- |
| `eventId` (== `metaId`) | **occurrence** identity, distinct per occurrence |
| `eventMasterId` | series identity; equals `eventId` for non-recurring events |
| `occurenceIndex` | string integer position in the series (note the upstream spelling) |
| `text` | title |
| `startDate` / `endDate` | `YYYY-MM-DDTHH:MM:SS.sssZ` |
| `allDay` | `"true"` / `"false"` |
| `timeZone` | IANA name, e.g. `Australia/Sydney`. **May be absent** |
| `recurrency` | `NONE`, `WEEKLY`, … |
| `rrule` | iCal RRULE string, e.g. `FREQ=WEEKLY;UNTIL=20261021;INTERVAL=1;BYDAY=WE` |
| `exdate` | array of excluded instants |
| `recurrencyDeletedOccurence` | array of excluded string indexes |
| `recurrencyExceptionOfId` | present when this occurrence overrides the series |
| `eventType` | `UNKNOWN` for ordinary events, `BIRTHDAY_ACCOUNT` observed — an **open vocabulary** |
| `calendarId` | source calendar, e.g. `calendar/<family_id>` or `calendarSpecialDays/<account_id>` |
| `where`, `description`, `color`, `attendeeIds` | optional detail |

### Three rules that are the whole point of this task

1. **The server already expands recurrence.** Each occurrence is its own object.
   Do **not** implement local expansion, do **not** add a recurrence library,
   and do **not** de-duplicate on `eventMasterId` — that would collapse a week's
   three soccer trainings into one. De-duplicate on `eventId` only.

2. **`exdate` and `recurrencyDeletedOccurence` have already been applied by the
   server.** A cancelled occurrence is simply absent. Carry the fields through
   as data; never subtract them again, or you delete a real event.

3. **All-day dates must never be timezone-converted.** An all-day event is
   `allDay:"true"`, `startDate = <date>T00:00:00.000Z`,
   `endDate = <date>T23:59:59.000Z`. The `Z` is a carrier, not a real instant:
   for a Sydney family, converting `2026-09-12T00:00:00.000Z` gives 12 Sep 10:00
   local and converting the end gives 13 Sep 09:59, smearing a one-day event
   across two days. Take the **date component verbatim** as the local calendar
   date. When `allDay` is `"false"`, the timestamps *are* genuine UTC instants
   and are converted normally.

## What to build in `calendar.py`

Frozen pydantic models in the style of `src/familywall_mcp/models.py`
(`extra="forbid"`, `frozen=True`).

```python
class TimedSpan(DomainModel):      # allDay == "false"
    start: datetime                # timezone-aware UTC
    end: datetime

class AllDaySpan(DomainModel):     # allDay == "true"
    start_date: date               # taken verbatim from the UTC date component
    end_date: date

class CalendarEvent(DomainModel):
    occurrence_id: str             # eventId
    series_id: str                 # eventMasterId
    occurrence_index: int | None
    title: str
    span: TimedSpan | AllDaySpan
    raw_start: str                 # the upstream string, preserved verbatim
    raw_end: str
    event_type: str                # preserved verbatim, never coerced
    calendar_id: str | None
    event_timezone: str | None     # IANA name as received, unvalidated
    location: str | None
    description: str | None
    recurrence_rule: str | None    # rrule, verbatim
    is_recurring: bool             # recurrency not in (None, "", "NONE")
    is_series_exception: bool      # recurrencyExceptionOfId present
```

- `parse_events(payload: object) -> ParsedEvents` where `ParsedEvents` carries
  `events: tuple[CalendarEvent, ...]` and `skipped: int`. A malformed **event**
  is skipped and counted; a malformed **payload** raises
  `MalformedPayloadError` from `familywall_mcp.errors`.
- Accept a bare array (the real shape) and, tolerantly, an object wrapping it
  under `events`, `datas`, `updatedCreated` or `results`.
- An unknown `eventType` is preserved and the event is still returned.
- `build_interval_fields(calendar_id: str, start: datetime, end: datetime) -> dict[str, str]`
  → `partnerScope=Family`, `calendarId`, `a00from`, `a00to` as ISO-8601 strings
  with an explicit offset. The docstring **must** record that the server
  **ignores `calendarId`** — it is sent for contract fidelity only and is not an
  access boundary, so no caller-supplied calendar ID may be treated as a filter.
  Epoch milliseconds are rejected by the server; always send ISO.

## What to build in `services/ranges.py`

Pure local-calendar arithmetic with `zoneinfo`. **Never** add seven 24-hour
periods to get a week — `Australia/Sydney` has a 23-hour and a 25-hour day every
year.

```python
class LocalRange(DomainModel):
    start: datetime   # timezone-aware, in the requested zone
    end: datetime     # exclusive
    timezone: str
```

- `resolve_week(reference: date, timezone: str, week_starts_on: Literal["monday","sunday"]) -> LocalRange`
  — midnight-to-midnight local, exclusive end, always exactly 7 local days.
- `resolve_days(start: date, days: int, timezone: str) -> LocalRange` with a
  bound: more than 31 days raises `ValueError`.
- `parse_boundary_input(value: str, timezone: str) -> datetime` — accepts an
  RFC 3339 instant **with** an offset, or a bare `YYYY-MM-DD` interpreted as
  local midnight in `timezone`. A naive datetime string (no offset, has a time)
  is **rejected** with `ValueError`. An unknown IANA zone raises `ValueError`.
- `overlaps(event_start: datetime, event_end: datetime, window: LocalRange) -> bool`
  implementing the server's own rule: `start < window.end and end > window.start`.
  This is the **boundary adapter**. Keep it a named, separately tested function
  even though it is currently an identity match for the server, because it is
  the single place that would change if the server's behaviour drifted. Say that
  in the docstring.
- `all_day_overlaps(start_date: date, end_date: date, window: LocalRange) -> bool`
  comparing **dates against the window's local dates**, with no instant
  conversion anywhere.

## Fixtures

`tests/support/calendar_fixtures.py` holds **synthetic** payloads shaped like
the real ones: a timed event, an all-day event, a multi-day all-day event, three
occurrences of one weekly series sharing an `eventMasterId` with different
`eventId`s and `occurenceIndex`es, one occurrence carrying `exdate` and
`recurrencyDeletedOccurence`, a `BIRTHDAY_ACCOUNT` event on a
`calendarSpecialDays/<id>` calendar with **no** `timeZone` field, and one
deliberately malformed event. Invent all titles and IDs — no real family data
exists in this repository and none may be added.

## Tests — the acceptance criteria

Calendar adapter:
1. A timed event parses to `TimedSpan` with timezone-aware UTC datetimes.
2. An all-day event on `2026-09-12T00:00:00.000Z` → `2026-09-12T23:59:59.000Z`
   parses to `AllDaySpan(date(2026,9,12), date(2026,9,12))`. **Assert explicitly
   that no `Australia/Sydney` conversion happened** — this is the highest-value
   test in the suite.
3. A multi-day all-day event yields differing start and end dates.
4. Three occurrences of one series yield three distinct events with the same
   `series_id`; de-duplication by `occurrence_id` keeps all three.
5. `exdate` / `recurrencyDeletedOccurence` are carried, and the occurrences
   present are **not** filtered out by them.
6. A `BIRTHDAY_ACCOUNT` event with no `timeZone` parses, keeps its `event_type`
   verbatim, and reports `event_timezone is None`.
7. An unknown `eventType` value is preserved.
8. One malformed event among several is skipped, `skipped == 1`, the rest are
   returned.
9. A non-array, non-wrapper payload raises `MalformedPayloadError`.
10. `raw_start` / `raw_end` equal the upstream strings exactly.
11. `build_interval_fields` emits ISO strings with an offset and the four
    expected keys.

Ranges:
12. The Sydney spring-forward week (October) is 7 local days and **not** 168
    hours; likewise the fall-back week (April) — assert the actual UTC durations
    differ from 168h.
13. The same local week in two timezones produces different absolute bounds.
14. Monday-start and Sunday-start produce different, correct 7-day ranges.
15. `parse_boundary_input` accepts `2026-09-14T00:00:00+10:00` and
    `2026-09-14`; rejects `2026-09-14T00:00:00` and `not-a-date`; rejects an
    unknown timezone.
16. `overlaps` matches the server's observed truth table exactly: window
    strictly inside an event → True; window ending exactly at the event start →
    False; ending one second later → True; window starting exactly at the event
    end → False; starting one second earlier → True.
17. An event crossing local midnight is in both days' ranges; an event crossing
    a week boundary is in both weeks.
18. `resolve_days` rejects 32 days and accepts 31.

## Checks to run before handing back

```
uv run ruff check .
uv run ruff format .
uv run mypy src
uv run pytest -m 'not live'
```

`mypy` is strict. Other agents are working in this repo concurrently — if a
check fails in a file you did not create, say so in the handoff and do not
"fix" it.

## Handoff

Write `docs/handoffs/04a-calendar-adapters.md` from
`docs/templates/handoff.md`.
