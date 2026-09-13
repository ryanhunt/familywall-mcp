# Task 04B — Weekly overview service

Owner: delegated. **Lead reviews the completeness/partial reporting before
integration.**

Depends on 04A (`familywall/calendar.py`, `services/ranges.py`), both delivered
and reviewed. Read them and use their real APIs. Do not modify them.

## File boundary — do not edit anything else

Create:
- `src/familywall_mcp/services/calendar.py`
- `tests/unit/test_calendar_service.py`

**Do not edit** `pyproject.toml`, `uv.lock`, `models.py`, `config.py`,
`errors.py`, `interfaces.py`, `cli.py`, anything under `familywall/`,
`services/ranges.py`, `services/lists.py`, `services/session.py`,
`services/__init__.py`, `storage/`, `tests/conftest.py`, any existing test, or
any doc but your own handoff.

Other tasks own `services/lists.py`, `services/session.py`, `storage/` and
`familywall/discovery.py`. Do not import them. Take your upstream dependency as
a **narrow injected protocol you define yourself**:

```python
class CalendarTransport(Protocol):
    async def call(self, endpoint: str, fields: Mapping[str, str]) -> object: ...
```

The lead wires the real session to it during integration.

## What this service does

Turns "what have we got on this week at home?" into a correct, honest answer.

```python
class DayAgenda(DomainModel):
    day: date
    events: tuple[CalendarEvent, ...]

class WeekOverview(DomainModel):
    range_start: datetime          # inclusive, local
    range_end: datetime            # exclusive, local
    timezone: str
    days: tuple[DayAgenda, ...]    # always exactly 7 for a week
    total_events: int
    complete: bool                 # False when a bound was reached
    notes: tuple[str, ...]         # human-readable caveats, e.g. skipped events
```

Behaviour:

- Resolve the local range with `services.ranges.resolve_week` (or
  `resolve_days`). Timezone and week start are **parameters**, not constants.
  Never hard-code a timezone — the reference TypeScript client hard-coded
  `Europe/London`, which would be wrong for this household.
- Build request fields with `familywall.calendar.build_interval_fields`, call
  the transport once per bounded window, and parse with `parse_events`.
- A fetch window longer than 31 days is split into successive calls. Bound the
  total: if more than a configured maximum of events would be returned
  (default 500), stop, set `complete=False`, and add a note. **A reached cap is
  reported as partial, never presented as a complete week.**
- Assign each event to every local day it covers:
  - a **timed** event is converted into the requested timezone and appears on
    each local day it touches, so an event crossing midnight appears on both;
  - an **all-day** event appears on every date from `start_date` to `end_date`
    inclusive, using **date arithmetic only** — never convert an all-day span
    through a timezone.
- De-duplicate by `occurrence_id`. **Never** de-duplicate by `series_id`: the
  three occurrences of one weekly training are three separate events.
- Do **not** filter out anything because of `exdate` or
  `recurrencyDeletedOccurence`. The server has already removed cancelled
  occurrences; re-applying them would delete a real event.
- Preserve events with an unrecognised `event_type` (e.g. `BIRTHDAY_ACCOUNT`)
  and events from other calendars. Report each event's calendar rather than
  dropping it.
- Order events within a day deterministically: all-day events first, then timed
  events by start instant, then by `occurrence_id` as a stable tiebreaker.
- `parse_events` returns a skipped count. A non-zero count adds a note and sets
  `complete=False`. **Empty-but-successful must be distinguishable from
  failed-to-fetch**: zero events with `complete=True` is a genuinely empty week;
  a transport failure propagates as its typed error and never becomes an empty
  week.

## Tests — the acceptance criteria

No network. Inject a fake transport that returns fixtures from
`tests/support/calendar_fixtures.py` and records the exact fields it received.

1. A Sydney week returns exactly 7 `DayAgenda` entries with the right dates.
2. The request fields carry the resolved ISO bounds with an explicit offset.
3. A timed event crossing local midnight appears on **both** days.
4. A single-day all-day event appears on exactly one day — **assert it is the
   date in the upstream string, with no timezone shift**. This is the
   highest-value test here.
5. A multi-day all-day event appears on every date it covers.
6. Three occurrences of one series all appear; they are not collapsed.
7. An occurrence carrying `exdate` is still shown.
8. A `BIRTHDAY_ACCOUNT` event from another calendar is preserved.
9. A skipped malformed event sets `complete=False` and adds a note.
10. Zero events returns an empty week with `complete=True` and no note.
11. A transport error propagates as its typed error — it does **not** become an
    empty week.
12. Exceeding the event cap sets `complete=False`, adds a note, and still
    returns what was fetched.
13. A 60-day range issues more than one request, each within the 31-day bound.
14. The spring-forward Sydney week still has 7 days and every event lands on the
    right local day.
15. The same week requested in a different timezone moves events to different
    local days.
16. Ordering within a day is deterministic and matches the stated rule.

## Checks to run before handing back

```
uv run ruff check .
uv run ruff format .
uv run mypy src
uv run pytest -m 'not live'
```

`mypy` is strict. If a check fails in a file you did not create, report it and
leave it alone.

## Handoff

Write `docs/handoffs/04b-week-service.md` from `docs/templates/handoff.md`.
