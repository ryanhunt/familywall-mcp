"""Tests for CalendarService.set_event_attendees: lookup, one write, readback,
receipts (brief 09e, tests E3-E10, E12)."""

from __future__ import annotations

import json
from collections.abc import Sequence
from datetime import UTC, date, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from familywall_mcp.errors import (
    FamilyWallError,
    TransportError,
    UpstreamRejectedError,
)
from familywall_mcp.models import FamilyContext, OperationReceipt, Principal
from familywall_mcp.services.calendar import (
    CalendarService,
    EventNotFoundError,
    EventWriteOutcome,
    SetAttendeesResult,
    UnsupportedEventError,
)
from familywall_mcp.services.members import ResolvedAssignment
from familywall_mcp.services.ranges import resolve_days
from familywall_mcp.storage.memory import InMemoryReceiptRepository

SYDNEY = ZoneInfo("Australia/Sydney")
PRINCIPAL = Principal(subject="subject-a")
FAMILY = FamilyContext(
    account_id="acct-self",
    family_id="family-123",
    calendar_id="calendar/family-123",
)

NAMED_ASSIGNMENT = ResolvedAssignment(
    to_all=False, account_ids=("acct-robin",), display_names=("Robin",)
)

DEFAULT_REMINDER_ENTRY = {
    "localId": "reminder-1",
    "reminderType": "SNOOZE",
    "reminderUnit": "MINUTE",
    "reminderValue": "30",
}


def existing_event(**overrides: object) -> dict[str, object]:
    """A safe-to-update event: family calendar, editable, ordinary ("UNKNOWN"),
    timed, non-recurring, currently assigned to just "acct-self"."""
    event: dict[str, object] = {
        "eventId": "event/existing-1",
        "eventMasterId": "event/existing-1",
        "occurenceIndex": "0",
        "text": "Dentist",
        "startDate": "2026-10-05T23:00:00.000Z",  # 2026-10-06T10:00 Sydney
        "endDate": "2026-10-06T00:00:00.000Z",  # 2026-10-06T11:00 Sydney
        "allDay": "false",
        "timeZone": "Australia/Sydney",
        "recurrency": "NONE",
        "eventType": "UNKNOWN",
        "calendarId": "calendar/family-123",
        "where": "Main St",
        "description": "",
        "attendeeIds": ["acct-self"],
        "toAll": "false",
        "editable": "true",
        "reminderList": [dict(DEFAULT_REMINDER_ENTRY)],
    }
    event.update(overrides)
    return event


class ScriptedTransport:
    """Replays a fixed script of (endpoint, response) pairs, in order, and
    records every call. Raises AssertionError on any unscripted extra call or
    endpoint mismatch, so an accidental retry or extra write fails loudly."""

    def __init__(self, script: Sequence[tuple[str, object]]) -> None:
        self._script = list(script)
        self.calls: list[tuple[str, dict[str, str]]] = []

    async def call(self, endpoint: str, fields: dict[str, str]) -> object:
        self.calls.append((endpoint, dict(fields)))
        if not self._script:
            raise AssertionError(f"unexpected extra call to {endpoint!r}")
        expected_endpoint, response = self._script.pop(0)
        if expected_endpoint != endpoint:
            raise AssertionError(f"expected a call to {expected_endpoint!r}, got {endpoint!r}")
        if isinstance(response, Exception):
            raise response
        return response

    @property
    def endpoints(self) -> list[str]:
        return [endpoint for endpoint, _ in self.calls]


def lookup_then_update_then_confirm(
    before: dict[str, object], after: dict[str, object] | None = None
) -> ScriptedTransport:
    """The happy-path script: evtlistinterval, evtupdate, evtlistinterval.

    ``after`` defaults to ``before`` (an unchanged readback)."""
    return ScriptedTransport(
        [
            ("evtlistinterval", [before]),
            ("evtupdate", {"eventId": before["eventId"], "text": before["text"]}),
            ("evtlistinterval", [after if after is not None else before]),
        ]
    )


async def set_attendees(
    transport: ScriptedTransport,
    receipts: InMemoryReceiptRepository | None = None,
    event_id: str = "event/existing-1",
    local_day: date | None = None,
    timezone: str = "Australia/Sydney",
    assignment: ResolvedAssignment | None = None,
    operation_id: str = "op-1",
) -> SetAttendeesResult:
    return await CalendarService(transport).set_event_attendees(
        PRINCIPAL,
        event_id,
        local_day or date(2026, 10, 6),
        timezone,
        assignment or NAMED_ASSIGNMENT,
        operation_id,
        receipts or InMemoryReceiptRepository(),
        FAMILY,
    )


class TestLookupAndRefusal:
    async def test_e3_unknown_event_id_gives_event_not_found(self) -> None:
        """E3: an unknown event ID gives event_not_found after exactly one read
        and no write."""
        transport = ScriptedTransport(
            [("evtlistinterval", [existing_event(eventId="event/other")])]
        )

        with pytest.raises(EventNotFoundError):
            await set_attendees(transport)

        assert transport.endpoints == ["evtlistinterval"]

    @pytest.mark.parametrize(
        "overrides",
        [
            {"calendarId": "calendarSpecialDays/acct-self"},
            {"editable": "false"},
            {"editable": None},
            {"recurrency": "WEEKLY"},
            {"recurrencyExceptionOfId": "event/master-1"},
            {"eventType": "BIRTHDAY_ACCOUNT"},
            {
                "allDay": "true",
                "startDate": "2026-10-06T00:00:00.000Z",
                "endDate": "2026-10-06T23:59:59.000Z",
            },
        ],
        ids=[
            "not_family_calendar",
            "editable_false",
            "editable_none",
            "recurring",
            "series_exception",
            "birthday_account_event",
            "all_day",
        ],
    )
    async def test_e4_each_refusal_case_makes_zero_writes_and_no_receipt(
        self, overrides: dict[str, object]
    ) -> None:
        """E4: every unsafe target is refused with unsupported_event, after
        only the lookup read, with no write and no receipt."""
        event = existing_event(**overrides)
        transport = ScriptedTransport([("evtlistinterval", [event])])
        receipts = InMemoryReceiptRepository()

        with pytest.raises(UnsupportedEventError):
            await set_attendees(transport, receipts)

        assert transport.endpoints == ["evtlistinterval"]
        assert await receipts.get(PRINCIPAL, "op-1") is None

    async def test_e12_lookup_window_is_the_local_day_and_dst_correct(self) -> None:
        """E12: the lookup window is resolve_days(date, 1, member_tz); on
        Australia/Sydney 2026-10-04 (a spring-forward day) that is 23 hours.

        (Subtracting the two aware datetimes directly would give 24h: they
        share one ``ZoneInfo`` instance, so Python's fast path for
        same-``tzinfo`` subtraction compares wall-clock fields only and skips
        the UTC-offset adjustment DST needs here. Converting to UTC first
        avoids that trap.)"""
        expected = resolve_days(date(2026, 10, 4), 1, "Australia/Sydney")
        elapsed = expected.end.astimezone(UTC) - expected.start.astimezone(UTC)
        assert elapsed == timedelta(hours=23)

        transport = lookup_then_update_then_confirm(existing_event())

        await set_attendees(transport, local_day=date(2026, 10, 4))

        _, lookup_fields = transport.calls[0]
        assert lookup_fields["a00from"] == expected.start.isoformat()
        assert lookup_fields["a00to"] == expected.end.isoformat()
        _, confirm_fields = transport.calls[2]
        assert confirm_fields["a00from"] == expected.start.isoformat()
        assert confirm_fields["a00to"] == expected.end.isoformat()


class TestConfirmation:
    async def test_e5_confirmed_when_only_attendees_change_in_the_right_order(self) -> None:
        """E5: attendees changed as requested, nothing else did; the endpoint
        order is evtlistinterval, evtupdate, evtlistinterval."""
        before = existing_event()
        after = existing_event(attendeeIds=["acct-robin"], toAll="false")
        transport = lookup_then_update_then_confirm(before, after)

        result = await set_attendees(transport)

        assert result.outcome is EventWriteOutcome.CONFIRMED
        assert result.event_id == "event/existing-1"
        assert transport.endpoints == ["evtlistinterval", "evtupdate", "evtlistinterval"]

    async def test_e1_write_sends_exactly_the_six_field_kinds(self) -> None:
        """The evtupdate call carries exactly the builder's six field kinds."""
        transport = lookup_then_update_then_confirm(
            existing_event(), existing_event(attendeeIds=["acct-robin"], toAll="false")
        )

        await set_attendees(transport)

        _, fields = transport.calls[1]
        assert fields == {
            "partnerScope": "Family",
            "option": "All",
            "calendarId": "calendar/family-123",
            "metaId": "event/existing-1",
            "isToAll": "false",
            "attendee.0.accountId": "acct-robin",
        }

    @pytest.mark.parametrize(
        ("overrides", "field"),
        [
            ({"text": "Dentist!"}, "title"),
            ({"where": "Elsewhere"}, "location"),
            ({"description": "unexpected"}, "description"),
            ({"reminderList": []}, "reminder"),
        ],
    )
    async def test_e6_a_non_attendee_field_changing_is_mismatched(
        self, overrides: dict[str, object], field: str
    ) -> None:
        """E6: a non-attendee field changing on readback (e.g. description
        cleared or reminderList dropped) gives mismatched, naming that field."""
        before = existing_event()
        after = existing_event(attendeeIds=["acct-robin"], toAll="false", **overrides)
        transport = lookup_then_update_then_confirm(before, after)

        result = await set_attendees(transport)

        assert result.outcome is EventWriteOutcome.MISMATCHED
        assert field in result.mismatched_fields

    @pytest.mark.parametrize(
        ("assignment", "readback_overrides"),
        [
            (
                ResolvedAssignment(
                    to_all=False,
                    account_ids=("acct-alex", "acct-robin"),
                    display_names=("Alex", "Robin"),
                ),
                {"attendeeIds": ["acct-alex"], "toAll": "false"},  # missing acct-robin
            ),
            (
                ResolvedAssignment(
                    to_all=True,
                    account_ids=("acct-alex", "acct-robin"),
                    display_names=("Alex", "Robin"),
                ),
                {"attendeeIds": [], "toAll": "false"},  # everyone requested, toAll false
            ),
        ],
        ids=["missing_id", "everyone_but_toall_false"],
    )
    async def test_e7_attendee_mismatch_cases(
        self, assignment: ResolvedAssignment, readback_overrides: dict[str, object]
    ) -> None:
        """E7: an attendee mismatch gives mismatched with 'attendees' named."""
        before = existing_event()
        after = existing_event(**readback_overrides)
        transport = lookup_then_update_then_confirm(before, after)

        result = await set_attendees(transport, assignment=assignment)

        assert result.outcome is EventWriteOutcome.MISMATCHED
        assert "attendees" in result.mismatched_fields


class TestAcknowledgedAndUnknown:
    async def test_e8_lost_write_is_unknown_and_never_retried(self) -> None:
        """E8: a lost write gives unknown, with exactly one evtupdate call and
        no retry."""
        transport = ScriptedTransport(
            [
                ("evtlistinterval", [existing_event()]),
                ("evtupdate", TransportError()),
            ]
        )

        result = await set_attendees(transport)

        assert result.outcome is EventWriteOutcome.UNKNOWN
        assert transport.endpoints == ["evtlistinterval", "evtupdate"]

    async def test_readback_failure_is_acknowledged(self) -> None:
        transport = ScriptedTransport(
            [
                ("evtlistinterval", [existing_event()]),
                ("evtupdate", {"eventId": "event/existing-1"}),
                ("evtlistinterval", TransportError()),
            ]
        )

        result = await set_attendees(transport)

        assert result.outcome is EventWriteOutcome.ACKNOWLEDGED
        assert result.event_id == "event/existing-1"


class TestReceipts:
    async def test_e9_refused_write_records_rejected_and_replays_the_same_error(
        self,
    ) -> None:
        """E9: a refused write records 'rejected' with the error JSON; a
        replay raises the same error code, with zero calls."""
        error = UpstreamRejectedError()
        receipts = InMemoryReceiptRepository()
        transport = ScriptedTransport(
            [("evtlistinterval", [existing_event()]), ("evtupdate", error)]
        )

        with pytest.raises(UpstreamRejectedError):
            await set_attendees(transport, receipts)

        receipt = await receipts.get(PRINCIPAL, "op-1")
        assert receipt is not None
        assert receipt.status == "rejected"
        stored = json.loads(receipt.upstream_id or "{}")
        assert stored == {
            "code": error.info.code,
            "message": error.info.message,
            "recovery": error.info.recovery,
        }

        replay_transport = ScriptedTransport([])
        with pytest.raises(FamilyWallError) as excinfo:
            await set_attendees(replay_transport, receipts)

        assert excinfo.value.info.code == error.info.code
        assert replay_transport.calls == []

    async def test_e10_receipt_fields_and_zero_call_replay(self) -> None:
        """E10: the receipt carries resource_id=event_id and
        action='calendar.set_attendees'; a replay returns the stored result
        with zero calls."""
        receipts = InMemoryReceiptRepository()
        transport = lookup_then_update_then_confirm(
            existing_event(), existing_event(attendeeIds=["acct-robin"], toAll="false")
        )

        first = await set_attendees(transport, receipts)
        assert first.outcome is EventWriteOutcome.CONFIRMED

        receipt = await receipts.get(PRINCIPAL, "op-1")
        assert receipt is not None
        assert receipt.resource_id == "event/existing-1"
        assert receipt.action == "calendar.set_attendees"
        assert receipt.status == "succeeded"

        replay_transport = ScriptedTransport([])
        second = await set_attendees(replay_transport, receipts)

        assert second.outcome is EventWriteOutcome.CONFIRMED
        assert second.event_id == "event/existing-1"
        assert replay_transport.calls == []

    async def test_e10_same_key_different_assigned_to_conflicts(self) -> None:
        """E10: the same idempotency key with a different assigned_to is a
        conflict, with zero calls."""
        receipts = InMemoryReceiptRepository()
        await set_attendees(
            lookup_then_update_then_confirm(
                existing_event(), existing_event(attendeeIds=["acct-robin"], toAll="false")
            ),
            receipts,
        )
        different_assignment = ResolvedAssignment(
            to_all=False, account_ids=("acct-alex",), display_names=("Alex",)
        )
        transport = ScriptedTransport([])

        with pytest.raises(FamilyWallError) as excinfo:
            await set_attendees(transport, receipts, assignment=different_assignment)

        assert excinfo.value.info.code == "operation_id_conflict"
        assert transport.calls == []

    async def test_e10_key_used_by_another_action_conflicts(self) -> None:
        """E10: a key already used by a different action is a conflict, with
        zero calls."""
        receipts = InMemoryReceiptRepository()
        await receipts.put(
            OperationReceipt(
                subject=PRINCIPAL.subject,
                family_id=FAMILY.family_id,
                resource_id=FAMILY.calendar_id,
                action="calendar.create_event",
                operation_id="op-1",
                payload_hash="some-other-hash",
                status="succeeded",
                upstream_id='{"outcome": "confirmed", "event_id": "event/other"}',
                expires_at=datetime(2099, 1, 1, tzinfo=SYDNEY),
            )
        )
        transport = ScriptedTransport([])

        with pytest.raises(FamilyWallError) as excinfo:
            await set_attendees(transport, receipts)

        assert excinfo.value.info.code == "operation_id_conflict"
        assert transport.calls == []

    async def test_crash_mid_write_replays_as_unknown(self) -> None:
        receipts = InMemoryReceiptRepository()
        await set_attendees(
            ScriptedTransport(
                [("evtlistinterval", [existing_event()]), ("evtupdate", TransportError())]
            ),
            receipts,
        )
        receipt = await receipts.get(PRINCIPAL, "op-1")
        assert receipt is not None
        await receipts.put(receipt.model_copy(update={"status": "pending"}))

        transport = ScriptedTransport([])
        result = await set_attendees(transport, receipts)

        assert result.outcome is EventWriteOutcome.UNKNOWN
        assert transport.calls == []

    async def test_empty_operation_id_is_rejected_before_any_call(self) -> None:
        transport = ScriptedTransport([])

        with pytest.raises(ValueError):
            await set_attendees(transport, operation_id="  ")

        assert transport.calls == []

    async def test_receipts_are_isolated_by_subject(self) -> None:
        receipts = InMemoryReceiptRepository()
        await set_attendees(
            lookup_then_update_then_confirm(
                existing_event(), existing_event(attendeeIds=["acct-robin"], toAll="false")
            ),
            receipts,
        )
        other_transport = lookup_then_update_then_confirm(
            existing_event(), existing_event(attendeeIds=["acct-robin"], toAll="false")
        )

        result = await CalendarService(other_transport).set_event_attendees(
            Principal(subject="subject-b"),
            "event/existing-1",
            date(2026, 10, 6),
            "Australia/Sydney",
            NAMED_ASSIGNMENT,
            "op-1",
            receipts,
            FAMILY,
        )

        assert result.outcome is EventWriteOutcome.CONFIRMED
        assert other_transport.endpoints == ["evtlistinterval", "evtupdate", "evtlistinterval"]
