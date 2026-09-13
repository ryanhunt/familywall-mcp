"""Weekly calendar overview service.

This service provides a weekly view of family calendar events, handling timezone
conversion, deduplication, and event assignment to days.
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import date, datetime, timedelta
from typing import Literal, Protocol

from familywall_mcp.familywall.calendar import (
    AllDaySpan,
    CalendarEvent,
    TimedSpan,
    build_interval_fields,
    parse_events,
)
from familywall_mcp.models import DomainModel
from familywall_mcp.services.ranges import resolve_days, resolve_week


class CalendarTransport(Protocol):
    """Protocol for calendar event fetching via FamilyWall API."""

    async def call(self, endpoint: str, fields: Mapping[str, str]) -> object:
        """Call the calendar endpoint and return parsed response.

        Args:
            endpoint: The API endpoint name (e.g. "evtlistinterval").
            fields: Request fields (e.g. a00from, a00to, calendarId, partnerScope).

        Returns:
            The parsed response object (typically a list or dict of events).

        Raises:
            TransportError or other FamilyWallError subclasses on network/auth failures.
        """
        ...


class DayAgenda(DomainModel):
    """Calendar events for a single day."""

    day: date
    """The calendar date."""

    events: tuple[CalendarEvent, ...]
    """Events on this day, ordered: all-day first, then timed by start, then by occurrence_id."""


class WeekOverview(DomainModel):
    """A week-long calendar view."""

    range_start: datetime
    """Inclusive start of the week, local time."""

    range_end: datetime
    """Exclusive end of the week, local time."""

    timezone: str
    """IANA timezone name."""

    days: tuple[DayAgenda, ...]
    """Always exactly 7 entries for a week, one per day."""

    total_events: int
    """Count of unique events returned (de-duplicated by occurrence_id)."""

    complete: bool
    """False if a bound (event cap or malformed events) was reached."""

    notes: tuple[str, ...]
    """Human-readable caveats, e.g. 'skipped 3 malformed events'."""


class CalendarService:
    """Service for fetching and formatting weekly calendar overviews."""

    DEFAULT_MAX_EVENTS = 500
    MAX_DAYS_PER_REQUEST = 31

    def __init__(
        self,
        transport: CalendarTransport,
        max_events: int = DEFAULT_MAX_EVENTS,
    ) -> None:
        """Initialize the calendar service.

        Args:
            transport: The transport for calling the calendar API.
            max_events: Maximum total events before marking as incomplete (default 500).
        """
        self.transport = transport
        self.max_events = max_events

    async def get_week_overview(
        self,
        reference_date: date,
        timezone: str,
        calendar_id: str,
        week_starts_on: Literal["monday", "sunday"] = "monday",
    ) -> WeekOverview:
        """Fetch calendar events for a week and organize by day.

        Args:
            reference_date: A date in the target week.
            timezone: IANA timezone name for local interpretation.
            calendar_id: Calendar ID to fetch (typically "calendar/{family_id}").
            week_starts_on: "monday" or "sunday" (default "monday").

        Returns:
            A WeekOverview with a DayAgenda for each day of the week.

        Raises:
            FamilyWallError: If the transport fails (TypeError, TransportError, etc.).
        """
        # Resolve the local week boundaries
        week_range = resolve_week(reference_date, timezone, week_starts_on)

        # Fetch all events within the week, splitting into 31-day chunks if needed
        all_events: dict[str, CalendarEvent] = {}  # Keyed by occurrence_id
        total_skipped = 0
        complete = True
        notes: list[str] = []

        # Break the week into 31-day chunks and fetch each
        current = week_range.start
        while current < week_range.end:
            # Calculate the chunk end (31 days or week end, whichever is sooner)
            days_remaining = (week_range.end - current).days
            chunk_days = min(days_remaining, self.MAX_DAYS_PER_REQUEST)
            chunk_range = resolve_days(current.date(), chunk_days, timezone)

            # Fetch events for this chunk
            fields = build_interval_fields(calendar_id, chunk_range.start, chunk_range.end)
            try:
                payload = await self.transport.call("evtlistinterval", fields)
            except Exception:
                # Transport errors propagate; they should not become empty weeks
                raise

            # Parse and deduplicate
            parsed = parse_events(payload)

            if parsed.skipped > 0:
                complete = False
                notes.append(f"skipped {parsed.skipped} malformed events")
                total_skipped += parsed.skipped

            for event in parsed.events:
                # De-duplicate by occurrence_id; first seen wins
                if event.occurrence_id not in all_events:
                    all_events[event.occurrence_id] = event

            # Check against event cap
            if len(all_events) > self.max_events:
                complete = False
                # Keep only up to max_events
                event_list = list(all_events.values())
                all_events = {e.occurrence_id: e for e in event_list[: self.max_events]}
                notes.append(f"reached event cap at {self.max_events}")
                break

            # Move to next chunk
            current = chunk_range.end

        # Assign events to days
        days_dict: dict[date, list[CalendarEvent]] = {}

        # Initialize all 7 days of the week
        for day_offset in range(7):
            day_date = week_range.start.date() + timedelta(days=day_offset)
            days_dict[day_date] = []

        # Assign each event to the days it covers
        for event in all_events.values():
            if isinstance(event.span, TimedSpan):
                # Convert timed event to local timezone and find which days it touches
                _assign_timed_event(event, days_dict, timezone)
            elif isinstance(event.span, AllDaySpan):
                # All-day events are assigned by date range (no timezone conversion)
                _assign_all_day_event(event, days_dict)

        # Build DayAgenda entries with ordered events
        day_agendas: list[DayAgenda] = []
        for day_offset in range(7):
            day_date = week_range.start.date() + timedelta(days=day_offset)
            events = days_dict[day_date]
            ordered = _order_events_in_day(events)
            day_agendas.append(DayAgenda(day=day_date, events=tuple(ordered)))

        return WeekOverview(
            range_start=week_range.start,
            range_end=week_range.end,
            timezone=timezone,
            days=tuple(day_agendas),
            total_events=len(all_events),
            complete=complete,
            notes=tuple(notes),
        )


def _assign_timed_event(
    event: CalendarEvent,
    days_dict: dict[date, list[CalendarEvent]],
    timezone: str,
) -> None:
    """Assign a timed event to all local days it covers.

    A timed event crossing midnight appears on both days.

    Args:
        event: The event with a TimedSpan.
        days_dict: Mutable dict mapping date -> list of events.
        timezone: IANA timezone name for local interpretation.
    """
    if not isinstance(event.span, TimedSpan):
        return

    # Convert the event's UTC instants to the local timezone
    from zoneinfo import ZoneInfo

    tz = ZoneInfo(timezone)
    start_local = event.span.start.astimezone(tz)
    end_local = event.span.end.astimezone(tz)

    # Find all local days the event touches
    # Start with the start date
    day = start_local.date()
    end_date = end_local.date()

    # Add the event to each day from start to end (inclusive on end_date)
    while day <= end_date:
        if day in days_dict:
            days_dict[day].append(event)
        day += timedelta(days=1)


def _assign_all_day_event(
    event: CalendarEvent,
    days_dict: dict[date, list[CalendarEvent]],
) -> None:
    """Assign an all-day event to all dates it covers.

    All-day events are assigned using date arithmetic only, with no timezone conversion.

    Args:
        event: The event with an AllDaySpan.
        days_dict: Mutable dict mapping date -> list of events.
    """
    if not isinstance(event.span, AllDaySpan):
        return

    # Add the event to each date from start_date to end_date (inclusive)
    day = event.span.start_date
    while day <= event.span.end_date:
        if day in days_dict:
            days_dict[day].append(event)
        day += timedelta(days=1)


def _order_events_in_day(events: list[CalendarEvent]) -> list[CalendarEvent]:
    """Order events within a day deterministically.

    Order: all-day events first, then timed events by start time, then by occurrence_id.

    Args:
        events: Unordered list of events on a single day.

    Returns:
        Ordered list of events.
    """
    # Separate all-day and timed events
    all_day_events: list[CalendarEvent] = []
    timed_events: list[CalendarEvent] = []

    for event in events:
        if isinstance(event.span, AllDaySpan):
            all_day_events.append(event)
        else:
            timed_events.append(event)

    # Sort all-day events by occurrence_id (stable within the all-day group)
    all_day_events.sort(key=lambda e: e.occurrence_id)

    # Sort timed events by start time, then by occurrence_id
    if isinstance(timed_events[0].span, TimedSpan) if timed_events else False:
        timed_events.sort(
            key=lambda e: (
                e.span.start if isinstance(e.span, TimedSpan) else datetime.min,
                e.occurrence_id,
            )
        )
    else:
        # If there are timed events but they don't all have TimedSpan, sort only by occurrence_id
        # This shouldn't happen in practice, but handle it gracefully
        timed_events.sort(key=lambda e: e.occurrence_id)

    # Return all-day first, then timed
    return all_day_events + timed_events
