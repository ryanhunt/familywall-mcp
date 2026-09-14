# Handoff: Task 03A — Shopping-list wire adapters

## Outcome

Task 03A is complete with review fixes applied. The shopping list adapter module provides pure parsing of FamilyWall wire protocol payloads with comprehensive type safety and validation. All 47 tests pass, mypy strict mode passes, and ruff checks pass.

## Changed files

- `src/familywall_mcp/familywall/lists.py`: Core adapter module with `ListType` enum, `ShoppingList` and `ListItem` pydantic models, `ParsedItems` result type, and adapter functions (`parse_list_summaries`, `parse_list_items`) plus request field builders (`build_get_lists_fields`, `build_get_list_fields`, `build_create_item_fields`, `build_mark_item_fields`).
- `tests/unit/test_lists_adapter.py`: 47 comprehensive tests covering all 13 acceptance criteria and model constraints (44 original + 3 review fixes).
- `tests/support/list_fixtures.py`: Synthetic payloads with shopping lists (SHOPPING_LIST, TODOS, OTHER, and unknown MEALPLAN type) and items (complete, incomplete, Unicode, with categories and dates).

## Acceptance evidence

All 13 acceptance criteria from the brief are tested and passing:

1. **Bare array of four lists with unknown type**: `test_bare_array_of_four_lists_parses` verifies parse and unknown `MEALPLAN` type is preserved with `known_type is None`.
2. **Wrapper objects**: `test_wrapped_lists_key_parses`, `test_wrapped_taskLists_key_parses`, `test_wrapped_results_key_parses` all pass.
3. **Invalid payload shapes**: `test_non_array_non_wrapper_raises` verifies rejection.
4. **Wrong metaId prefix**: `test_wrong_prefix_raises` (lists) and `test_item_with_wrong_metaId_prefix_skipped` (items) verify validation.
5. **Numeric string/int coercion**: `test_numeric_string_total_items`, `test_numeric_int_total_items`, and `test_non_numeric_string_total_items` verify string "45" → 45, int 45 → 45, and "many" → None.
6. **Boolean parsing**: `test_complete_true_parses_to_bool_true`, `test_complete_false_parses_to_bool_false`, and `test_complete_invalid_value_raises` verify "true" → True, "false" → False, and "yes" raises.
7. **taskListId mapping**: `test_list_id_comes_from_taskListId` verifies list_id comes from taskListId with prefix intact.
8. **Unicode text**: `test_unicode_text_roundtrips` verifies "Café ☕" round-trips unchanged.
9. **One malformed item skipped**: `test_malformed_item_skipped_others_returned` verifies ParsedItems with 3 items and skipped=1.
10. **Unparseable date yields None**: `test_unparseable_date_yields_none` verifies invalid dates don't raise.
11. **Quantity field builders**: `test_build_create_item_fields_with_quantity`, `test_build_create_item_fields_without_quantity`, `test_build_create_item_fields_quantity_verbatim`, and `test_build_create_item_fields_empty_quantity_raises` verify omission when None, verbatim inclusion (spaces and Unicode), and rejection of empty string.
12. **Mark item fields**: `test_build_mark_item_fields_completed_true` and `test_build_mark_item_fields_completed_false` verify "true"/"false" strings; `test_build_mark_item_fields_no_list_id` verifies no list ID in fields.
13. **Get list fields prefix validation**: `test_build_get_list_fields_invalid_prefix` verifies rejection of non-taskList/ prefix.

## Validation

```
uv run ruff check src/familywall_mcp/familywall/lists.py tests/unit/test_lists_adapter.py tests/support/list_fixtures.py
All checks passed!

uv run ruff format src/familywall_mcp/familywall/lists.py tests/unit/test_lists_adapter.py tests/support/list_fixtures.py
(No changes needed)

uv run mypy src
Success: no issues found in 13 source files

uv run pytest tests/unit/test_lists_adapter.py -v
============================== 47 passed in 0.02s ==============================
```

## Security and privacy review

- No credentials, tokens, cookies, or real family data in code, fixtures, or tests.
- All fixtures use synthetic names (Weekly shop, Hardware, Müsli, Meal plan) and invented numeric IDs (101–104, 201–204).
- No real FamilyWall data was accessed or inspected.
- No environment variables printed or logged.

## Known limitations

- This is a pure parsing module with no I/O or HTTP as specified. Integration with the FamilyWall session and transport layer (client.py, wire.py) is expected in a subsequent phase.
- The module does not create, update, or delete items; it only parses payloads and builds request fields for those operations.
- Error details are safe and static; upstream response bodies are never exposed.

## Review fixes

Two defects found in review; both fixed in place with new tests:

**DEFECT 1 — Absent count fields raise instead of yielding None.**

Fixed `_coerce_int()` to handle `None` (absent key) by returning `None` instead of raising `MalformedPayloadError`. Added explicit bool rejection before int check since `isinstance(True, int)` is True in Python. Counts are cosmetic cosmetic; system lists vary their key sets.

New tests:
- `test_absent_total_items_yields_none`: List summary with no `totalTaskNumber` key parses with `total_items is None`.
- `test_totalTaskNumber_list_raises`: `totalTaskNumber: []` raises.
- `test_totalTaskNumber_bool_raises`: `totalTaskNumber: True` raises.

**DEFECT 2 — Write field builders omitted partnerScope.**

Added `"partnerScope": "Family"` to `build_create_item_fields()` and `build_mark_item_fields()`. Both write operations now match the read operations and include the required field. Updated test assertions to verify full field sets (get-lists, get-list, create-item, mark-item now all explicitly tested with partnerScope).

All checks pass; test count increased from 44 to 47.

## Next bounded task

Integration of list adapters with the FamilyWall transport layer, including wiring request/response flow for list operations and error handling in the client session layer.
