# Handoff — 09F list assignment: single-call `add_list_item` and `set_list_item_assignees`

## Outcome

`add_list_item` now creates the item directly in the chosen list, already
assigned, in **one `taskcreate2` call** — no `taskmove` is ever sent. This
supersedes the create-then-move behaviour of ADR 0002; see
[ADR 0003](../decisions/0003-single-call-add.md). `assigned_to: list[str] |
None` is resolved against the cached family discovery (refreshing once on an
`unknown_member`) before list selection, any receipt or any write, exactly as
`create_calendar_event` already does for calendar attendees. Blank means
everyone (`toAll=true` plus every member's account ID — extrapolated from the
`taskupdate2` encoding probe A2 verified, not directly observed on
`taskcreate2`); the outcome is `confirmed` only when a readback of the
requested list finds the item with exactly the requested assignment.

A new tool, `set_list_item_assignees(item_id, assigned_to=None,
idempotency_key=None)`, changes **only** who an existing item is assigned to.
It verifies the item belongs to an accessible list **before** writing — a
security requirement, since `taskupdate2` carries no list ID and the server
cannot enforce membership — then sends one partial `taskupdate2`
(`partnerScope`, `taskId`, `toAll`, `assignee.N`), and confirms by reading the
item back: any of `text`, `description`, `completed`, `list_id`, `due_date`
or `reminder` that changed is reported as `mismatched`, naming each
differing field.

A new `WriteOutcome.MISMATCHED` (`"mismatched"`) joins the existing
`confirmed`/`acknowledged`/`misfiled`/`unknown` four. `misfiled` is **not**
retired: there is no longer a move to fail, but the create response can
still name a list other than the one requested (an upstream inconsistency),
so it stays as a **detected-only** outcome — no move, no compensation. Both
tools resolve names, verify (for `set_list_item_assignees`) and select lists
(for `add_list_item`) before ever writing, send exactly one write with no
retry, and never expose an account ID in their responses.

`ListItem` gains `due_date: str | None` and `reminder: tuple[str, str, str]
| None`, both parsed leniently from `dueDate`/`reminder` — absent or
malformed gives `None` and never skips the item — so `set_list_item_assignees`
can compare them on readback.

This is brief 09 slice F, done offline (synthetic fixtures only, no live
calls). It builds on slice B (resolver, read models), slice D (generalized
receipts) and slice C (the refresh-once-on-`unknown_member` pattern, now
factored into a small shared helper, `_resolve_assignment_with_refresh`, used
by both list tools). Next: the lead's live check (slice G), including
everyone on `taskcreate2` (extrapolated, not directly observed), then merge.

## Changed files

- `src/familywall_mcp/familywall/lists.py`:
  - `ListItem` gains `due_date: str | None = None` and
    `reminder: tuple[str, str, str] | None = None` (verbatim `dueDate`
    string; verbatim `(reminderType, reminderUnit, reminderValue)` strings).
  - New `_parse_due_date`/`_parse_reminder`: both lenient, never raise; a
    malformed or absent value gives `None` without skipping the item.
    `parse_list_items` calls both for every entry.
  - New `build_create2_item_fields(*, list_id, text, to_all,
    assignee_account_ids)`: the complete `taskcreate2` form (`partnerScope`,
    `taskListId`, `text`, `taskCategoryId=""`, `dueDate="$empty"`,
    `picture="$empty"`, `toAll`, `assignee.N`), validating the `taskList/`
    prefix and rejecting an empty ID list.
  - New `build_update2_assignees_fields(*, item_id, to_all,
    assignee_account_ids)`: the partial `taskupdate2` form (`partnerScope`,
    `taskId`, `toAll`, `assignee.N`), validating the `task/` prefix and
    rejecting an empty ID list.
  - `build_create_item_fields`/`build_move_item_fields` are unchanged and
    left in place (decision 2): no longer called by `services/lists.py`, but
    still present and still exercised by their own adapter tests.
- `src/familywall_mcp/services/lists.py`:
  - `WriteOutcome` gains `MISMATCHED = "mismatched"`.
  - `AddItemResult` gains `mismatched_fields: tuple[str, ...] = ()`. New
    `SetItemAssigneesResult(outcome, mismatched_fields=())`.
  - `add_item` rewritten: takes a `ResolvedAssignment` (imported from
    `services.members`) instead of building `taskcreate` fields itself;
    sends exactly one `taskcreate2`; a definite refusal
    (`UpstreamRejectedError`/`AuthenticationError`) is recorded as
    `status="rejected"` and re-raised (the calendar pattern from slice D);
    an indeterminate error gives `unknown`; a response missing an ID gives
    `unknown`, an ID with no list gives `acknowledged`; a response naming a
    different list gives `misfiled` with both list IDs and **no readback**;
    otherwise a private `_confirm_added` reads the requested list back and
    compares the created item's assignment (`_assignment_matches`),
    returning `confirmed` or `mismatched` (`mismatched_fields=("assignees",)`).
  - New `set_item_assignees(principal, item_id, assignment, operation_id,
    receipt_repository, family_id)`: replays an existing receipt first (zero
    calls); otherwise verifies the item belongs to an accessible list
    **before** any write or receipt (raising `UnsupportedConfigurationError`
    with zero writes and zero receipts for a foreign item, keeping the found
    item as `before`); sends one partial `taskupdate2`; a private
    `_confirm_set_assignees` reads the item's list back and compares against
    `before` and `assignment` via `_set_assignees_mismatches`, naming every
    differing field (`assignees`, `text`, `description`, `completed`,
    `list_id`, `due_date`, `reminder`).
  - New module-level helpers: `_assignment_matches` (decision 5's match
    rule, shared by both new methods), `_set_assignees_mismatches`,
    `_replay_add_item`/`_replay_set_assignees` (mirroring calendar's
    `_replay_create`: action/hash conflict, `rejected` re-raises via
    `_rebuild_rejection`, `succeeded` parses the stored JSON, `pending`/
    `unknown` both resolve to `unknown`), `_rebuild_rejection` (shared),
    `_conflict_error` (the shared `operation_id_conflict` error).
  - Receipts: `add_item` keeps `action="list.add_item"`,
    `resource_id=<list id>`; `set_item_assignees` uses the new
    `action="list.set_assignees"`, `resource_id=item_id`. Both use a 24-hour
    TTL (module constant `RECEIPT_TTL`) and the existing
    `_compute_payload_hash` over the complete wire fields, so a pre-upgrade
    `add_list_item` receipt (the old `taskcreate` hash) now conflicts rather
    than replaying within its 24-hour life — safe, since no duplicate is
    created (F7).
  - `set_item_checked` is unchanged (out of scope for this slice).
- `src/familywall_mcp/models.py`: `OperationReceipt.action` Literal gains
  `"list.set_assignees"`.
- `src/familywall_mcp/tools/registry.py`:
  - New private `_resolve_assignment_with_refresh(assigned_to, principal,
    ctx)`: the refresh-once-on-`unknown_member` resolution, factored out of
    `_create_calendar_event`'s inline logic (decision 9) so both
    `_add_list_item` and `_set_list_item_assignees` share it. (A parallel
    slice may add an equivalent helper for calendar tools; `_create_calendar_event`
    itself was left using its existing inline logic, to stay inside this
    slice's file boundary and avoid touching calendar behaviour.)
  - `_add_list_item` gains `assigned_to: list[str] | None = None`; resolves
    it first, then selects the list (unchanged selection logic), then calls
    the new `add_item` signature. `AddListItemResponse` gains
    `assigned_to: tuple[str, ...] = ()`, `assigned_to_everyone: bool =
    False`, `mismatched_fields: tuple[str, ...] = ()`.
  - New `_set_list_item_assignees(item_id, assigned_to=None,
    idempotency_key=None)` and `SetListItemAssigneesResponse(family_name,
    outcome, item_id, assigned_to, assigned_to_everyone,
    mismatched_fields=())`.
  - New tool registration: `set_list_item_assignees`. `add_list_item`'s tool
    description no longer mentions create-then-move; both descriptions
    explain `assigned_to` (names as `list_family_members` shows them, blank
    for everyone).
- `tests/support/list_fixtures.py`: new
  `list_item_with_due_date_and_reminder`,
  `list_item_with_malformed_due_date`, `list_item_with_malformed_reminder`.
- `tests/unit/test_lists_adapter.py`: new `TestCreate2AndUpdate2Builders`
  (F1) and `TestDueDateAndReminderFields` (F12).
- `tests/unit/test_list_service.py`: `FakeTransport.call`'s key-derivation
  extended to recognise `text`/`taskId`/`taskListId` alongside the old
  `a00*` field names. New module-level `EVERYONE`/`NAMED`
  `ResolvedAssignment` fixtures. The old create-then-move `add_item` tests
  (which called a signature that no longer exists) are replaced by F2–F7 and
  F14 tests; the legacy-receipt/another-action/second-item/unicode tests are
  kept, updated to the new signature and endpoint. New `TestSetItemAssignees`
  class (F8–F11). `set_item_checked` tests are untouched.
- `tests/unit/test_tools.py`: `FakeSessionPool` gains `taskcreate2`/
  `taskupdate2` handling (and `tasklist` now echoes `assigneeIds`/`toAll`
  from `created_items`); new `_assignee_ids_from_fields` helper. The one
  existing `add_list_item` test that asserted a `taskmove` is updated to
  assert its absence instead (F2). New F6, F13 tests, plus write-gate and
  foreign-item refusal tests for `set_list_item_assignees`.
- `docs/decisions/0003-single-call-add.md` (new): the ADR.
- `docs/decisions/0002-non-atomic-add.md`: "Superseded by ADR 0003" note at
  the top.
- `README.md`: tool table (nine tools, `set_list_item_assignees` added,
  `add_list_item`'s description updated) and new behaviour bullets for both
  tools.
- `AGENTS.md`: tool-count sentence updated (six of nine live-verified;
  `add_list_item`'s new behaviour, `set_list_item_assignees` and
  `list_family_members` not yet live-verified).
- `docs/architecture.md`: `add_list_item`'s row updated, new
  `set_list_item_assignees` row.
- `docs/PROGRESS.md`: one new row for slice F.
- `docs/briefs/09-implementation-plan.md`: slice F marked done in the
  "where brief 09 stands" table, the slice-order table, and its own section;
  two pre-existing claims that `misfiled` "goes away" are corrected in place
  (same pattern slice D and slice C used for their own plan corrections),
  since the more detailed `09f-list-assignment.md` brief is explicit that
  `misfiled` stays as a detected-only outcome.
- `docs/handoffs/09f-list-assignment.md`: this file.

## Deviation from the brief

None from `09f-list-assignment.md` itself — all ten binding decisions and
all fourteen required tests (F1–F14) are implemented as specified.

As documented above, `docs/briefs/09-implementation-plan.md` (the older,
less detailed plan) said in two places that switching to `taskcreate2` would
make the `misfiled` outcome "go away". The binding brief for this slice
(`09f-list-assignment.md`, decision 1) is explicit that `misfiled` stays as
a **detected** outcome, and that is what is implemented. Both stale
statements in the plan doc are corrected in place, in this same change,
rather than left to silently contradict the brief and the implementation.

## Acceptance evidence (F1–F14)

- **F1** Both builders, complete forms (everyone/named), empty-list
  rejection, no names on the wire:
  `tests/unit/test_lists_adapter.py::TestCreate2AndUpdate2Builders` (nine
  tests: `test_f1_create2_complete_form_for_everyone`,
  `test_f1_create2_complete_form_for_named_members`,
  `test_f1_create2_rejects_empty_assignees`,
  `test_f1_create2_rejects_wrong_list_prefix`,
  `test_f1_create2_never_emits_a_name`,
  `test_f1_update2_complete_form_for_everyone`,
  `test_f1_update2_complete_form_for_named_members`,
  `test_f1_update2_rejects_empty_assignees`,
  `test_f1_update2_rejects_wrong_item_prefix`,
  `test_f1_update2_never_emits_a_name`).
- **F2** Add, confirmed — exactly one write (`taskcreate2`, no `taskmove`),
  then one readback:
  `tests/unit/test_list_service.py::TestListServiceMutation::test_f2_add_item_confirmed_one_write_no_move_then_readback`
  and `test_f2_add_item_named_members_confirmed`; also
  `tests/unit/test_tools.py::test_add_list_item_with_idempotency_uses_receipt_repo`
  (tool layer, asserts `"taskmove" not in endpoint_calls`).
- **F3** Add, misfiled — a response `taskListId` for a different list gives
  `misfiled` with both list IDs, no move and no retry:
  `TestListServiceMutation::test_f3_add_item_misfiled_response_names_a_different_list`.
- **F4** Add, mismatched/acknowledged on readback:
  `TestListServiceMutation::test_f4_add_item_readback_assignment_mismatch`
  and `test_f4_add_item_readback_absent_gives_acknowledged`.
- **F5** Add, lost/refused writes:
  `test_f5_add_item_lost_write_gives_unknown_no_retry`,
  `test_f5_add_item_invalid_envelope_becomes_unknown`,
  `test_f5_add_item_refused_write_gives_rejected_and_replay_raises`,
  `test_f5_add_item_authentication_error_also_gives_rejected`,
  `test_f5_add_item_pending_receipt_resolves_unknown_zero_calls`.
- **F6** Add, write gate and name resolution at the tool layer:
  `tests/unit/test_tools.py::test_add_list_item_refused_when_writes_disabled`
  (writes disabled), `test_f6_add_list_item_ambiguous_or_invalid_name_never_refreshes`
  (parametrized: invalid, ambiguous — zero calls, zero refreshes), and
  `test_f6_add_list_item_unknown_member_refreshes_once` (exactly one
  `accgetallfamily`, second lookup uses the refreshed family).
- **F7** Add receipts: `test_f7_add_item_records_action_and_resource_id`,
  `test_f7_add_item_replay_returns_stored_result`,
  `test_f7_add_item_different_assigned_to_same_key_conflicts`,
  `test_f7_pre_upgrade_receipt_conflicts_instead_of_replaying`.
- **F8** Set, foreign item: `test_f8_foreign_item_refused_zero_writes_zero_receipt`
  in `tests/unit/test_list_service.py::TestSetItemAssignees` (service layer,
  zero writes and zero receipts); `tests/unit/test_tools.py::test_set_list_item_assignees_foreign_item_refused`
  (tool layer, surfaced as `ErrorResponse`).
- **F9** Set, complete partial form + confirmed:
  `TestSetItemAssignees::test_f9_complete_partial_form_sent_confirmed_when_only_assignment_changes`.
- **F10** Set, readback mismatch naming the field:
  `TestSetItemAssignees::test_f10_readback_description_changed_gives_mismatched`,
  `test_f10_readback_due_date_changed_gives_mismatched`.
- **F11** Set, lost/refused writes and receipt fields:
  `TestSetItemAssignees::test_f11_lost_write_gives_unknown`,
  `test_f11_refused_write_gives_rejected_receipt_with_action_and_resource_id`,
  `test_f11_replay_returns_stored_result_zero_calls`.
- **F12** `ListItem.due_date`/`reminder` parsing:
  `tests/unit/test_lists_adapter.py::TestDueDateAndReminderFields` (four
  tests: verbatim parse, absent gives `None`, malformed `dueDate` gives
  `None` not skipped, malformed `reminder` gives `None` not skipped).
- **F13** Tool responses, names/everyone/no account ID:
  `tests/unit/test_tools.py::test_f13_add_list_item_response_names_and_no_account_id_leak`,
  `test_f13_set_list_item_assignees_response_names_and_no_account_id_leak`.
- **F14** No remaining test expects a `taskmove` from `add_list_item`:
  the old create-then-move tests in `test_list_service.py` were replaced
  (F2–F7 above) rather than left in place; a dedicated
  `TestListServiceMutation::test_f14_add_item_never_sends_taskmove_across_every_outcome`
  drives `add_item` through confirmed, misfiled, mismatched, unknown and
  rejected and asserts none of the five ever calls `taskmove`.
  `grep -rn taskmove tests/` shows no assertion expecting one from
  `add_list_item`.

## Validation

Run from `/Users/ryan/dev/familywall-mcp/.claude/worktrees/slice-f`:

- `uv run ruff check .`: **All checks passed.**
- `uv run ruff format --check .`: **115 files already formatted.**
- `uv run mypy src`: **Success: no issues found in 30 source files.**
- `uv run pytest -m 'not live'`: **517 passed** (488 before this slice per
  `docs/PROGRESS.md`'s slice-C row; net +29 across the files above, after
  replacing every obsolete create-then-move test).
- `uv build`: both sdist and wheel built successfully
  (`familywall_mcp-0.1.0.tar.gz`, `familywall_mcp-0.1.0-py3-none-any.whl`).
- `scripts/check`: every step above passes; the final `detect-secrets` step
  fails as the brief anticipates — `.secrets.baseline` is untouched by this
  diff (`git status --short .secrets.baseline` / `git diff --stat
  .secrets.baseline` both show no change), so the failure is pre-existing
  drift between the repo's current file contents and the committed
  baseline, unrelated to this slice's changes (tracked separately per PR
  #8). The baseline was not edited.

## Security and privacy review

- All test data is synthetic: neutral placeholder names (`Alex`, `Robin`,
  `Sam`, `Jordan`, plus the pre-existing `Test Member`/`Other Member`/`Jordan
  Lee`), fake account IDs (`acc-alex`, `acc-robin`, `acc-sam`, `acc-someone-else`,
  `acct/1`, `acct/3`, etc.), and fake list/item/family/calendar IDs. No real
  person's name is used anywhere in this diff.
- No `.env*` file or credential store was read. No network calls were made;
  every test uses in-memory fakes (`FakeTransport`, `FakeSessionPool` and its
  subclasses) or synthetic wire payloads.
- No environment variables were printed.
- Names never reach the wire: both new builders
  (`build_create2_item_fields`, `build_update2_assignees_fields`) only ever
  receive already-resolved account IDs — asserted directly in F1
  (`test_f1_create2_never_emits_a_name`, `test_f1_update2_never_emits_a_name`)
  — and both tool responses report names only, never an account ID,
  asserted in F13.
- The ownership check before `set_list_item_assignees` writes (F8) is a
  security requirement, implemented and tested at both the service layer
  (zero writes, zero receipts for a foreign item) and the tool layer
  (surfaced as a safe `ErrorResponse`, no upstream write).

## Known limitations

- The everyone encoding on `taskcreate2`, extrapolated when this slice was
  written, was live-verified in slice G.
- `add_list_item`'s new single-call behaviour and `set_list_item_assignees`
  are both offline/unit-tested only; neither has been exercised against a
  real FamilyWall account yet.
- `_resolve_assignment_with_refresh` is only used by the two list tools.
  `_create_calendar_event` still has its own inline copy of the same logic;
  merging them was explicitly left to the lead (decision 9), since a
  parallel slice may add its own calendar-side helper and the two would
  need reconciling anyway.
- Out of scope for this slice, unchanged: `set_list_item_checked`, due
  dates/reminders/categories as caller inputs (only read, via `ListItem`,
  never written), deleting items, and all calendar code.
- `docs/PROGRESS.md`'s "How to run it locally" section (around line 103)
  still says "Seven tools are exposed" and lists an incomplete/stale set
  (missing `list_family_members`, predating this slice); it was already
  stale before this change and is outside this slice's one-row file
  boundary for that file, so it was left as-is (noted here rather than
  silently fixed, following the same practice as handoff 09c's note about a
  stale `docs/architecture.md` line).

## Next bounded task

The lead's live check (slice G): run `add_list_item` (both everyone and a
named member, in a non-default list) and `set_list_item_assignees` once each
against a real FamilyWall account with disposable data, confirming in
particular that the everyone encoding on `taskcreate2` reads back as
expected (or that `mismatched` correctly catches it if not), then clean up
by exact ID and update `docs/compatibility.md`/`docs/contracts/familywall.md`
accordingly.

## Lead review and live verification (2026-09-25)

The lead also recorded the acknowledgement before the readback in both list
writes (with a crash-during-readback test) and merged the resolution helper
with slice E's. Reviewed against the brief; the required tests are present and the suite passes
on the combined branch. Live acceptance (slice G, authorised by the account
owner) on disposable data, deleted by exact ID afterwards with nothing left
behind: see `docs/handoffs/09g-live-acceptance.md`.
