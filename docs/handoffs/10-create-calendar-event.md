# Handoff — 10 `create_calendar_event`

## Outcome

`create_calendar_event` is a seventh MCP tool. It creates one timed,
non-recurring event on the family calendar whose only attendee is the
authenticated member, behind the existing write gate and durable receipts, and
reports `confirmed` only when an exact-ID readback matches the request. It was
live-verified on 2026-09-25.

This implements the create half of brief 09 step 6 for the one attendee
encoding already observed live. It deliberately skips the member resolver
(step 2) and receipt generalization (step 5); see limitations.

## Changed files

- `src/familywall_mcp/services/ranges.py`: `resolve_event_time` reads a local
  wall-clock time in the event zone (or converts an offset-aware time into it)
  and refuses bare dates and local times skipped or repeated by a DST change.
- `src/familywall_mcp/familywall/calendar.py`: `build_create_event_fields` (the
  complete `evtcreate` form; event zone, not `Europe/London`; no `color`) and
  `parse_created_event_id`.
- `src/familywall_mcp/services/calendar.py`: `NewTimedEvent`,
  `EventWriteOutcome` (confirmed / mismatched / acknowledged / unknown),
  `CreateEventResult`, and `CalendarService.create_event` with receipts,
  one write, and readback comparison.
- `src/familywall_mcp/tools/registry.py`: the `create_calendar_event` tool.
- `tests/unit/test_calendar_create.py` (new), `test_calendar_adapter.py`,
  `test_ranges.py`, `test_tools.py`: coverage below.
- `README.md`, `AGENTS.md`, `docs/PROGRESS.md`, `docs/architecture.md`,
  `docs/compatibility.md`, `docs/contracts/calendar.md`: tool count, behaviour
  and live evidence.

## Acceptance evidence

- Names never reach the wire; only the discovered authenticated member's
  account ID is sent as `attendee.0.accountId`:
  `test_create_sends_the_verified_single_attendee_form`,
  `test_create_calendar_event_confirms_in_the_members_timezone`.
- Write gate and invalid input make zero upstream calls:
  `test_create_calendar_event_is_blocked_by_the_write_gate`,
  `test_create_calendar_event_rejects_invalid_input_before_any_call`.
- Timezone and DST: `TestResolveEventTime` (+10/+11 offsets, gap and overlap
  refused), `test_never_hard_codes_london`,
  `test_start_in_another_zone_is_sent_in_the_event_zone`.
- `evtcreate` is sent at most once, never retried; a lost write is `unknown`, a
  refusal propagates: `TestAcknowledgedAndUnknown`.
- Confirmation needs an exact-ID readback; any differing field is named and the
  actual times reported: `TestConfirmation`.
- Replay, conflict, crash recovery and subject isolation: `TestReceipts`.
- Live: `docs/contracts/calendar.md#mutations`, `docs/compatibility.md`.

## Validation

- `uv sync --frozen --group dev`: ok.
- `scripts/check`: ruff check, ruff format --check, mypy (29 files) and
  `pytest -m 'not live'` (423 passed) and `uv build` all pass. The final
  detect-secrets step **fails on pre-existing findings unrelated to this
  change**: a placeholder `FAMILYWALL_PASSWORD` in a README client-config
  example (commit 6361c72) and the git-excluded `.claude/worktrees/` directory,
  which `--all-files` scans. No file changed here produces a finding.
- Live check, 2026-09-25, authorised by the account owner: a one-off script (not
  committed) drove the real tool against the account in `.env.local`, with the
  documented `https://api.familywall.com` base URL overriding that file's
  placeholder. Calls: `evtcreate`, `evtlistinterval` (outcome `confirmed`),
  then cleanup `evtdelete` of the exact ID and a readback showing it absent.

## Security and privacy review

- No credentials, cookies, tokens, account IDs, event IDs, member names or real
  event content in the diff, fixtures or docs. The live script printed only key
  names, value shapes and the synthetic event's own fields.
- Fixtures use synthetic IDs (`acct-self`, `event/new-1`, `calendar/family-123`).
- The tool accepts no account ID, calendar ID or principal from the caller.

## Known limitations

- Attendee is always the signed-in member. All-members (`isToAll=true`) and
  multiple attendees are unverified; brief 09's resolver is still needed.
- No all-day or recurring creation, no edit, no delete tool.
- Receipts reuse the list-shaped `OperationReceipt`: `list_id` holds the family
  calendar ID, and the payload hash is tagged with the endpoint and calendar so
  a key reused across tools conflicts. Brief 09 step 5 (generalized receipts
  with a migration) is not done.
- A refused create replays as `unknown`, not as the refusal; a new key is
  needed to try again.
- Readback uses the write transport, so an expired session during readback
  yields `acknowledged` rather than a re-login.
- Only a `+10:00` instant was observed live; the DST period uses the same code
  path but is `pending-live`.
- Timed events longer than 14 days are refused.
- Brief 09 still says calendar writes are not implemented; it is a plan and was
  left as written.

## Next bounded task

Brief 09 step 1 for events: a controlled probe of `isToAll=true` and a
two-attendee `attendee.N.accountId` create, followed by step 2's name resolver so
`create_calendar_event` can take `assigned_to`.
