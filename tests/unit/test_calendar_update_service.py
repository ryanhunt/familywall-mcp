"""Tests for partial updates of single calendar events."""

from __future__ import annotations

from datetime import date, datetime
from typing import Any
from zoneinfo import ZoneInfo

import pytest
from tests.unit.test_calendar_write_service import ScriptedTransport, wire_event

from familywall_mcp.errors import FamilyWallError, TransportError
from familywall_mcp.models import Principal
from familywall_mcp.services.calendar_writes import CalendarWriteService
from familywall_mcp.services.lists import WriteOutcome
from familywall_mcp.storage.memory import InMemoryReceiptRepository

BERLIN = ZoneInfo("Europe/Berlin")
EVENT = "event/1_5"

# 14:00-15:00 Berlin (CEST) on 2026-10-07.
TIMED = {
    "start": "2026-10-07T12:00:00.000Z",
    "end": "2026-10-07T13:00:00.000Z",
}


def event(**overrides: Any) -> dict[str, Any]:
    raw = wire_event(EVENT, text="Zahnarzt", **{**TIMED, **overrides.pop("span", {})})
    raw.update(overrides)
    return raw


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


async def update(
    service: CalendarWriteService,
    principal: Principal,
    receipts: InMemoryReceiptRepository,
    *,
    operation_id: str = "upd-1",
    **changes: Any,
) -> Any:
    return await service.update_event(
        principal,
        event_id=changes.pop("event_id", EVENT),
        title=changes.pop("title", None),
        start=changes.pop("start", None),
        end=changes.pop("end", None),
        location=changes.pop("location", None),
        description=changes.pop("description", None),
        timezone="Europe/Berlin",
        operation_id=operation_id,
        receipt_repository=receipts,
        family_id="family/1",
        calendar_id="calendar/1",
    )


def sent_update(transport: ScriptedTransport) -> dict[str, str]:
    return next(fields for endpoint, fields in transport.calls if endpoint == "evtupdate")


def single_event_server(transport: ScriptedTransport, before: dict, after: dict) -> None:
    transport.reply("evtget", before, after)
    transport.reply("evtupdateoption", ["All"])
    transport.reply("evtupdate", after)


class TestUpdateEvent:
    async def test_title_only_sends_only_the_title_and_confirms(
        self,
        service: CalendarWriteService,
        transport: ScriptedTransport,
        principal: Principal,
        receipts: InMemoryReceiptRepository,
    ) -> None:
        single_event_server(transport, event(), event(text="Zahnarzt Dr. M"))

        result = await update(service, principal, receipts, title="Zahnarzt Dr. M")

        assert result.outcome is WriteOutcome.CONFIRMED
        assert transport.endpoints() == ["evtget", "evtupdateoption", "evtupdate", "evtget"]
        assert sent_update(transport) == {
            "partnerScope": "Family",
            "option": "All",
            "metaId": EVENT,
            "text": "Zahnarzt Dr. M",
        }

    async def test_moving_the_start_keeps_the_duration(
        self,
        service: CalendarWriteService,
        transport: ScriptedTransport,
        principal: Principal,
        receipts: InMemoryReceiptRepository,
    ) -> None:
        moved = event(span={"start": "2026-10-08T08:30:00.000Z", "end": "2026-10-08T09:30:00.000Z"})
        single_event_server(transport, event(), moved)

        result = await update(
            service, principal, receipts, start=datetime(2026, 10, 8, 10, 30, tzinfo=BERLIN)
        )

        assert result.outcome is WriteOutcome.CONFIRMED
        sent = sent_update(transport)
        assert sent["startDate"] == "2026-10-08T08:30:00"
        assert sent["endDate"] == "2026-10-08T09:30:00"
        assert "text" not in sent

    async def test_changing_only_the_end(
        self,
        service: CalendarWriteService,
        transport: ScriptedTransport,
        principal: Principal,
        receipts: InMemoryReceiptRepository,
    ) -> None:
        longer = event(span={"end": "2026-10-07T13:15:00.000Z"})
        single_event_server(transport, event(), longer)

        result = await update(
            service, principal, receipts, end=datetime(2026, 10, 7, 15, 15, tzinfo=BERLIN)
        )

        assert result.outcome is WriteOutcome.CONFIRMED
        sent = sent_update(transport)
        assert sent["startDate"] == "2026-10-07T12:00:00"
        assert sent["endDate"] == "2026-10-07T13:15:00"

    async def test_all_day_event_moves_by_date_keeping_its_length(
        self,
        service: CalendarWriteService,
        transport: ScriptedTransport,
        principal: Principal,
        receipts: InMemoryReceiptRepository,
    ) -> None:
        before = event(
            allDay="true",
            span={"start": "2026-10-01T00:00:00.000Z", "end": "2026-10-03T23:59:59.000Z"},
        )
        after = event(
            allDay="true",
            span={"start": "2026-10-10T00:00:00.000Z", "end": "2026-10-12T23:59:59.000Z"},
        )
        single_event_server(transport, before, after)

        result = await update(service, principal, receipts, start=date(2026, 10, 10))

        assert result.outcome is WriteOutcome.CONFIRMED
        sent = sent_update(transport)
        assert sent["startDate"] == "2026-10-10T00:00:00"
        assert sent["endDate"] == "2026-10-12T23:59:59"
        # Without allDay the server turns the event into a timed one (live, 2026-10-10).
        assert sent["allDay"] == "true"

    async def test_location_can_be_cleared(
        self,
        service: CalendarWriteService,
        transport: ScriptedTransport,
        principal: Principal,
        receipts: InMemoryReceiptRepository,
    ) -> None:
        single_event_server(transport, event(where="Praxis"), event(where=""))

        result = await update(service, principal, receipts, location="")

        assert result.outcome is WriteOutcome.CONFIRMED
        assert sent_update(transport)["where"] == ""

    async def test_timed_event_given_a_date_is_refused_before_writing(
        self,
        service: CalendarWriteService,
        transport: ScriptedTransport,
        principal: Principal,
        receipts: InMemoryReceiptRepository,
    ) -> None:
        single_event_server(transport, event(), event())

        with pytest.raises(FamilyWallError) as exc:
            await update(service, principal, receipts, start=date(2026, 10, 8))

        assert exc.value.info.code == "invalid_event"
        assert "evtupdate" not in transport.endpoints()

    async def test_end_before_start_is_refused_before_writing(
        self,
        service: CalendarWriteService,
        transport: ScriptedTransport,
        principal: Principal,
        receipts: InMemoryReceiptRepository,
    ) -> None:
        single_event_server(transport, event(), event())

        with pytest.raises(FamilyWallError) as exc:
            await update(
                service, principal, receipts, end=datetime(2026, 10, 7, 13, 0, tzinfo=BERLIN)
            )

        assert exc.value.info.code == "invalid_event"
        assert "evtupdate" not in transport.endpoints()

    async def test_no_change_is_refused_with_zero_calls(
        self,
        service: CalendarWriteService,
        transport: ScriptedTransport,
        principal: Principal,
        receipts: InMemoryReceiptRepository,
    ) -> None:
        with pytest.raises(FamilyWallError) as exc:
            await update(service, principal, receipts)

        assert exc.value.info.code == "invalid_event"
        assert transport.calls == []

    async def test_blank_title_is_refused_with_zero_calls(
        self,
        service: CalendarWriteService,
        transport: ScriptedTransport,
        principal: Principal,
        receipts: InMemoryReceiptRepository,
    ) -> None:
        with pytest.raises(FamilyWallError) as exc:
            await update(service, principal, receipts, title="  ")

        assert exc.value.info.code == "invalid_event"
        assert transport.calls == []

    async def test_recurring_occurrence_is_refused_without_asking_the_server(
        self,
        service: CalendarWriteService,
        transport: ScriptedTransport,
        principal: Principal,
        receipts: InMemoryReceiptRepository,
    ) -> None:
        transport.reply("evtget", event(recurrency="WEEKLY", rrule="FREQ=WEEKLY"))

        with pytest.raises(FamilyWallError) as exc:
            await update(service, principal, receipts, title="x")

        assert exc.value.info.code == "recurring_event_update_unsupported"
        assert transport.endpoints() == ["evtget"]

    async def test_event_the_server_calls_a_series_is_refused(
        self,
        service: CalendarWriteService,
        transport: ScriptedTransport,
        principal: Principal,
        receipts: InMemoryReceiptRepository,
    ) -> None:
        transport.reply("evtget", event())
        transport.reply("evtupdateoption", ["All", "OnlyOne", "OnlyAfter"])

        with pytest.raises(FamilyWallError) as exc:
            await update(service, principal, receipts, title="x")

        assert exc.value.info.code == "recurring_event_update_unsupported"
        assert "evtupdate" not in transport.endpoints()

    async def test_unknown_when_readback_does_not_show_the_change(
        self,
        service: CalendarWriteService,
        transport: ScriptedTransport,
        principal: Principal,
        receipts: InMemoryReceiptRepository,
    ) -> None:
        single_event_server(transport, event(), event())  # title unchanged afterwards

        result = await update(service, principal, receipts, title="Neu")

        assert result.outcome is WriteOutcome.UNKNOWN

    async def test_acknowledged_when_readback_fails(
        self,
        service: CalendarWriteService,
        transport: ScriptedTransport,
        principal: Principal,
        receipts: InMemoryReceiptRepository,
    ) -> None:
        transport.reply("evtget", event(), TransportError())
        transport.reply("evtupdateoption", ["All"])
        transport.reply("evtupdate", event(text="Neu"))

        result = await update(service, principal, receipts, title="Neu")

        assert result.outcome is WriteOutcome.ACKNOWLEDGED

    async def test_unknown_on_transport_error(
        self,
        service: CalendarWriteService,
        transport: ScriptedTransport,
        principal: Principal,
        receipts: InMemoryReceiptRepository,
    ) -> None:
        transport.reply("evtget", event())
        transport.reply("evtupdateoption", ["All"])
        transport.reply("evtupdate", TransportError())

        result = await update(service, principal, receipts, title="Neu")

        assert result.outcome is WriteOutcome.UNKNOWN
        assert transport.endpoints() == ["evtget", "evtupdateoption", "evtupdate"]

    async def test_replay_with_same_key_sends_nothing(
        self,
        service: CalendarWriteService,
        transport: ScriptedTransport,
        principal: Principal,
        receipts: InMemoryReceiptRepository,
    ) -> None:
        single_event_server(transport, event(), event(text="Neu"))
        first = await update(service, principal, receipts, title="Neu")
        calls_after_first = len(transport.calls)

        again = await update(service, principal, receipts, title="Neu")

        assert again.outcome is first.outcome
        assert len(transport.calls) == calls_after_first

    async def test_same_key_different_change_conflicts(
        self,
        service: CalendarWriteService,
        transport: ScriptedTransport,
        principal: Principal,
        receipts: InMemoryReceiptRepository,
    ) -> None:
        single_event_server(transport, event(), event(text="Neu"))
        await update(service, principal, receipts, title="Neu")

        with pytest.raises(FamilyWallError) as exc:
            await update(service, principal, receipts, title="Anders")

        assert exc.value.info.code == "operation_id_conflict"
