"""Tests for calendar event parsing and adaptation."""

from __future__ import annotations

from datetime import UTC, date, datetime

import pytest
from tests.support.calendar_fixtures import (
    all_day_event,
    bare_event_array,
    birthday_account_event,
    event_with_assignment_fields,
    event_with_malformed_attendee_ids,
    event_with_malformed_editable,
    event_with_malformed_to_all,
    malformed_event,
    multiday_all_day_event,
    recurring_series_occurrence_1,
    recurring_series_occurrence_2,
    recurring_series_occurrence_3,
    recurring_with_exdate,
    timed_event,
    wrapped_datas_array,
    wrapped_event_array,
    wrapped_results_array,
    wrapped_updated_created,
)

from familywall_mcp.errors import MalformedPayloadError
from familywall_mcp.familywall.calendar import (
    AllDaySpan,
    TimedSpan,
    build_create_event_fields,
    build_interval_fields,
    parse_created_event_id,
    parse_events,
)


class TestTimedEventParsing:
    """Test parsing of timed events to TimedSpan."""

    def test_timed_event_parses_to_timed_span(self) -> None:
        """A timed event parses to TimedSpan with timezone-aware UTC datetimes."""
        payload = [timed_event()]
        result = parse_events(payload)

        assert len(result.events) == 1
        assert result.skipped == 0

        event = result.events[0]
        assert isinstance(event.span, TimedSpan)
        assert event.span.start == datetime(2026, 9, 14, 9, 30, 0, tzinfo=UTC)
        assert event.span.end == datetime(2026, 9, 14, 10, 0, 0, tzinfo=UTC)

    def test_raw_start_end_preserved(self) -> None:
        """raw_start and raw_end equal the upstream strings exactly."""
        event = timed_event()
        payload = [event]
        result = parse_events(payload)

        parsed = result.events[0]
        assert parsed.raw_start == event["startDate"]
        assert parsed.raw_end == event["endDate"]


class TestAllDayEventParsing:
    """Test parsing of all-day events to AllDaySpan."""

    def test_single_day_all_day_event(self) -> None:
        """An all-day event on 2026-09-12T00:00:00.000Z→23:59:59.000Z
        parses to AllDaySpan(date(2026,9,12), date(2026,9,12))."""
        payload = [all_day_event()]
        result = parse_events(payload)

        assert len(result.events) == 1
        event = result.events[0]

        assert isinstance(event.span, AllDaySpan)
        assert event.span.start_date == date(2026, 9, 12)
        assert event.span.end_date == date(2026, 9, 12)

    def test_all_day_no_sydney_conversion(self) -> None:
        """Assert explicitly that no Australia/Sydney conversion happened.

        An all-day event on the date 2026-09-12 should stay 2026-09-12,
        even though Sydney is UTC+10 (or UTC+11 in spring).
        """
        payload = [all_day_event()]
        result = parse_events(payload)

        event = result.events[0]
        assert isinstance(event.span, AllDaySpan)

        # The raw timestamp is 2026-09-12T00:00:00.000Z
        # If we naively converted to Sydney time, we'd get 2026-09-12T10:00:00+10:00
        # But we should NOT do that. We take the date verbatim: 2026-09-12
        assert event.span.start_date == date(2026, 9, 12)
        assert event.span.end_date == date(2026, 9, 12)

        # Sanity check: the event_timezone is Sydney, but we didn't use it
        assert event.event_timezone == "Australia/Sydney"

    def test_multiday_all_day_event(self) -> None:
        """A multi-day all-day event yields differing start and end dates."""
        payload = [multiday_all_day_event()]
        result = parse_events(payload)

        event = result.events[0]
        assert isinstance(event.span, AllDaySpan)
        assert event.span.start_date == date(2026, 9, 20)
        assert event.span.end_date == date(2026, 9, 23)


class TestRecurringEvents:
    """Test parsing of recurring event series."""

    def test_three_occurrences_same_series(self) -> None:
        """Three occurrences of one series yield three distinct events
        with the same series_id."""
        payload = [
            recurring_series_occurrence_1(),
            recurring_series_occurrence_2(),
            recurring_series_occurrence_3(),
        ]
        result = parse_events(payload)

        assert len(result.events) == 3
        assert result.skipped == 0

        # All have the same series_id
        assert result.events[0].series_id == "evt-series-master"
        assert result.events[1].series_id == "evt-series-master"
        assert result.events[2].series_id == "evt-series-master"

        # But different occurrence_ids
        assert result.events[0].occurrence_id == "evt-series-001"
        assert result.events[1].occurrence_id == "evt-series-002"
        assert result.events[2].occurrence_id == "evt-series-003"

        # De-duplication by occurrence_id keeps all three
        occurrence_ids = {e.occurrence_id for e in result.events}
        assert len(occurrence_ids) == 3

    def test_occurrence_index_parsed(self) -> None:
        """Occurrence index is parsed from the string integer."""
        payload = [
            recurring_series_occurrence_1(),
            recurring_series_occurrence_2(),
            recurring_series_occurrence_3(),
        ]
        result = parse_events(payload)

        assert result.events[0].occurrence_index == 0
        assert result.events[1].occurrence_index == 1
        assert result.events[2].occurrence_index == 2


class TestExdateHandling:
    """Test that exdate and recurrencyDeletedOccurence are preserved."""

    def test_exdate_preserved(self) -> None:
        """exdate and recurrencyDeletedOccurence are carried, not filtered."""
        payload = [recurring_with_exdate()]
        result = parse_events(payload)

        event = result.events[0]
        # The fields are present in the upstream but are not part of the
        # CalendarEvent model, so we just verify the event was parsed
        # and the presence of these fields didn't cause it to be skipped.
        assert event.occurrence_id == "evt-exdate-001"
        assert event.is_recurring is True


class TestBirthdayAccountEvent:
    """Test handling of special event types like BIRTHDAY_ACCOUNT."""

    def test_birthday_account_no_timezone(self) -> None:
        """A BIRTHDAY_ACCOUNT event with no timeZone parses, keeps its
        event_type verbatim, and reports event_timezone is None."""
        payload = [birthday_account_event()]
        result = parse_events(payload)

        event = result.events[0]
        assert event.event_type == "BIRTHDAY_ACCOUNT"
        assert event.event_timezone is None
        assert event.calendar_id == "calendarSpecialDays/account-456"

    def test_unknown_event_type_preserved(self) -> None:
        """An unknown eventType value is preserved."""
        event_dict = timed_event()
        event_dict["eventType"] = "CUSTOM_TYPE_XYZ"
        payload = [event_dict]
        result = parse_events(payload)

        assert result.events[0].event_type == "CUSTOM_TYPE_XYZ"


class TestMalformedEvents:
    """Test handling of malformed events."""

    def test_malformed_event_skipped(self) -> None:
        """One malformed event among several is skipped,
        skipped == 1, the rest are returned."""
        payload = [
            timed_event(),
            malformed_event(),  # Missing "text" field
            all_day_event(),
        ]
        result = parse_events(payload)

        assert len(result.events) == 2
        assert result.skipped == 1
        assert result.events[0].title == "Team standup"
        assert result.events[1].title == "Team building day"

    def test_malformed_payload_raises(self) -> None:
        """A non-array, non-wrapper payload raises MalformedPayloadError."""
        with pytest.raises(MalformedPayloadError):
            parse_events("not an array")

        with pytest.raises(MalformedPayloadError):
            parse_events(123)

        with pytest.raises(MalformedPayloadError):
            parse_events({"wrong_key": []})


class TestPayloadWrapping:
    """Test handling of wrapped event arrays."""

    def test_bare_array_accepted(self) -> None:
        """A bare array of events is accepted (the real shape)."""
        payload = bare_event_array()
        result = parse_events(payload)

        # Should have 8 events (the 9th is malformed)
        assert len(result.events) == 8
        assert result.skipped == 1

    def test_wrapped_events_key(self) -> None:
        """Events wrapped under "events" key are accepted."""
        payload = wrapped_event_array()
        result = parse_events(payload)

        assert len(result.events) == 8
        assert result.skipped == 1

    def test_wrapped_datas_key(self) -> None:
        """Events wrapped under "datas" key are accepted."""
        payload = wrapped_datas_array()
        result = parse_events(payload)

        assert len(result.events) == 8
        assert result.skipped == 1

    def test_wrapped_updated_created_key(self) -> None:
        """Events wrapped under "updatedCreated" key are accepted."""
        payload = wrapped_updated_created()
        result = parse_events(payload)

        assert len(result.events) == 8
        assert result.skipped == 1

    def test_wrapped_results_key(self) -> None:
        """Events wrapped under "results" key are accepted."""
        payload = wrapped_results_array()
        result = parse_events(payload)

        assert len(result.events) == 8
        assert result.skipped == 1


class TestIntervalFields:
    """Test building request parameters for evtlistinterval."""

    def test_build_interval_fields(self) -> None:
        """build_interval_fields emits ISO strings with an offset
        and the four expected keys."""
        # Use a UTC time
        start = datetime(2026, 9, 14, 0, 0, 0, tzinfo=UTC)
        end = datetime(2026, 9, 21, 0, 0, 0, tzinfo=UTC)
        calendar_id = "calendar/family-123"

        result = build_interval_fields(calendar_id, start, end)

        assert set(result.keys()) == {"partnerScope", "calendarId", "a00from", "a00to"}
        assert result["partnerScope"] == "Family"
        assert result["calendarId"] == calendar_id
        # Check that the ISO strings contain an offset
        assert "+00:00" in result["a00from"]
        assert "+00:00" in result["a00to"]
        assert "2026-09-14" in result["a00from"]
        assert "2026-09-21" in result["a00to"]

    def test_interval_fields_with_offset(self) -> None:
        """ISO strings preserve timezone offsets."""
        # Use a timezone-aware time with an offset
        from zoneinfo import ZoneInfo

        tz = ZoneInfo("Australia/Sydney")
        start = datetime(2026, 9, 14, 8, 0, 0, tzinfo=tz)
        end = datetime(2026, 9, 21, 8, 0, 0, tzinfo=tz)

        result = build_interval_fields("calendar/test", start, end)

        # The ISO strings should preserve the Sydney offset
        # Sydney is UTC+10 in September (spring)
        assert "+10:00" in result["a00from"]
        assert "+10:00" in result["a00to"]


class TestBuildCreateEventFields:
    """Test the evtcreate form builder."""

    def test_complete_form_for_a_timed_event(self) -> None:
        """The whole form is asserted, so an added or dropped field fails loudly."""
        from zoneinfo import ZoneInfo

        tz = ZoneInfo("Australia/Sydney")
        fields = build_create_event_fields(
            title="Dentist",
            start=datetime(2026, 10, 6, 10, 0, tzinfo=tz),
            end=datetime(2026, 10, 6, 11, 0, tzinfo=tz),
            timezone="Australia/Sydney",
            attendee_account_id="acct-synthetic-1",
            location="Main St",
            description="Checkup",
        )

        assert fields == {
            "partnerScope": "Family",
            "text": "Dentist",
            "startDate": "2026-10-06T10:00:00+11:00",
            "endDate": "2026-10-06T11:00:00+11:00",
            "timeZone": "Australia/Sydney",
            "where": "Main St",
            "description": "Checkup",
            "isToAll": "false",
            "attendee.0.accountId": "acct-synthetic-1",
            "picture": "$empty",
            "private": "",
            "recurrency": "NONE",
            "recurrencyInterval": "1",
            "byDay": "",
            "byMonthDay": "",
            "recurrencyEndDate": "$empty",
            "reminderList": "$empty",
        }

    def test_never_hard_codes_london(self) -> None:
        """The reference client's Europe/London default must not leak through."""
        from zoneinfo import ZoneInfo

        tz = ZoneInfo("America/New_York")
        fields = build_create_event_fields(
            title="Call",
            start=datetime(2026, 11, 1, 9, 0, tzinfo=tz),
            end=datetime(2026, 11, 1, 9, 30, tzinfo=tz),
            timezone="America/New_York",
            attendee_account_id="acct-synthetic-1",
        )
        assert fields["timeZone"] == "America/New_York"
        assert fields["startDate"] == "2026-11-01T09:00:00-05:00"
        assert "London" not in "".join(fields.values())

    def test_absent_location_and_description_are_empty(self) -> None:
        fields = build_create_event_fields(
            title="Call",
            start=datetime(2026, 10, 6, 0, 0, tzinfo=UTC),
            end=datetime(2026, 10, 6, 1, 0, tzinfo=UTC),
            timezone="UTC",
            attendee_account_id="acct-synthetic-1",
        )
        assert fields["where"] == ""
        assert fields["description"] == ""
        assert "color" not in fields

    def test_naive_datetime_is_rejected(self) -> None:
        with pytest.raises(ValueError):
            build_create_event_fields(
                title="Call",
                start=datetime(2026, 10, 6, 10, 0),
                end=datetime(2026, 10, 6, 11, 0),
                timezone="UTC",
                attendee_account_id="acct-synthetic-1",
            )


class TestParseCreatedEventId:
    """Test extraction of the created event's ID."""

    def test_event_id_is_preferred(self) -> None:
        assert parse_created_event_id({"eventId": "event/1", "metaId": "event/2"}) == "event/1"

    def test_meta_id_is_the_fallback(self) -> None:
        assert parse_created_event_id({"metaId": "event/2"}) == "event/2"

    @pytest.mark.parametrize(
        "payload",
        [None, "true", [], {}, {"eventId": ""}, {"eventId": 7}, {"text": "Dentist"}],
    )
    def test_unusable_responses_yield_none(self, payload: object) -> None:
        assert parse_created_event_id(payload) is None


class TestAssignmentFields:
    """T15: attendee_ids, to_all and editable parse; absent fields default; each
    malformed case skips the event and increments skipped."""

    def test_t15_assignment_fields_parse(self) -> None:
        """Present attendeeIds, toAll and editable all parse onto the event."""
        payload = [event_with_assignment_fields()]
        result = parse_events(payload)

        assert result.skipped == 0
        event = result.events[0]
        assert event.attendee_ids == ("acc-alice", "acc-bob")
        assert event.to_all is False
        assert event.editable is True

    def test_t15_absent_assignment_fields_give_defaults(self) -> None:
        """An event with none of the three fields defaults to () / None / None."""
        payload = [timed_event()]
        result = parse_events(payload)

        assert result.skipped == 0
        event = result.events[0]
        assert event.attendee_ids == ()
        assert event.to_all is None
        assert event.editable is None

    def test_t15_malformed_attendee_ids_skips_event(self) -> None:
        """attendeeIds containing a non-string entry skips the event."""
        payload = [event_with_malformed_attendee_ids()]
        result = parse_events(payload)

        assert len(result.events) == 0
        assert result.skipped == 1

    def test_t15_malformed_to_all_skips_event(self) -> None:
        """toAll: "maybe" skips the event."""
        payload = [event_with_malformed_to_all()]
        result = parse_events(payload)

        assert len(result.events) == 0
        assert result.skipped == 1

    def test_t15_malformed_editable_skips_event(self) -> None:
        """editable: "maybe" skips the event."""
        payload = [event_with_malformed_editable()]
        result = parse_events(payload)

        assert len(result.events) == 0
        assert result.skipped == 1
