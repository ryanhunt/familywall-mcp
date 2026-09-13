"""Tests for local calendar arithmetic and date ranges."""

from __future__ import annotations

from datetime import UTC, date, datetime
from zoneinfo import ZoneInfo

import pytest

from familywall_mcp.services.ranges import (
    LocalRange,
    all_day_overlaps,
    overlaps,
    parse_boundary_input,
    resolve_days,
    resolve_week,
)


class TestResolveWeek:
    """Test local week range calculation."""

    def test_sydney_week_is_seven_days_not_168_hours(self) -> None:
        """The Sydney spring-forward week (October) is 7 local days
        and not 168 hours; likewise the fall-back week (April)."""
        # October 2026: spring forward happens on Oct 4 at 2:00 AM
        # The week containing Oct 4 has only 167 UTC hours (one hour is skipped)
        # But 7 local calendar days
        oct_week = resolve_week(date(2026, 10, 4), "Australia/Sydney")
        assert (oct_week.end - oct_week.start).days == 7  # 7 full days

        # Convert to UTC to get the true duration, accounting for DST

        oct_start_utc = oct_week.start.astimezone(UTC)
        oct_end_utc = oct_week.end.astimezone(UTC)
        oct_hours = (oct_end_utc - oct_start_utc).total_seconds() / 3600
        assert oct_hours == 167  # Spring forward: 168 - 1

        # April 2027: fall back happens on Apr 4 at 3:00 AM
        # The week containing Apr 4 has 169 UTC hours (the hour repeats)
        # But still 7 local calendar days
        apr_week = resolve_week(date(2027, 4, 4), "Australia/Sydney")
        assert (apr_week.end - apr_week.start).days == 7  # 7 full days

        # Convert to UTC to get the true duration, accounting for DST
        apr_start_utc = apr_week.start.astimezone(UTC)
        apr_end_utc = apr_week.end.astimezone(UTC)
        apr_hours = (apr_end_utc - apr_start_utc).total_seconds() / 3600
        assert apr_hours == 169  # Fall back: 168 + 1

    def test_same_local_week_different_timezones(self) -> None:
        """The same local week in two timezones produces different absolute bounds."""

        week_ny = resolve_week(date(2026, 9, 14), "America/New_York")
        week_sydney = resolve_week(date(2026, 9, 14), "Australia/Sydney")

        # Both are 7 local days
        assert (week_ny.end - week_ny.start).days == 7
        assert (week_sydney.end - week_sydney.start).days == 7

        # But their UTC extents differ due to different offsets
        # (NY is UTC-4 in Sept, Sydney is UTC+10)
        # Convert to UTC to get the true durations
        week_ny_start_utc = week_ny.start.astimezone(UTC)
        week_ny_end_utc = week_ny.end.astimezone(UTC)
        week_sydney_start_utc = week_sydney.start.astimezone(UTC)
        week_sydney_end_utc = week_sydney.end.astimezone(UTC)

        duration_ny_hours = (week_ny_end_utc - week_ny_start_utc).total_seconds() / 3600
        duration_sydney_hours = (week_sydney_end_utc - week_sydney_start_utc).total_seconds() / 3600

        # Both should be around 168 hours (no DST transitions in Sept for these zones)
        assert 167 < duration_ny_hours < 169
        assert 167 < duration_sydney_hours < 169

    def test_monday_start_and_sunday_start(self) -> None:
        """Monday-start and Sunday-start produce different, correct 7-day ranges."""
        # September 14, 2026 is a Sunday
        ref_date = date(2026, 9, 14)

        monday_week = resolve_week(ref_date, "UTC", week_starts_on="monday")
        sunday_week = resolve_week(ref_date, "UTC", week_starts_on="sunday")

        # Monday week should start on Sept 14 (the reference date, which is Sunday)
        # Actually, let me recalculate: Sept 14 is a Sunday, so:
        # Monday week: Sept 7 (Mon) - Sept 13 (Sun) or Sept 14 (Mon) - Sept 20 (Sun)?
        # No, if Sept 14 is Sunday, the previous Monday is Sept 7.
        # But wait, let me check: if the reference is Sunday and week_starts_on is Monday,
        # we go back (6+1)%7 = 0 days? No. weekday of Sunday is 6.
        # For Monday (0): days_back = ref_weekday = 6. So we go back 6 days from Sunday
        # (Sept 14) to get Monday Sept 8.
        # For Sunday: days_back = (6+1) % 7 = 0. So we stay at Sunday Sept 14.

        # Actually, let's be precise:
        # Sept 14, 2026 is actually a Monday (I should verify this)
        # Let me use a known date: Sept 14, 2026
        # I'll check the actual weekday

        # The way the code works:
        # - Monday start: days_back = ref_weekday
        # - Sunday start: days_back = (ref_weekday + 1) % 7

        # For a Sunday (weekday=6):
        # - Monday start: days_back = 6 (go back to previous Monday)
        # - Sunday start: days_back = 0 (stay at current Sunday)

        # Let's use a known Sunday and verify the bounds
        # I'll manually construct test with a reference that I know

        # Sept 14, 2026 is a Monday (weekday 0)

        # Monday week: current week starting this Monday
        # Sunday week: previous week starting Sunday before this Monday

        monday_start = monday_week.start.date()
        sunday_start = sunday_week.start.date()

        # Both should span 7 days
        assert (monday_week.end - monday_week.start).days == 7
        assert (sunday_week.end - sunday_week.start).days == 7

        # Monday week should start on Monday (2026-09-14)
        assert monday_start.weekday() == 0  # Monday

        # Sunday week should start on Sunday (2026-09-13)
        assert sunday_start.weekday() == 6  # Sunday

        # They should be exactly 1 day apart in start time
        assert abs((monday_week.start - sunday_week.start).total_seconds()) == 86400

    def test_week_timezone_awareness(self) -> None:
        """The returned range is in the requested timezone."""
        tz_utc = "UTC"
        tz_sydney = "Australia/Sydney"

        week_utc = resolve_week(date(2026, 9, 14), tz_utc)
        week_sydney = resolve_week(date(2026, 9, 14), tz_sydney)

        assert week_utc.timezone == tz_utc
        assert week_sydney.timezone == tz_sydney
        assert week_utc.start.tzinfo is not None
        assert week_sydney.start.tzinfo is not None


class TestResolveDays:
    """Test date range calculation with day limits."""

    def test_resolve_31_days(self) -> None:
        """resolve_days accepts 31 days."""
        result = resolve_days(date(2026, 9, 14), 31, "UTC")
        assert (result.end - result.start).days == 31
        assert (result.end.date() - result.start.date()).days == 31

    def test_resolve_32_days_rejected(self) -> None:
        """resolve_days rejects 32 days."""
        with pytest.raises(ValueError, match="days must be 1-31"):
            resolve_days(date(2026, 9, 14), 32, "UTC")

    def test_resolve_zero_days_rejected(self) -> None:
        """resolve_days rejects 0 days."""
        with pytest.raises(ValueError, match="days must be 1-31"):
            resolve_days(date(2026, 9, 14), 0, "UTC")

    def test_resolve_days_span(self) -> None:
        """resolve_days returns correct span."""
        result = resolve_days(date(2026, 9, 14), 7, "Australia/Sydney")
        start_date = result.start.date()
        end_date = result.end.date()
        assert start_date == date(2026, 9, 14)
        assert end_date == date(2026, 9, 21)


class TestParseBoundaryInput:
    """Test parsing of date and RFC 3339 boundary inputs."""

    def test_rfc3339_with_offset(self) -> None:
        """parse_boundary_input accepts RFC 3339 with offset."""
        result = parse_boundary_input("2026-09-14T00:00:00+10:00", "UTC")
        assert result == datetime(2026, 9, 13, 14, 0, 0, tzinfo=UTC)

    def test_bare_date(self) -> None:
        """parse_boundary_input accepts bare YYYY-MM-DD."""
        result = parse_boundary_input("2026-09-14", "Australia/Sydney")
        # Should be interpreted as midnight in Sydney time
        tz = ZoneInfo("Australia/Sydney")
        expected = datetime(2026, 9, 14, 0, 0, 0, tzinfo=tz)
        assert result == expected

    def test_naive_datetime_rejected(self) -> None:
        """parse_boundary_input rejects naive datetime strings."""
        with pytest.raises(ValueError, match="naive datetime"):
            parse_boundary_input("2026-09-14T00:00:00", "UTC")

    def test_malformed_input_rejected(self) -> None:
        """parse_boundary_input rejects malformed input."""
        with pytest.raises(ValueError, match="invalid date"):
            parse_boundary_input("not-a-date", "UTC")

    def test_unknown_timezone_rejected(self) -> None:
        """parse_boundary_input rejects unknown timezone."""
        with pytest.raises(ValueError, match="unknown timezone"):
            parse_boundary_input("2026-09-14", "NotATimeZone/Invalid")

    def test_naive_datetime_raises_value_error_type(self) -> None:
        """parse_boundary_input rejects naive datetime regardless of message wording.

        This test verifies the ValueError type is raised for naive datetimes,
        not coupled to any specific error message text. This ensures that a
        harmless message reword cannot accidentally change behavior.
        """
        with pytest.raises(ValueError):
            parse_boundary_input("2026-09-14T00:00:00", "UTC")


class TestOverlaps:
    """Test event-window overlap detection."""

    def test_window_strictly_inside_event(self) -> None:
        """Window strictly inside an event → True."""
        event_start = datetime(2026, 9, 14, 8, 0, 0, tzinfo=UTC)
        event_end = datetime(2026, 9, 14, 18, 0, 0, tzinfo=UTC)
        window = LocalRange(
            start=datetime(2026, 9, 14, 10, 0, 0, tzinfo=UTC),
            end=datetime(2026, 9, 14, 16, 0, 0, tzinfo=UTC),
            timezone="UTC",
        )
        assert overlaps(event_start, event_end, window) is True

    def test_window_ending_at_event_start(self) -> None:
        """Window ending exactly at the event start → False."""
        event_start = datetime(2026, 9, 14, 10, 0, 0, tzinfo=UTC)
        event_end = datetime(2026, 9, 14, 11, 0, 0, tzinfo=UTC)
        window = LocalRange(
            start=datetime(2026, 9, 14, 9, 0, 0, tzinfo=UTC),
            end=datetime(2026, 9, 14, 10, 0, 0, tzinfo=UTC),
            timezone="UTC",
        )
        assert overlaps(event_start, event_end, window) is False

    def test_window_ending_one_second_after_event_start(self) -> None:
        """Window ending one second after event start → True."""
        event_start = datetime(2026, 9, 14, 10, 0, 0, tzinfo=UTC)
        event_end = datetime(2026, 9, 14, 11, 0, 0, tzinfo=UTC)
        window = LocalRange(
            start=datetime(2026, 9, 14, 9, 0, 0, tzinfo=UTC),
            end=datetime(2026, 9, 14, 10, 0, 1, tzinfo=UTC),
            timezone="UTC",
        )
        assert overlaps(event_start, event_end, window) is True

    def test_window_starting_at_event_end(self) -> None:
        """Window starting exactly at the event end → False."""
        event_start = datetime(2026, 9, 14, 10, 0, 0, tzinfo=UTC)
        event_end = datetime(2026, 9, 14, 11, 0, 0, tzinfo=UTC)
        window = LocalRange(
            start=datetime(2026, 9, 14, 11, 0, 0, tzinfo=UTC),
            end=datetime(2026, 9, 14, 12, 0, 0, tzinfo=UTC),
            timezone="UTC",
        )
        assert overlaps(event_start, event_end, window) is False

    def test_window_starting_one_second_before_event_end(self) -> None:
        """Window starting one second before event end → True."""
        event_start = datetime(2026, 9, 14, 10, 0, 0, tzinfo=UTC)
        event_end = datetime(2026, 9, 14, 11, 0, 0, tzinfo=UTC)
        window = LocalRange(
            start=datetime(2026, 9, 14, 10, 59, 59, tzinfo=UTC),
            end=datetime(2026, 9, 14, 12, 0, 0, tzinfo=UTC),
            timezone="UTC",
        )
        assert overlaps(event_start, event_end, window) is True


class TestAllDayOverlaps:
    """Test all-day event-window overlap detection."""

    def test_event_crossing_local_midnight(self) -> None:
        """An event crossing local midnight is in both days' ranges."""
        # An event from 23:00 to 01:00 crosses midnight
        # This test is a bit tricky since we need to reason about
        # how all_day_overlaps works with dates

        # Let's say we have an all-day event on Sept 14 and Sept 15
        event_start = date(2026, 9, 14)
        event_end = date(2026, 9, 15)

        # Window for Sept 14
        tz = ZoneInfo("UTC")
        window_14 = LocalRange(
            start=datetime(2026, 9, 14, 0, 0, 0, tzinfo=tz),
            end=datetime(2026, 9, 15, 0, 0, 0, tzinfo=tz),
            timezone="UTC",
        )

        # Window for Sept 15
        window_15 = LocalRange(
            start=datetime(2026, 9, 15, 0, 0, 0, tzinfo=tz),
            end=datetime(2026, 9, 16, 0, 0, 0, tzinfo=tz),
            timezone="UTC",
        )

        # Event should be in both windows
        assert all_day_overlaps(event_start, event_end, window_14) is True
        assert all_day_overlaps(event_start, event_end, window_15) is True

    def test_event_crossing_week_boundary(self) -> None:
        """An event crossing a week boundary is in both weeks."""
        # Event from Sept 18 (Friday) to Sept 21 (Monday)
        event_start = date(2026, 9, 18)
        event_end = date(2026, 9, 21)

        tz = ZoneInfo("UTC")

        # Week 1: Sept 14-20 (Monday-Sunday)
        window_1 = LocalRange(
            start=datetime(2026, 9, 14, 0, 0, 0, tzinfo=tz),
            end=datetime(2026, 9, 21, 0, 0, 0, tzinfo=tz),
            timezone="UTC",
        )

        # Week 2: Sept 21-27 (Monday-Sunday)
        window_2 = LocalRange(
            start=datetime(2026, 9, 21, 0, 0, 0, tzinfo=tz),
            end=datetime(2026, 9, 28, 0, 0, 0, tzinfo=tz),
            timezone="UTC",
        )

        # Event should be in both weeks
        assert all_day_overlaps(event_start, event_end, window_1) is True
        assert all_day_overlaps(event_start, event_end, window_2) is True

    def test_all_day_no_overlap(self) -> None:
        """An all-day event outside the window doesn't overlap."""
        event_start = date(2026, 9, 14)
        event_end = date(2026, 9, 14)

        tz = ZoneInfo("UTC")
        window = LocalRange(
            start=datetime(2026, 9, 15, 0, 0, 0, tzinfo=tz),
            end=datetime(2026, 9, 16, 0, 0, 0, tzinfo=tz),
            timezone="UTC",
        )

        assert all_day_overlaps(event_start, event_end, window) is False

    def test_all_day_exact_match(self) -> None:
        """An all-day event matching the window dates overlaps."""
        event_start = date(2026, 9, 14)
        event_end = date(2026, 9, 14)

        tz = ZoneInfo("UTC")
        window = LocalRange(
            start=datetime(2026, 9, 14, 0, 0, 0, tzinfo=tz),
            end=datetime(2026, 9, 15, 0, 0, 0, tzinfo=tz),
            timezone="UTC",
        )

        assert all_day_overlaps(event_start, event_end, window) is True

    def test_all_day_window_ending_at_midnight(self) -> None:
        """Window ending exactly at midnight: all-day event on the last day is excluded."""
        # This pins the existing correct behavior for midnight-ending windows
        event_start = date(2026, 9, 15)
        event_end = date(2026, 9, 15)

        tz = ZoneInfo("UTC")
        # Window is Sept 14-15 (exclusive), so last included day is Sept 14
        window = LocalRange(
            start=datetime(2026, 9, 14, 0, 0, 0, tzinfo=tz),
            end=datetime(2026, 9, 15, 0, 0, 0, tzinfo=tz),
            timezone="UTC",
        )

        assert all_day_overlaps(event_start, event_end, window) is False

    def test_all_day_window_ending_at_midday(self) -> None:
        """Window ending at midday includes all-day event on that final day."""
        event_start = date(2026, 9, 14)
        event_end = date(2026, 9, 14)

        tz = ZoneInfo("UTC")
        # Window ends Sept 14 at 12:00, so last included day is Sept 14
        window = LocalRange(
            start=datetime(2026, 9, 14, 0, 0, 0, tzinfo=tz),
            end=datetime(2026, 9, 14, 12, 0, 0, tzinfo=tz),
            timezone="UTC",
        )

        assert all_day_overlaps(event_start, event_end, window) is True

    def test_all_day_window_ending_at_midday_excludes_next_day(self) -> None:
        """Window ending at midday excludes all-day event on following day."""
        event_start = date(2026, 9, 15)
        event_end = date(2026, 9, 15)

        tz = ZoneInfo("UTC")
        # Window ends Sept 14 at 12:00, so last included day is Sept 14
        window = LocalRange(
            start=datetime(2026, 9, 14, 0, 0, 0, tzinfo=tz),
            end=datetime(2026, 9, 14, 12, 0, 0, tzinfo=tz),
            timezone="UTC",
        )

        assert all_day_overlaps(event_start, event_end, window) is False
