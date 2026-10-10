"""Tests for reminders and all-day events on created calendar events."""

from __future__ import annotations

from datetime import date

import pytest
from tests.unit.test_calendar_create import (
    FAMILY,
    PRINCIPAL,
    SELF_ONLY_ASSIGNMENT,
    new_event,
    stored_event,
)
from tests.unit.test_calendar_create import ScriptedTransport as CreateTransport

from familywall_mcp.errors import TransportError
from familywall_mcp.familywall.calendar import Reminder
from familywall_mcp.services.calendar import (
    CalendarService,
    CreateEventResult,
    EventWriteOutcome,
    NewAllDayEvent,
    NewTimedEvent,
)
from familywall_mcp.storage.memory import InMemoryReceiptRepository

DAY_1 = Reminder(unit="DAY", value=1)
SETTINGS = {
    "defaultReminderType": "SNOOZE",
    "defaultReminderUnit": "DAY",
    "defaultReminderValue": "1",
}


def created(*readback: dict[str, object], settings: object = None) -> CreateTransport:
    responses: dict[str, object] = {
        "evtcreate": {"eventId": "event/new-1"},
        "evtlistinterval": list(readback),
    }
    if settings is not None:
        responses["settingsgetperfamily"] = settings
    return CreateTransport(responses)


def stored_with(*pairs: tuple[str, str], **overrides: object) -> dict[str, object]:
    return stored_event(
        reminderList=[
            {"reminderType": "SNOOZE", "reminderUnit": u, "reminderValue": v} for u, v in pairs
        ],
        **overrides,
    )


async def create_event(
    transport: CreateTransport,
    receipts: InMemoryReceiptRepository | None = None,
    request: NewTimedEvent | NewAllDayEvent | None = None,
    reminders: tuple[Reminder, ...] | None = None,
) -> CreateEventResult:
    return await CalendarService(transport).create_event(
        PRINCIPAL,
        request or new_event(),
        SELF_ONLY_ASSIGNMENT,
        "op-1",
        receipts or InMemoryReceiptRepository(),
        FAMILY,
        reminders=reminders,
    )


ALL_DAY = NewAllDayEvent(
    title="Dentist",
    start_date=date(2026, 10, 6),
    end_date=date(2026, 10, 7),
    timezone="Australia/Sydney",
    location="Main St",
)


def stored_all_day(*pairs: tuple[str, str]) -> dict[str, object]:
    return stored_with(
        *pairs,
        allDay="true",
        startDate="2026-10-06T00:00:00.000Z",
        endDate="2026-10-07T23:59:59.000Z",
    )


class TestCreateReminders:
    async def test_timed_event_gets_the_members_own_default(self) -> None:
        transport = created(stored_with(("DAY", "1")), settings=SETTINGS)

        result = await create_event(transport)

        assert result.outcome is EventWriteOutcome.CONFIRMED
        assert result.reminders == (DAY_1,)
        fields = transport.sent("evtcreate")
        assert fields["reminderList.0.reminderUnit"] == "DAY"
        assert fields["reminderList.0.reminderValue"] == "1"
        assert transport.endpoints == ["settingsgetperfamily", "evtcreate", "evtlistinterval"]

    async def test_default_that_did_not_stick_is_mismatched(self) -> None:
        transport = created(stored_with(("MINUTE", "30")), settings=SETTINGS)

        result = await create_event(transport)

        assert result.outcome is EventWriteOutcome.MISMATCHED
        assert result.mismatched_fields == ("reminder",)

    async def test_all_day_event_reminds_at_the_start_of_the_day(self) -> None:
        transport = created(stored_all_day(("MINUTE", "0")))

        result = await create_event(transport, request=ALL_DAY)

        assert result.outcome is EventWriteOutcome.CONFIRMED
        assert result.reminders == (Reminder(unit="MINUTE", value=0),)
        assert "settingsgetperfamily" not in transport.endpoints
        fields = transport.sent("evtcreate")
        assert fields["allDay"] == "true"
        assert fields["startDate"] == "2026-10-06T00:00:00"
        assert fields["endDate"] == "2026-10-07T23:59:59"

    async def test_all_day_readback_with_other_dates_is_mismatched(self) -> None:
        transport = created(stored_with(("MINUTE", "0"), allDay="true"))

        result = await create_event(transport, request=ALL_DAY)

        assert result.outcome is EventWriteOutcome.MISMATCHED
        assert "start" in result.mismatched_fields

    @pytest.mark.parametrize("settings", [TransportError(), {}, {"defaultReminderUnit": "WEEK"}])
    async def test_unreadable_settings_fall_back_to_thirty_minutes(self, settings: object) -> None:
        transport = created(stored_event(), settings=settings)

        result = await create_event(transport)

        assert result.outcome is EventWriteOutcome.CONFIRMED
        assert result.reminders == (Reminder(unit="MINUTE", value=30),)

    async def test_explicit_reminders_skip_the_settings(self) -> None:
        transport = created(stored_with(("HOUR", "2")))

        result = await create_event(transport, reminders=(Reminder(unit="HOUR", value=2),))

        assert result.outcome is EventWriteOutcome.CONFIRMED
        assert "settingsgetperfamily" not in transport.endpoints
        assert transport.sent("evtcreate")["reminderList.0.reminderUnit"] == "HOUR"

    async def test_explicitly_no_reminders(self) -> None:
        transport = created(stored_with())

        result = await create_event(transport, reminders=())

        assert result.outcome is EventWriteOutcome.CONFIRMED
        assert result.reminders == ()
        assert transport.sent("evtcreate")["reminderList"] == "$empty"

    async def test_replay_contacts_nothing_not_even_settings(self) -> None:
        receipts = InMemoryReceiptRepository()
        first = await create_event(created(stored_with(("DAY", "1")), settings=SETTINGS), receipts)
        again_transport = created(stored_with(("DAY", "1")), settings=SETTINGS)

        again = await create_event(again_transport, receipts)

        assert again_transport.calls == []
        assert again.reminders == first.reminders
