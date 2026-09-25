# Handoff — 09B member resolver

## Outcome

Family-member names now resolve to FamilyWall account IDs locally, entirely
offline. `list_family_members` is a new, eighth, read-only MCP tool that shows
display names, first names and which member is you — never an account ID.
`get_week_overview` and `get_list_items` now report who each event or item is
assigned to (`assigned_to`, `assigned_to_everyone`, `unresolved_members`), also
by name only. No write tool changed in this slice; `resolve_members` is not
yet wired into any write path (that is slice C).

This implements brief [09b-member-resolver.md](09b-member-resolver.md) in
full: all 18 required tests (T1–T18) exist and pass.

## Changed files

- `src/familywall_mcp/services/members.py` (new): `MemberSelectionError`
  (`invalid_member_name` / `unknown_member` / `ambiguous_member`, static
  messages and recovery text that never echo the input or any member's
  name), `ResolvedAssignment`, `AssignmentView`, `resolve_members`,
  `describe_assignment`.
- `src/familywall_mcp/familywall/calendar.py`: `CalendarEvent` gains
  `attendee_ids: tuple[str, ...] = ()` (from `attendeeIds`),
  `to_all: bool | None = None` (from `toAll`, via `coerce_bool`) and
  `editable: bool | None = None` (from `editable`). `_parse_single_event`
  parses all three; a malformed value (wrong list-item type, or a `toAll`/
  `editable` value other than `"true"`/`"false"`) skips the event under the
  existing skip-and-count policy.
- `src/familywall_mcp/familywall/lists.py`: `ListItem` gains
  `assignee_ids: tuple[str, ...] = ()` (from `assigneeIds`) and
  `to_all: bool | None = None` (from `toAll`). `parse_list_items` parses both
  with the same absent-defaults/malformed-skips policy as events.
- `src/familywall_mcp/tools/registry.py`: new `list_family_members` tool
  (`FamilyMemberView`, `ListFamilyMembersResponse`); `ListItemResponse` gains
  `assigned_to`, `assigned_to_everyone`, `unresolved_members`; the
  `get_week_overview` per-event dict gains the same three fields. Both are
  built with the new `_event_view`/`_list_item_view` helpers, which call
  `describe_assignment`.
- Tests: new `tests/unit/test_member_selection.py` (T1–T14);
  `tests/unit/test_calendar_adapter.py` (T15, `TestAssignmentFields`);
  `tests/unit/test_lists_adapter.py` (T16, `TestAssignmentFields`);
  `tests/unit/test_tools.py` (T17, T18). `tests/support/calendar_fixtures.py`
  and `tests/support/list_fixtures.py` gained synthetic fixtures for the new
  fields, including malformed variants; `list_fixtures.py`'s existing
  `list_items_bare_array()` had its `"toAll"` values corrected from a Python
  `False` literal to the wire-accurate string `"false"` (the contract states
  booleans arrive as strings throughout; this field was present in the
  fixture but unused until now).
- Docs: `README.md` (tool table, count, and an assignment-by-name note),
  `AGENTS.md` (tool-count sentence), `docs/PROGRESS.md` (one row),
  `docs/briefs/09-implementation-plan.md` (slice B marked done, with a status
  note on the one deviation below).

## Acceptance evidence

Each required test's docstring/name carries its T-number; file::test below.

- T1 normalisation (case/whitespace/fullwidth Unicode):
  `test_member_selection.py::test_t1_normalisation_matches_across_case_whitespace_and_fullwidth_unicode`
- T2 full name beats first name:
  `test_member_selection.py::test_t2_exact_full_name_wins_over_first_name_match`
- T3 unique first name resolves:
  `test_member_selection.py::test_t3_unique_first_name_resolves`
- T4 duplicate first name is ambiguous:
  `test_member_selection.py::test_t4_duplicate_first_name_is_ambiguous`
- T5 duplicate display name is ambiguous:
  `test_member_selection.py::test_t5_duplicate_display_name_is_ambiguous`
- T6 unknown name:
  `test_member_selection.py::test_t6_unknown_name_is_unknown_member`
- T7 whitespace-only entry:
  `test_member_selection.py::test_t7_whitespace_only_entry_is_invalid_member_name`
- T8 `None`/`[]` mean everyone, in discovery order:
  `test_member_selection.py::test_t8_none_and_empty_list_mean_everyone_in_discovery_order`
- T9 de-dup to one ID; distinct-name order preserved:
  `test_member_selection.py::test_t9_duplicate_reference_dedupes_and_distinct_order_is_preserved`
- T10 naming everyone individually is still `to_all=False`:
  `test_member_selection.py::test_t10_naming_every_member_individually_gives_to_all_false`
- T11 cross-family isolation:
  `test_member_selection.py::test_t11_name_from_a_different_family_is_unknown_member`
- T12 no substring matching:
  `test_member_selection.py::test_t12_no_substring_matching`
- T13 static error text (no input, no member name), all three codes:
  `test_member_selection.py::test_t13_error_text_is_static_and_never_echoes_input_or_member_names`
- T14 `describe_assignment` names/unresolved/everyone-passthrough incl. `None`:
  `test_member_selection.py::test_t14_describe_assignment_reports_names_unresolved_and_everyone_passthrough`
- T15 calendar adapter (parse, defaults, three malformed cases):
  `test_calendar_adapter.py::TestAssignmentFields::test_t15_assignment_fields_parse`,
  `test_t15_absent_assignment_fields_give_defaults`,
  `test_t15_malformed_attendee_ids_skips_event`,
  `test_t15_malformed_to_all_skips_event`,
  `test_t15_malformed_editable_skips_event`
- T16 list adapter (parse, defaults, two malformed cases):
  `test_lists_adapter.py::TestAssignmentFields::test_t16_assignment_fields_parse`,
  `test_t16_absent_assignment_fields_give_defaults`,
  `test_t16_malformed_assignee_ids_skips_item`,
  `test_t16_malformed_to_all_skips_item`
- T17 `list_family_members` zero calls, `is_you`, no account ID in output:
  `test_tools.py::test_t17_list_family_members_zero_calls_is_you_and_no_account_id`
- T18 `get_week_overview`/`get_list_items` name-mapping, everyone flag,
  unresolved count, no account ID in output:
  `test_tools.py::test_t18_get_week_overview_maps_assignment_to_names_and_counts_unresolved`,
  `test_t18_get_list_items_maps_assignment_to_names_and_counts_unresolved`

Additional acceptance from the brief:
- No tool schema accepts or returns an account ID: `list_family_members` takes
  no arguments and its response is display names/`is_you` only (T17); the two
  read tools' new fields are name-only (T18). Confirmed by asserting the
  synthetic account IDs are absent from `model_dump_json()` in both T17 and
  T18.
- Existing fields on `get_week_overview`/`get_list_items` are unchanged: the
  full existing test suites for both pass unmodified, plus the new T18 tests
  build on the same response shape.

## Validation

Run from this worktree (`.claude/worktrees/slice-b`):

- `uv sync --frozen --group dev`: ok (51 packages).
- `uv run ruff check .`: `All checks passed!`
- `uv run ruff format --check .`: `108 files already formatted`.
- `uv run mypy src`: `Success: no issues found in 30 source files`.
- `uv run pytest -m 'not live'`: `449 passed` (423 before this slice + 26 new:
  14 in T1–T14, 5 in T15, 4 in T16, 1 in T17, 2 in T18).
- `uv build`: `Successfully built dist/familywall_mcp-0.1.0.tar.gz` and the
  wheel.
- `scripts/check`: fails only at its `detect-secrets` step, as the brief
  anticipated. Diffing the pre- and post-scan baselines shows the only
  change is a finding in `README.md` (the pre-existing placeholder
  `FAMILYWALL_PASSWORD` example already called out in PR #10/handoff 10,
  fixed separately in PR #8) and the loss of a stale `.env.local` entry that
  is untracked in this worktree; nothing under `src/familywall_mcp/services/members.py`
  or the other changed files appears. `.secrets.baseline` was left
  byte-for-byte unchanged on disk (`git status --short .secrets.baseline`
  is empty) — not edited, per instructions.

## Security and privacy review

- No credentials, tokens, cookies, real family data or `.env` contents appear
  in the diff. Every fixture, test and docstring uses synthetic names
  (`Alice Smith`, `Robin Smith`, `Sam`/`Sam Lee`, `Jordan Kim`, …) and
  synthetic IDs (`acc-alice`, `acct/1`, `acct/unknown`, `task/301`, …).
- `MemberSelectionError`'s three messages and recovery text are static
  string constants (`INVALID_MEMBER_NAME`, `UNKNOWN_MEMBER`,
  `AMBIGUOUS_MEMBER` in `services/members.py`); T13 asserts the input and
  every member's name/surname are absent from the rendered text.
- `list_family_members`, `get_week_overview` and `get_list_items` are
  asserted (T17, T18) to omit the synthetic account IDs from
  `model_dump_json()`. `describe_assignment` only ever emits `names` (a
  `tuple[str, ...]`), `everyone` and an `unresolved` count — there is no
  field an ID could hide in.
- No network calls were made; all tests run against synthetic in-repo
  fixtures and fakes.

## Known limitations

- `resolve_members` is not yet called from any tool. No write tool accepts
  `assigned_to` in this slice; that is slice C (calendar) and slice F
  (lists).
- Hosted discovery is never refreshed on an unknown name in this slice, by
  design — `resolve_members` only ever sees the `DiscoveredFamily` it is
  given. A one-time hosted refresh on an unknown name is explicitly slice C's
  job, once names are first used for a write (brief 09b, "Handoff target").
- Nothing here has been live-verified against a real FamilyWall account; it
  is offline-only, as the brief specifies. `list_family_members` and the new
  read-model fields should get a live check alongside slice C/F (brief 09's
  slice G).
- `list_fixtures.py`'s `list_items_bare_array()` fixture's `"toAll"` values
  were corrected from a Python `False` literal to the wire-accurate string
  `"false"` so that adding real `toAll` parsing didn't start skipping those
  four items. This is a pre-existing-fixture correction, not new behaviour.

## Next bounded task

Slice C: add `assigned_to` to `create_calendar_event` (attendees), switching
its default from the signed-in member to everyone once probe A1's everyone
encoding is used, per `docs/briefs/09-implementation-plan.md`.

## Lead review (2026-09-25)

- Replaced a sample member name in the brief, tests and this handoff that
  matched a real family member's first name; fixtures now use only neutral
  synthetic names. A family-name audit of this worktree (all real member names
  and IDs, fetched read-only and never printed) finds no matches.
- Extended T13 to assert the `ambiguous_member` text as well.
- Read-only live regression check of the new parsers against the real account:
  69 events and 904 list items across 9 lists parsed with **0 skipped**;
  `toAll` arrived as a string on every event and item.
