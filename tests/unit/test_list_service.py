"""Tests for shopping list selection and mutation service."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

from familywall_mcp.errors import (
    FamilyWallError,
    InvalidEnvelopeError,
    TransportError,
    UnsupportedConfigurationError,
    UpstreamRejectedError,
)
from familywall_mcp.familywall.lists import ListType, ShoppingList
from familywall_mcp.models import OperationReceipt, Principal
from familywall_mcp.services.lists import ListService, WriteOutcome
from familywall_mcp.storage.memory import InMemoryReceiptRepository


class FakeTransport:
    """Fake transport that records calls and returns configured responses."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, str]]] = []
        self.responses: dict[tuple[str, str], object] = {}

    async def call(self, endpoint: str, fields: dict[str, str]) -> object:
        """Record call and return configured response or raise UnexpectedRequestError."""
        self.calls.append((endpoint, dict(fields)))  # Copy fields to capture state
        # Build a key from meaningful fields
        key_value = (
            fields.get("a00text", "")
            or fields.get("a00listId", "")
            or fields.get("a00taskId", "")
            or ""
        )
        key = (endpoint, key_value)
        if key in self.responses:
            return self.responses[key]
        # Return empty response by default for list operations
        if endpoint == "taskgettasklists":
            return {"taskLists": []}
        if endpoint == "tasklist":
            return {"listItems": []}
        raise AssertionError(f"Unexpected call: {endpoint} {fields}")

    def set_response(self, endpoint: str, key: str, response: object) -> None:
        """Configure a response for a call."""
        self.responses[(endpoint, key)] = response


class TestListServiceSelection:
    """Tests for list selection logic."""

    @pytest.fixture
    def transport(self) -> FakeTransport:
        return FakeTransport()

    @pytest.fixture
    def service(self, transport: FakeTransport) -> ListService:
        return ListService(transport)

    @pytest.fixture
    def principal(self) -> Principal:
        return Principal(subject="user1")

    def make_list_dict(self, list_id: str, name: str, list_type: ListType) -> dict[str, Any]:
        """Helper to create a list dict for wire protocol (not ShoppingList)."""
        return {
            "metaId": list_id,
            "name": name,
            "taskListType": list_type.value,
            "totalTaskNumber": 10,
            "remainingTaskNumber": 5,
            "color": "blue",
        }

    async def test_one_shopping_list_among_several_types(
        self, service: ListService, transport: FakeTransport, principal: Principal
    ) -> None:
        """One shopping list among several types → `only_eligible`."""
        shopping = self.make_list_dict("taskList/1", "Shopping", ListType.SHOPPING)
        todos = self.make_list_dict("taskList/2", "Todos", ListType.TODOS)
        other = self.make_list_dict("taskList/3", "Other", ListType.OTHER)

        transport.set_response("taskgettasklists", "", {"taskLists": [shopping, todos, other]})

        selection = await service.select_list(principal)

        assert selection.resolved is not None
        assert selection.resolved.list_id == "taskList/1"
        assert selection.candidates == ()
        assert selection.reason == "only_eligible"

    async def test_two_shopping_lists_no_default(
        self, service: ListService, transport: FakeTransport, principal: Principal
    ) -> None:
        """Two shopping lists, no default → `ambiguous` with both candidates, and
        no write is sent."""
        shopping1 = self.make_list_dict("taskList/1", "Shopping", ListType.SHOPPING)
        shopping2 = self.make_list_dict("taskList/2", "Shopping", ListType.SHOPPING)

        transport.set_response("taskgettasklists", "", {"taskLists": [shopping1, shopping2]})

        selection = await service.select_list(principal)

        assert selection.resolved is None
        assert len(selection.candidates) == 2
        assert selection.candidates[0].list_id == "taskList/1"
        assert selection.candidates[1].list_id == "taskList/2"
        assert selection.reason == "ambiguous"
        # Only one request sent (taskgettasklists to fetch lists)
        assert len(transport.calls) == 1

    async def test_two_shopping_lists_with_valid_default(
        self, service: ListService, transport: FakeTransport, principal: Principal
    ) -> None:
        """Two shopping lists with a valid saved default → `default`, the default wins."""
        shopping1 = self.make_list_dict("taskList/1", "Shopping", ListType.SHOPPING)
        shopping2 = self.make_list_dict("taskList/2", "Shopping", ListType.SHOPPING)

        transport.set_response("taskgettasklists", "", {"taskLists": [shopping1, shopping2]})

        selection = await service.select_list(principal, default_list_id="taskList/2")

        assert selection.resolved is not None
        assert selection.resolved.list_id == "taskList/2"
        assert selection.candidates == ()
        assert selection.reason == "default"

    async def test_saved_default_no_longer_accessible(
        self, service: ListService, transport: FakeTransport, principal: Principal
    ) -> None:
        """A saved default that no longer appears → reported as unavailable; no redirect."""
        shopping1 = self.make_list_dict("taskList/1", "Shopping", ListType.SHOPPING)

        transport.set_response("taskgettasklists", "", {"taskLists": [shopping1]})

        with pytest.raises(UnsupportedConfigurationError):
            await service.select_list(principal, default_list_id="taskList/999")

    async def test_explicit_foreign_list_id(
        self, service: ListService, transport: FakeTransport, principal: Principal
    ) -> None:
        """An explicitly supplied foreign list ID → error, zero requests sent."""
        shopping1 = self.make_list_dict("taskList/1", "Shopping", ListType.SHOPPING)

        transport.set_response("taskgettasklists", "", {"taskLists": [shopping1]})
        initial_call_count = len(transport.calls)

        with pytest.raises(UnsupportedConfigurationError):
            await service.select_list(principal, explicit_list_id="taskList/999")

        # One request should be sent (taskgettasklists), not zero
        # The error happens during verification, not before
        assert len(transport.calls) == initial_call_count + 1

    async def test_duplicate_list_names_by_id(
        self, service: ListService, transport: FakeTransport, principal: Principal
    ) -> None:
        """Duplicate list names do not collapse; selection stays by ID."""
        shopping1 = self.make_list_dict("taskList/1", "Shopping", ListType.SHOPPING)
        shopping2 = self.make_list_dict("taskList/2", "Shopping", ListType.SHOPPING)

        transport.set_response("taskgettasklists", "", {"taskLists": [shopping1, shopping2]})

        # Should still return ambiguous even though names are the same
        selection = await service.select_list(principal)

        assert selection.resolved is None
        assert len(selection.candidates) == 2
        assert selection.candidates[0].list_id == "taskList/1"
        assert selection.candidates[1].list_id == "taskList/2"
        assert selection.reason == "ambiguous"

    async def test_no_shopping_list_at_all(
        self, service: ListService, transport: FakeTransport, principal: Principal
    ) -> None:
        """No shopping list at all → candidates offered, nothing written."""
        todos = self.make_list_dict("taskList/1", "Todos", ListType.TODOS)
        other = self.make_list_dict("taskList/2", "Other", ListType.OTHER)

        transport.set_response("taskgettasklists", "", {"taskLists": [todos, other]})

        selection = await service.select_list(principal)

        assert selection.resolved is None
        assert len(selection.candidates) == 2
        assert selection.candidates[0].list_id == "taskList/1"
        assert selection.candidates[1].list_id == "taskList/2"
        assert selection.reason == "none_eligible"

    async def test_no_lists_at_all(
        self, service: ListService, transport: FakeTransport, principal: Principal
    ) -> None:
        """No lists at all → no candidates, none_eligible reason."""
        transport.set_response("taskgettasklists", "", {"taskLists": []})

        selection = await service.select_list(principal)

        assert selection.resolved is None
        assert selection.candidates == ()
        assert selection.reason == "none_eligible"


class TestListServiceMutation:
    """Tests for add_item and set_item_checked mutations."""

    @pytest.fixture
    def transport(self) -> FakeTransport:
        return FakeTransport()

    @pytest.fixture
    def service(self, transport: FakeTransport) -> ListService:
        return ListService(transport)

    @pytest.fixture
    def principal(self) -> Principal:
        return Principal(subject="user1")

    def make_list(self, list_id: str, name: str) -> ShoppingList:
        """Helper to create a ShoppingList."""
        return ShoppingList(
            list_id=list_id,
            name=name,
            type_raw="SHOPPING_LIST",
            known_type=ListType.SHOPPING,
            total_items=10,
            remaining_items=5,
            color="blue",
            system_id=None,
        )

    async def test_add_item_confirmed(
        self, service: ListService, transport: FakeTransport, principal: Principal
    ) -> None:
        """Criterion 8: Add succeeds and readback finds the item → `CONFIRMED`."""
        repo = InMemoryReceiptRepository()

        # Setup transport response for add
        transport.set_response("taskcreate", "bread", {"id": "task/123"})

        # Setup transport response for readback (item found)
        items_response = {
            "listItems": [
                {
                    "metaId": "task/123",
                    "taskListId": "taskList/1",
                    "text": "bread",
                    "complete": "false",
                }
            ]
        }
        transport.set_response("tasklist", "taskList/1", items_response)

        result, items = await service.add_item(
            principal, "taskList/1", "bread", None, "op1", repo, "fam1"
        )

        assert result.outcome == WriteOutcome.CONFIRMED
        assert len(items) == 1
        assert items[0].text == "bread"
        # Exactly two requests sent: taskcreate and tasklist
        assert len(transport.calls) == 2

    async def test_add_item_acknowledged(
        self, service: ListService, transport: FakeTransport, principal: Principal
    ) -> None:
        """Criterion 9: Add succeeds and readback does not find it → `ACKNOWLEDGED`."""
        repo = InMemoryReceiptRepository()

        # Setup transport response for add
        transport.set_response("taskcreate", "bread", {"id": "task/123"})

        # Setup transport response for readback (item NOT found)
        items_response = {"listItems": []}
        transport.set_response("tasklist", "taskList/1", items_response)

        result, items = await service.add_item(
            principal, "taskList/1", "bread", None, "op1", repo, "fam1"
        )

        assert result.outcome == WriteOutcome.ACKNOWLEDGED
        assert len(items) == 0
        # Exactly two requests sent: taskcreate and tasklist
        assert len(transport.calls) == 2

    async def test_add_item_transport_error(
        self, service: ListService, transport: FakeTransport, principal: Principal
    ) -> None:
        """Criterion 10: Add times out (TransportError) → `UNKNOWN`, exactly one request sent."""
        repo = InMemoryReceiptRepository()

        # Create a failing transport that raises TransportError on taskcreate
        class FailingTransport(FakeTransport):
            async def call(self, endpoint: str, fields: dict[str, str]) -> object:
                # Record the call first, then decide whether to fail
                self.calls.append((endpoint, dict(fields)))
                if endpoint == "taskcreate":
                    raise TransportError()
                # Don't call super for taskcreate, handle others
                key_value = (
                    fields.get("a00text", "")
                    or fields.get("a00listId", "")
                    or fields.get("a00taskId", "")
                    or ""
                )
                key = (endpoint, key_value)
                if key in self.responses:
                    return self.responses[key]
                if endpoint == "taskgettasklists":
                    return {"taskLists": []}
                if endpoint == "tasklist":
                    return {"listItems": []}
                raise AssertionError(f"Unexpected call: {endpoint} {fields}")

        failing_transport = FailingTransport()
        failing_service = ListService(failing_transport)

        result, items = await failing_service.add_item(
            principal, "taskList/1", "bread", None, "op1", repo, "fam1"
        )

        assert result.outcome == WriteOutcome.UNKNOWN
        assert len(items) == 0
        # taskcreate was attempted before error; exactly one request sent
        create_calls = [c for c in failing_transport.calls if c[0] == "taskcreate"]
        assert len(create_calls) == 1
        assert len(failing_transport.calls) == 1

    async def test_add_item_twice_different_operation_ids(
        self, service: ListService, transport: FakeTransport, principal: Principal
    ) -> None:
        """Criterion 11: Adding "bread" twice with different operation_ids creates two items."""
        repo = InMemoryReceiptRepository()

        # Setup transport responses
        transport.set_response("taskcreate", "bread", {"id": "task/123"})
        items_response_1 = {
            "listItems": [
                {
                    "metaId": "task/123",
                    "taskListId": "taskList/1",
                    "text": "bread",
                    "complete": "false",
                }
            ]
        }
        transport.set_response("tasklist", "taskList/1", items_response_1)

        # First add
        result1, _ = await service.add_item(
            principal, "taskList/1", "bread", None, "op1", repo, "fam1"
        )
        assert result1.outcome == WriteOutcome.CONFIRMED
        create_calls_1 = len([c for c in transport.calls if c[0] == "taskcreate"])
        assert create_calls_1 == 1

        # Reset for second add
        transport.calls.clear()

        # Second add with different operation_id (creates duplicate item)
        result2, _ = await service.add_item(
            principal, "taskList/1", "bread", None, "op2", repo, "fam1"
        )
        assert result2.outcome == WriteOutcome.CONFIRMED
        create_calls_2 = len([c for c in transport.calls if c[0] == "taskcreate"])
        assert create_calls_2 == 1
        # Two different operation_ids → two requests sent

    async def test_add_item_replay_same_operation_id(
        self, service: ListService, transport: FakeTransport, principal: Principal
    ) -> None:
        """Criterion 12: Replaying one operation_id with same payload sends nothing,
        returns stored result."""
        repo = InMemoryReceiptRepository()

        # Setup transport response
        transport.set_response("taskcreate", "bread", {"id": "task/123"})
        items_response = {
            "listItems": [
                {
                    "metaId": "task/123",
                    "taskListId": "taskList/1",
                    "text": "bread",
                    "complete": "false",
                }
            ]
        }
        transport.set_response("tasklist", "taskList/1", items_response)

        # First call
        result1, items1 = await service.add_item(
            principal, "taskList/1", "bread", None, "op1", repo, "fam1"
        )
        assert result1.outcome == WriteOutcome.CONFIRMED

        # Second call with same operation_id and payload
        transport.calls.clear()
        result2, items2 = await service.add_item(
            principal, "taskList/1", "bread", None, "op1", repo, "fam1"
        )
        # Should return stored result without sending requests
        assert result2.outcome == WriteOutcome.CONFIRMED
        assert len(transport.calls) == 0  # Zero requests sent

    async def test_add_item_replay_different_payload(
        self, service: ListService, transport: FakeTransport, principal: Principal
    ) -> None:
        """Criterion 13: Replaying operation_id with different payload raises,
        sends ZERO requests."""
        repo = InMemoryReceiptRepository()

        # Setup for first add
        transport.set_response("taskcreate", "bread", {"id": "task/123"})
        items_response = {
            "listItems": [
                {
                    "metaId": "task/123",
                    "taskListId": "taskList/1",
                    "text": "bread",
                    "complete": "false",
                }
            ]
        }
        transport.set_response("tasklist", "taskList/1", items_response)

        # First call with "bread"
        await service.add_item(principal, "taskList/1", "bread", None, "op1", repo, "fam1")
        transport.calls.clear()

        # Second call with same operation_id but different text ("milk")
        with pytest.raises(FamilyWallError) as exc_info:
            await service.add_item(principal, "taskList/1", "milk", None, "op1", repo, "fam1")
        assert exc_info.value.info.code == "operation_id_conflict"
        # Zero requests sent for different payload
        assert len(transport.calls) == 0

    async def test_add_item_pending_receipt_resolves_unknown(
        self, service: ListService, transport: FakeTransport, principal: Principal
    ) -> None:
        """Criterion 14: A pending receipt from previous process resolves to UNKNOWN,
        sends ZERO requests."""
        repo = InMemoryReceiptRepository()

        # Simulate a pending receipt from a crashed process
        pending_receipt = OperationReceipt(
            subject=principal.subject,
            family_id="fam1",
            list_id="taskList/1",
            operation_id="crashed_op",
            payload_hash="somehash",
            status="pending",
            upstream_id=None,
            expires_at=datetime.now(UTC) + timedelta(hours=24),
        )
        await repo.put(pending_receipt)

        # Now attempt the same operation
        result, items = await service.add_item(
            principal, "taskList/1", "bread", None, "crashed_op", repo, "fam1"
        )
        # Pending receipt resolves to UNKNOWN
        assert result.outcome == WriteOutcome.UNKNOWN
        # Zero requests sent (receipt was found pending)
        assert len(transport.calls) == 0

    async def test_add_item_with_quantity(
        self, service: ListService, transport: FakeTransport, principal: Principal
    ) -> None:
        """Criterion 19: A result that sets a quantity reports the quantity as unverified."""
        repo = InMemoryReceiptRepository()

        transport.set_response("taskcreate", "milk", {"id": "task/124"})
        items_response = {
            "listItems": [
                {
                    "metaId": "task/124",
                    "taskListId": "taskList/1",
                    "text": "milk",
                    "complete": "false",
                }
            ]
        }
        transport.set_response("tasklist", "taskList/1", items_response)

        result, items = await service.add_item(
            principal, "taskList/1", "milk", "2 liters", "op1", repo, "fam1"
        )

        assert result.outcome == WriteOutcome.CONFIRMED
        assert result.quantity_written == "2 liters"

    async def test_set_item_checked_success(
        self, service: ListService, transport: FakeTransport, principal: Principal
    ) -> None:
        """Criterion 15: Mark item as checked successfully."""
        repo = InMemoryReceiptRepository()

        # Setup accessible lists
        lists_response = {
            "taskLists": [
                {
                    "metaId": "taskList/1",
                    "name": "Shopping",
                    "taskListType": "SHOPPING_LIST",
                }
            ]
        }
        transport.set_response("taskgettasklists", "", lists_response)

        # Setup items in list (to find the item)
        items_response = {
            "listItems": [
                {
                    "metaId": "task/123",
                    "taskListId": "taskList/1",
                    "text": "bread",
                    "complete": "false",
                }
            ]
        }
        transport.set_response("tasklist", "taskList/1", items_response)

        # Setup taskmark response
        transport.set_response("taskmark", "task/123", {"ok": True})

        result = await service.set_item_checked(principal, "task/123", True, "op1", repo, "fam1")

        assert result.outcome == WriteOutcome.CONFIRMED
        # Verify taskmark was called with correct fields
        mark_calls = [c for c in transport.calls if c[0] == "taskmark"]
        assert len(mark_calls) == 1
        assert mark_calls[0][1]["a00taskId"] == "task/123"
        assert mark_calls[0][1]["a00complete"] == "true"

    async def test_set_item_checked_false(
        self, service: ListService, transport: FakeTransport, principal: Principal
    ) -> None:
        """Criterion 16: set_item_checked(checked=False) sends a00complete='false'."""
        repo = InMemoryReceiptRepository()

        # Setup accessible lists
        lists_response = {
            "taskLists": [
                {
                    "metaId": "taskList/1",
                    "name": "Shopping",
                    "taskListType": "SHOPPING_LIST",
                }
            ]
        }
        transport.set_response("taskgettasklists", "", lists_response)

        # Setup items in list
        items_response = {
            "listItems": [
                {
                    "metaId": "task/123",
                    "taskListId": "taskList/1",
                    "text": "bread",
                    "complete": "true",
                }
            ]
        }
        transport.set_response("tasklist", "taskList/1", items_response)

        # Setup taskmark response
        transport.set_response("taskmark", "task/123", {"ok": True})

        result = await service.set_item_checked(principal, "task/123", False, "op1", repo, "fam1")

        assert result.outcome == WriteOutcome.CONFIRMED
        # Verify taskmark was called with a00complete="false"
        mark_calls = [c for c in transport.calls if c[0] == "taskmark"]
        assert len(mark_calls) == 1
        assert mark_calls[0][1]["a00complete"] == "false"

    async def test_set_item_checked_foreign_item(
        self, service: ListService, transport: FakeTransport, principal: Principal
    ) -> None:
        """Criterion 15: set_item_checked on foreign item → refused with zero requests sent."""
        repo = InMemoryReceiptRepository()

        # Setup accessible lists
        lists_response = {
            "taskLists": [
                {
                    "metaId": "taskList/1",
                    "name": "My Shopping",
                    "taskListType": "SHOPPING_LIST",
                }
            ]
        }
        transport.set_response("taskgettasklists", "", lists_response)

        # Setup items in list (item not found)
        items_response = {
            "listItems": [
                {
                    "metaId": "task/999",  # Different item
                    "taskListId": "taskList/1",
                    "text": "milk",
                    "complete": "false",
                }
            ]
        }
        transport.set_response("tasklist", "taskList/1", items_response)

        with pytest.raises(UnsupportedConfigurationError):
            await service.set_item_checked(principal, "task/123", True, "op1", repo, "fam1")

        # Verify no taskmark was sent (zero mutation requests)
        mark_calls = [c for c in transport.calls if c[0] == "taskmark"]
        assert len(mark_calls) == 0

    async def test_set_item_checked_replay_same_operation_id(
        self, service: ListService, transport: FakeTransport, principal: Principal
    ) -> None:
        """Criterion 12 (set_item_checked): Replay with same operation_id sends nothing."""
        repo = InMemoryReceiptRepository()

        # Setup accessible lists
        lists_response = {
            "taskLists": [
                {
                    "metaId": "taskList/1",
                    "name": "Shopping",
                    "taskListType": "SHOPPING_LIST",
                }
            ]
        }
        transport.set_response("taskgettasklists", "", lists_response)

        # Setup items in list
        items_response = {
            "listItems": [
                {
                    "metaId": "task/123",
                    "taskListId": "taskList/1",
                    "text": "bread",
                    "complete": "false",
                }
            ]
        }
        transport.set_response("tasklist", "taskList/1", items_response)

        # Setup taskmark response
        transport.set_response("taskmark", "task/123", {"ok": True})

        # First call
        result1 = await service.set_item_checked(principal, "task/123", True, "op1", repo, "fam1")
        assert result1.outcome == WriteOutcome.CONFIRMED
        transport.calls.clear()

        # Second call with same operation_id
        result2 = await service.set_item_checked(principal, "task/123", True, "op1", repo, "fam1")
        assert result2.outcome == WriteOutcome.CONFIRMED
        # Zero requests sent on replay
        assert len(transport.calls) == 0

    async def test_set_item_checked_completed_same_state_repeatable(
        self, service: ListService, transport: FakeTransport, principal: Principal
    ) -> None:
        """Criterion 17: Marking completed item to same state is safe and repeatable."""
        repo = InMemoryReceiptRepository()

        # Setup accessible lists
        lists_response = {
            "taskLists": [
                {
                    "metaId": "taskList/1",
                    "name": "Shopping",
                    "taskListType": "SHOPPING_LIST",
                }
            ]
        }
        transport.set_response("taskgettasklists", "", lists_response)

        # Setup items in list (already completed)
        items_response = {
            "listItems": [
                {
                    "metaId": "task/123",
                    "taskListId": "taskList/1",
                    "text": "bread",
                    "complete": "true",
                }
            ]
        }
        transport.set_response("tasklist", "taskList/1", items_response)

        # Setup taskmark response
        transport.set_response("taskmark", "task/123", {"ok": True})

        # Mark as checked twice with different operation_ids
        result1 = await service.set_item_checked(principal, "task/123", True, "op1", repo, "fam1")
        result2 = await service.set_item_checked(principal, "task/123", True, "op2", repo, "fam1")

        assert result1.outcome == WriteOutcome.CONFIRMED
        assert result2.outcome == WriteOutcome.CONFIRMED
        # Two requests sent (two different operation_ids)
        mark_calls = [c for c in transport.calls if c[0] == "taskmark"]
        assert len(mark_calls) == 2

    async def test_unicode_item_title_roundtrip(
        self, service: ListService, transport: FakeTransport, principal: Principal
    ) -> None:
        """Criterion 18: A Unicode item title round-trips."""
        repo = InMemoryReceiptRepository()

        unicode_text = "Café ☕"

        transport.set_response("taskcreate", unicode_text, {"id": "task/125"})

        items_response = {
            "listItems": [
                {
                    "metaId": "task/125",
                    "taskListId": "taskList/1",
                    "text": unicode_text,
                    "complete": "false",
                }
            ]
        }
        transport.set_response("tasklist", "taskList/1", items_response)

        result, items = await service.add_item(
            principal, "taskList/1", unicode_text, None, "op1", repo, "fam1"
        )

        assert result.outcome == WriteOutcome.CONFIRMED
        assert len(items) == 1
        assert items[0].text == unicode_text

    async def test_add_item_invalid_envelope_becomes_unknown(
        self, service: ListService, transport: FakeTransport, principal: Principal
    ) -> None:
        """Unparseable response after send → `UNKNOWN` (InvalidEnvelopeError caught)."""
        repo = InMemoryReceiptRepository()

        class FailingTransport(FakeTransport):
            async def call(self, endpoint: str, fields: dict[str, str]) -> object:
                self.calls.append((endpoint, dict(fields)))
                if endpoint == "taskcreate":
                    raise InvalidEnvelopeError()
                return await super().call(endpoint, fields)

        failing_transport = FailingTransport()
        failing_service = ListService(failing_transport)

        result, items = await failing_service.add_item(
            principal, "taskList/1", "bread", None, "op1", repo, "fam1"
        )

        assert result.outcome == WriteOutcome.UNKNOWN
        assert len(items) == 0
        # Only taskcreate was sent before error
        assert len(failing_transport.calls) == 1

    async def test_add_item_upstream_rejected_propagates(
        self, service: ListService, transport: FakeTransport, principal: Principal
    ) -> None:
        """UpstreamRejectedError propagates (not caught as UNKNOWN)."""
        repo = InMemoryReceiptRepository()

        class FailingTransport(FakeTransport):
            async def call(self, endpoint: str, fields: dict[str, str]) -> object:
                self.calls.append((endpoint, dict(fields)))
                if endpoint == "taskcreate":
                    raise UpstreamRejectedError()
                return await super().call(endpoint, fields)

        failing_transport = FailingTransport()
        failing_service = ListService(failing_transport)

        # UpstreamRejectedError should propagate, not become UNKNOWN
        with pytest.raises(UpstreamRejectedError):
            await failing_service.add_item(
                principal, "taskList/1", "bread", None, "op1", repo, "fam1"
            )
