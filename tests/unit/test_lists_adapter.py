"""Tests for shopping list wire adapters."""

from __future__ import annotations

from datetime import UTC

import pytest
from pydantic import ValidationError
from tests.support.list_fixtures import (
    list_item_with_assignment_fields,
    list_item_with_due_date_and_reminder,
    list_item_with_malformed_assignee_ids,
    list_item_with_malformed_due_date,
    list_item_with_malformed_reminder,
    list_item_with_malformed_to_all,
    list_item_without_assignment_fields,
    list_items_bare_array,
    list_items_with_malformed_entry,
    list_items_wrapped_items,
    list_items_wrapped_listItems,
    list_items_wrapped_tasks,
    list_summaries_bare_array,
    list_summaries_wrapped_lists,
    list_summaries_wrapped_results,
    list_summaries_wrapped_taskLists,
)

from familywall_mcp.errors import MalformedPayloadError
from familywall_mcp.familywall.lists import (
    ListItem,
    ListType,
    ParsedItems,
    ShoppingList,
    build_create2_item_fields,
    build_create_item_fields,
    build_get_list_fields,
    build_get_lists_fields,
    build_mark_item_fields,
    build_update2_assignees_fields,
    parse_list_items,
    parse_list_summaries,
)


class TestParseListSummaries:
    """Tests for parse_list_summaries."""

    def test_bare_array_of_four_lists_parses(self) -> None:
        """Criterion 1: A bare array of four lists parses, preserving unknown type."""
        payload = list_summaries_bare_array()
        lists = parse_list_summaries(payload)

        assert len(lists) == 4

        # Check first list (SHOPPING_LIST)
        assert lists[0].list_id == "taskList/101"
        assert lists[0].name == "Weekly shop"
        assert lists[0].type_raw == "SHOPPING_LIST"
        assert lists[0].known_type is ListType.SHOPPING

        # Check unknown type (MEALPLAN)
        assert lists[3].list_id == "taskList/104"
        assert lists[3].name == "Meal plan"
        assert lists[3].type_raw == "MEALPLAN"
        assert lists[3].known_type is None

    def test_wrapped_lists_key_parses(self) -> None:
        """Criterion 2: Wrapper object with 'lists' key parses."""
        payload = list_summaries_wrapped_lists()
        lists = parse_list_summaries(payload)
        assert len(lists) == 4

    def test_wrapped_taskLists_key_parses(self) -> None:
        """Criterion 2: Wrapper object with 'taskLists' key parses."""
        payload = list_summaries_wrapped_taskLists()
        lists = parse_list_summaries(payload)
        assert len(lists) == 4

    def test_wrapped_results_key_parses(self) -> None:
        """Criterion 2: Wrapper object with 'results' key parses."""
        payload = list_summaries_wrapped_results()
        lists = parse_list_summaries(payload)
        assert len(lists) == 4

    def test_non_array_non_wrapper_raises(self) -> None:
        """Criterion 3: Non-array, non-wrapper payload raises MalformedPayloadError."""
        with pytest.raises(MalformedPayloadError):
            parse_list_summaries("not an array")

        with pytest.raises(MalformedPayloadError):
            parse_list_summaries({"invalid_key": [{}]})

        with pytest.raises(MalformedPayloadError):
            parse_list_summaries({"lists": "not an array"})

    def test_wrong_prefix_raises(self) -> None:
        """Criterion 4: A list with metaId 'task/1' raises (wrong prefix)."""
        payload = [{"metaId": "task/1", "name": "Bad", "taskListType": "OTHER"}]
        with pytest.raises(MalformedPayloadError):
            parse_list_summaries(payload)

    def test_numeric_string_total_items(self) -> None:
        """Criterion 5: totalTaskNumber as string '45' yields 45."""
        payload = [
            {
                "metaId": "taskList/1",
                "name": "Test",
                "taskListType": "SHOPPING_LIST",
                "totalTaskNumber": "45",
                "remainingTaskNumber": 10,
            }
        ]
        lists = parse_list_summaries(payload)
        assert lists[0].total_items == 45

    def test_numeric_int_total_items(self) -> None:
        """Criterion 5: totalTaskNumber as int 45 yields 45."""
        payload = [
            {
                "metaId": "taskList/1",
                "name": "Test",
                "taskListType": "SHOPPING_LIST",
                "totalTaskNumber": 45,
                "remainingTaskNumber": 10,
            }
        ]
        lists = parse_list_summaries(payload)
        assert lists[0].total_items == 45

    def test_non_numeric_string_total_items(self) -> None:
        """Criterion 5: totalTaskNumber as 'many' yields None."""
        payload = [
            {
                "metaId": "taskList/1",
                "name": "Test",
                "taskListType": "SHOPPING_LIST",
                "totalTaskNumber": "many",
                "remainingTaskNumber": 10,
            }
        ]
        lists = parse_list_summaries(payload)
        assert lists[0].total_items is None

    def test_absent_total_items_yields_none(self) -> None:
        """Absent totalTaskNumber key yields None (cosmetic field, system lists vary)."""
        payload = [
            {
                "metaId": "taskList/1",
                "name": "Test",
                "taskListType": "SHOPPING_LIST",
                # No totalTaskNumber key at all
                "remainingTaskNumber": 10,
            }
        ]
        lists = parse_list_summaries(payload)
        assert lists[0].total_items is None

    def test_totalTaskNumber_list_raises(self) -> None:
        """totalTaskNumber as a list raises MalformedPayloadError."""
        payload = [
            {
                "metaId": "taskList/1",
                "name": "Test",
                "taskListType": "SHOPPING_LIST",
                "totalTaskNumber": [],
                "remainingTaskNumber": 10,
            }
        ]
        with pytest.raises(MalformedPayloadError):
            parse_list_summaries(payload)

    def test_totalTaskNumber_bool_raises(self) -> None:
        """totalTaskNumber as bool raises MalformedPayloadError."""
        payload = [
            {
                "metaId": "taskList/1",
                "name": "Test",
                "taskListType": "SHOPPING_LIST",
                "totalTaskNumber": True,
                "remainingTaskNumber": 10,
            }
        ]
        with pytest.raises(MalformedPayloadError):
            parse_list_summaries(payload)

    def test_missing_required_fields(self) -> None:
        """Missing required fields raises MalformedPayloadError."""
        # Missing metaId
        with pytest.raises(MalformedPayloadError):
            parse_list_summaries([{"name": "Test", "taskListType": "SHOPPING_LIST"}])

        # Missing name
        with pytest.raises(MalformedPayloadError):
            parse_list_summaries([{"metaId": "taskList/1", "taskListType": "SHOPPING_LIST"}])

        # Missing taskListType
        with pytest.raises(MalformedPayloadError):
            parse_list_summaries([{"metaId": "taskList/1", "name": "Test"}])


class TestParseListItems:
    """Tests for parse_list_items."""

    def test_items_parse_completely(self) -> None:
        """Criterion 6: Items parse correctly."""
        payload = list_items_bare_array()
        result = parse_list_items(payload)

        assert len(result.items) == 4
        assert result.skipped == 0

    def test_complete_true_parses_to_bool_true(self) -> None:
        """Criterion 6: complete: 'true' yields True."""
        payload = [
            {
                "metaId": "task/1",
                "taskListId": "taskList/1",
                "text": "Done",
                "complete": "true",
            }
        ]
        result = parse_list_items(payload)
        assert result.items[0].completed is True

    def test_complete_false_parses_to_bool_false(self) -> None:
        """Criterion 6: complete: 'false' yields False."""
        payload = [
            {
                "metaId": "task/1",
                "taskListId": "taskList/1",
                "text": "Not done",
                "complete": "false",
            }
        ]
        result = parse_list_items(payload)
        assert result.items[0].completed is False

    def test_complete_invalid_value_raises(self) -> None:
        """Criterion 6: Unrecognised boolean value raises."""
        payload = [
            {
                "metaId": "task/1",
                "taskListId": "taskList/1",
                "text": "Test",
                "complete": "yes",
            }
        ]
        result = parse_list_items(payload)
        assert result.skipped == 1
        assert len(result.items) == 0

    def test_list_id_comes_from_taskListId(self) -> None:
        """Criterion 7: list_id on an item comes from taskListId keeping prefix."""
        payload = list_items_bare_array()
        result = parse_list_items(payload)
        assert result.items[0].list_id == "taskList/101"

    def test_unicode_text_roundtrips(self) -> None:
        """Criterion 8: Unicode item text round-trips unchanged."""
        payload = list_items_bare_array()
        result = parse_list_items(payload)
        # Find the item with Unicode
        unicode_item = next(item for item in result.items if "☕" in item.text)
        assert unicode_item.text == "Café ☕"

    def test_malformed_item_skipped_others_returned(self) -> None:
        """Criterion 9: One malformed item among four is skipped, others returned."""
        payload = list_items_with_malformed_entry()
        result = parse_list_items(payload)

        assert len(result.items) == 3  # Should have first 3 valid items (before malformed)
        assert result.skipped == 1

    def test_wrapped_items_key_parses(self) -> None:
        """Items wrapped under 'items' key parse."""
        payload = list_items_wrapped_items()
        result = parse_list_items(payload)
        assert len(result.items) == 4
        assert result.skipped == 0

    def test_wrapped_tasks_key_parses(self) -> None:
        """Items wrapped under 'tasks' key parse."""
        payload = list_items_wrapped_tasks()
        result = parse_list_items(payload)
        assert len(result.items) == 4
        assert result.skipped == 0

    def test_wrapped_listItems_key_parses(self) -> None:
        """Items wrapped under 'listItems' key parse."""
        payload = list_items_wrapped_listItems()
        result = parse_list_items(payload)
        assert len(result.items) == 4
        assert result.skipped == 0

    def test_unparseable_date_yields_none(self) -> None:
        """Criterion 10: Unparseable creationDate yields None and doesn't raise."""
        payload = [
            {
                "metaId": "task/1",
                "taskListId": "taskList/1",
                "text": "Test",
                "complete": "false",
                "creationDate": "invalid-date",
            }
        ]
        result = parse_list_items(payload)
        assert result.skipped == 0
        assert result.items[0].created_at is None

    def test_valid_iso8601_date_parses(self) -> None:
        """Valid ISO-8601 date with Z parses to UTC datetime."""
        payload = [
            {
                "metaId": "task/1",
                "taskListId": "taskList/1",
                "text": "Test",
                "complete": "false",
                "creationDate": "2026-09-13T13:34:16.302Z",
            }
        ]
        result = parse_list_items(payload)
        assert result.items[0].created_at is not None
        assert result.items[0].created_at.year == 2026
        assert result.items[0].created_at.month == 9
        assert result.items[0].created_at.day == 13
        assert result.items[0].created_at.tzinfo == UTC

    def test_item_with_wrong_metaId_prefix_skipped(self) -> None:
        """Item with metaId: 'taskList/1' (wrong prefix) is skipped."""
        payload = [
            {
                "metaId": "taskList/1",
                "taskListId": "taskList/101",
                "text": "Test",
                "complete": "false",
            }
        ]
        result = parse_list_items(payload)
        assert result.skipped == 1
        assert len(result.items) == 0

    def test_item_with_wrong_taskListId_prefix_skipped(self) -> None:
        """Item with taskListId without taskList/ prefix is skipped."""
        payload = [
            {
                "metaId": "task/1",
                "taskListId": "task/101",  # Wrong prefix
                "text": "Test",
                "complete": "false",
            }
        ]
        result = parse_list_items(payload)
        assert result.skipped == 1
        assert len(result.items) == 0

    def test_missing_required_item_fields(self) -> None:
        """Missing required item fields skipped."""
        # Missing metaId
        payload = [{"taskListId": "taskList/1", "text": "Test", "complete": "false"}]
        result = parse_list_items(payload)
        assert result.skipped == 1

        # Missing taskListId
        payload = [{"metaId": "task/1", "text": "Test", "complete": "false"}]
        result = parse_list_items(payload)
        assert result.skipped == 1

        # Missing text
        payload = [{"metaId": "task/1", "taskListId": "taskList/1", "complete": "false"}]
        result = parse_list_items(payload)
        assert result.skipped == 1

    def test_non_array_payload_raises(self) -> None:
        """Non-array, non-wrapper payload raises MalformedPayloadError."""
        with pytest.raises(MalformedPayloadError):
            parse_list_items("not an array")

        with pytest.raises(MalformedPayloadError):
            parse_list_items({"invalid_key": []})


class TestRequestFieldBuilders:
    """Tests for request field builder functions."""

    def test_build_get_lists_fields(self) -> None:
        """build_get_lists_fields returns correct structure."""
        fields = build_get_lists_fields()
        assert fields == {"partnerScope": "Family"}

    def test_build_get_list_fields_valid(self) -> None:
        """Criterion 13: build_get_list_fields with valid prefix."""
        fields = build_get_list_fields("taskList/101")
        assert fields == {"partnerScope": "Family", "a00listId": "taskList/101"}

    def test_build_get_list_fields_invalid_prefix(self) -> None:
        """Criterion 13: build_get_list_fields rejects wrong prefix."""
        with pytest.raises(MalformedPayloadError):
            build_get_list_fields("task/101")

        with pytest.raises(MalformedPayloadError):
            build_get_list_fields("invalid/101")

    def test_build_create_item_fields_minimal(self) -> None:
        """Criterion 1: build_create_item_fields emits exactly partnerScope and a00text."""
        fields = build_create_item_fields("Milk")
        assert fields == {
            "partnerScope": "Family",
            "a00text": "Milk",
        }
        assert len(fields) == 2
        assert "a00quantity" not in fields
        assert "a00taskListId" not in fields
        assert "a00listId" not in fields

    def test_build_create_item_fields_no_list_id_parameter(self) -> None:
        """Criterion 1: build_create_item_fields no longer accepts list_id."""
        # This should raise TypeError at runtime since list_id is not a parameter
        with pytest.raises(TypeError):
            build_create_item_fields("taskList/101", "Bread")  # type: ignore

    def test_build_create_item_fields_no_quantity_parameter(self) -> None:
        """Criterion 1: build_create_item_fields no longer accepts quantity."""
        # This should raise TypeError since quantity is not a parameter
        with pytest.raises(TypeError):
            build_create_item_fields("Bread", None)  # type: ignore

    def test_build_create_item_fields_unicode_text(self) -> None:
        """build_create_item_fields preserves Unicode in text."""
        fields = build_create_item_fields("Café ☕")
        assert fields["a00text"] == "Café ☕"

    def test_build_move_item_fields_valid(self) -> None:
        """Criterion 2: build_move_item_fields emits exactly three fields."""
        from familywall_mcp.familywall.lists import build_move_item_fields

        fields = build_move_item_fields("task/201", "taskList/101")
        assert fields == {
            "partnerScope": "Family",
            "a00taskId": "task/201",
            "a00taskListId": "taskList/101",
        }
        assert len(fields) == 3

    def test_build_move_item_fields_validates_item_prefix(self) -> None:
        """Criterion 2: build_move_item_fields rejects wrong item prefix."""
        from familywall_mcp.familywall.lists import build_move_item_fields

        with pytest.raises(MalformedPayloadError):
            build_move_item_fields("taskList/201", "taskList/101")

        with pytest.raises(MalformedPayloadError):
            build_move_item_fields("invalid/201", "taskList/101")

    def test_build_move_item_fields_validates_list_prefix(self) -> None:
        """Criterion 2: build_move_item_fields rejects wrong list prefix."""
        from familywall_mcp.familywall.lists import build_move_item_fields

        with pytest.raises(MalformedPayloadError):
            build_move_item_fields("task/201", "task/101")

        with pytest.raises(MalformedPayloadError):
            build_move_item_fields("task/201", "invalid/101")

    def test_build_mark_item_fields_completed_true(self) -> None:
        """Criterion 12: build_mark_item_fields with completed=True."""
        fields = build_mark_item_fields("task/201", True)
        assert fields == {
            "partnerScope": "Family",
            "a00taskId": "task/201",
            "a00complete": "true",
        }

    def test_build_mark_item_fields_completed_false(self) -> None:
        """Criterion 12: build_mark_item_fields with completed=False produces 'false'."""
        fields = build_mark_item_fields("task/201", False)
        assert fields == {
            "partnerScope": "Family",
            "a00taskId": "task/201",
            "a00complete": "false",
        }

    def test_build_mark_item_fields_no_list_id(self) -> None:
        """Criterion 12: build_mark_item_fields field set contains no list ID."""
        fields = build_mark_item_fields("task/201", True)
        assert "a00listId" not in fields
        assert "a00taskListId" not in fields
        assert "taskList" not in str(fields)

    def test_build_mark_item_fields_wrong_prefix(self) -> None:
        """build_mark_item_fields rejects wrong prefix."""
        with pytest.raises(MalformedPayloadError):
            build_mark_item_fields("taskList/201", True)


class TestModelStructure:
    """Tests for model structure and constraints."""

    def test_shopping_list_is_frozen(self) -> None:
        """ShoppingList model is frozen (immutable)."""
        list_obj = ShoppingList(
            list_id="taskList/1",
            name="Test",
            type_raw="SHOPPING_LIST",
            known_type=ListType.SHOPPING,
            total_items=5,
            remaining_items=2,
            color="#FF0000",
            system_id=None,
        )
        with pytest.raises(ValidationError):
            list_obj.name = "Modified"  # type: ignore

    def test_list_item_is_frozen(self) -> None:
        """ListItem model is frozen (immutable)."""
        item = ListItem(
            item_id="task/1",
            list_id="taskList/1",
            text="Test",
            completed=False,
            description=None,
            author_account_id=None,
            category_names=(),
            created_at=None,
            completed_at=None,
        )
        with pytest.raises(ValidationError):
            item.text = "Modified"  # type: ignore

    def test_shopping_list_forbids_extra_fields(self) -> None:
        """ShoppingList rejects extra fields (extra='forbid')."""
        with pytest.raises(ValidationError):
            ShoppingList(
                list_id="taskList/1",
                name="Test",
                type_raw="SHOPPING_LIST",
                known_type=ListType.SHOPPING,
                total_items=5,
                remaining_items=2,
                color="#FF0000",
                system_id=None,
                extra_field="should fail",  # type: ignore
            )

    def test_list_item_forbids_extra_fields(self) -> None:
        """ListItem rejects extra fields (extra='forbid')."""
        with pytest.raises(ValidationError):
            ListItem(
                item_id="task/1",
                list_id="taskList/1",
                text="Test",
                completed=False,
                description=None,
                author_account_id=None,
                category_names=(),
                created_at=None,
                completed_at=None,
                extra_field="should fail",  # type: ignore
            )

    def test_shopping_list_requires_list_id(self) -> None:
        """ShoppingList requires non-empty list_id."""
        with pytest.raises(ValidationError):
            ShoppingList(
                list_id="",
                name="Test",
                type_raw="SHOPPING_LIST",
                known_type=ListType.SHOPPING,
                total_items=5,
                remaining_items=2,
                color="#FF0000",
                system_id=None,
            )

    def test_list_item_requires_text(self) -> None:
        """ListItem requires non-empty text."""
        with pytest.raises(ValidationError):
            ListItem(
                item_id="task/1",
                list_id="taskList/1",
                text="",
                completed=False,
                description=None,
                author_account_id=None,
                category_names=(),
                created_at=None,
                completed_at=None,
            )


class TestParsedItemsResult:
    """Tests for ParsedItems result type."""

    def test_parsed_items_is_named_tuple(self) -> None:
        """ParsedItems is a NamedTuple with items and skipped."""
        result = ParsedItems(items=tuple(), skipped=0)
        assert result.items == tuple()
        assert result.skipped == 0
        assert hasattr(result, "items")
        assert hasattr(result, "skipped")


class TestAssignmentFields:
    """T16: assignee_ids and to_all parse; absent fields default; each malformed
    case skips the item."""

    def test_t16_assignment_fields_parse(self) -> None:
        """Present assigneeIds and toAll both parse onto the item."""
        payload = [list_item_with_assignment_fields()]
        result = parse_list_items(payload)

        assert result.skipped == 0
        item = result.items[0]
        assert item.assignee_ids == ("acc-alice", "acc-bob")
        assert item.to_all is False

    def test_t16_absent_assignment_fields_give_defaults(self) -> None:
        """An item with neither field defaults to () / None."""
        payload = [list_item_without_assignment_fields()]
        result = parse_list_items(payload)

        assert result.skipped == 0
        item = result.items[0]
        assert item.assignee_ids == ()
        assert item.to_all is None

    def test_t16_malformed_assignee_ids_skips_item(self) -> None:
        """assigneeIds containing a non-string entry skips the item."""
        payload = [list_item_with_malformed_assignee_ids()]
        result = parse_list_items(payload)

        assert len(result.items) == 0
        assert result.skipped == 1

    def test_t16_malformed_to_all_skips_item(self) -> None:
        """toAll: "maybe" skips the item."""
        payload = [list_item_with_malformed_to_all()]
        result = parse_list_items(payload)

        assert len(result.items) == 0
        assert result.skipped == 1


class TestCreate2AndUpdate2Builders:
    """F1: build_create2_item_fields and build_update2_assignees_fields."""

    def test_f1_create2_complete_form_for_everyone(self) -> None:
        """The complete taskcreate2 form for everyone, fields in the given order."""
        fields = build_create2_item_fields(
            list_id="taskList/101",
            text="Bread",
            to_all=True,
            assignee_account_ids=("acc-alex", "acc-robin"),
        )
        assert fields == {
            "partnerScope": "Family",
            "taskListId": "taskList/101",
            "text": "Bread",
            "taskCategoryId": "",
            "dueDate": "$empty",
            "picture": "$empty",
            "toAll": "true",
            "assignee.0": "acc-alex",
            "assignee.1": "acc-robin",
        }
        assert list(fields.keys()) == [
            "partnerScope",
            "taskListId",
            "text",
            "taskCategoryId",
            "dueDate",
            "picture",
            "toAll",
            "assignee.0",
            "assignee.1",
        ]

    def test_f1_create2_complete_form_for_named_members(self) -> None:
        """The complete taskcreate2 form for a single named member."""
        fields = build_create2_item_fields(
            list_id="taskList/101",
            text="Bread",
            to_all=False,
            assignee_account_ids=("acc-sam",),
        )
        assert fields == {
            "partnerScope": "Family",
            "taskListId": "taskList/101",
            "text": "Bread",
            "taskCategoryId": "",
            "dueDate": "$empty",
            "picture": "$empty",
            "toAll": "false",
            "assignee.0": "acc-sam",
        }

    def test_f1_create2_rejects_empty_assignees(self) -> None:
        """build_create2_item_fields rejects an empty ID list, for both to_all values."""
        with pytest.raises(ValueError, match="assignee_account_ids"):
            build_create2_item_fields(
                list_id="taskList/101", text="Bread", to_all=True, assignee_account_ids=()
            )
        with pytest.raises(ValueError, match="assignee_account_ids"):
            build_create2_item_fields(
                list_id="taskList/101", text="Bread", to_all=False, assignee_account_ids=()
            )

    def test_f1_create2_rejects_wrong_list_prefix(self) -> None:
        """build_create2_item_fields rejects a list_id without the taskList/ prefix."""
        with pytest.raises(MalformedPayloadError):
            build_create2_item_fields(
                list_id="task/101", text="Bread", to_all=True, assignee_account_ids=("acc-sam",)
            )

    def test_f1_create2_never_emits_a_name(self) -> None:
        """No display name ever appears in the taskcreate2 form."""
        fields = build_create2_item_fields(
            list_id="taskList/101",
            text="Bread",
            to_all=False,
            assignee_account_ids=("acc-jordan",),
        )
        assert "Jordan" not in fields.values()

    def test_f1_update2_complete_form_for_everyone(self) -> None:
        """The complete partial taskupdate2 form for everyone."""
        fields = build_update2_assignees_fields(
            item_id="task/201", to_all=True, assignee_account_ids=("acc-alex", "acc-robin")
        )
        assert fields == {
            "partnerScope": "Family",
            "taskId": "task/201",
            "toAll": "true",
            "assignee.0": "acc-alex",
            "assignee.1": "acc-robin",
        }
        assert list(fields.keys()) == [
            "partnerScope",
            "taskId",
            "toAll",
            "assignee.0",
            "assignee.1",
        ]

    def test_f1_update2_complete_form_for_named_members(self) -> None:
        """The complete partial taskupdate2 form for a single named member."""
        fields = build_update2_assignees_fields(
            item_id="task/201", to_all=False, assignee_account_ids=("acc-sam",)
        )
        assert fields == {
            "partnerScope": "Family",
            "taskId": "task/201",
            "toAll": "false",
            "assignee.0": "acc-sam",
        }

    def test_f1_update2_rejects_empty_assignees(self) -> None:
        """build_update2_assignees_fields rejects an empty ID list, for both to_all values."""
        with pytest.raises(ValueError, match="assignee_account_ids"):
            build_update2_assignees_fields(item_id="task/201", to_all=True, assignee_account_ids=())
        with pytest.raises(ValueError, match="assignee_account_ids"):
            build_update2_assignees_fields(
                item_id="task/201", to_all=False, assignee_account_ids=()
            )

    def test_f1_update2_rejects_wrong_item_prefix(self) -> None:
        """build_update2_assignees_fields rejects an item_id without the task/ prefix."""
        with pytest.raises(MalformedPayloadError):
            build_update2_assignees_fields(
                item_id="taskList/201", to_all=True, assignee_account_ids=("acc-sam",)
            )

    def test_f1_update2_never_emits_a_name(self) -> None:
        """No display name ever appears in the taskupdate2 form."""
        fields = build_update2_assignees_fields(
            item_id="task/201", to_all=False, assignee_account_ids=("acc-jordan",)
        )
        assert "Jordan" not in fields.values()


class TestDueDateAndReminderFields:
    """F12: ListItem.due_date/reminder parse verbatim; absent or malformed gives
    None, and the item is never skipped."""

    def test_f12_due_date_and_reminder_parse_verbatim(self) -> None:
        """Present dueDate and a full reminder object both parse onto the item."""
        payload = [list_item_with_due_date_and_reminder()]
        result = parse_list_items(payload)

        assert result.skipped == 0
        item = result.items[0]
        assert item.due_date == "2026-10-01T09:00:00.000Z"
        assert item.reminder == ("SNOOZE", "MINUTE", "30")

    def test_f12_absent_due_date_and_reminder_give_none(self) -> None:
        """An item with neither field defaults to None / None, and is not skipped."""
        payload = [list_item_without_assignment_fields()]
        result = parse_list_items(payload)

        assert result.skipped == 0
        item = result.items[0]
        assert item.due_date is None
        assert item.reminder is None

    def test_f12_malformed_due_date_gives_none_not_skipped(self) -> None:
        """dueDate as a non-string gives None, and the item is not skipped."""
        payload = [list_item_with_malformed_due_date()]
        result = parse_list_items(payload)

        assert result.skipped == 0
        assert len(result.items) == 1
        assert result.items[0].due_date is None

    def test_f12_malformed_reminder_gives_none_not_skipped(self) -> None:
        """A reminder object missing a required field gives None, and the item
        is not skipped."""
        payload = [list_item_with_malformed_reminder()]
        result = parse_list_items(payload)

        assert result.skipped == 0
        assert len(result.items) == 1
        assert result.items[0].reminder is None
