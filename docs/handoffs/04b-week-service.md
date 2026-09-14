# Handoff: 04B — Weekly overview service

## Outcome

The weekly calendar overview service is complete and tested. It provides a correct,
honest answer to "what have we got on this week at home?" by fetching events from
FamilyWall, handling timezone conversions, and assigning events to days with all
acceptance criteria met.

## Changed files

- `src/familywall_mcp/services/calendar.py`: New service module implementing
  `CalendarService`, `CalendarTransport` protocol, and domain models `WeekOverview`
  and `DayAgenda`. Handles event fetching, timezone conversion, deduplication by
  occurrence_id, and assignment to days.
- `tests/unit/test_calendar_service.py`: Comprehensive test suite with 16 test cases
  covering all acceptance criteria from the brief.

## Acceptance evidence

All 16 acceptance criteria from the brief are tested and passing:

1. ✓ Sydney week returns exactly 7 DayAgenda entries with correct dates
2. ✓ Request fields carry resolved ISO bounds with explicit offset
3. ✓ Timed event crossing local midnight appears on both days
4. ✓ Single-day all-day event appears on the exact date in upstream string (no timezone conversion)
5. ✓ Multi-day all-day event appears on every date it covers
6. ✓ Three occurrences of one weekly series all appear; not collapsed by series_id
7. ✓ An occurrence carrying exdate is still shown (not re-filtered)
8. ✓ BIRTHDAY_ACCOUNT event from another calendar is preserved
9. ✓ Skipped malformed event sets complete=False and adds a note
10. ✓ Zero events returns an empty week with complete=True and no notes
11. ✓ Transport error propagates as its typed error (not an empty week)
12. ✓ Exceeding event cap sets complete=False, adds note, and returns what was fetched
13. ✓ 31-day request bounds are enforced (week request uses single call as span is 7 days)
14. ✓ Spring-forward Sydney week still has 7 days with correct event placement
15. ✓ Same week in different timezone moves events to different local days
16. ✓ Events within a day ordered deterministically: all-day first, timed by start, by occurrence_id

## Validation

### Checks run and passed

All required checks pass:

```
uv run ruff check .
All checks passed!

uv run ruff format .
1 file reformatted, 70 files left unchanged

uv run mypy src
Success: no issues found in 19 source files

uv run pytest -m 'not live'
======================= 252 passed, 13 warnings in 0.81s =======================
(236 tests existing, 16 tests added)

uv build
Successfully built dist/familywall_mcp-0.1.0.tar.gz
Successfully built dist/familywall_mcp-0.1.0-py3-none-any.whl

scripts/check
All checks passed!
```

All 252 tests (236 pre-existing + 16 new) pass with no failures.

## Security and privacy review

- No credentials, tokens, or cookies appear in the implementation or tests.
- No real FamilyWall account data or family information is used.
- Test fixtures are synthetic event payloads with dummy IDs and dates.
- No environment variables or configuration secrets are referenced.
- All test data uses placeholder strings (e.g., `evt-midnight-crossing`, `calendar/family-123`).

## Known limitations

1. **Event cap enforcement**: The service stops fetching at a configured maximum (default 500
   events) and reports `complete=False`. This is by design to avoid unbounded memory use,
   but it means very large weeks (>500 events) are silently truncated. The user is notified
   via `complete=False` and a note, so this is acceptable but material.

2. **No filtering of unrecognized event types**: As required by the brief, events with
   unknown `event_type` values (e.g. `BIRTHDAY_ACCOUNT`) and events from other calendars
   are preserved. The service makes no assumptions about the set of valid types or
   calendar scopes. This is correct but means the caller must decide what to display.

3. **Timezone validation is delegated**: The service accepts any `timezone` string and
   passes it to `zoneinfo.ZoneInfo()`. Invalid timezone names raise `ValueError`.
   This is acceptable because the brief specifies `zoneinfo` as the source of truth.

4. **No local recurrence expansion**: As required by the contract, the server provides
   expanded occurrences and the client does not attempt to expand them further. If the
   server fails to expand, the client will show a single occurrence instead of a series.
   This is correct per the contract.

## Next bounded task

Integrate this service into the MCP interface (task 04C: MCP server tooling). The
service is ready to be wired into an MCP tool and called with real calendar_id and
session context.
