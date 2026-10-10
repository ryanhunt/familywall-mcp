"""Tests for editing and deleting list items, and deleting whole lists."""

from __future__ import annotations

from typing import Any

import pytest

from familywall_mcp.errors import FamilyWallError, TransportError, UnsupportedConfigurationError
from familywall_mcp.familywall.lists import (
    build_delete_item_fields,
    build_delete_list_fields,
    build_update_item_fields,
)
from familywall_mcp.models import Principal
from familywall_mcp.services.list_edits import ListEditService
from familywall_mcp.services.lists import WriteOutcome
from familywall_mcp.storage.memory import InMemoryReceiptRepository

LIST = "taskList/1"
ITEM = "task/1"


class ScriptedTransport:
    """Records calls; replies from a per-endpoint queue (the last reply repeats)."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, str]]] = []
        self.replies: dict[str, list[object]] = {}

    def reply(self, endpoint: str, *responses: object) -> None:
        self.replies[endpoint] = list(responses)

    async def call(self, endpoint: str, fields: dict[str, str]) -> object:
        self.calls.append((endpoint, dict(fields)))
        queue = self.replies.get(endpoint)
        if not queue:
            raise AssertionError(f"Unexpected call: {endpoint} {fields}")
        response = queue.pop(0) if len(queue) > 1 else queue[0]
        if isinstance(response, Exception):
            raise response
        return response

    def endpoints(self) -> list[str]:
        return [endpoint for endpoint, _ in self.calls]


def a_list(list_id: str = LIST, name: str = "Urlaub", system_id: str | None = None) -> dict:
    raw: dict[str, Any] = {"metaId": list_id, "name": name, "taskListType": "TODOS"}
    if system_id is not None:
        raw["systemId"] = system_id
    return raw


def an_item(item_id: str = ITEM, text: str = "Milch", list_id: str = LIST) -> dict:
    return {"metaId": item_id, "text": text, "complete": "false", "taskListId": list_id}


def lists_reply(*lists: dict) -> dict:
    return {"taskLists": list(lists)}


def items_reply(*items: dict) -> dict:
    return {"listItems": list(items)}


@pytest.fixture
def transport() -> ScriptedTransport:
    return ScriptedTransport()


@pytest.fixture
def service(transport: ScriptedTransport) -> ListEditService:
    return ListEditService(transport)


@pytest.fixture
def receipts() -> InMemoryReceiptRepository:
    return InMemoryReceiptRepository()


@pytest.fixture
def principal() -> Principal:
    return Principal(subject="test-subject")


def sent(transport: ScriptedTransport, endpoint: str) -> dict[str, str]:
    return next(fields for name, fields in transport.calls if name == endpoint)


class TestWire:
    def test_update_item_fields(self) -> None:
        assert build_update_item_fields("task/1", "Neu") == {
            "partnerScope": "Family",
            "taskId": "task/1",
            "text": "Neu",
        }

    def test_delete_item_fields(self) -> None:
        assert build_delete_item_fields("task/1") == {
            "partnerScope": "Family",
            "a00taskId": "task/1",
        }

    def test_delete_list_fields(self) -> None:
        assert build_delete_list_fields("taskList/1") == {
            "partnerScope": "Family",
            "a00id": "taskList/1",
        }

    @pytest.mark.parametrize(
        ("builder", "bad"),
        [
            (lambda i: build_update_item_fields(i, "x"), "taskList/1"),
            (build_delete_item_fields, "event/1"),
            (build_delete_list_fields, "task/1"),
        ],
    )
    def test_wrong_prefix_is_rejected(self, builder: Any, bad: str) -> None:
        with pytest.raises(ValueError):
            builder(bad)


async def edit(service: ListEditService, principal: Principal, receipts: Any, **kw: Any) -> Any:
    return await service.update_item_text(
        principal,
        kw.pop("item_id", ITEM),
        kw.pop("text", "Hafermilch"),
        kw.pop("operation_id", "op-1"),
        receipts,
        "family/1",
    )


class TestUpdateItemText:
    async def test_confirmed(
        self,
        service: ListEditService,
        transport: ScriptedTransport,
        principal: Principal,
        receipts: Any,
    ) -> None:
        transport.reply("taskgettasklists", lists_reply(a_list()))
        transport.reply("tasklist", items_reply(an_item()), items_reply(an_item(text="Hafermilch")))
        transport.reply("taskupdate2", an_item(text="Hafermilch"))

        result = await edit(service, principal, receipts)

        assert result.outcome is WriteOutcome.CONFIRMED
        assert transport.endpoints() == ["taskgettasklists", "tasklist", "taskupdate2", "tasklist"]
        assert sent(transport, "taskupdate2") == {
            "partnerScope": "Family",
            "taskId": ITEM,
            "text": "Hafermilch",
        }

    async def test_foreign_item_is_refused_before_writing(
        self,
        service: ListEditService,
        transport: ScriptedTransport,
        principal: Principal,
        receipts: Any,
    ) -> None:
        transport.reply("taskgettasklists", lists_reply(a_list()))
        transport.reply("tasklist", items_reply(an_item("task/other")))

        with pytest.raises(UnsupportedConfigurationError):
            await edit(service, principal, receipts)

        assert "taskupdate2" not in transport.endpoints()

    async def test_blank_text_is_refused_with_zero_calls(
        self,
        service: ListEditService,
        transport: ScriptedTransport,
        principal: Principal,
        receipts: Any,
    ) -> None:
        with pytest.raises(FamilyWallError) as exc:
            await edit(service, principal, receipts, text="  ")

        assert exc.value.info.code == "invalid_item"
        assert transport.calls == []

    async def test_unknown_when_readback_shows_old_text(
        self,
        service: ListEditService,
        transport: ScriptedTransport,
        principal: Principal,
        receipts: Any,
    ) -> None:
        transport.reply("taskgettasklists", lists_reply(a_list()))
        transport.reply("tasklist", items_reply(an_item()))
        transport.reply("taskupdate2", an_item())

        result = await edit(service, principal, receipts)

        assert result.outcome is WriteOutcome.UNKNOWN

    async def test_unknown_on_transport_error_and_replay_stays_unknown(
        self,
        service: ListEditService,
        transport: ScriptedTransport,
        principal: Principal,
        receipts: Any,
    ) -> None:
        transport.reply("taskgettasklists", lists_reply(a_list()))
        transport.reply("tasklist", items_reply(an_item()))
        transport.reply("taskupdate2", TransportError())

        first = await edit(service, principal, receipts)
        calls = len(transport.calls)
        again = await edit(service, principal, receipts)

        assert first.outcome is WriteOutcome.UNKNOWN
        assert again.outcome is WriteOutcome.UNKNOWN
        assert len(transport.calls) == calls

    async def test_replay_of_confirmed_sends_nothing(
        self,
        service: ListEditService,
        transport: ScriptedTransport,
        principal: Principal,
        receipts: Any,
    ) -> None:
        transport.reply("taskgettasklists", lists_reply(a_list()))
        transport.reply("tasklist", items_reply(an_item()), items_reply(an_item(text="Hafermilch")))
        transport.reply("taskupdate2", an_item(text="Hafermilch"))
        await edit(service, principal, receipts)
        calls = len(transport.calls)

        again = await edit(service, principal, receipts)

        assert again.outcome is WriteOutcome.CONFIRMED
        assert len(transport.calls) == calls

    async def test_same_key_different_text_conflicts(
        self,
        service: ListEditService,
        transport: ScriptedTransport,
        principal: Principal,
        receipts: Any,
    ) -> None:
        transport.reply("taskgettasklists", lists_reply(a_list()))
        transport.reply("tasklist", items_reply(an_item()), items_reply(an_item(text="Hafermilch")))
        transport.reply("taskupdate2", an_item(text="Hafermilch"))
        await edit(service, principal, receipts)

        with pytest.raises(FamilyWallError) as exc:
            await edit(service, principal, receipts, text="Sojamilch")

        assert exc.value.info.code == "operation_id_conflict"


async def remove_item(
    service: ListEditService, principal: Principal, receipts: Any, expected: str = "Milch"
) -> Any:
    return await service.delete_item(principal, ITEM, expected, "del-1", receipts, "family/1")


class TestDeleteItem:
    async def test_confirmed(
        self,
        service: ListEditService,
        transport: ScriptedTransport,
        principal: Principal,
        receipts: Any,
    ) -> None:
        transport.reply("taskgettasklists", lists_reply(a_list()))
        transport.reply("tasklist", items_reply(an_item()), items_reply())
        transport.reply("taskdelete", "true")

        result = await remove_item(service, principal, receipts)

        assert result.outcome is WriteOutcome.CONFIRMED
        assert transport.endpoints() == ["taskgettasklists", "tasklist", "taskdelete", "tasklist"]
        assert sent(transport, "taskdelete") == {"partnerScope": "Family", "a00taskId": ITEM}

    async def test_foreign_item_is_refused_before_writing(
        self,
        service: ListEditService,
        transport: ScriptedTransport,
        principal: Principal,
        receipts: Any,
    ) -> None:
        transport.reply("taskgettasklists", lists_reply(a_list()))
        transport.reply("tasklist", items_reply())

        with pytest.raises(UnsupportedConfigurationError):
            await remove_item(service, principal, receipts)

        assert "taskdelete" not in transport.endpoints()

    async def test_unknown_when_server_does_not_say_true(
        self,
        service: ListEditService,
        transport: ScriptedTransport,
        principal: Principal,
        receipts: Any,
    ) -> None:
        transport.reply("taskgettasklists", lists_reply(a_list()))
        transport.reply("tasklist", items_reply(an_item()))
        transport.reply("taskdelete", "false")

        result = await remove_item(service, principal, receipts)

        assert result.outcome is WriteOutcome.UNKNOWN

    async def test_unknown_when_item_still_present(
        self,
        service: ListEditService,
        transport: ScriptedTransport,
        principal: Principal,
        receipts: Any,
    ) -> None:
        transport.reply("taskgettasklists", lists_reply(a_list()))
        transport.reply("tasklist", items_reply(an_item()))
        transport.reply("taskdelete", "true")

        result = await remove_item(service, principal, receipts)

        assert result.outcome is WriteOutcome.UNKNOWN


async def remove_list(
    service: ListEditService,
    principal: Principal,
    receipts: Any,
    list_id: str = "taskList/9",
    expected: str = "Camping trip (1)",
) -> Any:
    return await service.delete_list(principal, list_id, expected, "dl-1", receipts, "family/1")


class TestDeleteList:
    async def test_confirmed(
        self,
        service: ListEditService,
        transport: ScriptedTransport,
        principal: Principal,
        receipts: Any,
    ) -> None:
        target = a_list("taskList/9", "Camping trip (1)")
        transport.reply("taskgettasklists", lists_reply(a_list(), target), lists_reply(a_list()))
        transport.reply("taskdeletelist", "true")

        result = await remove_list(service, principal, receipts)

        assert result.outcome is WriteOutcome.CONFIRMED
        assert transport.endpoints() == ["taskgettasklists", "taskdeletelist", "taskgettasklists"]
        assert sent(transport, "taskdeletelist") == {
            "partnerScope": "Family",
            "a00id": "taskList/9",
        }

    @pytest.mark.parametrize("system_id", ["-10", "-11"])
    async def test_default_lists_are_never_deleted(
        self,
        service: ListEditService,
        transport: ScriptedTransport,
        principal: Principal,
        receipts: Any,
        system_id: str,
    ) -> None:
        transport.reply(
            "taskgettasklists", lists_reply(a_list("taskList/9", "SYS-CAT-TODOS", system_id))
        )

        with pytest.raises(FamilyWallError) as exc:
            await remove_list(service, principal, receipts)

        assert exc.value.info.code == "default_list_delete_unsupported"
        assert "taskdeletelist" not in transport.endpoints()

    async def test_foreign_list_is_refused_before_writing(
        self,
        service: ListEditService,
        transport: ScriptedTransport,
        principal: Principal,
        receipts: Any,
    ) -> None:
        transport.reply("taskgettasklists", lists_reply(a_list()))

        with pytest.raises(UnsupportedConfigurationError):
            await remove_list(service, principal, receipts)

        assert "taskdeletelist" not in transport.endpoints()

    async def test_unknown_when_list_still_present(
        self,
        service: ListEditService,
        transport: ScriptedTransport,
        principal: Principal,
        receipts: Any,
    ) -> None:
        transport.reply("taskgettasklists", lists_reply(a_list("taskList/9", "Camping trip (1)")))
        transport.reply("taskdeletelist", "true")

        result = await remove_list(service, principal, receipts)

        assert result.outcome is WriteOutcome.UNKNOWN

    async def test_malformed_id_is_refused_with_zero_calls(
        self,
        service: ListEditService,
        transport: ScriptedTransport,
        principal: Principal,
        receipts: Any,
    ) -> None:
        with pytest.raises(FamilyWallError) as exc:
            await remove_list(service, principal, receipts, list_id="task/1")

        assert exc.value.info.code == "invalid_item"
        assert transport.calls == []


class TestDeleteConfirmation:
    """Deletes are immediate with no undo, so the caller must name what it deletes."""

    async def test_item_text_mismatch_deletes_nothing(
        self,
        service: ListEditService,
        transport: ScriptedTransport,
        principal: Principal,
        receipts: Any,
    ) -> None:
        transport.reply("taskgettasklists", lists_reply(a_list()))
        transport.reply("tasklist", items_reply(an_item(text="Milch")))

        with pytest.raises(FamilyWallError) as exc:
            await remove_item(service, principal, receipts, expected="Brot")

        assert exc.value.info.code == "confirmation_mismatch"
        assert "Milch" in exc.value.info.message
        assert "taskdelete" not in transport.endpoints()

    async def test_item_confirmation_ignores_case_and_outer_spaces(
        self,
        service: ListEditService,
        transport: ScriptedTransport,
        principal: Principal,
        receipts: Any,
    ) -> None:
        transport.reply("taskgettasklists", lists_reply(a_list()))
        transport.reply("tasklist", items_reply(an_item(text="Milch")), items_reply())
        transport.reply("taskdelete", "true")

        result = await remove_item(service, principal, receipts, expected="  milch ")

        assert result.outcome is WriteOutcome.CONFIRMED

    async def test_list_name_mismatch_deletes_nothing(
        self,
        service: ListEditService,
        transport: ScriptedTransport,
        principal: Principal,
        receipts: Any,
    ) -> None:
        transport.reply("taskgettasklists", lists_reply(a_list("taskList/9", "Camping trip")))

        with pytest.raises(FamilyWallError) as exc:
            await remove_list(service, principal, receipts, expected="Camping trip (1)")

        assert exc.value.info.code == "confirmation_mismatch"
        assert "taskdeletelist" not in transport.endpoints()

    async def test_blank_confirmation_is_refused_with_zero_calls(
        self,
        service: ListEditService,
        transport: ScriptedTransport,
        principal: Principal,
        receipts: Any,
    ) -> None:
        with pytest.raises(FamilyWallError) as exc:
            await remove_item(service, principal, receipts, expected=" ")

        assert exc.value.info.code == "invalid_item"
        assert transport.calls == []
