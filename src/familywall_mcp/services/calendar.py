"""Weekly calendar overview service.

This service provides a weekly view of family calendar events, handling timezone
conversion, deduplication, and event assignment to days.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from datetime import UTC, date, datetime, timedelta
from enum import StrEnum
from typing import TYPE_CHECKING, Literal, Protocol
from zoneinfo import ZoneInfo, available_timezones

from pydantic import Field, ValidationError, model_validator

from familywall_mcp.errors import (
    AuthenticationError,
    ErrorInfo,
    FamilyWallError,
    InvalidEnvelopeError,
    MalformedPayloadError,
    RateLimitedError,
    TransportError,
    UpstreamRejectedError,
)
from familywall_mcp.familywall.calendar import (
    AllDaySpan,
    CalendarEvent,
    EventReminder,
    TimedSpan,
    build_create_event_fields,
    build_interval_fields,
    parse_created_event_id,
    parse_events,
)
from familywall_mcp.models import DomainModel, FamilyContext, OperationReceipt, Principal
from familywall_mcp.services.members import ResolvedAssignment
from familywall_mcp.services.ranges import resolve_days, resolve_week

if TYPE_CHECKING:
    from familywall_mcp.interfaces import ReceiptRepository

MAX_EVENT_DURATION = timedelta(days=14)
"""Longest timed event accepted; it also bounds the readback window."""

READBACK_MARGIN = timedelta(days=1)
"""Readback window padding, wide enough to find an event shifted by a zone error."""

RECEIPT_TTL = timedelta(hours=24)

DEFAULT_REMINDER = (EventReminder(type="SNOOZE", unit="MINUTE", value="30"),)
"""The web app's own default reminder for a timed event (probe A1, 2026-09-25)."""

_INDETERMINATE_ERRORS = (
    TransportError,
    RateLimitedError,
    InvalidEnvelopeError,
    MalformedPayloadError,
)


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


class EventWriteOutcome(StrEnum):
    """Four-state outcome of a calendar mutation."""

    CONFIRMED = "confirmed"  # a readback shows the event exactly as requested
    MISMATCHED = "mismatched"  # created, but the readback differs from the request
    ACKNOWLEDGED = "acknowledged"  # upstream accepted it; no readback confirmed it
    UNKNOWN = "unknown"  # lost, timed out, unparseable, or not known to have been sent


class NewTimedEvent(DomainModel):
    """A validated request for one timed, non-recurring event."""

    title: str = Field(min_length=1, max_length=200)
    start: datetime
    end: datetime
    timezone: str
    location: str | None = Field(default=None, max_length=500)
    description: str | None = Field(default=None, max_length=4000)

    @model_validator(mode="after")
    def validate_event(self) -> NewTimedEvent:
        if self.timezone not in available_timezones():
            raise ValueError(f"unknown timezone: {self.timezone}")
        if self.start.tzinfo is None or self.end.tzinfo is None:
            raise ValueError("start and end must be timezone-aware")
        if self.end <= self.start:
            raise ValueError("end must be after start")
        if self.end - self.start > MAX_EVENT_DURATION:
            raise ValueError(f"events longer than {MAX_EVENT_DURATION.days} days are not supported")
        return self


class CreateEventResult(DomainModel):
    """Result of creating a calendar event."""

    outcome: EventWriteOutcome
    event_id: str | None = None  # populated whenever the create response named the event
    # Populated if and only if outcome is mismatched:
    mismatched_fields: tuple[str, ...] = ()
    actual_start: str | None = None  # local ISO datetime, or a date for an all-day event
    actual_end: str | None = None


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

    async def create_event(
        self,
        principal: Principal,
        request: NewTimedEvent,
        assignment: ResolvedAssignment,
        operation_id: str,
        receipt_repository: ReceiptRepository,
        family_context: FamilyContext,
    ) -> CreateEventResult:
        """Create one timed, non-recurring event, assigned per ``assignment``.

        ``assignment`` is already resolved (names to account IDs) by the
        caller; no member name ever reaches this method or the wire, only
        account IDs. Everyone is sent as ``to_all=True`` plus every member's
        account ID (the web app's own encoding, probe A1 2026-09-25); named
        members are sent as ``to_all=False`` with just those IDs.

        evtcreate is sent at most once and never retried. The outcome is confirmed
        only when a readback finds the created event ID with exactly the requested
        title, instants, zone, location, description, attendees and reminder in
        the family calendar.

        Receipts carry ``resource_id=family_context.calendar_id`` and
        ``action="calendar.create_event"``; the payload hash is tagged with the
        endpoint so a key reused across tools conflicts rather than replays. The
        hash is built from the complete wire form, so it naturally covers the
        attendee and reminder fields too. A definite refusal
        (``UpstreamRejectedError`` or ``AuthenticationError``) is recorded as
        ``status="rejected"`` with the error's code, message and recovery so a
        replay raises the same error rather than reporting ``unknown``.

        Args:
            principal: The authenticated subject.
            request: The validated event.
            assignment: The resolved attendees (everyone, or named members).
            operation_id: Operation ID supplied by the caller; must not be empty.
            receipt_repository: Repository for receipt tracking and idempotency.
            family_context: The verified family context of ``principal``.

        Returns:
            CreateEventResult with the outcome.

        Raises:
            ValueError: If operation_id is empty.
            FamilyWallError: If operation_id was used for a different request.
            UpstreamRejectedError: If FamilyWall refused the create.
            AuthenticationError: If the session was not accepted for the create.
        """
        if not operation_id or not operation_id.strip():
            raise ValueError("operation_id must not be empty")

        tz = ZoneInfo(request.timezone)
        start = request.start.astimezone(tz)
        end = request.end.astimezone(tz)
        fields = build_create_event_fields(
            title=request.title,
            start=start,
            end=end,
            timezone=request.timezone,
            to_all=assignment.to_all,
            attendee_account_ids=assignment.account_ids,
            location=request.location,
            description=request.description,
        )
        payload_hash = _hash_payload("evtcreate", family_context.calendar_id, fields)

        existing = await receipt_repository.get(principal, operation_id)
        if existing is not None:
            return _replay_create(existing, payload_hash)

        async def record(
            status: Literal["pending", "succeeded", "unknown", "rejected"],
            result: CreateEventResult | None = None,
            upstream_id: str | None = None,
        ) -> None:
            await receipt_repository.put(
                OperationReceipt(
                    subject=principal.subject,
                    family_id=family_context.family_id,
                    resource_id=family_context.calendar_id,
                    action="calendar.create_event",
                    operation_id=operation_id,
                    payload_hash=payload_hash,
                    status=status,
                    upstream_id=(
                        upstream_id
                        if upstream_id is not None
                        else (result.model_dump_json() if result else None)
                    ),
                    expires_at=datetime.now(UTC) + RECEIPT_TTL,
                )
            )

        await record("pending")

        try:
            response = await self.transport.call("evtcreate", fields)
        except (UpstreamRejectedError, AuthenticationError) as exc:
            # A definite refusal: nothing was created. Record it as rejected so a
            # replay of this key raises the same refusal instead of resending.
            info = exc.info
            await record(
                "rejected",
                upstream_id=json.dumps(
                    {"code": info.code, "message": info.message, "recovery": info.recovery}
                ),
            )
            raise
        except _INDETERMINATE_ERRORS:
            await record("unknown")
            return CreateEventResult(outcome=EventWriteOutcome.UNKNOWN)

        event_id = parse_created_event_id(response)
        acknowledged = CreateEventResult(outcome=EventWriteOutcome.ACKNOWLEDGED, event_id=event_id)
        # Record the acknowledgement before the readback, so a crash replays as
        # acknowledged rather than unknown.
        await record("succeeded", acknowledged)
        if event_id is None:
            return acknowledged

        result = await self._confirm_created(
            event_id, request, assignment, start, end, family_context
        )
        await record("succeeded", result)
        return result

    async def _confirm_created(
        self,
        event_id: str,
        request: NewTimedEvent,
        assignment: ResolvedAssignment,
        start: datetime,
        end: datetime,
        family_context: FamilyContext,
    ) -> CreateEventResult:
        """Read the created event back and compare it with the request."""
        fields = build_interval_fields(
            family_context.calendar_id, start - READBACK_MARGIN, end + READBACK_MARGIN
        )
        try:
            payload = await self.transport.call("evtlistinterval", fields)
            parsed = parse_events(payload)
        except (*_INDETERMINATE_ERRORS, UpstreamRejectedError, AuthenticationError):
            return CreateEventResult(outcome=EventWriteOutcome.ACKNOWLEDGED, event_id=event_id)

        found = next((e for e in parsed.events if e.occurrence_id == event_id), None)
        if found is None:
            return CreateEventResult(outcome=EventWriteOutcome.ACKNOWLEDGED, event_id=event_id)

        mismatched = _readback_mismatches(
            found, request, assignment, start, end, family_context.calendar_id
        )
        if not mismatched:
            return CreateEventResult(outcome=EventWriteOutcome.CONFIRMED, event_id=event_id)

        tz = start.tzinfo
        if isinstance(found.span, TimedSpan):
            actual_start = found.span.start.astimezone(tz).isoformat()
            actual_end = found.span.end.astimezone(tz).isoformat()
        else:
            actual_start = found.span.start_date.isoformat()
            actual_end = found.span.end_date.isoformat()
        return CreateEventResult(
            outcome=EventWriteOutcome.MISMATCHED,
            event_id=event_id,
            mismatched_fields=mismatched,
            actual_start=actual_start,
            actual_end=actual_end,
        )


def _hash_payload(endpoint: str, calendar_id: str, fields: Mapping[str, str]) -> str:
    """Hash a write's full intent for operation-ID conflict detection."""
    canonical = json.dumps(
        {"endpoint": endpoint, "calendar_id": calendar_id, "fields": dict(fields)},
        sort_keys=True,
    )
    return hashlib.sha256(canonical.encode()).hexdigest()


def _replay_create(existing: OperationReceipt, payload_hash: str) -> CreateEventResult:
    """Resolve a repeated operation ID from its stored receipt, with no upstream call."""
    # A receipt written by a different tool (or reused across tools) never replays
    # as this one; a migrated "legacy" receipt is accepted either way.
    if existing.action not in ("calendar.create_event", "legacy"):
        raise FamilyWallError(
            ErrorInfo(
                code="operation_id_conflict",
                message="An operation with this ID already exists with different content.",
                recovery="Use a different operation ID for this request.",
            )
        )
    if existing.payload_hash != payload_hash:
        raise FamilyWallError(
            ErrorInfo(
                code="operation_id_conflict",
                message="An operation with this ID already exists with different content.",
                recovery="Use a different operation ID for this request.",
            )
        )
    if existing.status == "rejected":
        raise _rebuild_rejection(existing.upstream_id)
    if existing.status == "succeeded" and existing.upstream_id:
        try:
            return CreateEventResult.model_validate_json(existing.upstream_id)
        except ValidationError:
            return CreateEventResult(outcome=EventWriteOutcome.ACKNOWLEDGED)
    # pending (a crash mid-write) and unknown both resolve to unknown.
    return CreateEventResult(outcome=EventWriteOutcome.UNKNOWN)


def _rebuild_rejection(upstream_id: str | None) -> FamilyWallError:
    """Rebuild the original refusal from its stored JSON, for a rejected replay.

    Falls back to a plain ``UpstreamRejectedError`` if the JSON is missing or
    unreadable, so a replay never raises anything but a ``FamilyWallError``.
    """
    if upstream_id:
        try:
            info = json.loads(upstream_id)
            return FamilyWallError(
                ErrorInfo(code=info["code"], message=info["message"], recovery=info["recovery"])
            )
        except (json.JSONDecodeError, KeyError, TypeError):
            pass
    return UpstreamRejectedError()


def _readback_mismatches(
    event: CalendarEvent,
    request: NewTimedEvent,
    assignment: ResolvedAssignment,
    start: datetime,
    end: datetime,
    calendar_id: str,
) -> tuple[str, ...]:
    """Name every field where the read-back event differs from the request."""
    mismatched: list[str] = []
    if event.title != request.title:
        mismatched.append("title")
    if not isinstance(event.span, TimedSpan):
        mismatched.append("all_day")
    else:
        if event.span.start != start:
            mismatched.append("start")
        if event.span.end != end:
            mismatched.append("end")
    if event.event_timezone != request.timezone:
        mismatched.append("timezone")
    if (event.location or "") != (request.location or ""):
        mismatched.append("location")
    if (event.description or "") != (request.description or ""):
        mismatched.append("description")
    if event.is_recurring or event.is_series_exception:
        mismatched.append("recurrence")
    if event.calendar_id != calendar_id:
        mismatched.append("calendar")
    if assignment.to_all:
        attendees_ok = event.to_all is True
    else:
        attendees_ok = event.to_all is False and set(event.attendee_ids) == set(
            assignment.account_ids
        )
    if not attendees_ok:
        mismatched.append("attendees")
    if event.reminders != DEFAULT_REMINDER:
        mismatched.append("reminder")
    return tuple(mismatched)


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
