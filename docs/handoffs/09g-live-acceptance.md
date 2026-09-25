# Handoff — 09G live acceptance

## Outcome

Every tool added or changed by brief 09 was exercised once against the real
FamilyWall account on disposable data, and each returned `confirmed`. All
disposable events and list items were deleted by exact ID, and read-only
checks found none left behind. This closes brief 09.

## Changed files

- `docs/compatibility.md`: rows for every tool below, and the `evtupdate`
  where/description question is closed.
- `docs/contracts/calendar.md`, `docs/contracts/familywall.md`,
  `docs/decisions/0003-single-call-add.md`: the evidence below.
- `docs/PROGRESS.md`, `AGENTS.md`, the 09E/09F handoffs and the plan: status.

## Acceptance evidence (2026-09-25, authorised by the account owner)

Run through `ToolRegistry` on the combined branch (slices B, D, C, E, F), with
writes enabled and a fresh idempotency key per call.

| Tool and case | Outcome |
| --- | --- |
| `list_family_members` | 5 members, exactly one marked as you, no upstream call |
| `create_calendar_event`, `assigned_to` blank, with a location and a note | `confirmed`, everyone |
| `set_calendar_event_attendees` → two named members | `confirmed`, nothing else changed |
| `set_calendar_event_attendees` → blank (everyone) | `confirmed`, nothing else changed |
| `add_list_item`, blank, default To Do list | `confirmed`, everyone, via `taskcreate2` |
| `add_list_item`, two named members, a non-default list | `confirmed`, one call, no `taskmove` |
| `set_list_item_assignees` → blank (everyone) | `confirmed`, other fields unchanged |
| `set_list_item_assignees` → one member | `confirmed`, other fields unchanged |

What it settled:

- The everyone encoding on `taskcreate2`, extrapolated in slice F, is correct.
- A non-empty `where` and `description` survive an attendee-only `evtupdate`.
- Slice C's live check covered `create_calendar_event` with named members.

Output recorded only outcomes and counts. No names, account IDs, event IDs or
item IDs were printed or kept.

## Validation

- `uv run pytest -m 'not live'`: 554 passed on the combined branch; ruff,
  format, mypy and build clean.
- A family-name audit of each slice's worktree found no real member names or IDs.

## Security and privacy review

The one-off live scripts lived outside the repository and were deleted after
use. Test data used the prefix `fw-mcp slice G` and was removed by exact ID.

## Known limitations

- `evtupdate` on all-day events, and any write to a recurring event, remain
  unobserved and refused.
- The hosted OAuth deployment has not been live-verified with these tools.

## Next bounded task

Merge the stack in order, then deploy and let the owner confirm from a real MCP
client that the default "blank means everyone" behaves as they expect.
