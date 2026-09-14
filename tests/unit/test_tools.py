"""Tests for the MCP tools registry and tool implementations."""

from __future__ import annotations

from datetime import UTC, date, datetime

import pytest

from familywall_mcp.config import AppConfig
from familywall_mcp.familywall.discovery import DiscoveredFamily, FamilyMember
from familywall_mcp.models import FamilyContext, Principal
from familywall_mcp.services.principal_context import FixedContextResolver, PrincipalContext
from familywall_mcp.storage.memory import InMemoryReceiptRepository
from familywall_mcp.tools.registry import (
    ErrorResponse,
    ToolRegistry,
)


class FakeSessionPool:
    """Minimal fake SessionPool for testing."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, str, dict[str, str], str]] = []
        self.created_items: dict[str, dict[str, object]] = {}  # metaId -> item info

    async def call(
        self,
        principal: Principal,
        endpoint: str,
        fields: dict[str, str],
        read_write: str,
    ) -> object:
        """Record the call and return a synthetic result."""
        self.calls.append((principal.subject, endpoint, fields, read_write))
        if endpoint == "taskgettasklists":
            return {
                "taskLists": [
                    {
                        "metaId": "taskList/1",
                        "name": "Shopping",
                        "taskListType": "SHOPPING_LIST",
                        "totalTaskNumber": 5,
                        "remainingTaskNumber": 3,
                    }
                ]
            }
        elif endpoint == "tasklist":
            # Check if this is a lookup by list ID
            list_id = fields.get("a00listId", "")
            # Return items that were created/moved to this list
            items = []
            for item_id, item_info in self.created_items.items():
                if item_info.get("taskListId") == list_id:
                    items.append(
                        {
                            "metaId": item_id,
                            "text": item_info.get("text", "Item"),
                            "complete": "false",
                            "taskListId": list_id,
                        }
                    )
            # Always include at least one item for test compatibility
            if not items:
                items = [
                    {
                        "metaId": "task/1",
                        "text": "Milk",
                        "complete": "false",
                        "taskListId": list_id or "taskList/1",
                    }
                ]
            return {"listItems": items}
        elif endpoint == "taskcreate":
            item_id = "task/2"
            text = fields.get("a00text", "Item")
            # Record the created item as being in the default list initially
            self.created_items[item_id] = {
                "text": text,
                "taskListId": "taskList/default",
            }
            return {
                "metaId": item_id,
                "text": text,
                "taskListId": "taskList/default",
            }
        elif endpoint == "taskmove":
            # Move the item to the target list
            task_id = fields.get("a00taskId", "")
            target_list_id = fields.get("a00taskListId", "")
            if task_id in self.created_items:
                self.created_items[task_id]["taskListId"] = target_list_id
            return {"ok": True}
        elif endpoint == "taskmark":
            return {"ok": True}
        return {"synthetic": "result"}

    async def aclose(self) -> None:
        """Close the pool."""
        pass


class FakeCalendarService:
    """Fake CalendarService for testing."""

    async def get_week_overview(
        self,
        reference_date: object,
        timezone: str,
        calendar_id: str,
        week_starts_on: str = "monday",
    ) -> object:
        """Return a fake week overview."""
        from familywall_mcp.services.calendar import DayAgenda, WeekOverview

        today = date.today()
        return WeekOverview(
            range_start=datetime.now(tz=UTC),
            range_end=datetime.now(tz=UTC),
            timezone=timezone,
            days=tuple(DayAgenda(day=today, events=()) for _ in range(7)),
            total_events=0,
            complete=True,
            notes=(),
        )


def create_discovered_family() -> DiscoveredFamily:
    """Create a fake discovered family with authenticated member."""
    return DiscoveredFamily(
        family_id="family/1",
        family_meta_id="family/1",
        calendar_id="calendar/1",
        name="The Test Family",
        members=(
            FamilyMember(
                account_id="acct/1",
                display_name="Test Member",
                first_name="Test",
                timezone="Australia/Sydney",
                is_authenticated_member=True,
            ),
            FamilyMember(
                account_id="acct/2",
                display_name="Other Member",
                first_name="Other",
                timezone="Europe/London",
                is_authenticated_member=False,
            ),
        ),
    )


def create_registry(
    config: AppConfig,
    discovered_family: DiscoveredFamily,
    enable_writes: bool = False,
) -> tuple[ToolRegistry, FakeSessionPool]:
    """Factory for creating a ToolRegistry with test dependencies.

    Returns a tuple of (registry, pool) so tests can inspect pool.calls.
    """
    pool = FakeSessionPool()
    principal = Principal(subject="test-subject")
    family_context = FamilyContext(
        account_id="acct/1",
        family_id="family/1",
        calendar_id="calendar/1",
    )

    context = PrincipalContext(
        family_context=family_context,
        discovered_family=discovered_family,
        authenticated_member_timezone=discovered_family.members[0].timezone or "UTC",
        calendar_service=FakeCalendarService(),  # type: ignore[arg-type]
    )
    registry = ToolRegistry(
        config=config,
        session_pool=pool,  # type: ignore
        context_resolver=FixedContextResolver(principal, context),
        receipt_repository=InMemoryReceiptRepository(),
    )
    return registry, pool


@pytest.mark.asyncio
async def test_get_connection_status_returns_real_family_name() -> None:
    """get_connection_status returns real family name from discovery."""
    config = AppConfig.from_env(
        {
            "FAMILYWALL_LOCAL_SUBJECT": "test-local-user",
            "FAMILYWALL_MODE": "stdio",
            "FAMILYWALL_ENABLE_WRITES": "false",
        }
    )
    discovered = create_discovered_family()
    registry, _ = create_registry(config, discovered)

    result = await registry._get_connection_status()

    assert isinstance(result, object)
    assert result.family_name == "The Test Family"
    assert result.member_count == 2
    assert result.authenticated_member_timezone == "Australia/Sydney"
    assert result.writes_enabled is False


@pytest.mark.asyncio
async def test_list_shopping_lists_names_family() -> None:
    """Criterion 6: list_shopping_lists issues taskgettasklists with read mode."""
    config = AppConfig.from_env(
        {
            "FAMILYWALL_LOCAL_SUBJECT": "test-local-user",
            "FAMILYWALL_MODE": "stdio",
        }
    )
    discovered = create_discovered_family()
    registry, pool = create_registry(config, discovered)

    result = await registry._list_shopping_lists()

    assert isinstance(result, object)
    assert result.family_name == "The Test Family"
    assert len(result.lists) > 0
    # Assert on the recorded pool calls
    assert len(pool.calls) == 1
    subject, endpoint, fields, read_write = pool.calls[0]
    assert endpoint == "taskgettasklists"
    assert read_write == "read"


@pytest.mark.asyncio
async def test_get_list_items_names_family() -> None:
    """Criterion 7: get_list_items issues tasklist with a00listId and read mode."""
    config = AppConfig.from_env(
        {
            "FAMILYWALL_LOCAL_SUBJECT": "test-local-user",
            "FAMILYWALL_MODE": "stdio",
        }
    )
    discovered = create_discovered_family()
    registry, pool = create_registry(config, discovered)

    result = await registry._get_list_items("taskList/1")

    assert isinstance(result, object)
    assert result.family_name == "The Test Family"
    # Assert on the recorded pool calls
    assert len(pool.calls) == 1
    subject, endpoint, fields, read_write = pool.calls[0]
    assert endpoint == "tasklist"
    assert fields.get("a00listId") == "taskList/1"
    assert read_write == "read"


@pytest.mark.asyncio
async def test_get_week_overview_uses_discovered_timezone_fallback() -> None:
    """get_week_overview with no timezone resolves to discovered member timezone."""
    config = AppConfig.from_env(
        {
            "FAMILYWALL_LOCAL_SUBJECT": "test-local-user",
            "FAMILYWALL_MODE": "stdio",
        }
    )
    discovered = create_discovered_family()
    registry, _ = create_registry(config, discovered)

    result = await registry._get_week_overview(reference_date=None, timezone=None)

    assert isinstance(result, object)
    assert result.family_name == "The Test Family"
    assert result.resolved_timezone == "Australia/Sydney"


@pytest.mark.asyncio
async def test_add_list_item_refused_when_writes_disabled() -> None:
    """Criterion 10: add_list_item refuses with zero recorded pool calls when writes_disabled."""
    config = AppConfig.from_env(
        {
            "FAMILYWALL_LOCAL_SUBJECT": "test-local-user",
            "FAMILYWALL_MODE": "stdio",
            "FAMILYWALL_ENABLE_WRITES": "false",
        }
    )
    discovered = create_discovered_family()
    registry, pool = create_registry(config, discovered, enable_writes=False)

    result = await registry._add_list_item("Test Item")

    assert isinstance(result, ErrorResponse)
    assert result.error_code == "writes_disabled"
    # Zero pool calls when writes are disabled
    assert len(pool.calls) == 0


@pytest.mark.asyncio
async def test_set_list_item_checked_refused_when_writes_disabled() -> None:
    """Criterion 10: set_list_item_checked refuses with zero pool calls when disabled."""
    config = AppConfig.from_env(
        {
            "FAMILYWALL_LOCAL_SUBJECT": "test-local-user",
            "FAMILYWALL_MODE": "stdio",
            "FAMILYWALL_ENABLE_WRITES": "false",
        }
    )
    discovered = create_discovered_family()
    registry, pool = create_registry(config, discovered, enable_writes=False)

    result = await registry._set_list_item_checked("task/1", True)

    assert isinstance(result, ErrorResponse)
    assert result.error_code == "writes_disabled"
    # Zero pool calls when writes are disabled
    assert len(pool.calls) == 0


@pytest.mark.asyncio
async def test_set_list_item_checked_verifies_and_marks() -> None:
    """Criterion 9: set_list_item_checked verifies membership before marking."""
    config = AppConfig.from_env(
        {
            "FAMILYWALL_LOCAL_SUBJECT": "test-local-user",
            "FAMILYWALL_MODE": "stdio",
            "FAMILYWALL_ENABLE_WRITES": "true",
        }
    )
    discovered = create_discovered_family()
    pool = FakeSessionPool()
    principal = Principal(subject="test-subject")
    family_context = FamilyContext(
        account_id="acct/1",
        family_id="family/1",
        calendar_id="calendar/1",
    )
    receipt_repo = InMemoryReceiptRepository()

    registry = ToolRegistry(
        config=config,
        session_pool=pool,  # type: ignore
        context_resolver=FixedContextResolver(
            principal,
            PrincipalContext(
                family_context=family_context,
                discovered_family=discovered,
                authenticated_member_timezone="Australia/Sydney",
                calendar_service=FakeCalendarService(),  # type: ignore[arg-type]
            ),
        ),
        receipt_repository=receipt_repo,
    )

    # Call set_list_item_checked
    result = await registry._set_list_item_checked("task/1", True, idempotency_key="op-123")

    assert result.family_name == "The Test Family"
    assert result.outcome == "confirmed"

    # Verify the recorded pool calls:
    # 1. taskgettasklists for list access verification
    # 2. tasklist for list access verification
    # 3. taskmark for marking the item
    endpoint_calls = [call[1] for call in pool.calls]
    assert "taskgettasklists" in endpoint_calls
    assert "tasklist" in endpoint_calls
    assert "taskmark" in endpoint_calls

    # Verify taskmark call has a00complete as string "true"
    taskmark_calls = [call for call in pool.calls if call[1] == "taskmark"]
    assert len(taskmark_calls) == 1
    _, _, fields, read_write = taskmark_calls[0]
    assert fields.get("a00complete") == "true"
    assert read_write == "write"


@pytest.mark.asyncio
async def test_error_response_is_safe() -> None:
    """Error responses contain only safe fields."""
    response = ErrorResponse(
        error_code="test_code",
        error_message="A test error.",
        error_recovery="Try again.",
    )

    assert response.error_code == "test_code"
    assert response.error_message == "A test error."
    assert response.error_recovery == "Try again."
    assert "token" not in str(response).lower()
    assert "cookie" not in str(response).lower()


@pytest.mark.asyncio
async def test_add_list_item_with_idempotency_uses_receipt_repo() -> None:
    """Criterion 8: add_list_item issues create, move, readback with write mode."""
    config = AppConfig.from_env(
        {
            "FAMILYWALL_LOCAL_SUBJECT": "test-local-user",
            "FAMILYWALL_MODE": "stdio",
            "FAMILYWALL_ENABLE_WRITES": "true",
        }
    )
    discovered = create_discovered_family()
    pool = FakeSessionPool()
    principal = Principal(subject="test-subject")
    family_context = FamilyContext(
        account_id="acct/1",
        family_id="family/1",
        calendar_id="calendar/1",
    )
    receipt_repo = InMemoryReceiptRepository()

    registry = ToolRegistry(
        config=config,
        session_pool=pool,  # type: ignore
        context_resolver=FixedContextResolver(
            principal,
            PrincipalContext(
                family_context=family_context,
                discovered_family=discovered,
                authenticated_member_timezone="Australia/Sydney",
                calendar_service=FakeCalendarService(),  # type: ignore[arg-type]
            ),
        ),
        receipt_repository=receipt_repo,
    )

    # First call with idempotency key
    result1 = await registry._add_list_item(
        "Milk",
        list_id="taskList/1",
        idempotency_key="test-key-123",
    )

    assert result1.family_name == "The Test Family"
    assert result1.outcome == "confirmed"

    # Verify the recorded pool calls for add flow:
    # 1. taskgettasklists for list selection
    # 2. taskcreate for item creation
    # 3. taskmove for moving to target list
    # 4. tasklist for readback
    endpoint_calls = [call[1] for call in pool.calls]
    assert "taskgettasklists" in endpoint_calls
    assert "taskcreate" in endpoint_calls
    assert "taskmove" in endpoint_calls
    assert "tasklist" in endpoint_calls

    # Verify write mode for create and move
    for _subject, endpoint, _fields, read_write in pool.calls:
        if endpoint in ("taskcreate", "taskmove"):
            assert read_write == "write", f"{endpoint} should use write mode"

    # Verify receipt was stored
    receipt = await receipt_repo.get(principal, "test-key-123")
    assert receipt is not None
