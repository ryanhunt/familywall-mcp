"""Single-event calendar writes: update and delete (create lives in CalendarService).

Scope is deliberately narrow. Only single, non-recurring events are updated or
deleted. The only delete the protocol is known to offer (`evtdelete` with
`option=All`) plausibly removes a whole series, so recurring events and series
exceptions are refused before any delete request is sent.

Both operations follow the list mutations' receipt discipline: a receipt is
written before the request leaves the process, an uncertain outcome is recorded
as UNKNOWN and never retried, and replaying an operation ID returns the stored
outcome without contacting FamilyWall again.
"""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, date, datetime, time, timedelta
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from familywall_mcp.errors import (
    ErrorInfo,
    FamilyWallError,
    InvalidEnvelopeError,
    MalformedPayloadError,
    RateLimitedError,
    TransportError,
)
from familywall_mcp.familywall.calendar import (
    AllDaySpan,
    CalendarEvent,
    Reminder,
    TimedSpan,
    all_day_wire_bounds,
    build_delete_event_fields,
    build_event_id_fields,
    build_interval_fields,
    build_update_event_fields,
    parse_event_options,
    parse_events,
    parse_single_event,
    reminder_fields,
    typed_reminders,
    utc_wall_clock,
)
from familywall_mcp.interfaces import ReceiptRepository
from familywall_mcp.models import DomainModel, OperationReceipt, Principal, ReceiptAction
from familywall_mcp.services.calendar import CalendarTransport
from familywall_mcp.services.lists import WriteOutcome

MAX_TITLE_LENGTH = 500
MAX_EVENT_DAYS = 366
RECEIPT_TTL = timedelta(hours=24)

# The request left the process but its effect is unknowable from here.
_UNCERTAIN_ERRORS = (TransportError, RateLimitedError, InvalidEnvelopeError, MalformedPayloadError)


class EventWriteResult(DomainModel):
    """Outcome of an event create or delete."""

    outcome: WriteOutcome
    event_id: str | None = None
    reminders: tuple[Reminder, ...] | None = None
    """For a create: the reminders sent, after resolving the member's default."""


def _invalid(message: str) -> FamilyWallError:
    return FamilyWallError(
        ErrorInfo(
            code="invalid_event",
            message=message,
            recovery="Correct the event details and try again.",
        )
    )


def _payload_hash(fields: dict[str, str]) -> str:
    return hashlib.sha256(json.dumps(fields, sort_keys=True).encode()).hexdigest()


def _zone(timezone: str) -> ZoneInfo:
    try:
        return ZoneInfo(timezone)
    except (ZoneInfoNotFoundError, ValueError) as exc:
        raise _invalid("The timezone is not a known IANA zone.") from exc


def _local_day(on_date: date, zone: ZoneInfo) -> tuple[datetime, datetime]:
    """The half-open local-midnight window covering one calendar day."""
    start = datetime.combine(on_date, time(0), tzinfo=zone)
    end = datetime.combine(on_date + timedelta(days=1), time(0), tzinfo=zone)
    return start, end


def _same_reminders(actual: tuple[Reminder, ...], expected: tuple[Reminder, ...]) -> bool:
    def key(r: Reminder) -> tuple[str, int]:
        return (r.unit, r.value)

    return sorted(map(key, actual)) == sorted(map(key, expected))


def _looks_like_series(event: CalendarEvent) -> bool:
    """Client-side series check, from the event's own recurrence fields."""
    return event.is_recurring or event.is_series_exception or event.series_id != event.occurrence_id


def _series_error(verb: str) -> FamilyWallError:
    past = {"delete": "deleted", "update": "changed"}[verb]
    return FamilyWallError(
        ErrorInfo(
            code=f"recurring_event_{verb}_unsupported",
            message=(
                f"This event belongs to a recurring series. Doing this here could "
                f"affect the whole series, so it was not {past}."
            ),
            recovery=f"{verb.capitalize()} this occurrence in the FamilyWall app.",
        )
    )


def _updated_span(
    span: TimedSpan | AllDaySpan,
    start: datetime | date | None,
    end: datetime | date | None,
) -> TimedSpan | AllDaySpan | None:
    """The span after applying a new start and/or end, or None if unchanged.

    A new start without a new end moves the event and keeps its length.
    """
    if start is None and end is None:
        return None
    if isinstance(span, TimedSpan):
        for value in (start, end):
            if value is not None and (not isinstance(value, datetime) or value.tzinfo is None):
                raise _invalid("A timed event takes times with a timezone, not dates.")
        assert start is None or isinstance(start, datetime)
        assert end is None or isinstance(end, datetime)
        new_start = start if start is not None else span.start
        if end is not None:
            new_end = end
        elif start is not None:
            new_end = start + (span.end - span.start)
        else:
            new_end = span.end
        if new_end <= new_start:
            raise _invalid("The event ends before it starts.")
        if new_end - new_start >= timedelta(days=MAX_EVENT_DAYS):
            raise _invalid(f"The event is longer than {MAX_EVENT_DAYS} days.")
        return TimedSpan(start=new_start.astimezone(UTC), end=new_end.astimezone(UTC))

    for value in (start, end):
        if value is not None and isinstance(value, datetime):
            raise _invalid("An all-day event takes dates, not times.")
    new_first = start if start is not None else span.start_date
    if end is not None:
        new_last = end
    elif start is not None:
        new_last = start + (span.end_date - span.start_date)
    else:
        new_last = span.end_date
    if new_last < new_first:
        raise _invalid("The event ends before it starts.")
    if (new_last - new_first).days >= MAX_EVENT_DAYS:
        raise _invalid(f"The event is longer than {MAX_EVENT_DAYS} days.")
    return AllDaySpan(start_date=new_first, end_date=new_last)


class CalendarWriteService:
    """Creates and deletes single calendar events."""

    def __init__(self, transport: CalendarTransport) -> None:
        self.transport = transport

    async def delete_event(
        self,
        principal: Principal,
        *,
        event_id: str,
        on_date: date,
        timezone: str,
        operation_id: str,
        receipt_repository: ReceiptRepository,
        family_id: str,
        calendar_id: str,
    ) -> EventWriteResult:
        """Delete one single, non-recurring event found on `on_date`.

        The event is looked up on that local day first. It must exist there and
        must be neither part of a series nor a series exception, or nothing is
        sent. The day is re-read afterwards to confirm the event is gone.

        Returns:
            CONFIRMED when the readback no longer shows the event, ACKNOWLEDGED
            when the readback failed, UNKNOWN when the server did not confirm the
            delete or the event is still present.

        Raises:
            FamilyWallError: `invalid_event`, `event_not_found`,
                `recurring_event_delete_unsupported` (all with no delete sent), or
                `operation_id_conflict`.
        """
        if not operation_id or not operation_id.strip():
            raise ValueError("operation_id must not be empty")

        try:
            fields = build_delete_event_fields(event_id)
        except ValueError as exc:
            raise _invalid("That is not an event identifier.") from exc
        zone = _zone(timezone)
        window = _local_day(on_date, zone)

        payload_hash = _payload_hash(fields)
        replayed = await self._replay(
            "calendar.delete_event", principal, operation_id, payload_hash, receipt_repository
        )
        if replayed is not None:
            return replayed

        target = await self._find(event_id, calendar_id, window)
        if target is None:
            raise FamilyWallError(
                ErrorInfo(
                    code="event_not_found",
                    message="No event with that identifier is on that day.",
                    recovery="Check the event and its date with get_week_overview.",
                )
            )
        if _looks_like_series(target):
            raise _series_error("delete")
        await self._require_single(event_id, "evtdeleteoption", "delete")

        await self._record(
            "calendar.delete_event",
            receipt_repository,
            principal,
            family_id,
            calendar_id,
            operation_id,
            payload_hash,
            "pending",
        )

        try:
            response = await self.transport.call("evtdelete", fields)
        except _UNCERTAIN_ERRORS:
            await self._record(
                "calendar.delete_event",
                receipt_repository,
                principal,
                family_id,
                calendar_id,
                operation_id,
                payload_hash,
                "unknown",
            )
            return EventWriteResult(outcome=WriteOutcome.UNKNOWN, event_id=event_id)

        if response not in ("true", True):
            outcome = WriteOutcome.UNKNOWN
        else:
            try:
                still_there = await self._find(event_id, calendar_id, window) is not None
            except FamilyWallError:
                outcome = WriteOutcome.ACKNOWLEDGED
            else:
                outcome = WriteOutcome.UNKNOWN if still_there else WriteOutcome.CONFIRMED

        result = EventWriteResult(outcome=outcome, event_id=event_id)
        status = "unknown" if outcome is WriteOutcome.UNKNOWN else "succeeded"
        await self._record(
            "calendar.delete_event",
            receipt_repository,
            principal,
            family_id,
            calendar_id,
            operation_id,
            payload_hash,
            status,
            result,
        )
        return result

    async def update_event(
        self,
        principal: Principal,
        *,
        event_id: str,
        title: str | None,
        start: datetime | date | None,
        end: datetime | date | None,
        location: str | None,
        description: str | None,
        reminders: tuple[Reminder, ...] | None = None,
        timezone: str,
        operation_id: str,
        receipt_repository: ReceiptRepository,
        family_id: str,
        calendar_id: str,
    ) -> EventWriteResult:
        """Change some fields of one single, non-recurring event.

        Only the given fields are sent; evtupdate leaves every other field,
        including reminders and attendees, as it was. Moving only the start keeps
        the duration. A timed event takes datetimes and an all-day event takes
        dates; converting between the two is not supported. The event is read
        with evtget before and after the update.

        Returns:
            CONFIRMED when the re-read shows every change, UNKNOWN when it does
            not or the request's effect is unknowable, ACKNOWLEDGED when the
            server accepted the update but the re-read failed.

        Raises:
            FamilyWallError: `invalid_event`, `recurring_event_update_unsupported`
                (both with no update sent), or `operation_id_conflict`.
        """
        if not operation_id or not operation_id.strip():
            raise ValueError("operation_id must not be empty")
        if not event_id.startswith("event/"):
            raise _invalid("That is not an event identifier.")
        if title is not None:
            title = title.strip()
            if not title:
                raise _invalid("The event needs a title.")
            if len(title) > MAX_TITLE_LENGTH:
                raise _invalid(f"The title is longer than {MAX_TITLE_LENGTH} characters.")
        if all(v is None for v in (title, start, end, location, description, reminders)):
            raise _invalid("Nothing to change.")
        _zone(timezone)

        # Keyed on the request, not the wire fields, so a replay needs no read.
        request_hash = _payload_hash(
            {
                "event_id": event_id,
                "title": repr(title),
                "start": repr(start.isoformat() if start is not None else None),
                "end": repr(end.isoformat() if end is not None else None),
                "location": repr(location),
                "description": repr(description),
                "reminders": repr(None if reminders is None else [r.label() for r in reminders]),
            }
        )
        replayed = await self._replay(
            "calendar.update_event", principal, operation_id, request_hash, receipt_repository
        )
        if replayed is not None:
            return replayed

        current = parse_single_event(
            await self.transport.call("evtget", build_event_id_fields(event_id))
        )
        if _looks_like_series(current):
            raise _series_error("update")

        changes: dict[str, str] = {}
        if title is not None:
            changes["text"] = title
        if location is not None:
            changes["where"] = location
        if description is not None:
            changes["description"] = description
        if reminders is not None:
            changes.update(reminder_fields(reminders))
        new_span = _updated_span(current.span, start, end)
        if new_span is not None:
            if isinstance(new_span, TimedSpan):
                changes["startDate"] = utc_wall_clock(new_span.start)
                changes["endDate"] = utc_wall_clock(new_span.end)
            else:
                # Without allDay the server stores the new bounds as a timed event
                # (live evidence, 2026-10-10).
                changes["allDay"] = "true"
                changes["startDate"], changes["endDate"] = all_day_wire_bounds(
                    new_span.start_date, new_span.end_date
                )
        fields = build_update_event_fields(event_id, changes)

        await self._require_single(event_id, "evtupdateoption", "update")

        await self._record(
            "calendar.update_event",
            receipt_repository,
            principal,
            family_id,
            calendar_id,
            operation_id,
            request_hash,
            "pending",
        )
        try:
            await self.transport.call("evtupdate", fields)
        except _UNCERTAIN_ERRORS:
            await self._record(
                "calendar.update_event",
                receipt_repository,
                principal,
                family_id,
                calendar_id,
                operation_id,
                request_hash,
                "unknown",
            )
            return EventWriteResult(outcome=WriteOutcome.UNKNOWN, event_id=event_id)

        try:
            after = parse_single_event(
                await self.transport.call("evtget", build_event_id_fields(event_id))
            )
        except FamilyWallError:
            outcome = WriteOutcome.ACKNOWLEDGED
        else:
            applied = (
                (title is None or after.title == title)
                and (location is None or (after.location or "") == location)
                and (description is None or (after.description or "") == description)
                and (new_span is None or after.span == new_span)
                and (
                    reminders is None
                    or _same_reminders(typed_reminders(after.reminders), reminders)
                )
            )
            outcome = WriteOutcome.CONFIRMED if applied else WriteOutcome.UNKNOWN

        result = EventWriteResult(outcome=outcome, event_id=event_id)
        status = "unknown" if outcome is WriteOutcome.UNKNOWN else "succeeded"
        await self._record(
            "calendar.update_event",
            receipt_repository,
            principal,
            family_id,
            calendar_id,
            operation_id,
            request_hash,
            status,
            result,
        )
        return result

    async def _require_single(self, event_id: str, endpoint: str, verb: str) -> None:
        """Ask the server which options it offers; a single event offers only "All".

        A second check, independent of the client-side recurrence fields: for a
        series occurrence the server also offers "OnlyOne" and "OnlyAfter", and
        "All" would then apply to the whole series.
        """
        options = parse_event_options(
            await self.transport.call(endpoint, build_event_id_fields(event_id))
        )
        if options != ("All",):
            raise _series_error(verb)

    async def _replay(
        self,
        action: ReceiptAction,
        principal: Principal,
        operation_id: str,
        payload_hash: str,
        receipt_repository: ReceiptRepository,
    ) -> EventWriteResult | None:
        """Return the stored outcome for a known operation ID, or None if new."""
        receipt = await receipt_repository.get(principal, operation_id)
        if receipt is None:
            return None
        if receipt.action not in (action, "legacy") or receipt.payload_hash != payload_hash:
            raise FamilyWallError(
                ErrorInfo(
                    code="operation_id_conflict",
                    message="An operation with this ID already exists with different content.",
                    recovery="Use a different operation ID for this request.",
                )
            )
        if receipt.upstream_id:
            try:
                return EventWriteResult.model_validate_json(receipt.upstream_id)
            except ValueError:
                pass
        # A pending receipt means a process died mid-request; an unknown one
        # means the request's effect was never established. Neither is success.
        return EventWriteResult(outcome=WriteOutcome.UNKNOWN)

    async def _record(
        self,
        action: ReceiptAction,
        receipt_repository: ReceiptRepository,
        principal: Principal,
        family_id: str,
        calendar_id: str,
        operation_id: str,
        payload_hash: str,
        status: str,
        result: EventWriteResult | None = None,
    ) -> None:
        await receipt_repository.put(
            OperationReceipt(
                subject=principal.subject,
                family_id=family_id,
                resource_id=calendar_id,
                action=action,
                operation_id=operation_id,
                payload_hash=payload_hash,
                status=status,  # type: ignore[arg-type]
                upstream_id=result.model_dump_json() if result is not None else None,
                expires_at=datetime.now(UTC) + RECEIPT_TTL,
            )
        )

    async def _find(
        self,
        event_id: str,
        calendar_id: str,
        window: tuple[datetime, datetime],
    ) -> CalendarEvent | None:
        fields = build_interval_fields(calendar_id, *window)
        parsed = parse_events(await self.transport.call("evtlistinterval", fields))
        for event in parsed.events:
            if event.occurrence_id == event_id:
                return event
        return None
