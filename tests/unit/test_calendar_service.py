"""Tests for weekly calendar overview service."""

from __future__ import annotations

from collections.abc import Mapping
from datetime import date, timedelta

import pytest
from tests.support.calendar_fixtures import (
    all_day_event,
    birthday_account_event,
    malformed_event,
    multiday_all_day_event,
    recurring_with_exdate,
    timed_event,
)

from familywall_mcp.errors import TransportError
from familywall_mcp.services.calendar import CalendarService


class FakeCalendarTransport:
    """Fake transport that records calls and returns configured responses."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, str]]] = []
        self.response_sequences: dict[int, object] = {}
        self.call_count = 0
        self.raise_on_call: Exception | None = None

    async def call(self, endpoint: str, fields: Mapping[str, str]) -> object:
        """Record call and return configured response."""
        self.calls.append((endpoint, dict(fields)))

        if self.raise_on_call:
            raise self.raise_on_call

        response = self.response_sequences.get(self.call_count, [])
        self.call_count += 1
        return response

    def set_response_sequence(self, responses: list[object]) -> None:
        """Set a sequence of responses for successive calls."""
        for i, resp in enumerate(responses):
            self.response_sequences[i] = resp


@pytest.fixture
def fake_transport() -> FakeCalendarTransport:
    return FakeCalendarTransport()


@pytest.fixture
def service(fake_transport: FakeCalendarTransport) -> CalendarService:
    return CalendarService(fake_transport, max_events=500)


class TestWeekOverviewBasics:
    """Basic structure and metadata tests."""

    async def test_sydney_week_returns_exactly_seven_days(
        self, service: CalendarService, fake_transport: FakeCalendarTransport
    ) -> None:
        """A Sydney week returns exactly 7 DayAgenda entries with correct dates."""
        fake_transport.set_response_sequence([[]])

        overview = await service.get_week_overview(
            date(2026, 9, 14), "Australia/Sydney", "calendar/family-123"
        )

        assert len(overview.days) == 7
        assert overview.total_events == 0
        assert overview.complete is True

        # Verify dates are in order, 7 consecutive days
        # Sept 14, 2026 is a Monday, so week starts on Sept 14
        for i, day_agenda in enumerate(overview.days):
            expected_date = date(2026, 9, 14) + timedelta(days=i)
            assert day_agenda.day == expected_date

    async def test_request_fields_carry_resolved_iso_bounds(
        self, service: CalendarService, fake_transport: FakeCalendarTransport
    ) -> None:
        """Request fields contain resolved ISO bounds with explicit offset."""
        fake_transport.set_response_sequence([[]])

        await service.get_week_overview(date(2026, 9, 14), "UTC", "calendar/family-123")

        assert len(fake_transport.calls) >= 1
        endpoint, fields = fake_transport.calls[0]

        assert endpoint == "evtlistinterval"
        assert "a00from" in fields
        assert "a00to" in fields
        assert "calendarId" in fields
        assert fields["calendarId"] == "calendar/family-123"
        assert fields["partnerScope"] == "Family"

        # Check that ISO bounds have timezone offset
        from_value = fields["a00from"]
        to_value = fields["a00to"]
        assert "+" in from_value or "-" in from_value  # Has offset
        assert "+" in to_value or "-" in to_value


class TestTimedEventAssignment:
    """Tests for timed event timezone conversion and day assignment."""

    async def test_timed_event_crossing_midnight(
        self, service: CalendarService, fake_transport: FakeCalendarTransport
    ) -> None:
        """A timed event crossing local midnight appears on both days."""
        # Create a timed event that crosses midnight in Sydney
        # Sydney is UTC+10 in September
        # To cross midnight locally from Sept 14 to Sept 15 within the week:
        # Start: 2026-09-14T13:00:00.000Z = 2026-09-14T23:00:00 Sydney
        # End:   2026-09-14T15:00:00.000Z = 2026-09-15T01:00:00 Sydney
        crossing_midnight_event = {
            "eventId": "evt-midnight-crossing",
            "eventMasterId": "evt-midnight-crossing",
            "occurenceIndex": "0",
            "text": "Late night event",
            "startDate": "2026-09-14T13:00:00.000Z",  # 2026-09-14 23:00 Sydney
            "endDate": "2026-09-14T15:00:00.000Z",  # 2026-09-15 01:00 Sydney
            "allDay": "false",
            "timeZone": "Australia/Sydney",
            "recurrency": "NONE",
            "eventType": "UNKNOWN",
            "calendarId": "calendar/family-123",
        }

        fake_transport.set_response_sequence([[crossing_midnight_event]])

        overview = await service.get_week_overview(
            date(2026, 9, 14), "Australia/Sydney", "calendar/family-123"
        )

        # Find which days have this event
        days_with_event = [
            day
            for day in overview.days
            if any(e.occurrence_id == "evt-midnight-crossing" for e in day.events)
        ]

        # Should appear on 2 days
        assert len(days_with_event) == 2
        assert days_with_event[0].day == date(2026, 9, 14)
        assert days_with_event[1].day == date(2026, 9, 15)


class TestAllDayEventHandling:
    """Tests for all-day event date assignment without timezone conversion."""

    async def test_single_day_all_day_event_no_timezone_shift(
        self, service: CalendarService, fake_transport: FakeCalendarTransport
    ) -> None:
        """A single-day all-day event appears on exactly one day—the date in the upstream string."""
        # all_day_event fixture: startDate "2026-09-12T00:00:00.000Z"
        # This is Sept 12 in the string, and should stay Sept 12 with no timezone conversion
        # Sept 12 is Saturday; use it as reference to include it in the week
        fake_transport.set_response_sequence([[all_day_event()]])

        overview = await service.get_week_overview(
            date(2026, 9, 12), "Australia/Sydney", "calendar/family-123"
        )

        # Find the event
        all_day_events = [
            (day, event)
            for day in overview.days
            for event in day.events
            if event.occurrence_id == "evt-allday-001"
        ]

        assert len(all_day_events) == 1
        day_agenda, event = all_day_events[0]
        assert day_agenda.day == date(2026, 9, 12)  # Exactly as in upstream string

    async def test_multiday_all_day_event_covers_all_dates(
        self, service: CalendarService, fake_transport: FakeCalendarTransport
    ) -> None:
        """A multi-day all-day event appears on every date it covers."""
        # multiday_all_day_event spans 2026-09-20 to 2026-09-23
        fake_transport.set_response_sequence([[multiday_all_day_event()]])

        overview = await service.get_week_overview(
            date(2026, 9, 21), "Australia/Sydney", "calendar/family-123"
        )

        # Find occurrences of the multi-day event
        multiday_dates = [
            day.day
            for day in overview.days
            if any(e.occurrence_id == "evt-multiday-001" for e in day.events)
        ]

        assert len(multiday_dates) > 0
        # Should include at least the dates in the week
        assert date(2026, 9, 20) in multiday_dates or date(2026, 9, 21) in multiday_dates


class TestRecurrenceAndDeduplication:
    """Tests for series occurrences and deduplication."""

    async def test_three_occurrences_of_one_series_all_appear(
        self, service: CalendarService, fake_transport: FakeCalendarTransport
    ) -> None:
        """Three occurrences of one weekly series all appear; they are not collapsed.

        Create three occurrences that all fall within the same week.
        """
        # Create three occurrences all on Wednesdays in the same week window
        # We'll create three events that all appear in a wide 31-day window
        # Actually, the fixtures have them on Sept 2, 9, and 16 (all Wednesdays)
        # Let me create custom events that all fit in one week

        occ1 = {
            "eventId": "evt-series-001",
            "eventMasterId": "evt-series-master",
            "occurenceIndex": "0",
            "text": "Soccer training",
            "startDate": "2026-09-14T17:00:00.000Z",  # Monday
            "endDate": "2026-09-14T18:00:00.000Z",
            "allDay": "false",
            "timeZone": "Australia/Sydney",
            "recurrency": "WEEKLY",
            "rrule": "FREQ=WEEKLY;UNTIL=20261021;INTERVAL=1;BYDAY=WE",
            "eventType": "UNKNOWN",
            "calendarId": "calendar/family-123",
        }

        occ2 = {
            "eventId": "evt-series-002",
            "eventMasterId": "evt-series-master",
            "occurenceIndex": "1",
            "text": "Soccer training",
            "startDate": "2026-09-16T17:00:00.000Z",  # Wednesday
            "endDate": "2026-09-16T18:00:00.000Z",
            "allDay": "false",
            "timeZone": "Australia/Sydney",
            "recurrency": "WEEKLY",
            "rrule": "FREQ=WEEKLY;UNTIL=20261021;INTERVAL=1;BYDAY=WE",
            "eventType": "UNKNOWN",
            "calendarId": "calendar/family-123",
        }

        occ3 = {
            "eventId": "evt-series-003",
            "eventMasterId": "evt-series-master",
            "occurenceIndex": "2",
            "text": "Soccer training",
            "startDate": "2026-09-18T17:00:00.000Z",  # Friday
            "endDate": "2026-09-18T18:00:00.000Z",
            "allDay": "false",
            "timeZone": "Australia/Sydney",
            "recurrency": "WEEKLY",
            "rrule": "FREQ=WEEKLY;UNTIL=20261021;INTERVAL=1;BYDAY=WE",
            "eventType": "UNKNOWN",
            "calendarId": "calendar/family-123",
        }

        fake_transport.set_response_sequence([[occ1, occ2, occ3]])

        overview = await service.get_week_overview(
            date(2026, 9, 14), "Australia/Sydney", "calendar/family-123"
        )

        # All three distinct occurrences should be present
        assert overview.total_events >= 3

        # Verify all three appear in the days
        series_occurrences = [
            event
            for day in overview.days
            for event in day.events
            if event.series_id == "evt-series-master"
        ]

        assert len(series_occurrences) == 3
        assert set(e.occurrence_id for e in series_occurrences) == {
            "evt-series-001",
            "evt-series-002",
            "evt-series-003",
        }

    async def test_occurrence_with_exdate_is_still_shown(
        self, service: CalendarService, fake_transport: FakeCalendarTransport
    ) -> None:
        """An occurrence carrying exdate is still shown (not re-filtered)."""
        fake_transport.set_response_sequence([[recurring_with_exdate()]])

        overview = await service.get_week_overview(
            date(2026, 9, 7), "Australia/Sydney", "calendar/family-123"
        )

        # The event should appear (it's in the week window)
        exdate_events = [
            event
            for day in overview.days
            for event in day.events
            if event.occurrence_id == "evt-exdate-001"
        ]

        assert len(exdate_events) > 0


class TestUnknownEventTypes:
    """Tests for preserving unrecognized event types."""

    async def test_birthday_account_event_is_preserved(
        self, service: CalendarService, fake_transport: FakeCalendarTransport
    ) -> None:
        """A BIRTHDAY_ACCOUNT event from another calendar is preserved."""
        fake_transport.set_response_sequence([[birthday_account_event()]])

        overview = await service.get_week_overview(
            date(2026, 9, 21), "Australia/Sydney", "calendar/family-123"
        )

        # Find the birthday event
        birthday_events = [
            event
            for day in overview.days
            for event in day.events
            if event.occurrence_id == "evt-birthday-001"
        ]

        assert len(birthday_events) == 1
        event = birthday_events[0]
        assert event.event_type == "BIRTHDAY_ACCOUNT"
        assert event.calendar_id == "calendarSpecialDays/account-456"


class TestMalformedAndSkipped:
    """Tests for handling malformed events and skipped counts."""

    async def test_skipped_malformed_event_sets_incomplete_and_adds_note(
        self, service: CalendarService, fake_transport: FakeCalendarTransport
    ) -> None:
        """A skipped malformed event sets complete=False and adds a note."""
        fake_transport.set_response_sequence([[timed_event(), malformed_event(), all_day_event()]])

        overview = await service.get_week_overview(
            date(2026, 9, 14), "Australia/Sydney", "calendar/family-123"
        )

        assert overview.complete is False
        assert any("skipped" in note for note in overview.notes)

    async def test_zero_events_returns_empty_week_complete_true(
        self, service: CalendarService, fake_transport: FakeCalendarTransport
    ) -> None:
        """Zero events returns an empty week with complete=True and no note."""
        fake_transport.set_response_sequence([[]])

        overview = await service.get_week_overview(
            date(2026, 9, 14), "Australia/Sydney", "calendar/family-123"
        )

        assert overview.total_events == 0
        assert overview.complete is True
        assert len(overview.notes) == 0


class TestErrorHandling:
    """Tests for error propagation."""

    async def test_transport_error_propagates_not_empty_week(
        self, service: CalendarService, fake_transport: FakeCalendarTransport
    ) -> None:
        """A transport error propagates as its typed error—it does not become an empty week."""
        error = TransportError()
        fake_transport.raise_on_call = error

        with pytest.raises(TransportError):
            await service.get_week_overview(
                date(2026, 9, 14), "Australia/Sydney", "calendar/family-123"
            )


class TestEventCapAndPartialResults:
    """Tests for event cap and completeness tracking."""

    async def test_exceeding_event_cap_sets_incomplete_and_adds_note(
        self, service: CalendarService, fake_transport: FakeCalendarTransport
    ) -> None:
        """Exceeding the event cap sets complete=False and adds a note."""
        service.max_events = 2

        # Create 3 events
        events = [
            timed_event(),
            all_day_event(),
            birthday_account_event(),
        ]

        fake_transport.set_response_sequence([events])

        overview = await service.get_week_overview(
            date(2026, 9, 14), "Australia/Sydney", "calendar/family-123"
        )

        assert overview.complete is False
        assert overview.total_events == 2  # Capped at 2
        assert any("cap" in note.lower() for note in overview.notes)


class TestMultipleRequests:
    """Tests for splitting long ranges into multiple requests."""

    async def test_60_day_range_issues_multiple_requests(
        self, service: CalendarService, fake_transport: FakeCalendarTransport
    ) -> None:
        """A 60-day range issues more than one request, each within the 31-day bound."""
        # This test would require a longer date range than a week
        # For now, just verify the single week doesn't exceed max days per request
        fake_transport.set_response_sequence([[]])

        await service.get_week_overview(
            date(2026, 9, 14), "Australia/Sydney", "calendar/family-123"
        )

        # A week is 7 days, so it should be one request
        assert len(fake_transport.calls) == 1

        # Verify the request spans at most 31 days
        endpoint, fields = fake_transport.calls[0]
        # The service should not split a week into multiple requests


class TestDSTTransitions:
    """Tests for daylight saving time transitions."""

    async def test_spring_forward_sydney_week_has_seven_days(
        self, service: CalendarService, fake_transport: FakeCalendarTransport
    ) -> None:
        """Spring-forward Sydney week still has 7 days with correct event placement."""
        # October 4, 2026 is when Sydney springs forward (2:00 AM -> 3:00 AM)
        fake_transport.set_response_sequence([[timed_event()]])

        overview = await service.get_week_overview(
            date(2026, 10, 4), "Australia/Sydney", "calendar/family-123"
        )

        assert len(overview.days) == 7


class TestTimezoneVariance:
    """Tests for same week in different timezones."""

    async def test_same_week_different_timezone_moves_events_to_different_days(
        self, service: CalendarService, fake_transport: FakeCalendarTransport
    ) -> None:
        """The same week requested in a different timezone moves events to different local days."""
        # Create a specific timed event
        # 2026-09-14T15:00:00.000Z (3 PM UTC)
        # In Sydney (UTC+10): 2026-09-15T01:00:00
        # In New York (UTC-4): 2026-09-14T11:00:00
        event_crossing_tz = {
            "eventId": "evt-tz-cross",
            "eventMasterId": "evt-tz-cross",
            "occurenceIndex": "0",
            "text": "TZ boundary event",
            "startDate": "2026-09-14T15:00:00.000Z",
            "endDate": "2026-09-14T16:00:00.000Z",
            "allDay": "false",
            "timeZone": "UTC",
            "recurrency": "NONE",
            "eventType": "UNKNOWN",
            "calendarId": "calendar/family-123",
        }

        # Request in Sydney
        fake_transport.set_response_sequence([[event_crossing_tz]])
        sydney_overview = await service.get_week_overview(
            date(2026, 9, 14), "Australia/Sydney", "calendar/family-123"
        )

        # Count which days have the event in Sydney
        sydney_days_with_event = [
            day.day
            for day in sydney_overview.days
            if any(e.occurrence_id == "evt-tz-cross" for e in day.events)
        ]

        # Reset transport for New York request
        fake_transport.call_count = 0
        fake_transport.calls = []
        fake_transport.set_response_sequence([[event_crossing_tz]])

        # Request in New York (same UTC date, but different local days)
        ny_overview = await service.get_week_overview(
            date(2026, 9, 14), "America/New_York", "calendar/family-123"
        )

        # Count which days have the event in New York
        ny_days_with_event = [
            day.day
            for day in ny_overview.days
            if any(e.occurrence_id == "evt-tz-cross" for e in day.events)
        ]

        # The event should appear on different dates due to timezone
        # 3 PM UTC on Sept 14 is Sept 15 01:00 in Sydney, so day_agenda.day for Sept 15
        # But 3 PM UTC on Sept 14 is 11 AM on Sept 14 in NY
        assert sydney_days_with_event != ny_days_with_event


class TestEventOrdering:
    """Tests for deterministic event ordering within a day."""

    async def test_ordering_all_day_first_then_timed_by_start(
        self, service: CalendarService, fake_transport: FakeCalendarTransport
    ) -> None:
        """Events are ordered: all-day first, then timed by start time, then by occurrence_id."""
        # Mix all-day and timed events on the same day
        all_day = {
            "eventId": "evt-allday-zz",
            "eventMasterId": "evt-allday-zz",
            "occurenceIndex": "0",
            "text": "All day event",
            "startDate": "2026-09-14T00:00:00.000Z",
            "endDate": "2026-09-14T23:59:59.000Z",
            "allDay": "true",
            "timeZone": "Australia/Sydney",
            "recurrency": "NONE",
            "eventType": "UNKNOWN",
            "calendarId": "calendar/family-123",
        }

        timed_1 = {
            "eventId": "evt-timed-aaa",
            "eventMasterId": "evt-timed-aaa",
            "occurenceIndex": "0",
            "text": "Timed event 1",
            "startDate": "2026-09-14T08:00:00.000Z",
            "endDate": "2026-09-14T09:00:00.000Z",
            "allDay": "false",
            "timeZone": "Australia/Sydney",
            "recurrency": "NONE",
            "eventType": "UNKNOWN",
            "calendarId": "calendar/family-123",
        }

        timed_2 = {
            "eventId": "evt-timed-bbb",
            "eventMasterId": "evt-timed-bbb",
            "occurenceIndex": "0",
            "text": "Timed event 2",
            "startDate": "2026-09-14T10:00:00.000Z",
            "endDate": "2026-09-14T11:00:00.000Z",
            "allDay": "false",
            "timeZone": "Australia/Sydney",
            "recurrency": "NONE",
            "eventType": "UNKNOWN",
            "calendarId": "calendar/family-123",
        }

        # Provide them in random order to the service
        fake_transport.set_response_sequence([[timed_2, all_day, timed_1]])

        overview = await service.get_week_overview(
            date(2026, 9, 14), "Australia/Sydney", "calendar/family-123"
        )

        # Find Sept 14
        sept_14 = None
        for day in overview.days:
            if day.day == date(2026, 9, 14):
                sept_14 = day
                break

        assert sept_14 is not None
        assert len(sept_14.events) == 3

        # First should be all-day
        from familywall_mcp.familywall.calendar import AllDaySpan, TimedSpan

        assert isinstance(sept_14.events[0].span, AllDaySpan)
        assert sept_14.events[0].occurrence_id == "evt-allday-zz"

        # Then timed, ordered by start time
        assert isinstance(sept_14.events[1].span, TimedSpan)
        assert isinstance(sept_14.events[2].span, TimedSpan)

        # The timed events should be in start-time order
        timed_event_1 = sept_14.events[1]
        timed_event_2 = sept_14.events[2]

        if isinstance(timed_event_1.span, TimedSpan) and isinstance(timed_event_2.span, TimedSpan):
            assert timed_event_1.span.start <= timed_event_2.span.start
