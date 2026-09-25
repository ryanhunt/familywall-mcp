# Task 09F — List assignment: single-call `add_list_item` and `set_list_item_assignees`

Slice F of the [brief 09 implementation plan](09-implementation-plan.md). It builds on
slices B (resolver, `ListItem.assignee_ids`/`to_all`), D (receipts) and C (refresh-once
resolution in the tool layer). Offline only: synthetic fixtures, no live network.

## Objective

1. `add_list_item` creates the item **directly in the chosen list, with its assignment, in one
   `taskcreate2` call**, replacing create-then-move (owner decision 5).
2. A new narrow `set_list_item_assignees` changes **only** who an existing item is assigned to,
   confirmed by a readback showing the assignment changed and nothing else did.

In both, blank `assigned_to` means everyone.

## Scope

- Files allowed to change:
  - `src/familywall_mcp/familywall/lists.py` (new builders; `ListItem.due_date`/`reminder` and
    their parser)
  - `src/familywall_mcp/services/lists.py`
  - `src/familywall_mcp/models.py` (add `"list.set_assignees"` to the `action` Literal only)
  - `src/familywall_mcp/tools/registry.py` (the `add_list_item` argument and response, the new
    tool, and a refresh-once resolution helper; see decision 9)
  - tests: `tests/unit/test_lists_adapter.py`, `tests/unit/test_list_service.py`,
    `tests/unit/test_tools.py`, a new `tests/unit/test_list_assignment.py` if useful,
    `tests/support/*` (synthetic only)
  - docs: a new `docs/decisions/0003-single-call-add.md`; add a "Superseded by ADR 0003"
    line at the top of `docs/decisions/0002-non-atomic-add.md`; `README.md` (tool table, count
    and behaviour notes); `AGENTS.md` (the tool-count sentence); `docs/architecture.md` (table
    rows); `docs/PROGRESS.md` (one row); `docs/briefs/09-implementation-plan.md` (mark F done);
    a new `docs/handoffs/09f-list-assignment.md`
- Out of scope: `set_list_item_checked`, due dates, reminders or categories as caller inputs,
  deleting items, calendar code, and live verification (the lead does it).

## Evidence (live-verified 2026-09-25; `docs/contracts/familywall.md#probe-a2--list-assignment-and-the-2-endpoints-2026-09-25`)

- **`taskcreate2`** with `taskListId` (a non-default list), `text`, `taskCategoryId=""`,
  `dueDate=$empty`, `picture=$empty`, `toAll=false` and `assignee.0` created the item **in that
  list** with exactly that assignee. The response is the full task object, including `metaId`,
  `taskListId` and `assigneeIds`.
- **"Everyone" on tasks** (verified through `taskupdate2`) is `toAll=true` plus `assignee.N` for
  every member. It reads back as `toAll:"true"` with every member in `assigneeIds`, in a
  different order. **The same encoding on `taskcreate2` is extrapolated, not observed**: the
  readback check (decision 5) must catch it if the server disagrees, and the lead will verify it
  live before merge.
- **`taskupdate2` patches.** `partnerScope`, `taskId`, `toAll` and `assignee.N` changed the
  assignment and kept `text`, `description`, `dueDate`, the reminder and the list.
- **Reads:** `dueDate` (a UTC instant string) and `reminder`
  (`{reminderUnit, reminderType, reminderValue, localId}`) appear on task objects when set.
- **Current behaviour:** today's `taskcreate` with only `a00text` assigns every member.

## Binding decisions

1. **ADR 0003** (short; the same shape as ADR 0002) records the switch to a single
   `taskcreate2`, with the evidence above. It says **the create-then-move partial-failure state
   no longer exists**: no `taskmove` is sent by `add_list_item`. `misfiled` stays only as a
   **detected** outcome (the create response shows a list other than the one requested), with no
   move and no compensation. ADR 0002 gets a "Superseded by ADR 0003" line.
2. **Builders.**
   - `build_create2_item_fields(*, list_id, text, to_all, assignee_account_ids)` returns exactly:
     `partnerScope`, `taskListId`, `text`, `taskCategoryId=""`, `dueDate=$empty`,
     `picture=$empty`, `toAll` (`"true"`/`"false"`) and `assignee.N` in the given order.
   - `build_update2_assignees_fields(*, item_id, to_all, assignee_account_ids)` returns exactly:
     `partnerScope`, `taskId`, `toAll` and `assignee.N`.
   - Both reject an empty ID list. Names never appear on the wire. For everyone, pass every
     member's ID in discovery order.
   - The old `build_create_item_fields`/`build_move_item_fields` may stay (other code or tests may
     use them), but `add_list_item` no longer calls them.
3. **`ListItem`** gains `due_date: str | None = None` (the verbatim `dueDate` string) and
   `reminder: tuple[str, str, str] | None = None` (the verbatim type, unit and value), both
   parsed **leniently**: absent or malformed gives `None`, and never skips the item.
4. **`add_list_item(text, list_id=None, assigned_to=None, idempotency_key=None)`.**
   - **Resolve first, and select the list next.** Resolve `assigned_to` (refresh once on
     `unknown_member`) before list selection, any receipt or any write. List selection is
     unchanged.
   - **One write, never retried:** a single `taskcreate2`.
   - **Indeterminate errors** give `unknown`. `UpstreamRejectedError`/`AuthenticationError` give
     `rejected` with the error JSON, then re-raise (the calendar pattern from slice D). A replay
     raises the same error.
   - **Create response without `metaId` or `taskListId`:** `acknowledged` if an ID is present,
     otherwise `unknown`.
   - **Response `taskListId` is not the requested list:** `misfiled` with `actual_list_id` and
     `requested_list_id`. No move, no readback needed.
   - **Otherwise,** read back the requested list:
     - item absent: `acknowledged`;
     - assignment differs (decision 5): the new `WriteOutcome.MISMATCHED` (`"mismatched"`) with
       `mismatched_fields=("assignees",)`;
     - otherwise `confirmed`.
5. **Assignment match rule.** Everyone means `item.to_all is True`. Named members means
   `item.to_all is False` and `set(item.assignee_ids) == set(requested ids)`.
6. **Receipts for add.** `action="list.add_item"`, `resource_id=<list id>`, and a payload hash
   over the new `taskcreate2` fields with the existing `_compute_payload_hash`. Document that a
   pre-upgrade `add_list_item` receipt reused within its 24-hour life now conflicts instead of
   replaying (the fields changed), which is safe: no duplicate is created.
7. **`set_list_item_assignees(item_id, assigned_to=None, idempotency_key=None)`.**
   - **Resolve first.** Resolution comes before anything else.
   - **Verify ownership.** Confirm the item belongs to an accessible list **before** writing,
     exactly as `set_item_checked` does. A foreign item raises `UnsupportedConfigurationError`
     with zero writes and no receipt. Keep the found item as `before`.
   - **One write, never retried:** a single partial `taskupdate2`.
     - Indeterminate errors give `unknown`.
     - Refusals give `rejected`, then re-raise, as in decision 4.
   - **Receipts:** `action="list.set_assignees"` and `resource_id=item_id`, following the same
     pending, final and replay pattern.
   - **Readback of the item's list:**
     - item absent: `acknowledged`;
     - assignment differs: `mismatched` with `"assignees"`;
     - any of `text`, `description`, `completed`, `list_id`, `due_date` or `reminder` differs from
       `before`: `mismatched`, naming each differing field;
     - otherwise `confirmed`.
8. **Responses.**
   - `AddListItemResponse` keeps its fields and gains `assigned_to` (display names; every member
     for everyone), `assigned_to_everyone` and `mismatched_fields`.
   - The new `SetListItemAssigneesResponse` has `family_name`, `outcome`, `item_id`,
     `assigned_to`, `assigned_to_everyone` and `mismatched_fields`.
   - No account IDs anywhere.
9. **Resolution helper.** Put the refresh-once-on-`unknown_member` resolution in one small
   private helper in `registry.py`, used by the list tools. (A parallel slice may add a similar
   helper for calendar tools; that is expected, and the lead will merge them.)
10. **Tool descriptions.** Explain `assigned_to` (names as `list_family_members` shows them; omit
    for everyone). `add_list_item` must no longer mention create-then-move.

## Required tests (each must exist and pass; put the F-number in the name or docstring)

- **F1** Both builders: the complete forms for everyone and named, the empty-list rejection,
  and no names on the wire.
- **F2** Add, confirmed: exactly one write (`taskcreate2`, **no `taskmove`**), then one readback
  of the requested list.
- **F3** Add: a response `taskListId` for a different list gives `misfiled` with both list IDs,
  no move and no retry.
- **F4** Add: a readback with a different assignment gives `mismatched` with `"assignees"`; an
  absent item gives `acknowledged`.
- **F5** Add: a lost write gives `unknown` with no retry; a refused write gives `rejected`, and a
  replay raises the same code with zero calls.
- **F6** Add: writes disabled, or an invalid or ambiguous name, gives zero calls. An unknown name
  refreshes once.
- **F7** Add receipts: `action` and `resource_id` are correct, a replay returns the stored result,
  and a different `assigned_to` with the same key conflicts. A pre-upgrade receipt (old
  `taskcreate` hash) conflicts rather than replaying.
- **F8** Set: a foreign item gives `UnsupportedConfigurationError`, with no write and no receipt.
- **F9** Set: the complete partial form is sent; confirmed when the assignment changed and the
  other fields are unchanged.
- **F10** Set: a readback where `due_date` or `description` changed gives `mismatched`, naming
  the field.
- **F11** Set: a lost write gives `unknown`; a refusal gives `rejected`. The receipt carries
  `action="list.set_assignees"` and `resource_id=item_id`.
- **F12** `ListItem.due_date`/`reminder`: they parse verbatim; absent or malformed gives `None`
  and the item is **not** skipped.
- **F13** Tool responses: correct names and everyone flag, and no synthetic account ID in the
  serialised output, for both tools.
- **F14** Existing create-then-move tests are updated or removed to match ADR 0003, with no
  remaining test expecting a `taskmove` from `add_list_item`.

## Validation

Run from this worktree: `uv sync --frozen --group dev`, `uv run ruff check .`,
`uv run ruff format --check .`, `uv run mypy src`, `uv run pytest -m 'not live'`, `uv build`.
`scripts/check` may fail only at its detect-secrets step, on pre-existing findings (fixed
separately in PR #8); report that rather than editing `.secrets.baseline`.

## Security and privacy

Use **neutral synthetic names only** (for example Alex, Robin, Sam, Jordan), never a real
person's name. Never read `.env*` files or credential stores, and never make network calls.
Names never reach the wire. The ownership check before `set_list_item_assignees` writes is a
security requirement: the endpoint sends no list ID, so the server cannot enforce list
membership.

## Handoff target

The lead's live check (slice G), including **everyone on `taskcreate2`**, then merge.
