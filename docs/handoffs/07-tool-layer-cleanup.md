# Handoff: Task 07 — Tool-layer defects fixed

## Outcome

Both defects found by the live check are now fixed. The reporting bug is corrected so that `actual_list_id` and `requested_list_id` are populated only for `misfiled` outcomes. The dead injection of `ListService` into `ToolRegistry` is removed, and the four list tools now properly test against `FakeSessionPool` with assertions on endpoint names, request fields, and read/write modes. Criterion 11 sanity check passed: a test catches the broken add flow.

## Changed files

- `src/familywall_mcp/services/lists.py`: Updated `AddItemResult` documentation and fixed `add_item` to return `None` for `actual_list_id` and `requested_list_id` in all non-`MISFILED` outcomes (seven code paths corrected).
- `src/familywall_mcp/tools/registry.py`: Removed `list_service` parameter from `ToolRegistry.__init__`, removed `self._list_service` attribute, and updated `AddListItemResponse` documentation.
- `src/familywall_mcp/server.py`: Removed `list_service` construction and its argument in the `ToolRegistry` instantiation, removed unused import.
- `tests/unit/test_list_service.py`: Updated three tests to assert `actual_list_id is None` and `requested_list_id is None` for `CONFIRMED` and `ACKNOWLEDGED` outcomes (leaving misfiled tests unchanged).
- `tests/unit/test_tools.py`: Deleted the entire `FakeListService` class. Rewrote `create_registry()` to return a tuple `(registry, pool)` for inspection. Enhanced `FakeSessionPool` to track created items and simulate move/create flows. Rewrote five tool tests to assert on pool calls:
  - `test_list_shopping_lists_names_family`: Criterion 6 — asserts `taskgettasklists` with `read` mode.
  - `test_get_list_items_names_family`: Criterion 7 — asserts `tasklist` with `a00listId` and `read` mode.
  - `test_add_list_item_with_idempotency_uses_receipt_repo`: Criterion 8 — asserts `taskcreate`, `taskmove`, `tasklist` with write modes on the first two.
  - `test_set_list_item_checked_verifies_and_marks`: Criterion 9 — asserts membership verification before `taskmark` with `a00complete` as string `"true"`.
  - Updated `test_add_list_item_refused_when_writes_disabled` and `test_set_list_item_checked_refused_when_writes_disabled`: Criterion 10 — assert zero pool calls when writes are disabled.

## Acceptance evidence

- **Criterion 1**: `test_add_item_confirmed_with_move` passes, asserts `actual_list_id is None` and `requested_list_id is None`.
- **Criterion 2**: `test_add_item_no_move_when_already_in_target_list` passes, asserts both fields are `None` for confirmed outcome.
- **Criterion 3**: `test_add_item_acknowledged_move_succeeds_no_readback` passes, asserts both fields are `None` for acknowledged outcome.
- **Criterion 4**: Misfiled tests still populate both fields; `test_add_item_move_upstream_rejected_error` verifies `actual_list_id="taskList/default"` and `requested_list_id="taskList/1"`.
- **Criterion 5**: Dead `list_service` parameter removed from `ToolRegistry.__init__` (12 lines changed in registry.py).
- **Criterion 6**: `test_list_shopping_lists_names_family` asserts exactly one call to `taskgettasklists` with `read_write="read"`.
- **Criterion 7**: `test_get_list_items_names_family` asserts `tasklist` endpoint with `fields.get("a00listId")="taskList/1"` and `read_write="read"`.
- **Criterion 8**: `test_add_list_item_with_idempotency_uses_receipt_repo` asserts all four endpoints (`taskgettasklists`, `taskcreate`, `taskmove`, `tasklist`) are called with `taskcreate` and `taskmove` using `write` mode.
- **Criterion 9**: `test_set_list_item_checked_verifies_and_marks` asserts membership verification calls and `taskmark` with `fields.get("a00complete")="true"` and `read_write="write"`.
- **Criterion 10**: Both write-gate tests assert `len(pool.calls) == 0` when writes are disabled.
- **Criterion 11**: Sanity check passed. Temporarily broke `add_item` by returning `UNKNOWN` immediately after `taskcreate`. The test `test_add_list_item_with_idempotency_uses_receipt_repo` failed with `AssertionError: assert 'unknown' == 'confirmed'`, correctly detecting the broken flow. Code restored; test passes again.

## Validation

```bash
uv run ruff check .
# Result: All checks passed!

uv run ruff format .
# Result: 1 file reformatted, 85 files left unchanged

uv run mypy src
# Result: Success: no issues found in 24 source files

uv run pytest -m 'not live'
# Result: 286 passed, 13 warnings in 1.22s
# Test count increased from 285 to 286 (added test_set_list_item_checked_verifies_and_marks)

scripts/check
# Result: All checks passed! (includes ruff check, ruff format, mypy, pytest, and build)
```

## Security and privacy review

- No credentials, tokens, cookies, or real family data in diffs.
- No `.env` changes or environment variable dumps.
- FakeSessionPool and FakeCalendarService use synthetic test data only.
- Fixture names use lowercase generic identifiers (`task/1`, `taskList/1`, `family/1`).
- No Git identity or signing configuration modified.

## Known limitations

- None. All criteria met.

## Next bounded task

None. This task is complete and blocks no further work.
