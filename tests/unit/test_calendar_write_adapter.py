"""Tests for calendar write wire builders: evtupdate, evtdelete and their helpers."""

from __future__ import annotations

from datetime import UTC, date, datetime
from zoneinfo import ZoneInfo

import pytest

from familywall_mcp.errors import MalformedPayloadError
from familywall_mcp.familywall.calendar import build_delete_event_fields, utc_wall_clock

BERLIN = ZoneInfo("Europe/Berlin")


class TestUtcWallClock:
    """evtupdate times are sent as UTC wall-clock time.

    Live evidence (2026-09-23): evtcreate stored startDate "2026-09-30T14:00:00"
    sent with timeZone=Europe/Berlin as 14:00Z, so a time without an offset is
    read as UTC and the timeZone field does not shift it.
    """

    def test_sends_the_utc_instant(self) -> None:
        assert utc_wall_clock(datetime(2026, 9, 25, 14, 0, tzinfo=BERLIN)) == "2026-09-25T12:00:00"

    def test_winter_time_uses_the_winter_offset(self) -> None:
        """CET is UTC+1, so the conversion follows the date, not a fixed offset."""
        assert utc_wall_clock(datetime(2026, 12, 3, 9, 0, tzinfo=BERLIN)) == "2026-12-03T08:00:00"

    def test_utc_input_is_sent_unchanged(self) -> None:
        assert utc_wall_clock(datetime(2026, 9, 25, 12, 0, tzinfo=UTC)) == "2026-09-25T12:00:00"

    def test_rejects_naive_datetimes(self) -> None:
        """A naive datetime has no defined instant, so it is never guessed at."""
        with pytest.raises(ValueError):
            utc_wall_clock(datetime(2026, 9, 25, 14, 0))


class TestBuildDeleteEventFields:
    def test_fields(self) -> None:
        assert build_delete_event_fields("event/1_2") == {
            "partnerScope": "Family",
            "option": "All",
            "eventId.0": "event/1_2",
        }

    def test_rejects_non_event_id(self) -> None:
        with pytest.raises(ValueError):
            build_delete_event_fields("task/1_2")


class TestEventUpdateWire:
    """evtupdate is partial: only fields sent are changed (live-verified 2026-09-29)."""

    def test_update_fields_carry_only_the_changes(self) -> None:
        from familywall_mcp.familywall.calendar import build_update_event_fields

        assert build_update_event_fields("event/1_2", {"text": "Neu"}) == {
            "partnerScope": "Family",
            "option": "All",
            "metaId": "event/1_2",
            "text": "Neu",
        }

    def test_update_rejects_non_event_id(self) -> None:
        from familywall_mcp.familywall.calendar import build_update_event_fields

        with pytest.raises(ValueError):
            build_update_event_fields("task/1", {"text": "x"})

    def test_update_requires_a_change(self) -> None:
        from familywall_mcp.familywall.calendar import build_update_event_fields

        with pytest.raises(ValueError):
            build_update_event_fields("event/1_2", {})

    def test_update_refuses_to_override_the_envelope(self) -> None:
        """A change may never smuggle in its own option or target id."""
        from familywall_mcp.familywall.calendar import build_update_event_fields

        for key in ("option", "metaId", "partnerScope", "eventId"):
            with pytest.raises(ValueError):
                build_update_event_fields("event/1_2", {key: "x"})

    def test_utc_wall_clock(self) -> None:
        from familywall_mcp.familywall.calendar import utc_wall_clock

        assert utc_wall_clock(datetime(2026, 10, 7, 14, 30, tzinfo=BERLIN)) == "2026-10-07T12:30:00"
        with pytest.raises(ValueError):
            utc_wall_clock(datetime(2026, 10, 7, 14, 30))

    def test_all_day_wire_bounds(self) -> None:
        from familywall_mcp.familywall.calendar import all_day_wire_bounds

        assert all_day_wire_bounds(date(2026, 10, 1), date(2026, 10, 3)) == (
            "2026-10-01T00:00:00",
            "2026-10-03T23:59:59",
        )

    def test_get_and_option_fields(self) -> None:
        from familywall_mcp.familywall.calendar import build_event_id_fields

        assert build_event_id_fields("event/1_2") == {
            "partnerScope": "Family",
            "eventId": "event/1_2",
        }

    def test_parse_single_event(self) -> None:
        from tests.support.calendar_fixtures import timed_event

        from familywall_mcp.familywall.calendar import parse_single_event

        event = parse_single_event(timed_event())
        assert event.occurrence_id == "evt-timed-001"

    @pytest.mark.parametrize("payload", [None, [], "x", {"eventId": "e"}])
    def test_parse_single_event_rejects_malformed(self, payload: object) -> None:
        from familywall_mcp.familywall.calendar import parse_single_event

        with pytest.raises(MalformedPayloadError):
            parse_single_event(payload)

    def test_parse_event_options(self) -> None:
        from familywall_mcp.familywall.calendar import parse_event_options

        assert parse_event_options(["All"]) == ("All",)
        assert parse_event_options(["All", "OnlyOne", "OnlyAfter"]) == (
            "All",
            "OnlyOne",
            "OnlyAfter",
        )

    @pytest.mark.parametrize("payload", [None, "All", {}, [1], ["All", None]])
    def test_parse_event_options_rejects_malformed(self, payload: object) -> None:
        from familywall_mcp.familywall.calendar import parse_event_options

        with pytest.raises(MalformedPayloadError):
            parse_event_options(payload)
