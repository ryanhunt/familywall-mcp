"""Local calendar arithmetic and date range utilities.

This module provides date and time range calculation with proper timezone handling,
accounting for daylight-saving transitions and local calendar semantics.

Key design principles:
- Never add 7 consecutive 24-hour periods to calculate a week (DST transitions
  mean weeks can have 23 or 25 hours in zones with daylight saving time).
- Always work with local datetimes and zoneinfo for correct calendar semantics.
- All-day events are compared as dates, never converted to instants.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta
from typing import Literal
from zoneinfo import ZoneInfo, available_timezones

from familywall_mcp.models import DomainModel


class LocalRange(DomainModel):
    """A time range in a specific timezone, with exclusive end."""

    start: datetime
    """Timezone-aware datetime in the requested zone."""

    end: datetime
    """Exclusive end boundary, timezone-aware in the same zone."""

    timezone: str
    """IANA timezone name."""


def resolve_week(
    reference: date,
    timezone: str,
    week_starts_on: Literal["monday", "sunday"] = "monday",
) -> LocalRange:
    """Calculate the local week containing a reference date.

    The week spans midnight-to-midnight local time, always covers exactly 7
    local calendar days, and accounts for daylight-saving transitions.

    Args:
        reference: A calendar date in the target week.
        timezone: IANA timezone name.
        week_starts_on: Day the week begins ("monday" or "sunday").

    Returns:
        A LocalRange with timezone-aware start (00:00 local) and exclusive
        end (00:00 local the next day, 7 days later).

    Raises:
        ValueError: If timezone is unknown or invalid.
    """
    if timezone not in available_timezones():
        raise ValueError(f"unknown timezone: {timezone}")

    tz = ZoneInfo(timezone)

    # Find the start of the week by calculating weekday offset
    ref_weekday = reference.weekday()  # 0=Monday, 6=Sunday
    if week_starts_on == "sunday":  # noqa: SIM108
        # Sunday is weekday 6; calculate days back to the nearest Sunday
        days_back = (ref_weekday + 1) % 7
    else:
        # Monday is weekday 0; calculate days back to the nearest Monday
        days_back = ref_weekday

    week_start_date = reference - timedelta(days=days_back)

    # Build the range: start at midnight on week_start_date, iterate 7 days
    start_local = datetime.combine(week_start_date, datetime.min.time()).replace(tzinfo=tz)

    # Calculate the exclusive end by stepping forward 7 local days
    end_date = week_start_date + timedelta(days=7)
    end_local = datetime.combine(end_date, datetime.min.time()).replace(tzinfo=tz)

    return LocalRange(start=start_local, end=end_local, timezone=timezone)


def resolve_days(
    start: date,
    days: int,
    timezone: str,
) -> LocalRange:
    """Calculate a local range spanning a number of calendar days.

    Spans midnight-to-midnight in the given timezone, with an exclusive end.

    Args:
        start: The first calendar day in the range.
        days: Number of calendar days (1-31 inclusive).
        timezone: IANA timezone name.

    Returns:
        A LocalRange with timezone-aware start (00:00 local) and exclusive
        end (00:00 local after the last day).

    Raises:
        ValueError: If days > 31, days < 1, or timezone is unknown.
    """
    if days < 1 or days > 31:
        raise ValueError(f"days must be 1-31, got {days}")

    if timezone not in available_timezones():
        raise ValueError(f"unknown timezone: {timezone}")

    tz = ZoneInfo(timezone)

    start_local = datetime.combine(start, datetime.min.time()).replace(tzinfo=tz)
    end_date = start + timedelta(days=days)
    end_local = datetime.combine(end_date, datetime.min.time()).replace(tzinfo=tz)

    return LocalRange(start=start_local, end=end_local, timezone=timezone)


def parse_boundary_input(value: str, timezone: str) -> datetime:
    """Parse a date or RFC 3339 instant into a timezone-aware datetime.

    Accepts:
    - RFC 3339 instant with offset (e.g., "2026-09-14T00:00:00+10:00")
    - Bare date (e.g., "2026-09-14"), interpreted as local midnight in timezone

    Rejects:
    - Naive datetime strings (no offset, has a time component)
    - Unknown timezones
    - Malformed strings

    Args:
        value: The input string.
        timezone: IANA timezone name, used only for bare date interpretation.

    Returns:
        A timezone-aware datetime in UTC (RFC 3339) or the specified timezone
        (bare date).

    Raises:
        ValueError: For invalid format, naive datetimes, or unknown timezone.
    """
    if timezone not in available_timezones():
        raise ValueError(f"unknown timezone: {timezone}")

    # Try parsing as a bare date first (YYYY-MM-DD)
    if len(value) == 10 and value.count("-") == 2:
        try:
            parsed_date = datetime.fromisoformat(value).date()
            tz = ZoneInfo(timezone)
            return datetime.combine(parsed_date, datetime.min.time()).replace(tzinfo=tz)
        except ValueError:
            pass

    # Try parsing as RFC 3339 instant
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError as e:
        raise ValueError(f"invalid date or RFC 3339 instant: {value}") from e

    # Check if it's naive (no tzinfo); this check happens outside the try/except
    # so the decision to reject naive datetimes is not coupled to error message text
    if parsed.tzinfo is None:
        raise ValueError(f"naive datetime string (no offset) is not allowed: {value}")

    return parsed


def overlaps(
    event_start: datetime,
    event_end: datetime,
    window: LocalRange,
) -> bool:
    """Check if an event overlaps with a time window.

    Implements the server's observed overlap rule:
    `event_start < window.end and event_end > window.start`

    This is the boundary adapter between the client and server semantics.
    If server overlap rules change, this single function will be the place
    to update them.

    Args:
        event_start: Start instant (timezone-aware).
        event_end: Exclusive end instant (timezone-aware).
        window: The time window to check against.

    Returns:
        True if the event and window overlap.
    """
    return event_start < window.end and event_end > window.start


def all_day_overlaps(
    start_date: date,
    end_date: date,
    window: LocalRange,
) -> bool:
    """Check if an all-day event overlaps with a time window.

    Compares calendar dates against the window's local calendar dates,
    with no instant conversion anywhere.

    An all-day event spanning start_date to end_date (inclusive) overlaps
    the window if their date ranges intersect.

    Args:
        start_date: First calendar day of the event.
        end_date: Last calendar day of the event (inclusive).
        window: The time window to check against.

    Returns:
        True if the event's date range and window's date range overlap.
    """
    # Extract the local dates from the window boundaries, using the window's timezone
    window_start_date = window.start.date()

    # The window's end is exclusive. The last instant strictly before window.end
    # determines the last included date.
    # If window.end is exactly local midnight, the last included date is the previous day.
    # Otherwise, it's the date of window.end itself.
    if window.end.time() == datetime.min.time():
        # End is exactly midnight; last included date is previous day
        window_last_included_date = window.end.date() - timedelta(days=1)
    else:
        # End is at some other time; last included date is the end date itself
        window_last_included_date = window.end.date()

    # Check overlap: events overlap if their date ranges intersect
    # An all-day event spans [start_date, end_date] inclusive
    # Window spans [window_start_date, window_last_included_date] inclusive
    # So overlap is: start_date <= window_last_included_date and end_date >= window_start_date
    return start_date <= window_last_included_date and end_date >= window_start_date
