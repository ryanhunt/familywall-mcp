"""Synthetic calendar event payloads for testing."""

from __future__ import annotations


def timed_event() -> dict:
    """A single timed event with specific start and end times."""
    return {
        "eventId": "evt-timed-001",
        "eventMasterId": "evt-timed-001",
        "occurenceIndex": "0",
        "text": "Team standup",
        "startDate": "2026-09-14T09:30:00.000Z",
        "endDate": "2026-09-14T10:00:00.000Z",
        "allDay": "false",
        "timeZone": "Australia/Sydney",
        "recurrency": "NONE",
        "eventType": "UNKNOWN",
        "calendarId": "calendar/family-123",
    }


def all_day_event() -> dict:
    """A single all-day event (one calendar day)."""
    return {
        "eventId": "evt-allday-001",
        "eventMasterId": "evt-allday-001",
        "occurenceIndex": "0",
        "text": "Team building day",
        "startDate": "2026-09-12T00:00:00.000Z",
        "endDate": "2026-09-12T23:59:59.000Z",
        "allDay": "true",
        "timeZone": "Australia/Sydney",
        "recurrency": "NONE",
        "eventType": "UNKNOWN",
        "calendarId": "calendar/family-123",
    }


def multiday_all_day_event() -> dict:
    """An all-day event spanning multiple calendar days."""
    return {
        "eventId": "evt-multiday-001",
        "eventMasterId": "evt-multiday-001",
        "occurenceIndex": "0",
        "text": "School holidays",
        "startDate": "2026-09-20T00:00:00.000Z",
        "endDate": "2026-09-23T23:59:59.000Z",
        "allDay": "true",
        "timeZone": "Australia/Sydney",
        "recurrency": "NONE",
        "eventType": "UNKNOWN",
        "calendarId": "calendar/family-123",
    }


def recurring_series_occurrence_1() -> dict:
    """First occurrence of a weekly recurring event series."""
    return {
        "eventId": "evt-series-001",
        "eventMasterId": "evt-series-master",
        "occurenceIndex": "0",
        "text": "Soccer training",
        "startDate": "2026-09-02T17:00:00.000Z",
        "endDate": "2026-09-02T18:00:00.000Z",
        "allDay": "false",
        "timeZone": "Australia/Sydney",
        "recurrency": "WEEKLY",
        "rrule": "FREQ=WEEKLY;UNTIL=20261021;INTERVAL=1;BYDAY=WE",
        "eventType": "UNKNOWN",
        "calendarId": "calendar/family-123",
    }


def recurring_series_occurrence_2() -> dict:
    """Second occurrence of the same weekly series."""
    return {
        "eventId": "evt-series-002",
        "eventMasterId": "evt-series-master",
        "occurenceIndex": "1",
        "text": "Soccer training",
        "startDate": "2026-09-09T17:00:00.000Z",
        "endDate": "2026-09-09T18:00:00.000Z",
        "allDay": "false",
        "timeZone": "Australia/Sydney",
        "recurrency": "WEEKLY",
        "rrule": "FREQ=WEEKLY;UNTIL=20261021;INTERVAL=1;BYDAY=WE",
        "eventType": "UNKNOWN",
        "calendarId": "calendar/family-123",
    }


def recurring_series_occurrence_3() -> dict:
    """Third occurrence of the same weekly series."""
    return {
        "eventId": "evt-series-003",
        "eventMasterId": "evt-series-master",
        "occurenceIndex": "2",
        "text": "Soccer training",
        "startDate": "2026-09-16T17:00:00.000Z",
        "endDate": "2026-09-16T18:00:00.000Z",
        "allDay": "false",
        "timeZone": "Australia/Sydney",
        "recurrency": "WEEKLY",
        "rrule": "FREQ=WEEKLY;UNTIL=20261021;INTERVAL=1;BYDAY=WE",
        "eventType": "UNKNOWN",
        "calendarId": "calendar/family-123",
    }


def recurring_with_exdate() -> dict:
    """A recurring event with exdate and recurrencyDeletedOccurence arrays.

    Note: These fields are already applied by the server; we carry them
    through but never filter based on them.
    """
    return {
        "eventId": "evt-exdate-001",
        "eventMasterId": "evt-exdate-master",
        "occurenceIndex": "0",
        "text": "Weekly review",
        "startDate": "2026-09-07T10:00:00.000Z",
        "endDate": "2026-09-07T11:00:00.000Z",
        "allDay": "false",
        "timeZone": "Australia/Sydney",
        "recurrency": "WEEKLY",
        "rrule": "FREQ=WEEKLY;BYDAY=MO",
        "exdate": ["2026-09-14T10:00:00.000Z"],
        "recurrencyDeletedOccurence": ["1"],
        "eventType": "UNKNOWN",
        "calendarId": "calendar/family-123",
    }


def birthday_account_event() -> dict:
    """A BIRTHDAY_ACCOUNT event on a special calendar, with no timeZone field."""
    return {
        "eventId": "evt-birthday-001",
        "eventMasterId": "evt-birthday-001",
        "occurenceIndex": "0",
        "text": "Alice's birthday",
        "startDate": "2026-09-25T00:00:00.000Z",
        "endDate": "2026-09-25T23:59:59.000Z",
        "allDay": "true",
        "recurrency": "NONE",
        "eventType": "BIRTHDAY_ACCOUNT",
        "calendarId": "calendarSpecialDays/account-456",
    }


def malformed_event() -> dict:
    """A deliberately malformed event (missing required field)."""
    return {
        "eventId": "evt-bad-001",
        "eventMasterId": "evt-bad-001",
        # Missing required "text" field
        "startDate": "2026-09-14T09:00:00.000Z",
        "endDate": "2026-09-14T10:00:00.000Z",
        "allDay": "false",
    }


def bare_event_array() -> list:
    """A bare array of events (the real shape from FamilyWall)."""
    return [
        timed_event(),
        all_day_event(),
        multiday_all_day_event(),
        recurring_series_occurrence_1(),
        recurring_series_occurrence_2(),
        recurring_series_occurrence_3(),
        recurring_with_exdate(),
        birthday_account_event(),
        malformed_event(),
    ]


def wrapped_event_array() -> dict:
    """Events wrapped under the "events" key."""
    return {"events": bare_event_array()}


def wrapped_datas_array() -> dict:
    """Events wrapped under the "datas" key."""
    return {"datas": bare_event_array()}


def wrapped_updated_created() -> dict:
    """Events wrapped under the "updatedCreated" key."""
    return {"updatedCreated": bare_event_array()}


def wrapped_results_array() -> dict:
    """Events wrapped under the "results" key."""
    return {"results": bare_event_array()}
