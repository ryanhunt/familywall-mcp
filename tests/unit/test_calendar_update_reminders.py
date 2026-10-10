"""Tests for reminders on updated calendar events."""

from __future__ import annotations

from typing import Any

import pytest
from tests.unit.test_calendar_write_service import ScriptedTransport, wire_event

from familywall_mcp.familywall.calendar import Reminder
from familywall_mcp.models import Principal
from familywall_mcp.services.calendar_writes import CalendarWriteService
from familywall_mcp.services.lists import WriteOutcome
from familywall_mcp.storage.memory import InMemoryReceiptRepository

DAY_1 = Reminder(unit="DAY", value=1)


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


def sent(transport: ScriptedTransport, endpoint: str) -> dict[str, str]:
    return next(fields for name, fields in transport.calls if name == endpoint)


def with_reminders(raw: dict, *pairs: tuple[str, str]) -> dict:
    return {
        **raw,
        "reminderList": [
            {"reminderType": "SNOOZE", "reminderUnit": u, "reminderValue": v} for u, v in pairs
        ],
    }


async def update(service: Any, principal: Any, receipts: Any, **kw: Any) -> Any:
    return await service.update_event(
        principal,
        event_id="event/1_5",
        title=kw.pop("title", None),
        start=None,
        end=None,
        location=None,
        description=None,
        reminders=kw.pop("reminders", None),
        timezone="Europe/Berlin",
        operation_id="upd-1",
        receipt_repository=receipts,
        family_id="family/1",
        calendar_id="calendar/1",
    )


class TestUpdateReminders:
    async def test_setting_reminders_replaces_the_list(
        self, service: Any, transport: ScriptedTransport, principal: Any, receipts: Any
    ) -> None:
        before = wire_event("event/1_5")
        transport.reply("evtget", before, with_reminders(before, ("DAY", "1")))
        transport.reply("evtupdateoption", ["All"])
        transport.reply("evtupdate", {})

        result = await update(service, principal, receipts, reminders=(DAY_1,))

        assert result.outcome is WriteOutcome.CONFIRMED
        assert sent(transport, "evtupdate") == {
            "partnerScope": "Family",
            "option": "All",
            "metaId": "event/1_5",
            "reminderList.0.reminderType": "SNOOZE",
            "reminderList.0.reminderUnit": "DAY",
            "reminderList.0.reminderValue": "1",
        }

    async def test_clearing_reminders(
        self, service: Any, transport: ScriptedTransport, principal: Any, receipts: Any
    ) -> None:
        before = with_reminders(wire_event("event/1_5"), ("MINUTE", "30"))
        transport.reply("evtget", before, wire_event("event/1_5"))
        transport.reply("evtupdateoption", ["All"])
        transport.reply("evtupdate", {})

        result = await update(service, principal, receipts, reminders=())

        assert result.outcome is WriteOutcome.CONFIRMED
        assert sent(transport, "evtupdate")["reminderList"] == "$empty"

    async def test_unknown_when_readback_shows_other_reminders(
        self, service: Any, transport: ScriptedTransport, principal: Any, receipts: Any
    ) -> None:
        before = wire_event("event/1_5")
        transport.reply("evtget", before, with_reminders(before, ("MINUTE", "30")))
        transport.reply("evtupdateoption", ["All"])
        transport.reply("evtupdate", {})

        result = await update(service, principal, receipts, reminders=(DAY_1,))

        assert result.outcome is WriteOutcome.UNKNOWN

    async def test_title_update_sends_no_reminder_fields(
        self, service: Any, transport: ScriptedTransport, principal: Any, receipts: Any
    ) -> None:
        before = with_reminders(wire_event("event/1_5", text="Alt"), ("DAY", "1"))
        transport.reply("evtget", before, {**before, "text": "Neu"})
        transport.reply("evtupdateoption", ["All"])
        transport.reply("evtupdate", {})

        await update(service, principal, receipts, title="Neu")

        assert not any(k.startswith("reminderList") for k in sent(transport, "evtupdate"))
