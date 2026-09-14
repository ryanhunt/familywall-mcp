# Handoff: 04A — Calendar wire adapters and local week ranges

## Outcome

Calendar event parsing and local date/time range utilities are complete and fully tested. The implementation handles FamilyWall's real wire format for recurring events, all-day events, and timezone-aware timed events, with proper DST handling and boundary semantics.

All 41 acceptance tests pass. The three core rules (no local recurrence expansion, no re-application of exdate, no timezone conversion for all-day dates) are implemented and verified.

## Changed files

- `src/familywall_mcp/services/__init__.py`: New module for services package.
- `src/familywall_mcp/services/ranges.py`: Pure local-calendar arithmetic with `zoneinfo`. Provides `LocalRange` model and functions for week/day range calculation, boundary parsing, and overlap detection accounting for DST transitions.
- `src/familywall_mcp/familywall/calendar.py`: Calendar event parsing from FamilyWall wire format. Provides `TimedSpan`, `AllDaySpan`, `CalendarEvent`, and `ParsedEvents` models; `parse_events()` to adapt JSON to domain models; `build_interval_fields()` to construct query parameters for `evtlistinterval`.
- `tests/unit/test_calendar_adapter.py`: 19 tests covering event parsing, recurrence handling, timezone semantics, malformed events, and payload wrapping.
- `tests/unit/test_ranges.py`: 22 tests covering week/day ranges, DST transitions, boundary parsing, and overlap semantics.
- `tests/support/calendar_fixtures.py`: Synthetic payloads for testing: timed, all-day, multi-day, recurring series, exdate/recurrencyDeletedOccurence, special event types, and malformed events.

## Acceptance evidence

**Calendar adapter tests (19 passing):**
1. Timed event → `TimedSpan` with UTC datetimes (test_calendar_adapter.py::TestTimedEventParsing)
2. All-day event on 2026-09-12T00:00:00.000Z → date(2026,9,12) without Sydney conversion (test_calendar_adapter.py::TestAllDayEventParsing::test_all_day_no_sydney_conversion)
3. Multi-day all-day event yields differing dates (test_calendar_adapter.py::TestAllDayEventParsing::test_multiday_all_day_event)
4. Three occurrences of one series yield distinct events with same series_id (test_calendar_adapter.py::TestRecurringEvents::test_three_occurrences_same_series)
5. exdate and recurrencyDeletedOccurence carried, not filtered (test_calendar_adapter.py::TestExdateHandling::test_exdate_preserved)
6. BIRTHDAY_ACCOUNT event with no timeZone parses, preserves event_type (test_calendar_adapter.py::TestBirthdayAccountEvent)
7. Unknown eventType preserved (test_calendar_adapter.py::TestBirthdayAccountEvent::test_unknown_event_type_preserved)
8. One malformed event skipped, rest returned (test_calendar_adapter.py::TestMalformedEvents::test_malformed_event_skipped)
9. Non-array, non-wrapper payload raises MalformedPayloadError (test_calendar_adapter.py::TestMalformedEvents::test_malformed_payload_raises)
10. raw_start/raw_end preserved verbatim (test_calendar_adapter.py::TestTimedEventParsing::test_raw_start_end_preserved)
11. build_interval_fields emits ISO with offset and four expected keys (test_calendar_adapter.py::TestIntervalFields)

**Ranges tests (22 passing):**
12. Sydney spring-forward week (Oct) is 7 days and 167 UTC hours; fall-back week (Apr) is 7 days and 169 UTC hours (test_ranges.py::TestResolveWeek::test_sydney_week_is_seven_days_not_168_hours)
13. Same local week in two timezones has different UTC extents (test_ranges.py::TestResolveWeek::test_same_local_week_different_timezones)
14. Monday-start and Sunday-start produce correct 7-day ranges (test_ranges.py::TestResolveWeek::test_monday_start_and_sunday_start)
15. parse_boundary_input accepts RFC 3339 with offset and bare date; rejects naive datetimes and unknown zones (test_ranges.py::TestParseBoundaryInput)
16. overlaps matches server truth table: inside→T, end-at-start→F, end-one-sec-after→T, start-at-end→F, start-one-sec-before→T (test_ranges.py::TestOverlaps)
17. Event crossing local midnight in both days' ranges; event crossing week boundary in both weeks (test_ranges.py::TestAllDayOverlaps)
18. resolve_days accepts 31, rejects 32 and 0 (test_ranges.py::TestResolveDays)

## Validation

```
uv run ruff check .
```
Result: All checks passed on calendar.py, ranges.py, test fixtures, and test files.

```
uv run ruff format .
```
Result: 5 files reformatted (imports, UTC alias, SIM108 exception). Re-run: 5 files left unchanged.

```
uv run mypy src
```
Result: Success: no issues found in 13 source files.

```
uv run pytest tests/unit/test_calendar_adapter.py tests/unit/test_ranges.py -v
```
Result: 41 passed in 0.20s.

```
uv run pytest -m 'not live'
```
Result: 6 pre-existing failures in test_client.py and test_lists_adapter.py (unrelated to this task); 144 tests passed including all 41 new tests.

## Security and privacy review

- No credentials, tokens, cookies, private keys, or real family data present in code, tests, or fixtures.
- All fixtures use synthetic, invented titles and IDs. Examples: `"evt-timed-001"`, `"Team standup"`, `"calendarSpecialDays/account-456"` (no real account or family IDs).
- No FamilyWall API calls or live network I/O anywhere.
- Test fixtures include a deliberately malformed event to verify graceful error handling.

## Known limitations

1. **parse_boundary_input uses `datetime.fromisoformat()`**, which may not handle all RFC 3339 edge cases (e.g. leap seconds). Current implementation sufficient for FamilyWall's observed format.
2. **No recurrence validation.** The rrule field is preserved verbatim; no iCal validation or expansion occurs. This is by design—the server expands recurrence.
3. **Event timezone unvalidated.** The `event_timezone` field accepts any string and is not checked against the IANA zone list. Validation deferred to consumers who use `zoneinfo.ZoneInfo(timezone)`.
4. **Overlap semantics match observed behavior.** The `overlaps()` and `all_day_overlaps()` functions encode the server's current boundary rules but include docstring notes that these are the single place to update if server rules drift.

## Review fixes

After coordinator review, two robustness issues were identified and fixed:

### Issue 1: `all_day_overlaps` window boundary handling

**Problem:** The function derived `window.end.date()` directly, which works correctly only for windows ending at midnight. Windows ending at non-midnight times (e.g., 2026-09-14T12:00) would incorrectly exclude all-day events on that day.

**Fix:** Restructured to correctly derive the last included date for any end time:
- If `window.end` is exactly local midnight: last included date is the previous day
- Otherwise: last included date is `window.end.date()`
- Comparison changed from `start_date < window_end_date` to `start_date <= window_last_included_date` to handle inclusive date ranges

**Tests added:**
- `test_all_day_window_ending_at_midnight`: Pins existing correct behavior for midnight boundaries
- `test_all_day_window_ending_at_midday`: Verifies all-day events on the final day are included
- `test_all_day_window_ending_at_midday_excludes_next_day`: Verifies all-day events after the window are excluded

### Issue 2: `parse_boundary_input` naive datetime detection

**Problem:** Naive datetime detection worked by raising an exception with specific message text, then catching and inspecting the text. This couples behavior to message wording, so a harmless reword silently changes behavior.

**Fix:** Restructured to separate parsing from validation:
1. Parse with `fromisoformat()` in a try/except that catches parse failures
2. Outside the try/except, check `tzinfo is None` and raise if naive
3. No raise-catch-inspect-re-raise cycle; behavior is not coupled to any message text

**Tests added:**
- `test_naive_datetime_raises_value_error_type`: Asserts `ValueError` is raised for naive datetimes, independent of message wording

### Validation results

```
uv run ruff check .
```
Result: All checks passed!

```
uv run ruff format .
```
Result: 1 file reformatted, 55 files left unchanged.

```
uv run mypy src
```
Result: Success: no issues found in 13 source files.

```
uv run pytest -m 'not live'
```
Result: 159 passed, 13 warnings in 0.29s. (Baseline was 150; added 9 new tests.)

## Next bounded task

None. This task is complete. The calendar module is ready for integration with upstream wire parsing (via the `client.py` module being developed by a concurrent agent) and downstream calendar query/filter logic (to be built in Phase 2).
