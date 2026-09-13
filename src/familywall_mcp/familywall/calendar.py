"""Calendar event parsing and wire-to-domain adaptation.

This module parses FamilyWall calendar events and adapts them to local domain
models. It handles both timed and all-day events, with proper timezone and
datetime handling.

Key design principles:
- The FamilyWall server already expands recurrence; we never do local expansion.
- All-day events are pure dates; we never timezone-convert them.
- Events are de-duplicated by occurrence_id only, never by series_id.
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Any

from familywall_mcp.errors import MalformedPayloadError
from familywall_mcp.familywall.wire import coerce_bool
from familywall_mcp.models import DomainModel


class TimedSpan(DomainModel):
    """A time span for events with specific times (allDay == "false")."""

    start: datetime
    """Timezone-aware datetime in UTC."""

    end: datetime
    """Exclusive end, timezone-aware in UTC."""


class AllDaySpan(DomainModel):
    """A date span for all-day events (allDay == "true")."""

    start_date: date
    """Calendar date, taken verbatim from the UTC date component."""

    end_date: date
    """Calendar date, taken verbatim from the UTC date component."""


class CalendarEvent(DomainModel):
    """A parsed FamilyWall calendar event occurrence."""

    occurrence_id: str
    """Event ID; unique per occurrence."""

    series_id: str
    """Event master ID; same for all occurrences in a series."""

    occurrence_index: int | None
    """Position in the series (string integer from upstream)."""

    title: str
    """Event title."""

    span: TimedSpan | AllDaySpan
    """Either timed or all-day time span."""

    raw_start: str
    """Raw upstream startDate string, preserved verbatim."""

    raw_end: str
    """Raw upstream endDate string, preserved verbatim."""

    event_type: str
    """Event type as received (e.g. "UNKNOWN", "BIRTHDAY_ACCOUNT")."""

    calendar_id: str | None
    """Calendar ID (e.g. "calendar/<family_id>")."""

    event_timezone: str | None
    """IANA timezone name as received, unvalidated."""

    location: str | None
    """Event location."""

    description: str | None
    """Event description."""

    recurrence_rule: str | None
    """iCal RRULE string, preserved verbatim."""

    is_recurring: bool
    """True if recurrency is not in (None, "", "NONE")."""

    is_series_exception: bool
    """True if recurrencyExceptionOfId is present."""


class ParsedEvents(DomainModel):
    """Result of parsing a calendar event list."""

    events: tuple[CalendarEvent, ...]
    """Successfully parsed events."""

    skipped: int
    """Count of malformed events skipped."""


def parse_events(payload: object) -> ParsedEvents:
    """Parse calendar events from a FamilyWall API response.

    Accepts a bare array of events or, tolerantly, an object wrapping it
    under known keys (events, datas, updatedCreated, results).

    Malformed individual events are skipped; a malformed payload raises
    MalformedPayloadError.

    Args:
        payload: The parsed JSON object or array from an evtlistinterval response.

    Returns:
        ParsedEvents with successfully parsed events and a count of skipped ones.

    Raises:
        MalformedPayloadError: If payload is not a recognized shape.
    """
    events_list: list[Any] | None = None

    # Handle bare array (the real shape from FamilyWall)
    if isinstance(payload, list):
        events_list = payload
    # Handle wrapped arrays
    elif isinstance(payload, dict):
        for key in ("events", "datas", "updatedCreated", "results"):
            if key in payload and isinstance(payload[key], list):
                events_list = payload[key]
                break

    if events_list is None:
        raise MalformedPayloadError()

    parsed_events: list[CalendarEvent] = []
    skipped = 0

    for event_obj in events_list:
        try:
            event = _parse_single_event(event_obj)
            parsed_events.append(event)
        except (KeyError, ValueError, MalformedPayloadError):
            skipped += 1

    return ParsedEvents(events=tuple(parsed_events), skipped=skipped)


def _parse_single_event(obj: Any) -> CalendarEvent:
    """Parse a single event object.

    Args:
        obj: A raw event object from the API.

    Returns:
        A CalendarEvent.

    Raises:
        KeyError: For missing required fields.
        ValueError: For invalid field values.
        MalformedPayloadError: For type mismatches.
    """
    if not isinstance(obj, dict):
        raise ValueError("event must be an object")

    # Required string fields
    event_id: str = obj["eventId"]
    event_master_id: str = obj["eventMasterId"]
    title: str = obj["text"]
    raw_start: str = obj["startDate"]
    raw_end: str = obj["endDate"]
    all_day_str: str = obj["allDay"]

    if not isinstance(event_id, str):
        raise ValueError("eventId must be a string")
    if not isinstance(event_master_id, str):
        raise ValueError("eventMasterId must be a string")
    if not isinstance(title, str):
        raise ValueError("text must be a string")
    if not isinstance(raw_start, str):
        raise ValueError("startDate must be a string")
    if not isinstance(raw_end, str):
        raise ValueError("endDate must be a string")

    # Parse boolean (API sends "true"/"false" strings)
    all_day = coerce_bool(all_day_str)

    # Optional fields
    occurrence_index_raw = obj.get("occurenceIndex")
    occurrence_index: int | None = None
    if occurrence_index_raw is not None:
        if isinstance(occurrence_index_raw, str):
            occurrence_index = int(occurrence_index_raw)
        elif isinstance(occurrence_index_raw, int):
            occurrence_index = occurrence_index_raw
        else:
            raise ValueError("occurenceIndex must be a string or int")

    event_type: str = obj.get("eventType", "UNKNOWN")
    if not isinstance(event_type, str):
        raise ValueError("eventType must be a string")

    calendar_id: str | None = obj.get("calendarId")
    if calendar_id is not None and not isinstance(calendar_id, str):
        raise ValueError("calendarId must be a string or null")

    event_timezone: str | None = obj.get("timeZone")
    if event_timezone is not None and not isinstance(event_timezone, str):
        raise ValueError("timeZone must be a string or null")

    location: str | None = obj.get("where")
    if location is not None and not isinstance(location, str):
        raise ValueError("where must be a string or null")

    description: str | None = obj.get("description")
    if description is not None and not isinstance(description, str):
        raise ValueError("description must be a string or null")

    recurrence_rule: str | None = obj.get("rrule")
    if recurrence_rule is not None and not isinstance(recurrence_rule, str):
        raise ValueError("rrule must be a string or null")

    # Recurrency field: determine if recurring
    recurrency: str | None = obj.get("recurrency")
    if recurrency is not None and not isinstance(recurrency, str):
        raise ValueError("recurrency must be a string or null")
    is_recurring = recurrency not in (None, "", "NONE")

    # Check if this is a series exception
    is_series_exception = "recurrencyExceptionOfId" in obj

    # Parse the time span
    try:
        span = _parse_span(raw_start, raw_end, all_day)
    except ValueError as e:
        raise ValueError(f"failed to parse span: {e}") from e

    return CalendarEvent(
        occurrence_id=event_id,
        series_id=event_master_id,
        occurrence_index=occurrence_index,
        title=title,
        span=span,
        raw_start=raw_start,
        raw_end=raw_end,
        event_type=event_type,
        calendar_id=calendar_id,
        event_timezone=event_timezone,
        location=location,
        description=description,
        recurrence_rule=recurrence_rule,
        is_recurring=is_recurring,
        is_series_exception=is_series_exception,
    )


def _parse_span(raw_start: str, raw_end: str, all_day: bool) -> TimedSpan | AllDaySpan:
    """Parse start/end timestamps into a time span.

    For all-day events, extracts the date component without any timezone
    conversion. For timed events, parses as UTC instants.

    Args:
        raw_start: startDate string (format: YYYY-MM-DDTHH:MM:SS.sssZ)
        raw_end: endDate string (format: YYYY-MM-DDTHH:MM:SS.sssZ)
        all_day: Whether this is an all-day event.

    Returns:
        TimedSpan or AllDaySpan.

    Raises:
        ValueError: If timestamps cannot be parsed.
    """
    # Parse both as UTC datetimes
    try:
        start_dt = datetime.fromisoformat(raw_start.replace("Z", "+00:00"))
        end_dt = datetime.fromisoformat(raw_end.replace("Z", "+00:00"))
    except ValueError as e:
        raise ValueError(f"failed to parse timestamps: {e}") from e

    if all_day:
        # For all-day events, take the date component verbatim from the UTC
        # representation (no timezone conversion to the event's timezone).
        # The Z is a carrier, not a real instant.
        start_date = start_dt.date()
        end_date = end_dt.date()
        return AllDaySpan(start_date=start_date, end_date=end_date)
    else:
        # For timed events, the times are real UTC instants
        return TimedSpan(start=start_dt, end=end_dt)


def build_interval_fields(
    calendar_id: str,
    start: datetime,
    end: datetime,
) -> dict[str, str]:
    """Build query parameters for an evtlistinterval request.

    Constructs the interval boundary parameters in ISO 8601 format with
    explicit offset as required by FamilyWall.

    NOTE: The server ignores the calendarId parameter. It is sent here for
    contract fidelity only and does not act as an access boundary. No
    caller-supplied calendar ID may be treated as a filter.

    Args:
        calendar_id: Calendar ID to include in the request.
        start: Interval start (timezone-aware datetime).
        end: Interval end (timezone-aware datetime).

    Returns:
        A dict with keys: partnerScope, calendarId, a00from, a00to.
    """
    # Convert to ISO 8601 with explicit offset
    # isoformat() with timezoneinfo produces +HH:MM format
    start_iso = start.isoformat()
    end_iso = end.isoformat()

    return {
        "partnerScope": "Family",
        "calendarId": calendar_id,
        "a00from": start_iso,
        "a00to": end_iso,
    }
