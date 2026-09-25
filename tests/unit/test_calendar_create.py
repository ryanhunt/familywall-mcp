"""Tests for CalendarService.create_event: one write, readback, receipts."""

from __future__ import annotations

import json
from collections.abc import Mapping
from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from familywall_mcp.errors import (
    FamilyWallError,
    SessionExpiredError,
    TransportError,
    UpstreamRejectedError,
)
from familywall_mcp.models import FamilyContext, OperationReceipt, Principal
from familywall_mcp.services.calendar import (
    CalendarService,
    CreateEventResult,
    EventWriteOutcome,
    NewTimedEvent,
)
from familywall_mcp.storage.memory import InMemoryReceiptRepository

SYDNEY = ZoneInfo("Australia/Sydney")
PRINCIPAL = Principal(subject="subject-a")
FAMILY = FamilyContext(
    account_id="acct-self",
    family_id="family-123",
    calendar_id="calendar/family-123",
)


def new_event(**overrides: object) -> NewTimedEvent:
    values: dict[str, object] = {
        "title": "Dentist",
        "start": datetime(2026, 10, 6, 10, 0, tzinfo=SYDNEY),
        "end": datetime(2026, 10, 6, 11, 0, tzinfo=SYDNEY),
        "timezone": "Australia/Sydney",
        "location": "Main St",
        "description": None,
    }
    values.update(overrides)
    return NewTimedEvent(**values)  # type: ignore[arg-type]


def stored_event(**overrides: str) -> dict[str, str]:
    """How the created event reads back: 10:00-11:00 Sydney is 23:00-00:00 UTC."""
    event = {
        "eventId": "event/new-1",
        "eventMasterId": "event/new-1",
        "occurenceIndex": "0",
        "text": "Dentist",
        "startDate": "2026-10-05T23:00:00.000Z",
        "endDate": "2026-10-06T00:00:00.000Z",
        "allDay": "false",
        "timeZone": "Australia/Sydney",
        "recurrency": "NONE",
        "eventType": "UNKNOWN",
        "calendarId": "calendar/family-123",
        "where": "Main St",
    }
    event.update(overrides)
    return event


class ScriptedTransport:
    """Returns (or raises) a scripted response per endpoint and records every call."""

    def __init__(self, responses: dict[str, object]) -> None:
        self.responses = responses
        self.calls: list[tuple[str, dict[str, str]]] = []

    async def call(self, endpoint: str, fields: Mapping[str, str]) -> object:
        self.calls.append((endpoint, dict(fields)))
        if endpoint not in self.responses:
            raise AssertionError(f"unexpected endpoint {endpoint}")
        response = self.responses[endpoint]
        if isinstance(response, Exception):
            raise response
        return response

    @property
    def endpoints(self) -> list[str]:
        return [endpoint for endpoint, _ in self.calls]


def created_then_read(*readback: dict[str, str]) -> ScriptedTransport:
    return ScriptedTransport(
        {
            "evtcreate": {"eventId": "event/new-1", "text": "Dentist"},
            "evtlistinterval": list(readback),
        }
    )


async def create(
    transport: ScriptedTransport,
    receipts: InMemoryReceiptRepository | None = None,
    request: NewTimedEvent | None = None,
    operation_id: str = "op-1",
) -> CreateEventResult:
    return await CalendarService(transport).create_event(
        PRINCIPAL,
        request or new_event(),
        operation_id,
        receipts or InMemoryReceiptRepository(),
        FAMILY,
    )


class TestConfirmation:
    async def test_exact_readback_is_confirmed(self) -> None:
        transport = created_then_read(stored_event())

        result = await create(transport)

        assert result.outcome is EventWriteOutcome.CONFIRMED
        assert result.event_id == "event/new-1"
        assert transport.endpoints == ["evtcreate", "evtlistinterval"]

    async def test_create_sends_the_verified_single_attendee_form(self) -> None:
        transport = created_then_read(stored_event())

        await create(transport)

        _, fields = transport.calls[0]
        assert fields["attendee.0.accountId"] == "acct-self"
        assert fields["isToAll"] == "false"
        assert fields["startDate"] == "2026-10-06T10:00:00+11:00"
        assert fields["timeZone"] == "Australia/Sydney"
        assert not any(key.startswith("attendee.1") for key in fields)

    async def test_start_in_another_zone_is_sent_in_the_event_zone(self) -> None:
        """An instant supplied in UTC is re-expressed with the event zone's offset."""
        transport = created_then_read(stored_event())
        request = new_event(
            start=datetime.fromisoformat("2026-10-05T23:00:00+00:00"),
            end=datetime.fromisoformat("2026-10-06T00:00:00+00:00"),
        )

        result = await create(transport, request=request)

        assert transport.calls[0][1]["startDate"] == "2026-10-06T10:00:00+11:00"
        assert result.outcome is EventWriteOutcome.CONFIRMED

    async def test_readback_window_is_padded_around_the_event(self) -> None:
        transport = created_then_read(stored_event())

        await create(transport)

        _, fields = transport.calls[1]
        assert fields["a00from"] == "2026-10-05T10:00:00+11:00"
        assert fields["a00to"] == "2026-10-07T11:00:00+11:00"
        assert fields["calendarId"] == "calendar/family-123"

    async def test_shifted_readback_is_mismatched_with_actual_times(self) -> None:
        """A zone misread by the server is reported, not confirmed."""
        transport = created_then_read(
            stored_event(startDate="2026-10-06T10:00:00.000Z", endDate="2026-10-06T11:00:00.000Z")
        )

        result = await create(transport)

        assert result.outcome is EventWriteOutcome.MISMATCHED
        assert result.event_id == "event/new-1"
        assert result.mismatched_fields == ("start", "end")
        assert result.actual_start == "2026-10-06T21:00:00+11:00"
        assert result.actual_end == "2026-10-06T22:00:00+11:00"
        assert transport.endpoints == ["evtcreate", "evtlistinterval"]

    @pytest.mark.parametrize(
        ("overrides", "field"),
        [
            ({"text": "Dentist!"}, "title"),
            ({"timeZone": "Europe/London"}, "timezone"),
            ({"where": "Elsewhere"}, "location"),
            ({"description": "unexpected"}, "description"),
            ({"recurrency": "WEEKLY"}, "recurrence"),
            ({"calendarId": "calendarSpecialDays/acct-self"}, "calendar"),
        ],
    )
    async def test_each_differing_field_is_named(
        self, overrides: dict[str, str], field: str
    ) -> None:
        transport = created_then_read(stored_event(**overrides))

        result = await create(transport)

        assert result.outcome is EventWriteOutcome.MISMATCHED
        assert result.mismatched_fields == (field,)

    async def test_missing_timezone_on_readback_is_not_confirmed(self) -> None:
        event = stored_event()
        del event["timeZone"]
        transport = created_then_read(event)

        result = await create(transport)

        assert result.outcome is EventWriteOutcome.MISMATCHED
        assert result.mismatched_fields == ("timezone",)

    async def test_all_day_readback_is_mismatched_with_dates(self) -> None:
        transport = created_then_read(
            stored_event(
                allDay="true",
                startDate="2026-10-06T00:00:00.000Z",
                endDate="2026-10-06T23:59:59.000Z",
            )
        )

        result = await create(transport)

        assert result.outcome is EventWriteOutcome.MISMATCHED
        assert result.mismatched_fields == ("all_day",)
        assert result.actual_start == "2026-10-06"

    async def test_other_events_in_the_window_are_ignored(self) -> None:
        """Only the created ID is matched; a lookalike event does not confirm."""
        lookalike = stored_event(eventId="event/old", eventMasterId="event/old")
        transport = created_then_read(lookalike)

        result = await create(transport)

        assert result.outcome is EventWriteOutcome.ACKNOWLEDGED
        assert result.event_id == "event/new-1"


class TestAcknowledgedAndUnknown:
    async def test_readback_failure_is_acknowledged(self) -> None:
        transport = ScriptedTransport(
            {"evtcreate": {"eventId": "event/new-1"}, "evtlistinterval": TransportError()}
        )

        result = await create(transport)

        assert result.outcome is EventWriteOutcome.ACKNOWLEDGED
        assert result.event_id == "event/new-1"

    async def test_expired_session_on_readback_is_acknowledged(self) -> None:
        transport = ScriptedTransport(
            {"evtcreate": {"eventId": "event/new-1"}, "evtlistinterval": SessionExpiredError()}
        )

        result = await create(transport)

        assert result.outcome is EventWriteOutcome.ACKNOWLEDGED

    async def test_create_response_without_an_id_skips_readback(self) -> None:
        """Without an ID nothing can be matched exactly, so nothing is confirmed."""
        transport = ScriptedTransport({"evtcreate": "true"})

        result = await create(transport)

        assert result.outcome is EventWriteOutcome.ACKNOWLEDGED
        assert result.event_id is None
        assert transport.endpoints == ["evtcreate"]

    async def test_lost_create_is_unknown_and_never_retried(self) -> None:
        transport = ScriptedTransport({"evtcreate": TransportError()})

        result = await create(transport)

        assert result.outcome is EventWriteOutcome.UNKNOWN
        assert transport.endpoints == ["evtcreate"]

    @pytest.mark.parametrize("error", [UpstreamRejectedError(), SessionExpiredError()])
    async def test_refused_create_raises_after_one_call(self, error: Exception) -> None:
        transport = ScriptedTransport({"evtcreate": error})

        with pytest.raises(type(error)):
            await create(transport)

        assert transport.endpoints == ["evtcreate"]


class TestReceipts:
    async def test_replay_returns_the_stored_result_without_calls(self) -> None:
        receipts = InMemoryReceiptRepository()
        await create(created_then_read(stored_event()), receipts)
        replay_transport = ScriptedTransport({})

        result = await create(replay_transport, receipts)

        assert result.outcome is EventWriteOutcome.CONFIRMED
        assert result.event_id == "event/new-1"
        assert replay_transport.calls == []

    async def test_replay_keeps_a_mismatch_and_its_details(self) -> None:
        receipts = InMemoryReceiptRepository()
        shifted = stored_event(startDate="2026-10-06T10:00:00.000Z")
        await create(created_then_read(shifted), receipts)

        result = await create(ScriptedTransport({}), receipts)

        assert result.outcome is EventWriteOutcome.MISMATCHED
        assert result.mismatched_fields == ("start",)
        assert result.actual_start == "2026-10-06T21:00:00+11:00"

    async def test_reused_key_with_different_content_conflicts(self) -> None:
        receipts = InMemoryReceiptRepository()
        await create(created_then_read(stored_event()), receipts)
        transport = ScriptedTransport({})

        with pytest.raises(FamilyWallError) as excinfo:
            await create(transport, receipts, request=new_event(title="Physio"))

        assert excinfo.value.info.code == "operation_id_conflict"
        assert transport.calls == []

    async def test_key_used_by_a_list_write_conflicts(self) -> None:
        """R12: an existing receipt with a list.* action is a conflict, zero calls."""
        receipts = InMemoryReceiptRepository()
        await receipts.put(
            OperationReceipt(
                subject=PRINCIPAL.subject,
                family_id=FAMILY.family_id,
                resource_id="taskList/1",
                action="list.add_item",
                operation_id="op-1",
                payload_hash="list-hash",
                status="succeeded",
                upstream_id='{"outcome": "confirmed", "item_id": "task/1"}',
                expires_at=datetime(2099, 1, 1, tzinfo=SYDNEY),
            )
        )
        transport = ScriptedTransport({})

        with pytest.raises(FamilyWallError) as excinfo:
            await create(transport, receipts)

        assert excinfo.value.info.code == "operation_id_conflict"
        assert transport.calls == []

    async def test_crash_mid_write_replays_as_unknown(self) -> None:
        receipts = InMemoryReceiptRepository()
        await create(ScriptedTransport({"evtcreate": TransportError()}), receipts)
        receipt = await receipts.get(PRINCIPAL, "op-1")
        assert receipt is not None
        await receipts.put(receipt.model_copy(update={"status": "pending"}))
        transport = ScriptedTransport({})

        result = await create(transport, receipts)

        assert result.outcome is EventWriteOutcome.UNKNOWN
        assert transport.calls == []

    @pytest.mark.parametrize("error", [UpstreamRejectedError(), SessionExpiredError()])
    async def test_refused_create_records_rejected_and_replays_the_same_error(
        self, error: FamilyWallError
    ) -> None:
        """R11: a refused create records 'rejected' with the error JSON; a replay
        raises the same error code, with zero calls."""
        receipts = InMemoryReceiptRepository()
        with pytest.raises(type(error)):
            await create(ScriptedTransport({"evtcreate": error}), receipts)

        receipt = await receipts.get(PRINCIPAL, "op-1")
        assert receipt is not None
        assert receipt.status == "rejected"
        stored = json.loads(receipt.upstream_id or "{}")
        assert stored == {
            "code": error.info.code,
            "message": error.info.message,
            "recovery": error.info.recovery,
        }

        transport = ScriptedTransport({})
        with pytest.raises(FamilyWallError) as excinfo:
            await create(transport, receipts)

        assert excinfo.value.info.code == error.info.code
        assert transport.calls == []

    async def test_legacy_receipt_with_matching_hash_replays_stored_result(self) -> None:
        """R13: a 'legacy' receipt with a matching hash replays its stored result."""
        receipts = InMemoryReceiptRepository()
        result1 = await create(created_then_read(stored_event()), receipts)
        assert result1.outcome is EventWriteOutcome.CONFIRMED

        # Simulate a database migrated from the pre-`action` schema: the stored
        # receipt's action becomes "legacy", but its hash is untouched.
        receipt = await receipts.get(PRINCIPAL, "op-1")
        assert receipt is not None
        await receipts.put(receipt.model_copy(update={"action": "legacy"}))

        transport = ScriptedTransport({})
        result2 = await create(transport, receipts)

        assert result2.outcome is EventWriteOutcome.CONFIRMED
        assert result2.event_id == result1.event_id
        assert transport.calls == []

    async def test_acknowledgement_is_recorded_before_readback(self) -> None:
        """If the process dies during readback, a replay reports acknowledged."""
        receipts = InMemoryReceiptRepository()

        class DiesDuringReadback(ScriptedTransport):
            async def call(self, endpoint: str, fields: Mapping[str, str]) -> object:
                if endpoint == "evtlistinterval":
                    raise KeyboardInterrupt
                return await super().call(endpoint, fields)

        with pytest.raises(KeyboardInterrupt):
            await create(DiesDuringReadback({"evtcreate": {"eventId": "event/new-1"}}), receipts)

        result = await create(ScriptedTransport({}), receipts)

        assert result.outcome is EventWriteOutcome.ACKNOWLEDGED
        assert result.event_id == "event/new-1"

    async def test_receipt_is_scoped_to_the_family_calendar(self) -> None:
        receipts = InMemoryReceiptRepository()
        await create(created_then_read(stored_event()), receipts)

        receipt = await receipts.get(PRINCIPAL, "op-1")

        assert receipt is not None
        assert receipt.family_id == "family-123"
        assert receipt.resource_id == "calendar/family-123"
        assert receipt.action == "calendar.create_event"
        assert receipt.status == "succeeded"

    async def test_receipts_are_isolated_by_subject(self) -> None:
        receipts = InMemoryReceiptRepository()
        await create(created_then_read(stored_event()), receipts)
        other = created_then_read(stored_event())

        result = await CalendarService(other).create_event(
            Principal(subject="subject-b"), new_event(), "op-1", receipts, FAMILY
        )

        assert result.outcome is EventWriteOutcome.CONFIRMED
        assert other.endpoints == ["evtcreate", "evtlistinterval"]

    async def test_empty_operation_id_is_rejected_before_any_call(self) -> None:
        transport = ScriptedTransport({})

        with pytest.raises(ValueError):
            await create(transport, operation_id="  ")

        assert transport.calls == []


class TestNewTimedEvent:
    @pytest.mark.parametrize(
        "overrides",
        [
            {"title": "   "},
            {"end": datetime(2026, 10, 6, 10, 0, tzinfo=SYDNEY)},
            {"end": datetime(2026, 10, 6, 9, 0, tzinfo=SYDNEY)},
            {"end": datetime(2026, 10, 21, 10, 1, tzinfo=SYDNEY)},
            {"start": datetime(2026, 10, 6, 10, 0)},
            {"timezone": "Not/AZone"},
            {"title": "x" * 201},
        ],
    )
    def test_invalid_requests_are_rejected(self, overrides: dict[str, object]) -> None:
        with pytest.raises(ValueError):
            new_event(**overrides)

    def test_fourteen_day_event_is_accepted(self) -> None:
        event = new_event(end=datetime(2026, 10, 20, 10, 0, tzinfo=SYDNEY))
        assert event.end - event.start == datetime(2026, 10, 20) - datetime(2026, 10, 6)
