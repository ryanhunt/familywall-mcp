"""Tests for the MCP tools registry and tool implementations."""

from __future__ import annotations

from datetime import UTC, date, datetime

import pytest

from familywall_mcp.config import AppConfig
from familywall_mcp.familywall.discovery import DiscoveredFamily, FamilyMember
from familywall_mcp.models import FamilyContext, Principal
from familywall_mcp.services.lists import WriteOutcome
from familywall_mcp.storage.memory import InMemoryReceiptRepository
from familywall_mcp.tools.registry import (
    ErrorResponse,
    ToolRegistry,
)


class FakeSessionPool:
    """Minimal fake SessionPool for testing."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, str, dict[str, str], str]] = []

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
            return [
                {
                    "metaId": "taskList/1",
                    "name": "Shopping",
                    "taskListType": "SHOPPING_LIST",
                    "totalTaskNumber": 5,
                    "remainingTaskNumber": 3,
                }
            ]
        elif endpoint == "tasklist":
            return [
                {
                    "metaId": "task/1",
                    "text": "Milk",
                    "complete": "false",
                    "taskListId": "taskList/1",
                }
            ]
        return {"synthetic": "result"}

    async def aclose(self) -> None:
        """Close the pool."""
        pass


class FakeListService:
    """Fake ListService for testing."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, str]] = []

    async def list_accessible_lists(self, principal: Principal) -> tuple:
        """Return a fake list."""
        from familywall_mcp.familywall.lists import ListType, ShoppingList

        return (
            ShoppingList(
                list_id="taskList/1",
                name="Shopping",
                type_raw="SHOPPING_LIST",
                known_type=ListType.SHOPPING,
                total_items=5,
                remaining_items=3,
                color=None,
                system_id=None,
            ),
        )

    async def select_list(
        self,
        principal: Principal,
        explicit_list_id: str | None = None,
        default_list_id: str | None = None,
    ) -> object:
        """Return a list selection."""
        from familywall_mcp.familywall.lists import ListType, ShoppingList
        from familywall_mcp.services.lists import ListSelection

        resolved_list = ShoppingList(
            list_id="taskList/1",
            name="Shopping",
            type_raw="SHOPPING_LIST",
            known_type=ListType.SHOPPING,
            total_items=5,
            remaining_items=3,
            color=None,
            system_id=None,
        )
        return ListSelection(resolved=resolved_list, candidates=(), reason="default")

    async def add_item(
        self, principal, list_id, text, quantity, operation_id, receipt_repo, family_id
    ):
        """Fake add_item."""
        from familywall_mcp.services.lists import AddItemResult

        return (
            AddItemResult(
                outcome=WriteOutcome.CONFIRMED, quantity_written=quantity, item_id="task/2"
            ),
            (),
        )

    async def set_item_checked(
        self, principal, item_id, checked, operation_id, receipt_repo, family_id
    ):
        """Fake set_item_checked."""
        from familywall_mcp.services.lists import SetItemCheckedResult

        return SetItemCheckedResult(outcome=WriteOutcome.CONFIRMED)


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
) -> ToolRegistry:
    """Factory for creating a ToolRegistry with test dependencies."""
    pool = FakeSessionPool()
    principal = Principal(subject="test-subject")
    family_context = FamilyContext(
        account_id="acct/1",
        family_id="family/1",
        calendar_id="calendar/1",
    )

    return ToolRegistry(
        config=config,
        session_pool=pool,  # type: ignore
        principal=principal,
        family_context=family_context,
        discovered_family=discovered_family,
        authenticated_member_timezone=discovered_family.members[0].timezone or "UTC",
        list_service=FakeListService(),  # type: ignore
        calendar_service=FakeCalendarService(),  # type: ignore
        receipt_repository=InMemoryReceiptRepository(),
    )


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
    registry = create_registry(config, discovered)

    result = await registry._get_connection_status()

    assert isinstance(result, object)
    assert result.family_name == "The Test Family"
    assert result.member_count == 2
    assert result.authenticated_member_timezone == "Australia/Sydney"
    assert result.writes_enabled is False


@pytest.mark.asyncio
async def test_list_shopping_lists_names_family() -> None:
    """list_shopping_lists response names the discovered family."""
    config = AppConfig.from_env(
        {
            "FAMILYWALL_LOCAL_SUBJECT": "test-local-user",
            "FAMILYWALL_MODE": "stdio",
        }
    )
    discovered = create_discovered_family()
    registry = create_registry(config, discovered)

    result = await registry._list_shopping_lists()

    assert isinstance(result, object)
    assert result.family_name == "The Test Family"
    assert len(result.lists) > 0


@pytest.mark.asyncio
async def test_get_list_items_names_family() -> None:
    """get_list_items response names the discovered family."""
    config = AppConfig.from_env(
        {
            "FAMILYWALL_LOCAL_SUBJECT": "test-local-user",
            "FAMILYWALL_MODE": "stdio",
        }
    )
    discovered = create_discovered_family()
    registry = create_registry(config, discovered)

    result = await registry._get_list_items("taskList/1")

    assert isinstance(result, object)
    assert result.family_name == "The Test Family"


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
    registry = create_registry(config, discovered)

    result = await registry._get_week_overview(reference_date=None, timezone=None)

    assert isinstance(result, object)
    assert result.family_name == "The Test Family"
    assert result.resolved_timezone == "Australia/Sydney"


@pytest.mark.asyncio
async def test_add_list_item_refused_when_writes_disabled() -> None:
    """add_list_item refuses with zero upstream calls when writes_disabled."""
    config = AppConfig.from_env(
        {
            "FAMILYWALL_LOCAL_SUBJECT": "test-local-user",
            "FAMILYWALL_MODE": "stdio",
            "FAMILYWALL_ENABLE_WRITES": "false",
        }
    )
    discovered = create_discovered_family()
    registry = create_registry(config, discovered, enable_writes=False)

    result = await registry._add_list_item("Test Item")

    assert isinstance(result, ErrorResponse)
    assert result.error_code == "writes_disabled"


@pytest.mark.asyncio
async def test_set_list_item_checked_refused_when_writes_disabled() -> None:
    """set_list_item_checked refuses with zero upstream calls when writes_disabled."""
    config = AppConfig.from_env(
        {
            "FAMILYWALL_LOCAL_SUBJECT": "test-local-user",
            "FAMILYWALL_MODE": "stdio",
            "FAMILYWALL_ENABLE_WRITES": "false",
        }
    )
    discovered = create_discovered_family()
    registry = create_registry(config, discovered, enable_writes=False)

    result = await registry._set_list_item_checked("task/1", True)

    assert isinstance(result, ErrorResponse)
    assert result.error_code == "writes_disabled"


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
    """add_list_item uses the shared receipt repository for replay protection."""
    config = AppConfig.from_env(
        {
            "FAMILYWALL_LOCAL_SUBJECT": "test-local-user",
            "FAMILYWALL_MODE": "stdio",
            "FAMILYWALL_ENABLE_WRITES": "true",
        }
    )
    discovered = create_discovered_family()

    # Create registry with writes enabled
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
        principal=principal,
        family_context=family_context,
        discovered_family=discovered,
        authenticated_member_timezone="Australia/Sydney",
        list_service=FakeListService(),  # type: ignore
        calendar_service=FakeCalendarService(),  # type: ignore
        receipt_repository=receipt_repo,
    )

    # First call with idempotency key
    result1 = await registry._add_list_item(
        "Milk",
        list_id="taskList/1",
        idempotency_key="test-key-123",
    )

    assert result1.family_name == "The Test Family"

    # Verify receipt was stored
    receipt = await receipt_repo.get(principal, "test-key-123")
    assert receipt is not None
