"""Tests for calendar event creation and deletion."""

from __future__ import annotations

from datetime import date
from typing import Any
from zoneinfo import ZoneInfo

import pytest

from familywall_mcp.errors import FamilyWallError, TransportError
from familywall_mcp.models import Principal
from familywall_mcp.services.calendar_writes import CalendarWriteService
from familywall_mcp.services.lists import WriteOutcome
from familywall_mcp.storage.memory import InMemoryReceiptRepository

BERLIN = ZoneInfo("Europe/Berlin")
FAMILY = "family/1"
CALENDAR = "calendar/1"


def wire_event(
    event_id: str,
    *,
    text: str = "Termin",
    master_id: str | None = None,
    occurrence_index: str = "0",
    recurrency: str = "NONE",
    rrule: str | None = None,
    exception_of: str | None = None,
    start: str = "2026-09-25T12:00:00.000Z",
    end: str = "2026-09-25T13:00:00.000Z",
    all_day: str = "false",
) -> dict[str, Any]:
    """A wire-format event as evtlistinterval returns it."""
    event: dict[str, Any] = {
        "eventId": event_id,
        "eventMasterId": master_id or event_id,
        "occurenceIndex": occurrence_index,
        "text": text,
        "startDate": start,
        "endDate": end,
        "allDay": all_day,
        "recurrency": recurrency,
        "calendarId": CALENDAR,
    }
    if rrule is not None:
        event["rrule"] = rrule
    if exception_of is not None:
        event["recurrencyExceptionOfId"] = exception_of
    return event


class ScriptedTransport:
    """Records calls; replies from a per-endpoint queue (the last reply repeats)."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, str]]] = []
        self.replies: dict[str, list[object]] = {}

    def reply(self, endpoint: str, *responses: object) -> None:
        self.replies[endpoint] = list(responses)

    async def call(self, endpoint: str, fields: dict[str, str]) -> object:
        self.calls.append((endpoint, dict(fields)))
        queue = self.replies.get(endpoint)
        if not queue:
            raise AssertionError(f"Unexpected call: {endpoint} {fields}")
        response = queue.pop(0) if len(queue) > 1 else queue[0]
        if isinstance(response, Exception):
            raise response
        return response

    def endpoints(self) -> list[str]:
        return [endpoint for endpoint, _ in self.calls]


@pytest.fixture
def transport() -> ScriptedTransport:
    return ScriptedTransport()


@pytest.fixture
def service(transport: ScriptedTransport) -> CalendarWriteService:
    return CalendarWriteService(transport)


@pytest.fixture
def receipts() -> InMemoryReceiptRepository:
    return InMemoryReceiptRepository()


@pytest.fixture
def principal() -> Principal:
    return Principal(subject="test-subject")


async def delete(
    service: CalendarWriteService,
    principal: Principal,
    receipts: InMemoryReceiptRepository,
    event_id: str,
    *,
    operation_id: str = "del-1",
) -> Any:
    return await service.delete_event(
        principal,
        event_id=event_id,
        on_date=date(2026, 9, 25),
        timezone="Europe/Berlin",
        operation_id=operation_id,
        receipt_repository=receipts,
        family_id=FAMILY,
        calendar_id=CALENDAR,
    )


class TestDeleteEvent:
    async def test_single_event_is_deleted_and_confirmed(
        self,
        service: CalendarWriteService,
        transport: ScriptedTransport,
        principal: Principal,
        receipts: InMemoryReceiptRepository,
    ) -> None:
        transport.reply("evtlistinterval", [wire_event("event/1_5")], [])
        transport.reply("evtdelete", "true")
        transport.reply("evtdeleteoption", ["All"])

        result = await delete(service, principal, receipts, "event/1_5")

        assert result.outcome is WriteOutcome.CONFIRMED
        assert transport.endpoints() == [
            "evtlistinterval",
            "evtdeleteoption",
            "evtdelete",
            "evtlistinterval",
        ]
        assert transport.calls[1][1] == {"partnerScope": "Family", "eventId": "event/1_5"}
        assert transport.calls[2][1] == {
            "partnerScope": "Family",
            "option": "All",
            "eventId.0": "event/1_5",
        }

    async def test_lookup_covers_the_whole_local_day(
        self,
        service: CalendarWriteService,
        transport: ScriptedTransport,
        principal: Principal,
        receipts: InMemoryReceiptRepository,
    ) -> None:
        transport.reply("evtlistinterval", [wire_event("event/1_5")], [])
        transport.reply("evtdelete", "true")
        transport.reply("evtdeleteoption", ["All"])

        await delete(service, principal, receipts, "event/1_5")

        lookup = transport.calls[0][1]
        assert lookup["a00from"] == "2026-09-25T00:00:00+02:00"
        assert lookup["a00to"] == "2026-09-26T00:00:00+02:00"

    async def test_recurring_occurrence_is_refused_before_any_delete(
        self,
        service: CalendarWriteService,
        transport: ScriptedTransport,
        principal: Principal,
        receipts: InMemoryReceiptRepository,
    ) -> None:
        """option=All would take the whole series, so a series is never deleted."""
        transport.reply(
            "evtlistinterval",
            [
                wire_event(
                    "event/1_8_46",
                    master_id="event/1_8",
                    occurrence_index="46",
                    recurrency="WEEKLY",
                    rrule="FREQ=WEEKLY;INTERVAL=1;BYDAY=SA",
                )
            ],
        )

        with pytest.raises(FamilyWallError) as exc:
            await delete(service, principal, receipts, "event/1_8_46")

        assert exc.value.info.code == "recurring_event_delete_unsupported"
        assert "evtdelete" not in transport.endpoints()

    async def test_first_occurrence_of_series_is_refused(
        self,
        service: CalendarWriteService,
        transport: ScriptedTransport,
        principal: Principal,
        receipts: InMemoryReceiptRepository,
    ) -> None:
        """Occurrence 0 shares its id with the master; recurrence must still be detected."""
        transport.reply(
            "evtlistinterval",
            [wire_event("event/1_8", recurrency="WEEKLY", rrule="FREQ=WEEKLY")],
        )

        with pytest.raises(FamilyWallError) as exc:
            await delete(service, principal, receipts, "event/1_8")

        assert exc.value.info.code == "recurring_event_delete_unsupported"
        assert "evtdelete" not in transport.endpoints()

    async def test_series_exception_is_refused(
        self,
        service: CalendarWriteService,
        transport: ScriptedTransport,
        principal: Principal,
        receipts: InMemoryReceiptRepository,
    ) -> None:
        transport.reply(
            "evtlistinterval",
            [wire_event("event/1_3", exception_of="event/1_8")],
        )

        with pytest.raises(FamilyWallError) as exc:
            await delete(service, principal, receipts, "event/1_3")

        assert exc.value.info.code == "recurring_event_delete_unsupported"
        assert "evtdelete" not in transport.endpoints()

    async def test_event_not_on_that_day_is_refused(
        self,
        service: CalendarWriteService,
        transport: ScriptedTransport,
        principal: Principal,
        receipts: InMemoryReceiptRepository,
    ) -> None:
        transport.reply("evtlistinterval", [wire_event("event/1_other")])

        with pytest.raises(FamilyWallError) as exc:
            await delete(service, principal, receipts, "event/1_5")

        assert exc.value.info.code == "event_not_found"
        assert "evtdelete" not in transport.endpoints()

    async def test_malformed_id_is_refused_with_zero_calls(
        self,
        service: CalendarWriteService,
        transport: ScriptedTransport,
        principal: Principal,
        receipts: InMemoryReceiptRepository,
    ) -> None:
        with pytest.raises(FamilyWallError) as exc:
            await delete(service, principal, receipts, "task/1_5")

        assert exc.value.info.code == "invalid_event"
        assert transport.calls == []

    async def test_unknown_when_event_still_present_after_delete(
        self,
        service: CalendarWriteService,
        transport: ScriptedTransport,
        principal: Principal,
        receipts: InMemoryReceiptRepository,
    ) -> None:
        """A delete that did not take effect is never reported as confirmed."""
        transport.reply("evtlistinterval", [wire_event("event/1_5")])
        transport.reply("evtdelete", "false")
        transport.reply("evtdeleteoption", ["All"])

        result = await delete(service, principal, receipts, "event/1_5")

        assert result.outcome is WriteOutcome.UNKNOWN

    async def test_unknown_on_transport_error(
        self,
        service: CalendarWriteService,
        transport: ScriptedTransport,
        principal: Principal,
        receipts: InMemoryReceiptRepository,
    ) -> None:
        transport.reply("evtlistinterval", [wire_event("event/1_5")])
        transport.reply("evtdelete", TransportError())
        transport.reply("evtdeleteoption", ["All"])

        result = await delete(service, principal, receipts, "event/1_5")

        assert result.outcome is WriteOutcome.UNKNOWN
        assert transport.endpoints() == ["evtlistinterval", "evtdeleteoption", "evtdelete"]

    async def test_replay_with_same_key_sends_nothing(
        self,
        service: CalendarWriteService,
        transport: ScriptedTransport,
        principal: Principal,
        receipts: InMemoryReceiptRepository,
    ) -> None:
        transport.reply("evtlistinterval", [wire_event("event/1_5")], [])
        transport.reply("evtdelete", "true")
        transport.reply("evtdeleteoption", ["All"])
        await delete(service, principal, receipts, "event/1_5")
        calls_after_first = len(transport.calls)

        again = await delete(service, principal, receipts, "event/1_5")

        assert again.outcome is WriteOutcome.CONFIRMED
        assert len(transport.calls) == calls_after_first


class TestDeleteServerOptionCheck:
    """The server's own option list is a second, independent single-event check."""

    async def test_event_the_server_calls_a_series_is_refused(
        self,
        service: CalendarWriteService,
        transport: ScriptedTransport,
        principal: Principal,
        receipts: InMemoryReceiptRepository,
    ) -> None:
        transport.reply("evtlistinterval", [wire_event("event/1_5")])
        transport.reply("evtdeleteoption", ["All", "OnlyAfter", "OnlyOne"])

        with pytest.raises(FamilyWallError) as exc:
            await delete(service, principal, receipts, "event/1_5")

        assert exc.value.info.code == "recurring_event_delete_unsupported"
        assert "evtdelete" not in transport.endpoints()

    async def test_client_detected_series_is_refused_without_asking_the_server(
        self,
        service: CalendarWriteService,
        transport: ScriptedTransport,
        principal: Principal,
        receipts: InMemoryReceiptRepository,
    ) -> None:
        transport.reply(
            "evtlistinterval",
            [wire_event("event/1_8", recurrency="WEEKLY", rrule="FREQ=WEEKLY")],
        )

        with pytest.raises(FamilyWallError):
            await delete(service, principal, receipts, "event/1_8")

        assert transport.endpoints() == ["evtlistinterval"]
