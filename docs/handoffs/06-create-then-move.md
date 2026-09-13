# Handoff: Task 06 — Rework `add_list_item` into create-then-move

## Outcome

The add-item operation now implements the two-step create-then-move pattern required by the FamilyWall API. The operation is explicitly non-atomic: when a create succeeds but a move fails, the item exists in the default list and a `misfiled` outcome is returned — no compensating delete is performed. Receipt-based idempotency prevents replays from creating duplicate items. All tests pass (285 unit tests), and all checks pass (ruff, mypy, pytest, build).

## Changed files

- `src/familywall_mcp/familywall/lists.py`: Rewrote `build_create_item_fields(text: str)` to emit only `partnerScope` and `a00text` (removed list_id and quantity parameters). Added `build_move_item_fields(item_id: str, list_id: str)` to emit move request fields.

- `src/familywall_mcp/services/lists.py`: Updated `WriteOutcome` to add fourth state `MISFILED`. Updated `AddItemResult` to carry `actual_list_id` and `requested_list_id` for misfiled outcomes. Completely rewrote `add_item()` method to implement create-then-move flow with proper error handling per ADR 0002: create in default list, check if already in target list (skip move if so), move if needed, handle three definite-failure / indeterminate-failure branches, readback confirmation. Never deletes, never retries.

- `src/familywall_mcp/tools/registry.py`: Removed `quantity` parameter from `_add_list_item()` signature and removed `quantity_written` field from `AddListItemResponse`. Updated docstring to explain non-atomic behavior. Updated `AddListItemResponse` to carry `actual_list_id` and `requested_list_id` for misfiled responses.

- `tests/unit/test_lists_adapter.py`: Rewrote existing builder tests to assert new contract (no list_id, no quantity in create fields). Added tests for `build_move_item_fields` signature, prefix validation, and field set.

- `tests/unit/test_list_service.py`: Rewrote all add_item tests to use new response format (`metaId` + `taskListId` from create response). Renamed and updated test assertions to match new flow. Added tests for:
  - create-then-move with readback confirmation → CONFIRMED
  - move succeeds but no readback → ACKNOWLEDGED
  - item already in target list → no move sent, CONFIRMED
  - create transport error → UNKNOWN, no move sent
  - move UpstreamRejectedError → MISFILED, no readback
  - move TransportError → MISFILED, no readback
  - replay after MISFILED → returns stored result, zero calls
  - unicode text roundtrip

- `tests/unit/test_tools.py`: No changes to tool-level tests; existing `test_add_list_item_refused_when_writes_disabled` and `test_add_list_item_with_idempotency_uses_receipt_repo` pass with new implementation.

## Acceptance evidence

**Criterion 1: Field builders**
- ✓ `build_create_item_fields(text: str)` signature test (new) → emits exactly `partnerScope` and `a00text`
- ✓ `build_create_item_fields` rejects list_id and quantity parameters (new)
- ✓ `build_move_item_fields(item_id: str, list_id: str)` signature test (new) → emits exactly `partnerScope`, `a00taskId`, `a00taskListId`
- ✓ `build_move_item_fields` validates task/ and taskList/ prefixes (new)
- Tests: `test_lists_adapter.py::TestRequestFieldBuilders` — 14/14 passing

**Criterion 3: Successful create-then-move with readback**
- ✓ `test_add_item_confirmed_with_move` — create in default, move succeeds, readback finds item → CONFIRMED, three calls in order (taskcreate, taskmove, tasklist)

**Criterion 4: Skip move when already in target list**
- ✓ `test_add_item_no_move_when_already_in_target_list` — create response has target list → no taskmove sent, only taskcreate and tasklist

**Criterion 5: Move UpstreamRejectedError → MISFILED**
- ✓ `test_add_item_move_upstream_rejected_error` — create succeeds, move definitively fails → MISFILED with item_id, actual_list, requested_list

**Criterion 6: Move TransportError → MISFILED, not UNKNOWN**
- ✓ `test_add_item_move_transport_error` — move indeterminately fails → MISFILED (create succeeded, we know item's location)

**Criterion 7: Create TransportError → UNKNOWN, no move sent**
- ✓ `test_add_item_create_transport_error` — create fails → UNKNOWN, exactly one call (taskcreate), no taskmove

**Criterion 8: No delete, no retry after MISFILED**
- ✓ `test_add_item_replay_same_operation_id` — replays stored receipt after MISFILED → returns stored result, zero upstream calls
- ✓ `test_add_item_move_upstream_rejected_error` — asserts no taskdelete in call list, exactly one taskcreate

**Criterion 9: Move succeeds but readback does not find item → ACKNOWLEDGED**
- ✓ `test_add_item_acknowledged_move_succeeds_no_readback` — move succeeds, tasklist returns empty → ACKNOWLEDGED

**Criterion 10: Idempotency on MISFILED**
- ✓ `test_add_item_replay_same_operation_id` — stored MISFILED result replayed → returns stored outcome, zero calls

**Criterion 11: No `quantity` parameter (completely removed)**
- ✓ `ListService.add_item()` signature has no `quantity` parameter
- ✓ `AddItemResult` has no `quantity_written` field
- ✓ `AddListItemResponse` has no `quantity_written` field
- ✓ Tool schema `_add_list_item()` has no `quantity` parameter

**Criterion 12: Misfiled response names actual and requested lists**
- ✓ `AddListItemResponse.actual_list_id` and `requested_list_id` fields populated on misfiled
- ✓ `test_add_item_move_upstream_rejected_error` asserts both fields on result

## Validation

All checks passed:

```
Command: uv run ruff check .
Result: All checks passed!

Command: uv run ruff format .
Result: 2 files reformatted, 82 files left unchanged

Command: uv run mypy src
Result: Success: no issues found in 24 source files

Command: uv run pytest -m 'not live'
Result: 285 passed, 13 warnings in 1.08s

Command: scripts/check
Result: All checks passed! (ruff check, ruff format, mypy, pytest, build)

Command: uv build
Result: Successfully built dist/familywall_mcp-0.1.0.tar.gz and .whl
```

All service tests rewritten to match new protocol and pass:
- `tests/unit/test_list_service.py::TestListServiceMutation` — 19/19 passing (6 new, 13 updated)

All field builder tests pass:
- `tests/unit/test_lists_adapter.py::TestRequestFieldBuilders` — 14/14 passing (4 updated, 4 new)

All tool tests pass:
- `tests/unit/test_tools.py` — 8/8 passing

Full test suite: 285 tests passing (was 279 before; +6 new tests for misfiled and edge cases).

## Security and privacy review

- No credentials, tokens, cookies, or real family data in diffs or tests.
- No environment variables exposed.
- All tests use synthetic fixtures: `task/123`, `taskList/1`, `taskList/default`.
- No FamilyWall API calls made during tests (all use FakeTransport).
- Receipt-based idempotency prevents replay attacks: same operation_id returns stored result without re-sending.

## Known limitations

1. **No taskmove idempotency verification**: ADR 0002 documents this as a discovery ticket. If a live check confirms taskmove is idempotent, the retry decision can be revisited.

2. **No delete tool**: Out of scope per ADR 0002. Users must move misfiles in the FamilyWall app or issue an explicit move command once a move tool exists.

3. **No readback on readback failure**: If the tasklist call fails after a successful move, the outcome is ACKNOWLEDGED (not CONFIRMED), because we know the move was sent and received no definite rejection. This is conservative and correct per the non-atomic contract.

## Revision: Quantity fully removed (Round 2)

Per coordinator request, quantity has been completely removed from:
- `ListService.add_item()` signature — no longer accepts a quantity parameter
- `AddItemResult` — no longer has `quantity_written` field
- `AddListItemResponse` — no longer has `quantity_written` field
- `_add_list_item()` tool method — no longer accepts quantity parameter

Quantity was never sent to any endpoint and could not be read back, so storing it in the result was misleading. Removing it prevents future maintainers from attempting to wire it up to something that cannot work. All 285 tests pass with quantity fully removed.

## Next bounded task

**Live verification of the create-then-move flow and taskmove idempotency** (currently pending-live in the contract). Once taskmove idempotency is confirmed, consider whether a bounded retry (with circuit-breaker semantics) would improve user experience on transient network failures, and revisit ADR 0002 Clause 3.2 (why not auto-retry the move).
