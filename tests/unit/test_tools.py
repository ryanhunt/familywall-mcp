"""Tests for the MCP tools registry and tool implementations."""

from __future__ import annotations

from datetime import UTC, date, datetime

import pytest

from familywall_mcp.config import AppConfig
from familywall_mcp.errors import UpstreamRejectedError
from familywall_mcp.familywall.calendar import CalendarEvent, TimedSpan
from familywall_mcp.familywall.discovery import DiscoveredFamily, FamilyMember
from familywall_mcp.models import FamilyContext, Principal
from familywall_mcp.services.principal_context import FixedContextResolver, PrincipalContext
from familywall_mcp.storage.memory import InMemoryReceiptRepository
from familywall_mcp.tools.registry import (
    CreateCalendarEventResponse,
    ErrorResponse,
    GetListItemsResponse,
    ListFamilyMembersResponse,
    ToolRegistry,
)


class FakeSessionPool:
    """Minimal fake SessionPool for testing."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, str, dict[str, str], str]] = []
        self.created_items: dict[str, dict[str, object]] = {}  # metaId -> item info
        self.created_events: list[dict[str, str]] = []  # evtcreate forms, in order

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
        elif endpoint == "evtcreate":
            self.created_events.append(fields)
            return {"eventId": f"event/{len(self.created_events)}", "text": fields["text"]}
        elif endpoint == "evtlistinterval":
            return [
                _stored_event(f"event/{index}", form)
                for index, form in enumerate(self.created_events, start=1)
            ]
        return {"synthetic": "result"}

    async def aclose(self) -> None:
        """Close the pool."""
        pass


def _stored_event(event_id: str, form: dict[str, str]) -> dict[str, object]:
    """How an evtcreate form reads back: instants re-stamped in UTC, and the
    attendee/reminder fields mirrored back exactly as sent."""

    def utc(value: str) -> str:
        return datetime.fromisoformat(value).astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%S.000Z")

    attendee_ids = []
    index = 0
    while f"attendee.{index}.accountId" in form:
        attendee_ids.append(form[f"attendee.{index}.accountId"])
        index += 1

    return {
        "eventId": event_id,
        "eventMasterId": event_id,
        "occurenceIndex": "0",
        "text": form["text"],
        "startDate": utc(form["startDate"]),
        "endDate": utc(form["endDate"]),
        "allDay": "false",
        "timeZone": form["timeZone"],
        "recurrency": form["recurrency"],
        "eventType": "UNKNOWN",
        "calendarId": "calendar/1",
        "where": form["where"],
        "description": form["description"],
        "attendeeIds": [] if form["isToAll"] == "true" else attendee_ids,
        "toAll": form["isToAll"],
        "reminderList": [
            {
                "localId": "reminder-1",
                "reminderType": form["reminderList.0.reminderType"],
                "reminderUnit": form["reminderList.0.reminderUnit"],
                "reminderValue": form["reminderList.0.reminderValue"],
            }
        ],
    }


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


def create_family_with_ambiguous_members() -> DiscoveredFamily:
    """A family where two members share a first name, for an ambiguous_member case."""
    return DiscoveredFamily(
        family_id="family/1",
        family_meta_id="family/1",
        calendar_id="calendar/1",
        name="The Test Family",
        members=(
            FamilyMember(
                account_id="acct/1",
                display_name="Jordan Alex",
                first_name="Jordan",
                timezone="Australia/Sydney",
                is_authenticated_member=True,
            ),
            FamilyMember(
                account_id="acct/2",
                display_name="Jordan Robin",
                first_name="Jordan",
                timezone="Europe/London",
                is_authenticated_member=False,
            ),
        ),
    )


def _refreshed_family_payload() -> dict[str, object]:
    """The raw accgetallfamily payload once a new member ('Jordan Lee') exists.

    ``family_id: "1"`` (not the fixture's "family/1") so the parsed
    ``calendar_id`` still comes out as "calendar/1", matching the hardcoded
    ``calendarId`` in ``_stored_event`` above.
    """
    return {
        "family_id": "1",
        "metaId": "family/1",
        "name": "The Test Family",
        "members": [
            {
                "accountId": "acct/1",
                "name": "Test Member",
                "firstName": "Test",
                "timeZone": "Australia/Sydney",
                "isloggedaccount": "true",
            },
            {
                "accountId": "acct/2",
                "name": "Other Member",
                "firstName": "Other",
                "timeZone": "Europe/London",
                "isloggedaccount": "false",
            },
            {
                "accountId": "acct/3",
                "name": "Jordan Lee",
                "firstName": "Jordan",
                "timeZone": "Europe/London",
                "isloggedaccount": "false",
            },
        ],
    }


class FakeSessionPoolWithDiscoveryRefresh(FakeSessionPool):
    """A FakeSessionPool whose accgetallfamily answers with a richer family,
    for testing ContextResolver.refresh() (C9, C10)."""

    def __init__(self, refreshed_payload: dict[str, object]) -> None:
        super().__init__()
        self.refreshed_payload = refreshed_payload
        self.discovery_calls = 0

    async def call(
        self, principal: Principal, endpoint: str, fields: dict[str, str], read_write: str
    ) -> object:
        if endpoint == "accgetallfamily":
            self.calls.append((principal.subject, endpoint, dict(fields), read_write))
            self.discovery_calls += 1
            return self.refreshed_payload
        return await super().call(principal, endpoint, fields, read_write)


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


def _writes_config(enabled: bool) -> AppConfig:
    return AppConfig.from_env(
        {
            "FAMILYWALL_LOCAL_SUBJECT": "test-local-user",
            "FAMILYWALL_MODE": "stdio",
            "FAMILYWALL_ENABLE_WRITES": "true" if enabled else "false",
        }
    )


@pytest.mark.asyncio
async def test_create_calendar_event_is_blocked_by_the_write_gate() -> None:
    """With writes disabled, nothing is resolved or sent."""
    registry, pool = create_registry(_writes_config(False), create_discovered_family())

    result = await registry._create_calendar_event(
        "Dentist", "2026-10-06T10:00", "2026-10-06T11:00"
    )

    assert isinstance(result, ErrorResponse)
    assert result.error_code == "writes_disabled"
    assert pool.calls == []


@pytest.mark.asyncio
async def test_c15_create_calendar_event_defaults_to_everyone() -> None:
    """C15: omitting assigned_to now means everyone (the old self-only default
    was PR #9's, superseded by decision 1). Local times default to the
    member's zone; create and readback use write mode."""
    registry, pool = create_registry(_writes_config(True), create_discovered_family())

    result = await registry._create_calendar_event(
        "Dentist",
        "2026-10-06T10:00",
        "2026-10-06T11:00",
        location="Main St",
        idempotency_key="op-cal-1",
    )

    assert isinstance(result, CreateCalendarEventResponse)
    assert result.outcome == "confirmed"
    assert result.event_id == "event/1"
    assert result.assigned_to == ("Test Member", "Other Member")
    assert result.assigned_to_everyone is True
    assert result.timezone == "Australia/Sydney"
    assert result.start == "2026-10-06T10:00:00+11:00"
    assert result.end == "2026-10-06T11:00:00+11:00"
    assert [(call[1], call[3]) for call in pool.calls] == [
        ("evtcreate", "write"),
        ("evtlistinterval", "write"),
    ]
    form = pool.calls[0][2]
    assert form["isToAll"] == "true"
    assert form["attendee.0.accountId"] == "acct/1"
    assert form["attendee.1.accountId"] == "acct/2"
    assert "Test Member" not in form.values()
    assert "Other Member" not in form.values()


@pytest.mark.asyncio
async def test_c15_naming_only_the_signed_in_member_sends_the_single_attendee_form() -> None:
    """C15: naming only the signed-in member still sends the single-attendee
    form (isToAll=false, one attendee.0.accountId) that PR #9 verified live."""
    registry, pool = create_registry(_writes_config(True), create_discovered_family())

    result = await registry._create_calendar_event(
        "Dentist",
        "2026-10-06T10:00",
        "2026-10-06T11:00",
        assigned_to=["Test Member"],
        idempotency_key="op-self-only",
    )

    assert isinstance(result, CreateCalendarEventResponse)
    assert result.outcome == "confirmed"
    assert result.assigned_to == ("Test Member",)
    assert result.assigned_to_everyone is False

    form = pool.calls[0][2]
    assert form["isToAll"] == "false"
    assert form["attendee.0.accountId"] == "acct/1"
    assert not any(key.startswith("attendee.1") for key in form)


@pytest.mark.asyncio
async def test_create_calendar_event_honours_an_explicit_timezone() -> None:
    registry, pool = create_registry(_writes_config(True), create_discovered_family())

    result = await registry._create_calendar_event(
        "Call", "2026-10-06T10:00", "2026-10-06T10:30", timezone="Europe/London"
    )

    assert isinstance(result, CreateCalendarEventResponse)
    assert result.outcome == "confirmed"
    assert pool.calls[0][2]["startDate"] == "2026-10-06T10:00:00+01:00"
    assert pool.calls[0][2]["timeZone"] == "Europe/London"


@pytest.mark.asyncio
async def test_create_calendar_event_replays_an_idempotency_key() -> None:
    registry, pool = create_registry(_writes_config(True), create_discovered_family())

    first = await registry._create_calendar_event(
        "Dentist", "2026-10-06T10:00", "2026-10-06T11:00", idempotency_key="op-cal-1"
    )
    calls_after_first = len(pool.calls)
    second = await registry._create_calendar_event(
        "Dentist", "2026-10-06T10:00", "2026-10-06T11:00", idempotency_key="op-cal-1"
    )

    assert isinstance(second, CreateCalendarEventResponse)
    assert second.outcome == first.outcome == "confirmed"
    assert len(pool.calls) == calls_after_first
    assert len(pool.created_events) == 1


@pytest.mark.asyncio
async def test_c8_unknown_member_with_no_refresh_available() -> None:
    """C8: stdio has no session pool to refresh discovery with, so an unknown
    name fails immediately, with zero upstream calls."""
    registry, pool = create_registry(_writes_config(True), create_discovered_family())

    result = await registry._create_calendar_event(
        "Dentist", "2026-10-06T10:00", "2026-10-06T11:00", assigned_to=["Jordan"]
    )

    assert isinstance(result, ErrorResponse)
    assert result.error_code == "unknown_member"
    assert pool.calls == []


@pytest.mark.asyncio
async def test_c9_unknown_member_found_after_one_refresh() -> None:
    """C9: an unknown name that a discovery refresh does find succeeds after
    exactly one refresh, and the second lookup uses the refreshed family."""
    config = _writes_config(True)
    discovered = create_discovered_family()
    pool = FakeSessionPoolWithDiscoveryRefresh(_refreshed_family_payload())
    principal = Principal(subject="test-subject")
    family_context = FamilyContext(
        account_id="acct/1", family_id="family/1", calendar_id="calendar/1"
    )
    context = PrincipalContext(
        family_context=family_context,
        discovered_family=discovered,
        authenticated_member_timezone="Australia/Sydney",
        calendar_service=FakeCalendarService(),  # type: ignore[arg-type]
    )
    registry = ToolRegistry(
        config=config,
        session_pool=pool,  # type: ignore[arg-type]
        context_resolver=FixedContextResolver(principal, context, pool),  # type: ignore[arg-type]
        receipt_repository=InMemoryReceiptRepository(),
    )

    result = await registry._create_calendar_event(
        "Dentist",
        "2026-10-06T10:00",
        "2026-10-06T11:00",
        assigned_to=["Jordan"],
        idempotency_key="op-jordan",
    )

    assert isinstance(result, CreateCalendarEventResponse)
    assert result.outcome == "confirmed"
    assert result.assigned_to == ("Jordan Lee",)
    assert result.assigned_to_everyone is False
    assert pool.discovery_calls == 1

    endpoint_calls = [call[1] for call in pool.calls]
    assert endpoint_calls.count("accgetallfamily") == 1
    evtcreate_fields = next(
        fields for _, endpoint, fields, _ in pool.calls if endpoint == "evtcreate"
    )
    assert evtcreate_fields["isToAll"] == "false"
    assert evtcreate_fields["attendee.0.accountId"] == "acct/3"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("discovered", "assigned_to", "expected_code"),
    [
        (create_discovered_family(), ["   "], "invalid_member_name"),
        (create_family_with_ambiguous_members(), ["Jordan"], "ambiguous_member"),
    ],
    ids=["invalid_member_name", "ambiguous_member"],
)
async def test_c10_ambiguous_or_invalid_name_never_refreshes(
    discovered: DiscoveredFamily, assigned_to: list[str], expected_code: str
) -> None:
    """C10: an ambiguous or invalid name is refused without ever calling
    refresh(), unlike unknown_member (C9). Uses a pool that would record a
    discovery call if refresh() were (incorrectly) attempted."""
    config = _writes_config(True)
    pool = FakeSessionPoolWithDiscoveryRefresh(_refreshed_family_payload())
    principal = Principal(subject="test-subject")
    family_context = FamilyContext(
        account_id="acct/1", family_id="family/1", calendar_id="calendar/1"
    )
    context = PrincipalContext(
        family_context=family_context,
        discovered_family=discovered,
        authenticated_member_timezone="Australia/Sydney",
        calendar_service=FakeCalendarService(),  # type: ignore[arg-type]
    )
    registry = ToolRegistry(
        config=config,
        session_pool=pool,  # type: ignore[arg-type]
        context_resolver=FixedContextResolver(principal, context, pool),  # type: ignore[arg-type]
        receipt_repository=InMemoryReceiptRepository(),
    )

    result = await registry._create_calendar_event(
        "Dentist", "2026-10-06T10:00", "2026-10-06T11:00", assigned_to=assigned_to
    )

    assert isinstance(result, ErrorResponse)
    assert result.error_code == expected_code
    assert pool.calls == []
    assert pool.discovery_calls == 0


@pytest.mark.asyncio
async def test_c12_response_names_and_no_account_id_leak() -> None:
    """C12: assigned_to/assigned_to_everyone are correct for both modes, and
    no account ID ever appears in the serialised response."""
    registry, _pool = create_registry(_writes_config(True), create_discovered_family())

    everyone_result = await registry._create_calendar_event(
        "Family dinner",
        "2026-10-06T18:00",
        "2026-10-06T19:00",
        idempotency_key="op-everyone",
    )
    named_result = await registry._create_calendar_event(
        "Dentist",
        "2026-10-07T10:00",
        "2026-10-07T11:00",
        assigned_to=["Test Member"],
        idempotency_key="op-named",
    )

    assert isinstance(everyone_result, CreateCalendarEventResponse)
    assert everyone_result.assigned_to == ("Test Member", "Other Member")
    assert everyone_result.assigned_to_everyone is True

    assert isinstance(named_result, CreateCalendarEventResponse)
    assert named_result.assigned_to == ("Test Member",)
    assert named_result.assigned_to_everyone is False

    for result in (everyone_result, named_result):
        serialised = result.model_dump_json()
        assert "acct/1" not in serialised
        assert "acct/2" not in serialised


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("title", "start", "end", "timezone"),
    [
        ("Dentist", "2026-10-06T11:00", "2026-10-06T10:00", None),  # end before start
        ("Dentist", "2026-10-04T02:30", "2026-10-04T04:00", None),  # Sydney DST gap
        ("Dentist", "2026-10-06", "2026-10-07", None),  # all-day
        ("Dentist", "2026-10-06T10:00", "2026-10-06T11:00", "Mars/Base"),  # unknown zone
        ("   ", "2026-10-06T10:00", "2026-10-06T11:00", None),  # blank title
        ("Dentist", "tomorrow", "2026-10-06T11:00", None),  # unparseable
    ],
)
async def test_create_calendar_event_rejects_invalid_input_before_any_call(
    title: str, start: str, end: str, timezone: str | None
) -> None:
    registry, pool = create_registry(_writes_config(True), create_discovered_family())

    result = await registry._create_calendar_event(title, start, end, timezone=timezone)

    assert isinstance(result, ErrorResponse)
    assert result.error_code == "invalid_event"
    assert pool.calls == []


@pytest.mark.asyncio
async def test_create_calendar_event_reports_a_refused_create() -> None:
    class RefusingPool(FakeSessionPool):
        async def call(
            self, principal: Principal, endpoint: str, fields: dict[str, str], read_write: str
        ) -> object:
            if endpoint == "evtcreate":
                self.calls.append((principal.subject, endpoint, fields, read_write))
                raise UpstreamRejectedError()
            return await super().call(principal, endpoint, fields, read_write)

    registry, _ = create_registry(_writes_config(True), create_discovered_family())
    pool = RefusingPool()
    registry._session_pool = pool  # type: ignore[assignment]

    result = await registry._create_calendar_event(
        "Dentist", "2026-10-06T10:00", "2026-10-06T11:00"
    )

    assert isinstance(result, ErrorResponse)
    assert result.error_code == "upstream_rejected"
    assert [call[1] for call in pool.calls] == ["evtcreate"]


@pytest.mark.asyncio
async def test_create_calendar_event_is_registered_as_a_write_tool() -> None:
    from mcp.server import MCPServer

    registry, _ = create_registry(_writes_config(True), create_discovered_family())
    server = MCPServer(name="test")
    registry.register_tools(server)

    tools = {tool.name: tool for tool in await server.list_tools()}

    tool = tools["create_calendar_event"]
    assert tool.annotations is not None
    assert tool.annotations.read_only_hint is False
    assert tool.input_schema["required"] == ["title", "start", "end"]


@pytest.mark.asyncio
async def test_t17_list_family_members_zero_calls_is_you_and_no_account_id() -> None:
    """T17: list_family_members makes zero pool calls, reports is_you correctly, and
    never leaks an account ID in its serialised response."""
    config = AppConfig.from_env(
        {
            "FAMILYWALL_LOCAL_SUBJECT": "test-local-user",
            "FAMILYWALL_MODE": "stdio",
        }
    )
    discovered = create_discovered_family()
    registry, pool = create_registry(config, discovered)

    result = await registry._list_family_members()

    assert isinstance(result, ListFamilyMembersResponse)
    assert result.family_name == "The Test Family"
    assert len(pool.calls) == 0

    assert [m.display_name for m in result.members] == ["Test Member", "Other Member"]
    assert [m.first_name for m in result.members] == ["Test", "Other"]
    assert [m.is_you for m in result.members] == [True, False]

    serialised = result.model_dump_json()
    assert "acct/1" not in serialised
    assert "acct/2" not in serialised


def _synthetic_event(attendee_ids: tuple[str, ...], to_all: bool | None) -> CalendarEvent:
    """A synthetic timed event carrying only the assignment fields under test."""
    return CalendarEvent(
        occurrence_id="event/synthetic-1",
        series_id="event/synthetic-1",
        occurrence_index=0,
        title="Family dinner",
        span=TimedSpan(
            start=datetime(2026, 9, 14, 18, 0, tzinfo=UTC),
            end=datetime(2026, 9, 14, 19, 0, tzinfo=UTC),
        ),
        raw_start="2026-09-14T18:00:00.000Z",
        raw_end="2026-09-14T19:00:00.000Z",
        event_type="UNKNOWN",
        calendar_id="calendar/1",
        event_timezone="Australia/Sydney",
        location=None,
        description=None,
        recurrence_rule=None,
        is_recurring=False,
        is_series_exception=False,
        attendee_ids=attendee_ids,
        to_all=to_all,
        editable=True,
    )


class FakeCalendarServiceWithEvent(FakeCalendarService):
    """A FakeCalendarService whose week overview carries one synthetic event."""

    def __init__(self, event: CalendarEvent) -> None:
        self._event = event

    async def get_week_overview(
        self,
        reference_date: object,
        timezone: str,
        calendar_id: str,
        week_starts_on: str = "monday",
    ) -> object:
        from familywall_mcp.services.calendar import DayAgenda, WeekOverview

        today = date.today()
        days = tuple(
            DayAgenda(day=today, events=(self._event,) if index == 0 else ()) for index in range(7)
        )
        return WeekOverview(
            range_start=datetime.now(tz=UTC),
            range_end=datetime.now(tz=UTC),
            timezone=timezone,
            days=days,
            total_events=1,
            complete=True,
            notes=(),
        )


@pytest.mark.asyncio
async def test_t18_get_week_overview_maps_assignment_to_names_and_counts_unresolved() -> None:
    """T18: get_week_overview event entries map attendee IDs to names, pass through the
    everyone flag, count unresolved IDs, and never leak an account ID."""
    config = AppConfig.from_env(
        {"FAMILYWALL_LOCAL_SUBJECT": "test-local-user", "FAMILYWALL_MODE": "stdio"}
    )
    discovered = create_discovered_family()
    event = _synthetic_event(attendee_ids=("acct/1", "acct/unknown"), to_all=False)

    pool = FakeSessionPool()
    principal = Principal(subject="test-subject")
    family_context = FamilyContext(
        account_id="acct/1", family_id="family/1", calendar_id="calendar/1"
    )
    registry = ToolRegistry(
        config=config,
        session_pool=pool,  # type: ignore
        context_resolver=FixedContextResolver(
            principal,
            PrincipalContext(
                family_context=family_context,
                discovered_family=discovered,
                authenticated_member_timezone="Australia/Sydney",
                calendar_service=FakeCalendarServiceWithEvent(event),  # type: ignore[arg-type]
            ),
        ),
        receipt_repository=InMemoryReceiptRepository(),
    )

    result = await registry._get_week_overview(reference_date=None, timezone=None)

    day_with_event = next(day for day in result.days if day["events"])
    event_view = day_with_event["events"][0]
    assert event_view["assigned_to"] == ["Test Member"]
    assert event_view["assigned_to_everyone"] is False
    assert event_view["unresolved_members"] == 1

    serialised = result.model_dump_json()
    assert "acct/1" not in serialised
    assert "acct/unknown" not in serialised


class FakeSessionPoolWithAssignedItem(FakeSessionPool):
    """A FakeSessionPool whose tasklist response carries one assigned item."""

    async def call(
        self, principal: Principal, endpoint: str, fields: dict[str, str], read_write: str
    ) -> object:
        if endpoint == "tasklist":
            self.calls.append((principal.subject, endpoint, fields, read_write))
            return {
                "listItems": [
                    {
                        "metaId": "task/501",
                        "taskListId": fields.get("a00listId", "taskList/1"),
                        "text": "Pack lunch",
                        "complete": "false",
                        "assigneeIds": ["acct/1", "acct/unknown"],
                        "toAll": "false",
                    }
                ]
            }
        return await super().call(principal, endpoint, fields, read_write)


@pytest.mark.asyncio
async def test_t18_get_list_items_maps_assignment_to_names_and_counts_unresolved() -> None:
    """T18: get_list_items items map assignee IDs to names, pass through the everyone
    flag, count unresolved IDs, and never leak an account ID."""
    config = AppConfig.from_env(
        {"FAMILYWALL_LOCAL_SUBJECT": "test-local-user", "FAMILYWALL_MODE": "stdio"}
    )
    discovered = create_discovered_family()
    registry, _pool = create_registry(config, discovered)
    registry._session_pool = FakeSessionPoolWithAssignedItem()  # type: ignore[assignment]

    result = await registry._get_list_items("taskList/1")

    assert isinstance(result, GetListItemsResponse)
    item = result.items[0]
    assert item.assigned_to == ("Test Member",)
    assert item.assigned_to_everyone is False
    assert item.unresolved_members == 1

    serialised = result.model_dump_json()
    assert "acct/1" not in serialised
    assert "acct/unknown" not in serialised
