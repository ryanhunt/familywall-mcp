"""MCP tool registry and implementations."""

from __future__ import annotations

import uuid
from datetime import date
from typing import Literal

from mcp.server import MCPServer
from mcp.types import ToolAnnotations

from familywall_mcp.config import AppConfig
from familywall_mcp.errors import FamilyWallError
from familywall_mcp.familywall.discovery import DiscoveredFamily
from familywall_mcp.interfaces import ReceiptRepository
from familywall_mcp.models import DomainModel, FamilyContext, Principal
from familywall_mcp.services.calendar import CalendarService
from familywall_mcp.services.lists import ListService
from familywall_mcp.services.session import SessionPool
from familywall_mcp.services.transport import read_transport, write_transport


class ConnectionStatusResponse(DomainModel):
    """Response from get_connection_status."""

    family_name: str
    member_count: int
    authenticated_member_timezone: str
    writes_enabled: bool


class ShoppingListSummary(DomainModel):
    """Summary of a shopping list in list_shopping_lists response."""

    list_id: str
    name: str
    type_raw: str
    total_items: int | None
    remaining_items: int | None


class ListShoppingListsResponse(DomainModel):
    """Response from list_shopping_lists."""

    family_name: str
    lists: tuple[ShoppingListSummary, ...]


class ListItemResponse(DomainModel):
    """An item in the GetListItemsResponse."""

    item_id: str
    text: str
    completed: bool
    description: str | None


class GetListItemsResponse(DomainModel):
    """Response from get_list_items."""

    family_name: str
    list_id: str
    items: tuple[ListItemResponse, ...]


class GetWeekOverviewResponse(DomainModel):
    """Response from get_week_overview."""

    family_name: str
    resolved_timezone: str
    week_start: str
    week_end: str
    total_events: int
    complete: bool
    notes: tuple[str, ...]
    days: tuple[object, ...]  # Contains DayAgenda objects


class AddListItemResponse(DomainModel):
    """Response from add_list_item."""

    family_name: str
    outcome: str
    item_id: str | None
    # Populated if and only if outcome is misfiled:
    actual_list_id: str | None = None  # where the item actually is
    # Populated if and only if outcome is misfiled:
    requested_list_id: str | None = None  # where it was supposed to go


class SetListItemCheckedResponse(DomainModel):
    """Response from set_list_item_checked."""

    family_name: str
    outcome: str


class ErrorResponse(DomainModel):
    """Safe error response from any tool."""

    error_code: str
    error_message: str
    error_recovery: str


class ToolRegistry:
    """Registry of MCP tools for FamilyWall.

    Holds service instances and tool implementations, registering them with the
    MCP server for discovery and invocation.
    """

    def __init__(
        self,
        config: AppConfig,
        session_pool: SessionPool,
        principal: Principal,
        family_context: FamilyContext,
        discovered_family: DiscoveredFamily,
        authenticated_member_timezone: str,
        calendar_service: CalendarService,
        receipt_repository: ReceiptRepository,
    ) -> None:
        """Initialize the tool registry.

        Args:
            config: Application configuration.
            session_pool: The session pool for making API calls.
            principal: The authenticated principal.
            family_context: The family context from discovery.
            discovered_family: The complete discovered family with members and names.
            authenticated_member_timezone: The authenticated member's timezone for fallback.
            calendar_service: The calendar service.
            receipt_repository: The receipt repository for idempotency tracking.
        """
        self._config = config
        self._session_pool = session_pool
        self._principal = principal
        self._family_context = family_context
        self._discovered_family = discovered_family
        self._authenticated_member_timezone = authenticated_member_timezone
        self._calendar_service = calendar_service
        self._receipt_repository = receipt_repository

    def register_tools(self, server: MCPServer) -> None:
        """Register all tools with the MCP server.

        This method must be called before starting the server.

        Args:
            server: The MCP server to register tools with.
        """
        # Read tools
        server.tool(
            name="get_connection_status",
            description="Get the connection status and authenticated member's timezone.",
            annotations=ToolAnnotations(read_only_hint=True),
        )(self._get_connection_status)

        server.tool(
            name="list_shopping_lists",
            description="List all shopping lists accessible to the authenticated user.",
            annotations=ToolAnnotations(read_only_hint=True),
        )(self._list_shopping_lists)

        server.tool(
            name="get_list_items",
            description="Get the items in a specific shopping list.",
            annotations=ToolAnnotations(read_only_hint=True),
        )(self._get_list_items)

        server.tool(
            name="get_week_overview",
            description="Get a weekly calendar overview.",
            annotations=ToolAnnotations(read_only_hint=True),
        )(self._get_week_overview)

        # Write tools
        server.tool(
            name="add_list_item",
            description=(
                "Add an item to a shopping list. Without idempotency_key, retries "
                "will create duplicate items."
            ),
            annotations=ToolAnnotations(read_only_hint=False, destructive_hint=False),
        )(self._add_list_item)

        server.tool(
            name="set_list_item_checked",
            description="Mark a list item as checked or unchecked.",
            annotations=ToolAnnotations(read_only_hint=False, destructive_hint=False),
        )(self._set_list_item_checked)

    async def _get_connection_status(self) -> ConnectionStatusResponse | ErrorResponse:
        """Get the connection status and authenticated member's timezone.

        Returns:
            ConnectionStatusResponse with family details and write status, or an error.
        """
        try:
            return ConnectionStatusResponse(
                family_name=self._discovered_family.name,
                member_count=len(self._discovered_family.members),
                authenticated_member_timezone=self._authenticated_member_timezone,
                writes_enabled=self._config.enable_writes,
            )
        except FamilyWallError as exc:
            return ErrorResponse(
                error_code=exc.info.code,
                error_message=exc.info.message,
                error_recovery=exc.info.recovery,
            )

    async def _list_shopping_lists(self) -> ListShoppingListsResponse | ErrorResponse:
        """List all shopping lists."""
        try:
            transport = read_transport(self._session_pool, self._principal)
            service = ListService(transport)
            lists = await service.list_accessible_lists(self._principal)

            summaries = tuple(
                ShoppingListSummary(
                    list_id=lst.list_id,
                    name=lst.name,
                    type_raw=lst.type_raw,
                    total_items=lst.total_items,
                    remaining_items=lst.remaining_items,
                )
                for lst in lists
            )
            return ListShoppingListsResponse(
                family_name=self._discovered_family.name,
                lists=summaries,
            )
        except FamilyWallError as exc:
            return ErrorResponse(
                error_code=exc.info.code,
                error_message=exc.info.message,
                error_recovery=exc.info.recovery,
            )

    async def _get_list_items(self, list_id: str) -> GetListItemsResponse | ErrorResponse:
        """Get items in a list."""
        try:
            transport = read_transport(self._session_pool, self._principal)
            service = ListService(transport)
            items = await service.get_list_items(self._principal, list_id)

            item_responses = tuple(
                ListItemResponse(
                    item_id=item.item_id,
                    text=item.text,
                    completed=item.completed,
                    description=item.description,
                )
                for item in items
            )
            return GetListItemsResponse(
                family_name=self._discovered_family.name,
                list_id=list_id,
                items=item_responses,
            )
        except FamilyWallError as exc:
            return ErrorResponse(
                error_code=exc.info.code,
                error_message=exc.info.message,
                error_recovery=exc.info.recovery,
            )

    async def _get_week_overview(
        self,
        reference_date: str | None = None,
        timezone: str | None = None,
        week_starts_on: Literal["monday", "sunday"] = "monday",
    ) -> GetWeekOverviewResponse | ErrorResponse:
        """Get a weekly calendar overview."""
        try:
            # Parse reference_date or use today
            ref_date = date.fromisoformat(reference_date) if reference_date else date.today()

            # Use provided timezone or fall back to authenticated member's timezone
            resolved_timezone = timezone or self._authenticated_member_timezone

            overview = await self._calendar_service.get_week_overview(
                reference_date=ref_date,
                timezone=resolved_timezone,
                calendar_id=self._family_context.calendar_id,
                week_starts_on=week_starts_on,
            )

            return GetWeekOverviewResponse(
                family_name=self._discovered_family.name,
                resolved_timezone=overview.timezone,
                week_start=overview.range_start.isoformat(),
                week_end=overview.range_end.isoformat(),
                total_events=overview.total_events,
                complete=overview.complete,
                notes=overview.notes,
                days=tuple(
                    {
                        "day": str(day_agenda.day),
                        "events": [
                            {
                                "occurrence_id": evt.occurrence_id,
                                "title": evt.title,
                            }
                            for evt in day_agenda.events
                        ],
                    }
                    for day_agenda in overview.days
                ),
            )
        except FamilyWallError as exc:
            return ErrorResponse(
                error_code=exc.info.code,
                error_message=exc.info.message,
                error_recovery=exc.info.recovery,
            )

    async def _add_list_item(
        self,
        text: str,
        list_id: str | None = None,
        idempotency_key: str | None = None,
    ) -> AddListItemResponse | ErrorResponse:
        """Add an item to a list (via create-then-move).

        Creates the item in the default list, then moves it to the requested list if needed.
        The operation is non-atomic: if create succeeds but move fails, the item exists in
        the default list and a 'misfiled' outcome is returned. No automatic retry or delete
        is performed.

        If writes are disabled, returns an error response with zero upstream calls.

        Args:
            text: The item text.
            list_id: Optional list ID to add to; if not provided, uses default or only list.
            idempotency_key: Optional operation ID for idempotency; generates UUID if not provided.

        Returns:
            AddListItemResponse on success, or ErrorResponse on failure.
        """
        # Write gate: check if writes are enabled BEFORE any upstream call
        if not self._config.enable_writes:
            return ErrorResponse(
                error_code="writes_disabled",
                error_message="Write operations are disabled.",
                error_recovery="Set FAMILYWALL_ENABLE_WRITES=true to enable writes.",
            )

        try:
            transport = write_transport(self._session_pool, self._principal)
            service = ListService(transport)

            # Select list
            selection = await service.select_list(self._principal, explicit_list_id=list_id)
            if selection.resolved is None:
                # Ambiguous or no eligible list; return candidates without writing
                return ErrorResponse(
                    error_code="ambiguous_list_selection",
                    error_message="Could not determine which list to add to.",
                    error_recovery="Specify a list_id parameter.",
                )

            # Generate operation ID if not provided
            operation_id = idempotency_key or str(uuid.uuid4())

            # Add the item
            result, _items = await service.add_item(
                self._principal,
                selection.resolved.list_id,
                text,
                operation_id,
                self._receipt_repository,
                self._family_context.family_id,
            )

            return AddListItemResponse(
                family_name=self._discovered_family.name,
                outcome=result.outcome.value,
                item_id=result.item_id,
                actual_list_id=result.actual_list_id,
                requested_list_id=result.requested_list_id,
            )
        except FamilyWallError as exc:
            return ErrorResponse(
                error_code=exc.info.code,
                error_message=exc.info.message,
                error_recovery=exc.info.recovery,
            )

    async def _set_list_item_checked(
        self,
        item_id: str,
        checked: bool,
        idempotency_key: str | None = None,
    ) -> SetListItemCheckedResponse | ErrorResponse:
        """Mark an item as checked or unchecked.

        If writes are disabled, returns an error response with zero upstream calls.

        Args:
            item_id: The item metaId (task/...).
            checked: Whether the item is complete.
            idempotency_key: Optional operation ID for idempotency; generates UUID if not provided.

        Returns:
            SetListItemCheckedResponse on success, or ErrorResponse on failure.
        """
        # Write gate: check if writes are enabled BEFORE any upstream call
        if not self._config.enable_writes:
            return ErrorResponse(
                error_code="writes_disabled",
                error_message="Write operations are disabled.",
                error_recovery="Set FAMILYWALL_ENABLE_WRITES=true to enable writes.",
            )

        try:
            transport = write_transport(self._session_pool, self._principal)
            service = ListService(transport)

            # Generate operation ID if not provided
            operation_id = idempotency_key or str(uuid.uuid4())

            # Mark the item
            result = await service.set_item_checked(
                self._principal,
                item_id,
                checked,
                operation_id,
                self._receipt_repository,
                self._family_context.family_id,
            )

            return SetListItemCheckedResponse(
                family_name=self._discovered_family.name,
                outcome=result.outcome.value,
            )
        except FamilyWallError as exc:
            return ErrorResponse(
                error_code=exc.info.code,
                error_message=exc.info.message,
                error_recovery=exc.info.recovery,
            )
