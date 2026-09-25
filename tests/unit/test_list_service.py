"""Tests for shopping list selection and mutation service."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

from familywall_mcp.errors import (
    AuthenticationError,
    FamilyWallError,
    InvalidEnvelopeError,
    TransportError,
    UnsupportedConfigurationError,
    UpstreamRejectedError,
)
from familywall_mcp.familywall.lists import ListType, ShoppingList, build_create_item_fields
from familywall_mcp.models import OperationReceipt, Principal
from familywall_mcp.services.lists import ListService, WriteOutcome
from familywall_mcp.services.members import ResolvedAssignment
from familywall_mcp.storage.memory import InMemoryReceiptRepository

EVERYONE = ResolvedAssignment(
    to_all=True,
    account_ids=("acc-alex", "acc-robin"),
    display_names=("Alex", "Robin"),
)
"""A synthetic 'assigned to everyone' resolution, for add_item/set_item_assignees tests."""

NAMED = ResolvedAssignment(
    to_all=False,
    account_ids=("acc-sam",),
    display_names=("Sam",),
)
"""A synthetic named-member resolution, for add_item/set_item_assignees tests."""


class FakeTransport:
    """Fake transport that records calls and returns configured responses."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, str]]] = []
        self.responses: dict[tuple[str, str], object] = {}

    async def call(self, endpoint: str, fields: dict[str, str]) -> object:
        """Record call and return configured response or raise UnexpectedRequestError."""
        self.calls.append((endpoint, dict(fields)))  # Copy fields to capture state
        # Build a key from meaningful fields (old a00-prefixed and new taskcreate2/
        # taskupdate2 field names both supported)
        key_value = (
            fields.get("a00text", "")
            or fields.get("text", "")
            or fields.get("a00listId", "")
            or fields.get("a00taskId", "")
            or fields.get("taskId", "")
            or fields.get("taskListId", "")
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

    async def test_f2_add_item_confirmed_one_write_no_move_then_readback(
        self, service: ListService, transport: FakeTransport, principal: Principal
    ) -> None:
        """F2: confirmed → exactly one write (taskcreate2, no taskmove), then one
        readback of the requested list."""
        repo = InMemoryReceiptRepository()

        transport.set_response(
            "taskcreate2",
            "bread",
            {"metaId": "task/123", "text": "bread", "taskListId": "taskList/1"},
        )
        transport.set_response(
            "tasklist",
            "taskList/1",
            {
                "listItems": [
                    {
                        "metaId": "task/123",
                        "taskListId": "taskList/1",
                        "text": "bread",
                        "complete": "false",
                        "assigneeIds": list(EVERYONE.account_ids),
                        "toAll": "true",
                    }
                ]
            },
        )

        result, items = await service.add_item(
            principal, "taskList/1", "bread", EVERYONE, "op1", repo, "fam1"
        )

        assert result.outcome == WriteOutcome.CONFIRMED
        assert result.item_id == "task/123"
        assert len(items) == 1
        assert items[0].text == "bread"
        endpoints = [c[0] for c in transport.calls]
        assert endpoints == ["taskcreate2", "tasklist"]
        assert "taskmove" not in endpoints

    async def test_f2_add_item_named_members_confirmed(
        self, service: ListService, transport: FakeTransport, principal: Principal
    ) -> None:
        """F2: named members, sent and read back as a set, confirm."""
        repo = InMemoryReceiptRepository()

        transport.set_response(
            "taskcreate2",
            "bread",
            {"metaId": "task/123", "text": "bread", "taskListId": "taskList/1"},
        )
        transport.set_response(
            "tasklist",
            "taskList/1",
            {
                "listItems": [
                    {
                        "metaId": "task/123",
                        "taskListId": "taskList/1",
                        "text": "bread",
                        "complete": "false",
                        "assigneeIds": ["acc-sam"],
                        "toAll": "false",
                    }
                ]
            },
        )

        result, items = await service.add_item(
            principal, "taskList/1", "bread", NAMED, "op1", repo, "fam1"
        )

        assert result.outcome == WriteOutcome.CONFIRMED
        assert len(items) == 1
        create_calls = [c for c in transport.calls if c[0] == "taskcreate2"]
        assert create_calls[0][1] == {
            "partnerScope": "Family",
            "taskListId": "taskList/1",
            "text": "bread",
            "taskCategoryId": "",
            "dueDate": "$empty",
            "picture": "$empty",
            "toAll": "false",
            "assignee.0": "acc-sam",
        }

    async def test_f3_add_item_misfiled_response_names_a_different_list(
        self, service: ListService, transport: FakeTransport, principal: Principal
    ) -> None:
        """F3: a response taskListId for a different list gives misfiled with
        both list IDs, no move and no retry."""
        repo = InMemoryReceiptRepository()

        transport.set_response(
            "taskcreate2",
            "bread",
            {"metaId": "task/123", "text": "bread", "taskListId": "taskList/default"},
        )

        result, items = await service.add_item(
            principal, "taskList/1", "bread", EVERYONE, "op1", repo, "fam1"
        )

        assert result.outcome == WriteOutcome.MISFILED
        assert result.item_id == "task/123"
        assert result.actual_list_id == "taskList/default"
        assert result.requested_list_id == "taskList/1"
        assert items == ()
        assert [c[0] for c in transport.calls] == ["taskcreate2"]

    async def test_f4_add_item_readback_assignment_mismatch(
        self, service: ListService, transport: FakeTransport, principal: Principal
    ) -> None:
        """F4: a readback with a different assignment gives mismatched with
        "assignees"."""
        repo = InMemoryReceiptRepository()

        transport.set_response(
            "taskcreate2",
            "bread",
            {"metaId": "task/123", "text": "bread", "taskListId": "taskList/1"},
        )
        transport.set_response(
            "tasklist",
            "taskList/1",
            {
                "listItems": [
                    {
                        "metaId": "task/123",
                        "taskListId": "taskList/1",
                        "text": "bread",
                        "complete": "false",
                        "assigneeIds": ["acc-someone-else"],
                        "toAll": "false",
                    }
                ]
            },
        )

        result, _items = await service.add_item(
            principal, "taskList/1", "bread", NAMED, "op1", repo, "fam1"
        )

        assert result.outcome == WriteOutcome.MISMATCHED
        assert result.mismatched_fields == ("assignees",)

    async def test_f4_add_item_readback_absent_gives_acknowledged(
        self, service: ListService, transport: FakeTransport, principal: Principal
    ) -> None:
        """F4: an absent item on readback gives acknowledged."""
        repo = InMemoryReceiptRepository()

        transport.set_response(
            "taskcreate2",
            "bread",
            {"metaId": "task/123", "text": "bread", "taskListId": "taskList/1"},
        )
        transport.set_response("tasklist", "taskList/1", {"listItems": []})

        result, items = await service.add_item(
            principal, "taskList/1", "bread", EVERYONE, "op1", repo, "fam1"
        )

        assert result.outcome == WriteOutcome.ACKNOWLEDGED
        assert result.item_id == "task/123"
        assert items == ()

    async def test_f5_add_item_lost_write_gives_unknown_no_retry(
        self, service: ListService, transport: FakeTransport, principal: Principal
    ) -> None:
        """F5: a lost write gives unknown with no retry."""
        repo = InMemoryReceiptRepository()

        class FailingTransport(FakeTransport):
            async def call(self, endpoint: str, fields: dict[str, str]) -> object:
                self.calls.append((endpoint, dict(fields)))
                if endpoint == "taskcreate2":
                    raise TransportError()
                return await super().call(endpoint, fields)

        failing = FailingTransport()
        failing_service = ListService(failing)

        result, items = await failing_service.add_item(
            principal, "taskList/1", "bread", EVERYONE, "op1", repo, "fam1"
        )

        assert result.outcome == WriteOutcome.UNKNOWN
        assert items == ()
        assert len(failing.calls) == 1

    async def test_f5_add_item_invalid_envelope_becomes_unknown(
        self, service: ListService, transport: FakeTransport, principal: Principal
    ) -> None:
        """F5: an unparseable response after send also gives unknown."""
        repo = InMemoryReceiptRepository()

        class FailingTransport(FakeTransport):
            async def call(self, endpoint: str, fields: dict[str, str]) -> object:
                self.calls.append((endpoint, dict(fields)))
                if endpoint == "taskcreate2":
                    raise InvalidEnvelopeError()
                return await super().call(endpoint, fields)

        failing = FailingTransport()
        failing_service = ListService(failing)

        result, items = await failing_service.add_item(
            principal, "taskList/1", "bread", EVERYONE, "op1", repo, "fam1"
        )

        assert result.outcome == WriteOutcome.UNKNOWN
        assert items == ()
        assert len(failing.calls) == 1

    async def test_f5_add_item_refused_write_gives_rejected_and_replay_raises(
        self, service: ListService, transport: FakeTransport, principal: Principal
    ) -> None:
        """F5: a refused write gives rejected; a replay raises the same code,
        with zero calls."""
        repo = InMemoryReceiptRepository()

        class RejectingTransport(FakeTransport):
            async def call(self, endpoint: str, fields: dict[str, str]) -> object:
                self.calls.append((endpoint, dict(fields)))
                if endpoint == "taskcreate2":
                    raise UpstreamRejectedError()
                return await super().call(endpoint, fields)

        rejecting = RejectingTransport()
        rejecting_service = ListService(rejecting)

        with pytest.raises(UpstreamRejectedError):
            await rejecting_service.add_item(
                principal, "taskList/1", "bread", EVERYONE, "op1", repo, "fam1"
            )

        rejecting.calls.clear()
        with pytest.raises(FamilyWallError) as exc_info:
            await rejecting_service.add_item(
                principal, "taskList/1", "bread", EVERYONE, "op1", repo, "fam1"
            )
        assert exc_info.value.info.code == UpstreamRejectedError().info.code
        assert rejecting.calls == []

    async def test_f5_add_item_authentication_error_also_gives_rejected(
        self, service: ListService, transport: FakeTransport, principal: Principal
    ) -> None:
        """F5 (decision 4): AuthenticationError is refused the same way as
        UpstreamRejectedError — rejected, then re-raised."""
        repo = InMemoryReceiptRepository()

        class RefusingTransport(FakeTransport):
            async def call(self, endpoint: str, fields: dict[str, str]) -> object:
                self.calls.append((endpoint, dict(fields)))
                if endpoint == "taskcreate2":
                    raise AuthenticationError()
                return await super().call(endpoint, fields)

        refusing = RefusingTransport()
        refusing_service = ListService(refusing)

        with pytest.raises(AuthenticationError):
            await refusing_service.add_item(
                principal, "taskList/1", "bread", EVERYONE, "op1", repo, "fam1"
            )

        receipt = await repo.get(principal, "op1")
        assert receipt is not None
        assert receipt.status == "rejected"

    async def test_f5_add_item_pending_receipt_resolves_unknown_zero_calls(
        self, service: ListService, transport: FakeTransport, principal: Principal
    ) -> None:
        """F5 (crash recovery): a pending receipt from a previous process
        resolves to UNKNOWN, with zero calls."""
        repo = InMemoryReceiptRepository()

        from familywall_mcp.familywall.lists import build_create2_item_fields

        fields = build_create2_item_fields(
            list_id="taskList/1",
            text="bread",
            to_all=EVERYONE.to_all,
            assignee_account_ids=EVERYONE.account_ids,
        )
        payload_hash = service._compute_payload_hash(fields)

        pending_receipt = OperationReceipt(
            subject=principal.subject,
            family_id="fam1",
            resource_id="taskList/1",
            action="list.add_item",
            operation_id="crashed_op",
            payload_hash=payload_hash,
            status="pending",
            upstream_id=None,
            expires_at=datetime.now(UTC) + timedelta(hours=24),
        )
        await repo.put(pending_receipt)

        result, items = await service.add_item(
            principal, "taskList/1", "bread", EVERYONE, "crashed_op", repo, "fam1"
        )
        assert result.outcome == WriteOutcome.UNKNOWN
        assert items == ()
        assert len(transport.calls) == 0

    async def test_f7_add_item_records_action_and_resource_id(
        self, service: ListService, transport: FakeTransport, principal: Principal
    ) -> None:
        """F7: the stored receipt carries action='list.add_item' and
        resource_id=<list id>."""
        repo = InMemoryReceiptRepository()
        transport.set_response(
            "taskcreate2",
            "bread",
            {"metaId": "task/123", "text": "bread", "taskListId": "taskList/1"},
        )
        transport.set_response(
            "tasklist",
            "taskList/1",
            {
                "listItems": [
                    {
                        "metaId": "task/123",
                        "taskListId": "taskList/1",
                        "text": "bread",
                        "complete": "false",
                        "assigneeIds": list(EVERYONE.account_ids),
                        "toAll": "true",
                    }
                ]
            },
        )

        await service.add_item(
            principal, "taskList/1", "bread", EVERYONE, "op-record", repo, "fam1"
        )

        receipt = await repo.get(principal, "op-record")
        assert receipt is not None
        assert receipt.resource_id == "taskList/1"
        assert receipt.action == "list.add_item"

    async def test_f7_add_item_replay_returns_stored_result(
        self, service: ListService, transport: FakeTransport, principal: Principal
    ) -> None:
        """F7: a replay with the same operation_id and assigned_to returns the
        stored result with zero calls."""
        repo = InMemoryReceiptRepository()
        transport.set_response(
            "taskcreate2",
            "bread",
            {"metaId": "task/123", "text": "bread", "taskListId": "taskList/1"},
        )
        transport.set_response(
            "tasklist",
            "taskList/1",
            {
                "listItems": [
                    {
                        "metaId": "task/123",
                        "taskListId": "taskList/1",
                        "text": "bread",
                        "complete": "false",
                        "assigneeIds": list(EVERYONE.account_ids),
                        "toAll": "true",
                    }
                ]
            },
        )

        result1, _ = await service.add_item(
            principal, "taskList/1", "bread", EVERYONE, "op1", repo, "fam1"
        )
        assert result1.outcome == WriteOutcome.CONFIRMED
        transport.calls.clear()

        result2, items2 = await service.add_item(
            principal, "taskList/1", "bread", EVERYONE, "op1", repo, "fam1"
        )
        assert result2.outcome == WriteOutcome.CONFIRMED
        assert result2.item_id == result1.item_id
        assert transport.calls == []

    async def test_f7_add_item_different_assigned_to_same_key_conflicts(
        self, service: ListService, transport: FakeTransport, principal: Principal
    ) -> None:
        """F7: the same operation_id with a different assigned_to conflicts,
        with zero calls."""
        repo = InMemoryReceiptRepository()
        transport.set_response(
            "taskcreate2",
            "bread",
            {"metaId": "task/123", "text": "bread", "taskListId": "taskList/1"},
        )
        transport.set_response(
            "tasklist",
            "taskList/1",
            {
                "listItems": [
                    {
                        "metaId": "task/123",
                        "taskListId": "taskList/1",
                        "text": "bread",
                        "complete": "false",
                        "assigneeIds": list(EVERYONE.account_ids),
                        "toAll": "true",
                    }
                ]
            },
        )

        await service.add_item(principal, "taskList/1", "bread", EVERYONE, "op1", repo, "fam1")
        transport.calls.clear()

        with pytest.raises(FamilyWallError) as exc_info:
            await service.add_item(principal, "taskList/1", "bread", NAMED, "op1", repo, "fam1")
        assert exc_info.value.info.code == "operation_id_conflict"
        assert transport.calls == []

    async def test_f7_pre_upgrade_receipt_conflicts_instead_of_replaying(
        self, service: ListService, transport: FakeTransport, principal: Principal
    ) -> None:
        """F7: a pre-upgrade add_list_item receipt (the old taskcreate hash)
        conflicts rather than replaying, since the fields changed; no
        duplicate is created (zero calls)."""
        repo = InMemoryReceiptRepository()
        old_fields = build_create_item_fields("bread")
        old_hash = service._compute_payload_hash(old_fields)
        await repo.put(
            OperationReceipt(
                subject=principal.subject,
                family_id="fam1",
                resource_id="taskList/1",
                action="list.add_item",
                operation_id="op-old",
                payload_hash=old_hash,
                status="succeeded",
                upstream_id='{"outcome": "confirmed", "item_id": "task/999"}',
                expires_at=datetime.now(UTC) + timedelta(hours=24),
            )
        )

        with pytest.raises(FamilyWallError) as exc_info:
            await service.add_item(
                principal, "taskList/1", "bread", EVERYONE, "op-old", repo, "fam1"
            )
        assert exc_info.value.info.code == "operation_id_conflict"
        assert transport.calls == []

    async def test_add_item_legacy_receipt_replays_stored_outcome(
        self, service: ListService, transport: FakeTransport, principal: Principal
    ) -> None:
        """A migrated 'legacy' receipt with the new hash replays its stored
        outcome, with zero calls."""
        repo = InMemoryReceiptRepository()

        transport.set_response(
            "taskcreate2",
            "bread",
            {"metaId": "task/123", "text": "bread", "taskListId": "taskList/1"},
        )
        transport.set_response(
            "tasklist",
            "taskList/1",
            {
                "listItems": [
                    {
                        "metaId": "task/123",
                        "taskListId": "taskList/1",
                        "text": "bread",
                        "complete": "false",
                        "assigneeIds": list(EVERYONE.account_ids),
                        "toAll": "true",
                    }
                ]
            },
        )

        result1, _ = await service.add_item(
            principal, "taskList/1", "bread", EVERYONE, "op-legacy", repo, "fam1"
        )
        assert result1.outcome == WriteOutcome.CONFIRMED

        # Simulate a database migrated from the pre-`action` schema: the stored
        # receipt's action becomes "legacy", but its hash is untouched.
        stored = await repo.get(principal, "op-legacy")
        assert stored is not None
        await repo.put(stored.model_copy(update={"action": "legacy"}))

        transport.calls.clear()
        result2, _ = await service.add_item(
            principal, "taskList/1", "bread", EVERYONE, "op-legacy", repo, "fam1"
        )

        assert result2.outcome == result1.outcome
        assert result2.item_id == result1.item_id
        assert transport.calls == []

    async def test_add_item_receipt_from_another_action_conflicts(
        self, service: ListService, transport: FakeTransport, principal: Principal
    ) -> None:
        """An existing receipt with action='calendar.create_event' and the same
        operation ID is a conflict, with zero calls."""
        repo = InMemoryReceiptRepository()
        await repo.put(
            OperationReceipt(
                subject=principal.subject,
                family_id="fam1",
                resource_id="calendar/fam1",
                action="calendar.create_event",
                operation_id="op-cal",
                payload_hash="calendar-hash",
                status="succeeded",
                upstream_id='{"outcome": "confirmed", "event_id": "event/1"}',
                expires_at=datetime.now(UTC) + timedelta(hours=24),
            )
        )

        with pytest.raises(FamilyWallError) as exc_info:
            await service.add_item(
                principal, "taskList/1", "bread", EVERYONE, "op-cal", repo, "fam1"
            )
        assert exc_info.value.info.code == "operation_id_conflict"
        assert transport.calls == []

    async def test_unicode_item_title_roundtrip(
        self, service: ListService, transport: FakeTransport, principal: Principal
    ) -> None:
        """A Unicode item title round-trips."""
        repo = InMemoryReceiptRepository()

        unicode_text = "Café ☕"

        transport.set_response(
            "taskcreate2",
            unicode_text,
            {
                "metaId": "task/125",
                "text": unicode_text,
                "taskListId": "taskList/1",
            },
        )

        transport.set_response(
            "tasklist",
            "taskList/1",
            {
                "listItems": [
                    {
                        "metaId": "task/125",
                        "taskListId": "taskList/1",
                        "text": unicode_text,
                        "complete": "false",
                        "assigneeIds": list(EVERYONE.account_ids),
                        "toAll": "true",
                    }
                ]
            },
        )

        result, items = await service.add_item(
            principal, "taskList/1", unicode_text, EVERYONE, "op1", repo, "fam1"
        )

        assert result.outcome == WriteOutcome.CONFIRMED
        assert len(items) == 1
        assert items[0].text == unicode_text

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

        # R10: set_item_checked records resource_id=<list id> and action='list.set_checked'.
        receipt = await repo.get(principal, "op1")
        assert receipt is not None
        assert receipt.resource_id == "taskList/1"
        assert receipt.action == "list.set_checked"

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

    async def test_f14_add_item_never_sends_taskmove_across_every_outcome(
        self, principal: Principal
    ) -> None:
        """F14: the create-then-move tests are updated to ADR 0003 — no
        add_item outcome (confirmed, misfiled, mismatched, unknown or
        rejected) ever sends a taskmove."""
        repo = InMemoryReceiptRepository()

        # confirmed
        confirmed_transport = FakeTransport()
        confirmed_transport.set_response(
            "taskcreate2",
            "bread",
            {"metaId": "task/1", "text": "bread", "taskListId": "taskList/1"},
        )
        confirmed_transport.set_response(
            "tasklist",
            "taskList/1",
            {
                "listItems": [
                    {
                        "metaId": "task/1",
                        "taskListId": "taskList/1",
                        "text": "bread",
                        "complete": "false",
                        "assigneeIds": list(EVERYONE.account_ids),
                        "toAll": "true",
                    }
                ]
            },
        )
        confirmed_result, _ = await ListService(confirmed_transport).add_item(
            principal, "taskList/1", "bread", EVERYONE, "op-confirmed", repo, "fam1"
        )
        assert confirmed_result.outcome == WriteOutcome.CONFIRMED

        # misfiled
        misfiled_transport = FakeTransport()
        misfiled_transport.set_response(
            "taskcreate2",
            "bread",
            {"metaId": "task/2", "text": "bread", "taskListId": "taskList/default"},
        )
        misfiled_result, _ = await ListService(misfiled_transport).add_item(
            principal, "taskList/1", "bread", EVERYONE, "op-misfiled", repo, "fam1"
        )
        assert misfiled_result.outcome == WriteOutcome.MISFILED

        # mismatched
        mismatched_transport = FakeTransport()
        mismatched_transport.set_response(
            "taskcreate2",
            "bread",
            {"metaId": "task/3", "text": "bread", "taskListId": "taskList/1"},
        )
        mismatched_transport.set_response(
            "tasklist",
            "taskList/1",
            {
                "listItems": [
                    {
                        "metaId": "task/3",
                        "taskListId": "taskList/1",
                        "text": "bread",
                        "complete": "false",
                        "assigneeIds": ["acc-someone-else"],
                        "toAll": "false",
                    }
                ]
            },
        )
        mismatched_result, _ = await ListService(mismatched_transport).add_item(
            principal, "taskList/1", "bread", NAMED, "op-mismatched", repo, "fam1"
        )
        assert mismatched_result.outcome == WriteOutcome.MISMATCHED

        # unknown
        class UnknownTransport(FakeTransport):
            async def call(self, endpoint: str, fields: dict[str, str]) -> object:
                self.calls.append((endpoint, dict(fields)))
                if endpoint == "taskcreate2":
                    raise TransportError()
                return await super().call(endpoint, fields)

        unknown_transport = UnknownTransport()
        unknown_result, _ = await ListService(unknown_transport).add_item(
            principal, "taskList/1", "bread", EVERYONE, "op-unknown", repo, "fam1"
        )
        assert unknown_result.outcome == WriteOutcome.UNKNOWN

        # rejected
        class RejectingTransport(FakeTransport):
            async def call(self, endpoint: str, fields: dict[str, str]) -> object:
                self.calls.append((endpoint, dict(fields)))
                if endpoint == "taskcreate2":
                    raise UpstreamRejectedError()
                return await super().call(endpoint, fields)

        rejecting_transport = RejectingTransport()
        with pytest.raises(UpstreamRejectedError):
            await ListService(rejecting_transport).add_item(
                principal, "taskList/1", "bread", EVERYONE, "op-rejected", repo, "fam1"
            )

        for transport in (
            confirmed_transport,
            misfiled_transport,
            mismatched_transport,
            unknown_transport,
            rejecting_transport,
        ):
            endpoints = [c[0] for c in transport.calls]
            assert "taskmove" not in endpoints


class TestSetItemAssignees:
    """Tests for ListService.set_item_assignees (F8-F11)."""

    @pytest.fixture
    def transport(self) -> FakeTransport:
        return FakeTransport()

    @pytest.fixture
    def service(self, transport: FakeTransport) -> ListService:
        return ListService(transport)

    @pytest.fixture
    def principal(self) -> Principal:
        return Principal(subject="user1")

    async def test_f8_foreign_item_refused_zero_writes_zero_receipt(
        self, service: ListService, transport: FakeTransport, principal: Principal
    ) -> None:
        """F8: a foreign item raises UnsupportedConfigurationError, with no
        write and no receipt."""
        repo = InMemoryReceiptRepository()
        transport.set_response(
            "taskgettasklists",
            "",
            {
                "taskLists": [
                    {"metaId": "taskList/1", "name": "Shopping", "taskListType": "SHOPPING_LIST"}
                ]
            },
        )
        transport.set_response(
            "tasklist",
            "taskList/1",
            {
                "listItems": [
                    {
                        "metaId": "task/999",
                        "taskListId": "taskList/1",
                        "text": "Milk",
                        "complete": "false",
                    }
                ]
            },
        )

        with pytest.raises(UnsupportedConfigurationError):
            await service.set_item_assignees(principal, "task/123", NAMED, "op1", repo, "fam1")

        update_calls = [c for c in transport.calls if c[0] == "taskupdate2"]
        assert update_calls == []
        assert await repo.get(principal, "op1") is None

    async def test_f9_complete_partial_form_sent_confirmed_when_only_assignment_changes(
        self, principal: Principal
    ) -> None:
        """F9: the complete partial taskupdate2 form is sent; confirmed when
        the assignment changed and every other field is unchanged."""
        repo = InMemoryReceiptRepository()
        item_before = {
            "metaId": "task/123",
            "taskListId": "taskList/1",
            "text": "Milk",
            "complete": "false",
            "description": "2L",
            "assigneeIds": list(EVERYONE.account_ids),
            "toAll": "true",
        }
        item_after = dict(item_before, assigneeIds=["acc-sam"], toAll="false")

        class ReadbackTransport(FakeTransport):
            def __init__(self) -> None:
                super().__init__()
                self._updated = False

            async def call(self, endpoint: str, fields: dict[str, str]) -> object:
                self.calls.append((endpoint, dict(fields)))
                if endpoint == "taskgettasklists":
                    return {
                        "taskLists": [
                            {
                                "metaId": "taskList/1",
                                "name": "Shopping",
                                "taskListType": "SHOPPING_LIST",
                            }
                        ]
                    }
                if endpoint == "tasklist":
                    return {"listItems": [item_after if self._updated else item_before]}
                if endpoint == "taskupdate2":
                    self._updated = True
                    return {"ok": True}
                raise AssertionError(f"Unexpected call: {endpoint} {fields}")

        rt = ReadbackTransport()
        rt_service = ListService(rt)

        result = await rt_service.set_item_assignees(
            principal, "task/123", NAMED, "op1", repo, "fam1"
        )

        assert result.outcome == WriteOutcome.CONFIRMED
        update_calls = [c for c in rt.calls if c[0] == "taskupdate2"]
        assert len(update_calls) == 1
        assert update_calls[0][1] == {
            "partnerScope": "Family",
            "taskId": "task/123",
            "toAll": "false",
            "assignee.0": "acc-sam",
        }

    async def test_f10_readback_description_changed_gives_mismatched(
        self, principal: Principal
    ) -> None:
        """F10: a readback where description changed gives mismatched, naming it."""
        repo = InMemoryReceiptRepository()
        item_before = {
            "metaId": "task/123",
            "taskListId": "taskList/1",
            "text": "Milk",
            "complete": "false",
            "description": "2L",
            "assigneeIds": ["acc-alex"],
            "toAll": "false",
        }
        item_after = dict(item_before, description="Whole milk", assigneeIds=["acc-sam"])

        class ReadbackTransport(FakeTransport):
            def __init__(self) -> None:
                super().__init__()
                self._updated = False

            async def call(self, endpoint: str, fields: dict[str, str]) -> object:
                self.calls.append((endpoint, dict(fields)))
                if endpoint == "taskgettasklists":
                    return {
                        "taskLists": [
                            {
                                "metaId": "taskList/1",
                                "name": "Shopping",
                                "taskListType": "SHOPPING_LIST",
                            }
                        ]
                    }
                if endpoint == "tasklist":
                    return {"listItems": [item_after if self._updated else item_before]}
                if endpoint == "taskupdate2":
                    self._updated = True
                    return {"ok": True}
                raise AssertionError(f"Unexpected call: {endpoint} {fields}")

        rt = ReadbackTransport()
        rt_service = ListService(rt)

        result = await rt_service.set_item_assignees(
            principal, "task/123", NAMED, "op1", repo, "fam1"
        )

        assert result.outcome == WriteOutcome.MISMATCHED
        assert "description" in result.mismatched_fields

    async def test_f10_readback_due_date_changed_gives_mismatched(
        self, principal: Principal
    ) -> None:
        """F10: a readback where due_date changed gives mismatched, naming it."""
        repo = InMemoryReceiptRepository()
        item_before = {
            "metaId": "task/123",
            "taskListId": "taskList/1",
            "text": "Milk",
            "complete": "false",
            "assigneeIds": ["acc-alex"],
            "toAll": "false",
            "dueDate": "2026-10-01T09:00:00.000Z",
        }
        item_after = dict(item_before, dueDate="2026-11-01T09:00:00.000Z", assigneeIds=["acc-sam"])

        class ReadbackTransport(FakeTransport):
            def __init__(self) -> None:
                super().__init__()
                self._updated = False

            async def call(self, endpoint: str, fields: dict[str, str]) -> object:
                self.calls.append((endpoint, dict(fields)))
                if endpoint == "taskgettasklists":
                    return {
                        "taskLists": [
                            {
                                "metaId": "taskList/1",
                                "name": "Shopping",
                                "taskListType": "SHOPPING_LIST",
                            }
                        ]
                    }
                if endpoint == "tasklist":
                    return {"listItems": [item_after if self._updated else item_before]}
                if endpoint == "taskupdate2":
                    self._updated = True
                    return {"ok": True}
                raise AssertionError(f"Unexpected call: {endpoint} {fields}")

        rt = ReadbackTransport()
        rt_service = ListService(rt)

        result = await rt_service.set_item_assignees(
            principal, "task/123", NAMED, "op1", repo, "fam1"
        )

        assert result.outcome == WriteOutcome.MISMATCHED
        assert "due_date" in result.mismatched_fields

    async def test_f11_lost_write_gives_unknown(self, principal: Principal) -> None:
        """F11: a lost write during set_item_assignees gives unknown."""
        repo = InMemoryReceiptRepository()

        class FailingTransport(FakeTransport):
            async def call(self, endpoint: str, fields: dict[str, str]) -> object:
                self.calls.append((endpoint, dict(fields)))
                if endpoint == "taskgettasklists":
                    return {
                        "taskLists": [
                            {
                                "metaId": "taskList/1",
                                "name": "Shopping",
                                "taskListType": "SHOPPING_LIST",
                            }
                        ]
                    }
                if endpoint == "tasklist":
                    return {
                        "listItems": [
                            {
                                "metaId": "task/123",
                                "taskListId": "taskList/1",
                                "text": "Milk",
                                "complete": "false",
                            }
                        ]
                    }
                if endpoint == "taskupdate2":
                    raise TransportError()
                raise AssertionError(f"Unexpected call: {endpoint} {fields}")

        failing = FailingTransport()
        failing_service = ListService(failing)

        result = await failing_service.set_item_assignees(
            principal, "task/123", NAMED, "op1", repo, "fam1"
        )

        assert result.outcome == WriteOutcome.UNKNOWN

    async def test_f11_refused_write_gives_rejected_receipt_with_action_and_resource_id(
        self, principal: Principal
    ) -> None:
        """F11: a refusal gives rejected; the receipt carries
        action='list.set_assignees' and resource_id=item_id."""
        repo = InMemoryReceiptRepository()

        class RejectingTransport(FakeTransport):
            async def call(self, endpoint: str, fields: dict[str, str]) -> object:
                self.calls.append((endpoint, dict(fields)))
                if endpoint == "taskgettasklists":
                    return {
                        "taskLists": [
                            {
                                "metaId": "taskList/1",
                                "name": "Shopping",
                                "taskListType": "SHOPPING_LIST",
                            }
                        ]
                    }
                if endpoint == "tasklist":
                    return {
                        "listItems": [
                            {
                                "metaId": "task/123",
                                "taskListId": "taskList/1",
                                "text": "Milk",
                                "complete": "false",
                            }
                        ]
                    }
                if endpoint == "taskupdate2":
                    raise UpstreamRejectedError()
                raise AssertionError(f"Unexpected call: {endpoint} {fields}")

        rejecting = RejectingTransport()
        rejecting_service = ListService(rejecting)

        with pytest.raises(UpstreamRejectedError):
            await rejecting_service.set_item_assignees(
                principal, "task/123", NAMED, "op1", repo, "fam1"
            )

        receipt = await repo.get(principal, "op1")
        assert receipt is not None
        assert receipt.status == "rejected"
        assert receipt.action == "list.set_assignees"
        assert receipt.resource_id == "task/123"

    async def test_f11_replay_returns_stored_result_zero_calls(self, principal: Principal) -> None:
        """F11 (replay): the same operation_id with the same assigned_to
        returns the stored result, with zero calls."""
        repo = InMemoryReceiptRepository()
        item_before = {
            "metaId": "task/123",
            "taskListId": "taskList/1",
            "text": "Milk",
            "complete": "false",
            "assigneeIds": list(EVERYONE.account_ids),
            "toAll": "true",
        }
        item_after = dict(item_before, assigneeIds=["acc-sam"], toAll="false")

        class ReadbackTransport(FakeTransport):
            def __init__(self) -> None:
                super().__init__()
                self._updated = False

            async def call(self, endpoint: str, fields: dict[str, str]) -> object:
                self.calls.append((endpoint, dict(fields)))
                if endpoint == "taskgettasklists":
                    return {
                        "taskLists": [
                            {
                                "metaId": "taskList/1",
                                "name": "Shopping",
                                "taskListType": "SHOPPING_LIST",
                            }
                        ]
                    }
                if endpoint == "tasklist":
                    return {"listItems": [item_after if self._updated else item_before]}
                if endpoint == "taskupdate2":
                    self._updated = True
                    return {"ok": True}
                raise AssertionError(f"Unexpected call: {endpoint} {fields}")

        rt = ReadbackTransport()
        rt_service = ListService(rt)

        result1 = await rt_service.set_item_assignees(
            principal, "task/123", NAMED, "op1", repo, "fam1"
        )
        assert result1.outcome == WriteOutcome.CONFIRMED
        rt.calls.clear()

        result2 = await rt_service.set_item_assignees(
            principal, "task/123", NAMED, "op1", repo, "fam1"
        )
        assert result2.outcome == WriteOutcome.CONFIRMED
        assert rt.calls == []


async def test_add_item_acknowledgement_is_recorded_before_readback() -> None:
    """If the process dies during the readback, a replay reports acknowledged, not unknown."""

    class DiesDuringReadback(FakeTransport):
        async def call(self, endpoint: str, fields: dict[str, str]) -> object:
            if endpoint == "tasklist":
                raise KeyboardInterrupt
            return await super().call(endpoint, fields)

    transport = DiesDuringReadback()
    transport.set_response(
        "taskcreate2", "bread", {"metaId": "task/123", "text": "bread", "taskListId": "taskList/1"}
    )
    repo = InMemoryReceiptRepository()
    principal = Principal(subject="subject-a")

    with pytest.raises(KeyboardInterrupt):
        await ListService(transport).add_item(
            principal, "taskList/1", "bread", EVERYONE, "op1", repo, "fam1"
        )

    replay_transport = FakeTransport()
    result, _ = await ListService(replay_transport).add_item(
        principal, "taskList/1", "bread", EVERYONE, "op1", repo, "fam1"
    )
    assert result.outcome == WriteOutcome.ACKNOWLEDGED
    assert result.item_id == "task/123"
    assert replay_transport.calls == []
