"""Tests for evtcreate wire builders: all-day events and reminders."""

from __future__ import annotations

from datetime import date, datetime
from zoneinfo import ZoneInfo

import pytest

from familywall_mcp.familywall.calendar import (
    build_create_all_day_event_fields,
    build_create_event_fields,
)

BERLIN = ZoneInfo("Europe/Berlin")


class TestBuildCreateAllDayEventFields:
    """All-day events use the midnight-to-23:59:59 encoding the server returns."""

    def test_single_day(self) -> None:
        fields = build_create_all_day_event_fields(
            title="Kita geschlossen",
            start_date=date(2026, 9, 25),
            end_date=date(2026, 9, 25),
            timezone="Europe/Berlin",
            to_all=True,
            attendee_account_ids=("acc-1", "acc-2"),
        )
        assert fields["allDay"] == "true"
        assert fields["startDate"] == "2026-09-25T00:00:00"
        assert fields["endDate"] == "2026-09-25T23:59:59"
        assert fields["recurrency"] == "NONE"
        assert fields["isToAll"] == "true"
        assert fields["attendee.0.accountId"] == "acc-1"
        assert fields["attendee.1.accountId"] == "acc-2"
        assert fields["where"] == ""
        assert fields["description"] == ""

    def test_multi_day_end_is_inclusive(self) -> None:
        fields = build_create_all_day_event_fields(
            title="Urlaub",
            start_date=date(2026, 10, 1),
            end_date=date(2026, 10, 3),
            timezone="Europe/Berlin",
            to_all=False,
            attendee_account_ids=("acc-2",),
        )
        assert fields["startDate"] == "2026-10-01T00:00:00"
        assert fields["endDate"] == "2026-10-03T23:59:59"
        assert fields["isToAll"] == "false"
        assert "attendee.1.accountId" not in fields

    def test_rejects_inverted_range_and_missing_attendees(self) -> None:
        with pytest.raises(ValueError):
            build_create_all_day_event_fields(
                title="X",
                start_date=date(2026, 10, 3),
                end_date=date(2026, 10, 1),
                timezone="UTC",
                to_all=True,
                attendee_account_ids=("acc-1",),
            )
        with pytest.raises(ValueError):
            build_create_all_day_event_fields(
                title="X",
                start_date=date(2026, 10, 1),
                end_date=date(2026, 10, 1),
                timezone="UTC",
                to_all=True,
                attendee_account_ids=(),
            )


class TestReminders:
    """Reminders replace the whole list on update; $empty clears it (live, 2026-09-29)."""

    def test_reminder_fields_are_indexed_like_the_web_serialiser(self) -> None:
        from familywall_mcp.familywall.calendar import Reminder, reminder_fields

        assert reminder_fields((Reminder(unit="DAY", value=1), Reminder(unit="HOUR", value=2))) == {
            "reminderList.0.reminderType": "SNOOZE",
            "reminderList.0.reminderUnit": "DAY",
            "reminderList.0.reminderValue": "1",
            "reminderList.1.reminderType": "SNOOZE",
            "reminderList.1.reminderUnit": "HOUR",
            "reminderList.1.reminderValue": "2",
        }

    def test_no_reminders_is_an_explicit_empty_list(self) -> None:
        from familywall_mcp.familywall.calendar import reminder_fields

        assert reminder_fields(()) == {"reminderList": "$empty"}

    def test_create_fields_carry_reminders(self) -> None:
        from familywall_mcp.familywall.calendar import Reminder

        fields = build_create_event_fields(
            title="X",
            start=datetime(2026, 9, 25, 14, 0, tzinfo=BERLIN),
            end=datetime(2026, 9, 25, 15, 0, tzinfo=BERLIN),
            timezone="Europe/Berlin",
            to_all=True,
            attendee_account_ids=("acc-1",),
            reminders=(Reminder(unit="DAY", value=1),),
        )
        assert "reminderList" not in fields
        assert fields["reminderList.0.reminderUnit"] == "DAY"
        assert fields["reminderList.0.reminderValue"] == "1"
        assert "reminderList.1.reminderUnit" not in fields

    def test_create_fields_can_send_no_reminder(self) -> None:
        fields = build_create_event_fields(
            title="X",
            start=datetime(2026, 9, 25, 14, 0, tzinfo=BERLIN),
            end=datetime(2026, 9, 25, 15, 0, tzinfo=BERLIN),
            timezone="Europe/Berlin",
            to_all=True,
            attendee_account_ids=("acc-1",),
            reminders=(),
        )
        assert fields["reminderList"] == "$empty"
        assert "reminderList.0.reminderUnit" not in fields

    def test_all_day_create_fields_default_to_no_reminder(self) -> None:
        fields = build_create_all_day_event_fields(
            title="X",
            start_date=date(2026, 9, 25),
            end_date=date(2026, 9, 25),
            timezone="UTC",
            to_all=True,
            attendee_account_ids=("acc-1",),
        )
        assert fields["reminderList"] == "$empty"

    @pytest.mark.parametrize(("unit", "value"), [("WEEK", 1), ("DAY", -1)])
    def test_reminder_rejects_bad_values(self, unit: str, value: int) -> None:
        from pydantic import ValidationError

        from familywall_mcp.familywall.calendar import Reminder

        with pytest.raises(ValidationError):
            Reminder(unit=unit, value=value)  # type: ignore[arg-type]

    def test_events_parse_their_reminders(self) -> None:
        from tests.support.calendar_fixtures import timed_event

        from familywall_mcp.familywall.calendar import (
            Reminder,
            parse_events,
            typed_reminders,
        )

        raw = {
            **timed_event(),
            "reminderList": [
                {
                    "reminderType": "SNOOZE",
                    "reminderUnit": "DAY",
                    "reminderValue": "1",
                    "localId": "7",
                },
                {"reminderType": "SNOOZE", "reminderUnit": "FORTNIGHT", "reminderValue": "1"},
            ],
        }
        event = parse_events([raw]).events[0]
        assert typed_reminders(event.reminders) == (Reminder(unit="DAY", value=1),)

    def test_event_without_reminders(self) -> None:
        from tests.support.calendar_fixtures import timed_event

        from familywall_mcp.familywall.calendar import parse_events, typed_reminders

        assert typed_reminders(parse_events([timed_event()]).events[0].reminders) == ()

    @pytest.mark.parametrize(
        "payload",
        [
            {
                "defaultReminderType": "SNOOZE",
                "defaultReminderUnit": "DAY",
                "defaultReminderValue": "1",
            },
            [
                {
                    "defaultReminderType": "SNOOZE",
                    "defaultReminderUnit": "DAY",
                    "defaultReminderValue": "1",
                }
            ],
        ],
    )
    def test_parse_default_reminder(self, payload: object) -> None:
        from familywall_mcp.familywall.calendar import Reminder, parse_default_reminder

        assert parse_default_reminder(payload) == Reminder(unit="DAY", value=1)

    @pytest.mark.parametrize("payload", [None, {}, [], {"defaultReminderUnit": "DAY"}, "x"])
    def test_parse_default_reminder_absent(self, payload: object) -> None:
        from familywall_mcp.familywall.calendar import parse_default_reminder

        assert parse_default_reminder(payload) is None
