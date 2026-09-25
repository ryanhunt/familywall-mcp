"""MCP tool registry and implementations."""

from __future__ import annotations

import datetime
import uuid
from datetime import date
from typing import Literal

from mcp.server import MCPServer
from mcp.types import ToolAnnotations
from pydantic import ValidationError

from familywall_mcp.config import AppConfig
from familywall_mcp.errors import FamilyWallError
from familywall_mcp.familywall.calendar import CalendarEvent
from familywall_mcp.familywall.discovery import DiscoveredFamily
from familywall_mcp.familywall.lists import ListItem
from familywall_mcp.interfaces import ReceiptRepository
from familywall_mcp.models import DomainModel, Principal
from familywall_mcp.services.calendar import CalendarService, NewTimedEvent
from familywall_mcp.services.lists import ListService
from familywall_mcp.services.members import (
    MemberSelectionError,
    ResolvedAssignment,
    describe_assignment,
    resolve_members,
)
from familywall_mcp.services.principal_context import ContextResolver, PrincipalContext
from familywall_mcp.services.ranges import resolve_event_time
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
    assigned_to: tuple[str, ...]
    assigned_to_everyone: bool | None
    unresolved_members: int


class GetListItemsResponse(DomainModel):
    """Response from get_list_items."""

    family_name: str
    list_id: str
    items: tuple[ListItemResponse, ...]


class FamilyMemberView(DomainModel):
    """A family member as shown by list_family_members. Never an account ID."""

    display_name: str
    first_name: str | None
    is_you: bool


class ListFamilyMembersResponse(DomainModel):
    """Response from list_family_members."""

    family_name: str
    members: tuple[FamilyMemberView, ...]


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
    assigned_to: tuple[str, ...] = ()
    """Display names only, never account IDs; every member's name for everyone."""
    assigned_to_everyone: bool = False
    # Populated if and only if outcome is mismatched:
    mismatched_fields: tuple[str, ...] = ()


class SetListItemCheckedResponse(DomainModel):
    """Response from set_list_item_checked."""

    family_name: str
    outcome: str


class SetListItemAssigneesResponse(DomainModel):
    """Response from set_list_item_assignees."""

    family_name: str
    outcome: str
    item_id: str
    assigned_to: tuple[str, ...]
    """Display names only, never account IDs; every member's name for everyone."""
    assigned_to_everyone: bool
    # Populated if and only if outcome is mismatched:
    mismatched_fields: tuple[str, ...] = ()


class CreateCalendarEventResponse(DomainModel):
    """Response from create_calendar_event."""

    family_name: str
    outcome: str
    event_id: str | None
    assigned_to: tuple[str, ...]
    """Display names only, never account IDs; every member's name for everyone."""
    assigned_to_everyone: bool
    timezone: str
    start: str  # local ISO datetime as requested
    end: str
    # Populated if and only if outcome is mismatched:
    mismatched_fields: tuple[str, ...] = ()
    actual_start: str | None = None  # what the readback shows
    actual_end: str | None = None


class SetCalendarEventAttendeesResponse(DomainModel):
    """Response from set_calendar_event_attendees."""

    family_name: str
    outcome: str
    event_id: str
    assigned_to: tuple[str, ...]
    """Display names only, never account IDs; every member's name for everyone."""
    assigned_to_everyone: bool
    # Populated if and only if outcome is mismatched:
    mismatched_fields: tuple[str, ...] = ()


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
        context_resolver: ContextResolver,
        receipt_repository: ReceiptRepository,
    ) -> None:
        """Initialize the tool registry.

        Args:
            config: Application configuration.
            session_pool: The session pool for making API calls.
            context_resolver: Resolves the current principal and its discovery
                context for each tool call. In stdio mode this is a fixed
                pair built once at startup; in hosted mode it reads the
                per-request access token, so one process can safely serve
                several concurrently-logged-in users without leaking one
                user's family/list data into another's tool call.
            receipt_repository: The receipt repository for idempotency tracking.
        """
        self._config = config
        self._session_pool = session_pool
        self._context_resolver = context_resolver
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

        server.tool(
            name="list_family_members",
            description=(
                "List the family's members: display name, first name and which one is "
                "you. Never returns account IDs. Use the exact display or first name shown "
                "here when naming who a calendar event or list item is assigned to."
            ),
            annotations=ToolAnnotations(read_only_hint=True),
        )(self._list_family_members)

        # Write tools
        server.tool(
            name="add_list_item",
            description=(
                "Add an item to a shopping list, assigned per assigned_to (member "
                "names exactly as list_family_members shows them; omit it, or pass "
                "an empty list, to assign everyone). Without idempotency_key, "
                "retries will create duplicate items."
            ),
            annotations=ToolAnnotations(read_only_hint=False, destructive_hint=False),
        )(self._add_list_item)

        server.tool(
            name="set_list_item_checked",
            description="Mark a list item as checked or unchecked.",
            annotations=ToolAnnotations(read_only_hint=False, destructive_hint=False),
        )(self._set_list_item_checked)

        server.tool(
            name="set_list_item_assignees",
            description=(
                "Change only who an existing list item is assigned to. assigned_to "
                "takes member names exactly as list_family_members shows them; omit "
                "it, or pass an empty list, to assign everyone. Nothing else about "
                "the item (its text, description, checked state, list, due date or "
                "reminder) is touched. Without idempotency_key, a retry simply "
                "resends the same assignment."
            ),
            annotations=ToolAnnotations(read_only_hint=False, destructive_hint=False),
        )(self._set_list_item_assignees)

        server.tool(
            name="create_calendar_event",
            description=(
                "Create one timed, non-recurring event on the family calendar. "
                "assigned_to takes member names exactly as list_family_members shows them "
                "(display or first name); omit it, or pass an empty list, to assign "
                "everyone in the family. start and end are local times such as "
                "2026-10-06T10:00 in timezone (default: the member's FamilyWall timezone), "
                "or RFC 3339 times with an offset. The event gets FamilyWall's default "
                "30-minute reminder. All-day and recurring events are not supported. "
                "Outcome is confirmed only when a readback matches the request; mismatched "
                "means the event exists but differs. Without idempotency_key, retries will "
                "create duplicate events."
            ),
            annotations=ToolAnnotations(read_only_hint=False, destructive_hint=False),
        )(self._create_calendar_event)

        server.tool(
            name="set_calendar_event_attendees",
            description=(
                "Change only who an existing, ordinary, one-off, timed family-calendar "
                "event is assigned to; nothing else about the event changes. event_id is "
                "the occurrence_id from get_week_overview, and date is that event's local "
                "date (YYYY-MM-DD) in your FamilyWall timezone. assigned_to takes member "
                "names exactly as list_family_members shows them; omit it, or pass an "
                "empty list, to assign everyone. An unknown name triggers one refresh of "
                "the cached family list before failing. All-day events, recurring events "
                "and series exceptions are refused, as is any event this account cannot "
                "edit. Outcome is confirmed only when a readback shows the new attendees "
                "and every other field unchanged; mismatched means something else also "
                "changed. Without idempotency_key, retries will conflict rather than "
                "resend."
            ),
            annotations=ToolAnnotations(read_only_hint=False, destructive_hint=False),
        )(self._set_calendar_event_attendees)

    async def _get_connection_status(self) -> ConnectionStatusResponse | ErrorResponse:
        """Get the connection status and authenticated member's timezone.

        Returns:
            ConnectionStatusResponse with family details and write status, or an error.
        """
        try:
            _principal, ctx = await self._context_resolver.resolve()
            return ConnectionStatusResponse(
                family_name=ctx.discovered_family.name,
                member_count=len(ctx.discovered_family.members),
                authenticated_member_timezone=ctx.authenticated_member_timezone,
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
            principal, ctx = await self._context_resolver.resolve()
            transport = read_transport(self._session_pool, principal)
            service = ListService(transport)
            lists = await service.list_accessible_lists(principal)

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
                family_name=ctx.discovered_family.name,
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
            principal, ctx = await self._context_resolver.resolve()
            transport = read_transport(self._session_pool, principal)
            service = ListService(transport)
            items = await service.get_list_items(principal, list_id)

            item_responses = tuple(_list_item_view(item, ctx.discovered_family) for item in items)
            return GetListItemsResponse(
                family_name=ctx.discovered_family.name,
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
            _principal, ctx = await self._context_resolver.resolve()
            # Parse reference_date or use today
            ref_date = date.fromisoformat(reference_date) if reference_date else date.today()

            # Use provided timezone or fall back to authenticated member's timezone
            resolved_timezone = timezone or ctx.authenticated_member_timezone

            overview = await ctx.calendar_service.get_week_overview(
                reference_date=ref_date,
                timezone=resolved_timezone,
                calendar_id=ctx.family_context.calendar_id,
                week_starts_on=week_starts_on,
            )

            return GetWeekOverviewResponse(
                family_name=ctx.discovered_family.name,
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
                            _event_view(evt, ctx.discovered_family) for evt in day_agenda.events
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

    async def _list_family_members(self) -> ListFamilyMembersResponse | ErrorResponse:
        """List family members' names and which one is you.

        Uses only the cached discovery context; makes zero upstream calls.

        Returns:
            ListFamilyMembersResponse with members in discovery order, or an error.
        """
        try:
            _principal, ctx = await self._context_resolver.resolve()
            members = tuple(
                FamilyMemberView(
                    display_name=member.display_name,
                    first_name=member.first_name,
                    is_you=member.is_authenticated_member,
                )
                for member in ctx.discovered_family.members
            )
            return ListFamilyMembersResponse(
                family_name=ctx.discovered_family.name,
                members=members,
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
        assigned_to: list[str] | None = None,
        idempotency_key: str | None = None,
    ) -> AddListItemResponse | ErrorResponse:
        """Add an item directly to a list in one taskcreate2 call, assigned per
        ``assigned_to``.

        Superseded create-then-move (ADR 0002; see ADR 0003): a single
        taskcreate2 carries the target list and the assignment, so no taskmove
        is ever sent. ``misfiled`` remains only as a detected outcome, when the
        create response itself names a different list.

        If writes are disabled, returns an error response with zero upstream
        calls. ``assigned_to`` is resolved against the cached family discovery
        (refreshing once on an unknown name) before list selection, any receipt
        or any write.

        Args:
            text: The item text.
            list_id: Optional list ID to add to; if not provided, uses default or only list.
            assigned_to: Member names to assign, exactly as list_family_members shows
                them. ``None`` or ``[]`` means everyone.
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
            principal, ctx = await self._context_resolver.resolve()
            principal, ctx, assignment = await self._resolve_assignment(principal, ctx, assigned_to)

            transport = write_transport(self._session_pool, principal)
            service = ListService(transport)

            # Select list
            selection = await service.select_list(principal, explicit_list_id=list_id)
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
                principal,
                selection.resolved.list_id,
                text,
                assignment,
                operation_id,
                self._receipt_repository,
                ctx.family_context.family_id,
            )

            return AddListItemResponse(
                family_name=ctx.discovered_family.name,
                outcome=result.outcome.value,
                item_id=result.item_id,
                actual_list_id=result.actual_list_id,
                requested_list_id=result.requested_list_id,
                assigned_to=assignment.display_names,
                assigned_to_everyone=assignment.to_all,
                mismatched_fields=result.mismatched_fields,
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
            principal, ctx = await self._context_resolver.resolve()
            transport = write_transport(self._session_pool, principal)
            service = ListService(transport)

            # Generate operation ID if not provided
            operation_id = idempotency_key or str(uuid.uuid4())

            # Mark the item
            result = await service.set_item_checked(
                principal,
                item_id,
                checked,
                operation_id,
                self._receipt_repository,
                ctx.family_context.family_id,
            )

            return SetListItemCheckedResponse(
                family_name=ctx.discovered_family.name,
                outcome=result.outcome.value,
            )
        except FamilyWallError as exc:
            return ErrorResponse(
                error_code=exc.info.code,
                error_message=exc.info.message,
                error_recovery=exc.info.recovery,
            )

    async def _set_list_item_assignees(
        self,
        item_id: str,
        assigned_to: list[str] | None = None,
        idempotency_key: str | None = None,
    ) -> SetListItemAssigneesResponse | ErrorResponse:
        """Change only who ``item_id`` is assigned to.

        Verifies the item belongs to an accessible list before writing (a
        security requirement: the underlying endpoint sends no list ID, so the
        server cannot enforce list membership). Nothing else about the item is
        touched. If writes are disabled, returns an error response with zero
        upstream calls. ``assigned_to`` is resolved against the cached family
        discovery (refreshing once on an unknown name) before any write.

        Args:
            item_id: The item metaId (task/...).
            assigned_to: Member names to assign, exactly as list_family_members shows
                them. ``None`` or ``[]`` means everyone.
            idempotency_key: Optional operation ID for idempotency; generates UUID if not provided.

        Returns:
            SetListItemAssigneesResponse on success, or ErrorResponse on failure.
        """
        # Write gate: check if writes are enabled BEFORE any upstream call
        if not self._config.enable_writes:
            return ErrorResponse(
                error_code="writes_disabled",
                error_message="Write operations are disabled.",
                error_recovery="Set FAMILYWALL_ENABLE_WRITES=true to enable writes.",
            )

        try:
            principal, ctx = await self._context_resolver.resolve()
            principal, ctx, assignment = await self._resolve_assignment(principal, ctx, assigned_to)

            transport = write_transport(self._session_pool, principal)
            service = ListService(transport)

            # Generate operation ID if not provided
            operation_id = idempotency_key or str(uuid.uuid4())

            result = await service.set_item_assignees(
                principal,
                item_id,
                assignment,
                operation_id,
                self._receipt_repository,
                ctx.family_context.family_id,
            )

            return SetListItemAssigneesResponse(
                family_name=ctx.discovered_family.name,
                outcome=result.outcome.value,
                item_id=item_id,
                assigned_to=assignment.display_names,
                assigned_to_everyone=assignment.to_all,
                mismatched_fields=result.mismatched_fields,
            )
        except FamilyWallError as exc:
            return ErrorResponse(
                error_code=exc.info.code,
                error_message=exc.info.message,
                error_recovery=exc.info.recovery,
            )

    async def _create_calendar_event(
        self,
        title: str,
        start: str,
        end: str,
        timezone: str | None = None,
        location: str | None = None,
        description: str | None = None,
        assigned_to: list[str] | None = None,
        idempotency_key: str | None = None,
    ) -> CreateCalendarEventResponse | ErrorResponse:
        """Create a timed event on the family calendar, assigned per ``assigned_to``.

        If writes are disabled, returns an error response with zero upstream calls.
        Invalid input is rejected before any write.

        Args:
            title: The event title.
            start: Local datetime in ``timezone``, or an RFC 3339 datetime with offset.
            end: Exclusive end, in the same forms as ``start``.
            timezone: IANA timezone; defaults to the authenticated member's timezone.
            location: Optional location.
            description: Optional description.
            assigned_to: Member names to assign, exactly as list_family_members shows
                them. ``None`` or ``[]`` means everyone. Resolved against the cached
                family discovery before any write; a name unknown to that cache
                triggers exactly one discovery refresh and a second resolution
                attempt (never for an ambiguous or otherwise invalid name).
            idempotency_key: Optional operation ID for idempotency; generates UUID if not provided.

        Returns:
            CreateCalendarEventResponse on success, or ErrorResponse on failure.
        """
        # Write gate: check if writes are enabled BEFORE any upstream call
        if not self._config.enable_writes:
            return ErrorResponse(
                error_code="writes_disabled",
                error_message="Write operations are disabled.",
                error_recovery="Set FAMILYWALL_ENABLE_WRITES=true to enable writes.",
            )

        try:
            principal, ctx = await self._context_resolver.resolve()
            resolved_timezone = timezone or ctx.authenticated_member_timezone

            try:
                request = NewTimedEvent(
                    title=title,
                    start=resolve_event_time(start, resolved_timezone),
                    end=resolve_event_time(end, resolved_timezone),
                    timezone=resolved_timezone,
                    location=location,
                    description=description,
                )
            except ValidationError as exc:
                return _invalid_event("; ".join(error["msg"] for error in exc.errors()))
            except ValueError as exc:
                return _invalid_event(str(exc))

            principal, ctx, assignment = await self._resolve_assignment(principal, ctx, assigned_to)

            transport = write_transport(self._session_pool, principal)
            service = CalendarService(transport)

            # Generate operation ID if not provided
            operation_id = idempotency_key or str(uuid.uuid4())

            result = await service.create_event(
                principal,
                request,
                assignment,
                operation_id,
                self._receipt_repository,
                ctx.family_context,
            )

            return CreateCalendarEventResponse(
                family_name=ctx.discovered_family.name,
                outcome=result.outcome.value,
                event_id=result.event_id,
                assigned_to=assignment.display_names,
                assigned_to_everyone=assignment.to_all,
                timezone=request.timezone,
                start=request.start.isoformat(),
                end=request.end.isoformat(),
                mismatched_fields=result.mismatched_fields,
                actual_start=result.actual_start,
                actual_end=result.actual_end,
            )
        except FamilyWallError as exc:
            return ErrorResponse(
                error_code=exc.info.code,
                error_message=exc.info.message,
                error_recovery=exc.info.recovery,
            )

    async def _set_calendar_event_attendees(
        self,
        event_id: str,
        date: str,
        assigned_to: list[str] | None = None,
        idempotency_key: str | None = None,
    ) -> SetCalendarEventAttendeesResponse | ErrorResponse:
        """Change only who an existing, safe-to-update event is assigned to.

        If writes are disabled, returns an error response with zero upstream calls.
        An unparseable ``date`` is rejected before any call.

        Args:
            event_id: The occurrence_id, e.g. from get_week_overview.
            date: The event's local date (``YYYY-MM-DD``) in the authenticated
                member's FamilyWall timezone.
            assigned_to: Member names to assign, exactly as list_family_members shows
                them. ``None`` or ``[]`` means everyone. Resolved the same
                refresh-once-on-unknown-name way, and at the same point (before any
                lookup, receipt or write), as ``create_calendar_event``'s
                ``assigned_to``.
            idempotency_key: Optional operation ID for idempotency; generates UUID if
                not provided.

        Returns:
            SetCalendarEventAttendeesResponse on success, or ErrorResponse on failure
            (including ``event_not_found`` if no event with ``event_id`` exists on
            that local day, and ``unsupported_event`` if the event is not an
            ordinary, one-off, timed, editable event on the family calendar).
        """
        # Write gate: check if writes are enabled BEFORE any upstream call
        if not self._config.enable_writes:
            return ErrorResponse(
                error_code="writes_disabled",
                error_message="Write operations are disabled.",
                error_recovery="Set FAMILYWALL_ENABLE_WRITES=true to enable writes.",
            )

        try:
            local_day = datetime.date.fromisoformat(date)
        except ValueError:
            return ErrorResponse(
                error_code="invalid_request",
                error_message="The date could not be parsed.",
                error_recovery="Use an ISO local date, e.g. 2026-10-06.",
            )

        try:
            principal, ctx = await self._context_resolver.resolve()
            principal, ctx, assignment = await self._resolve_assignment(principal, ctx, assigned_to)

            transport = write_transport(self._session_pool, principal)
            service = CalendarService(transport)

            # Generate operation ID if not provided
            operation_id = idempotency_key or str(uuid.uuid4())

            result = await service.set_event_attendees(
                principal,
                event_id,
                local_day,
                ctx.authenticated_member_timezone,
                assignment,
                operation_id,
                self._receipt_repository,
                ctx.family_context,
            )

            return SetCalendarEventAttendeesResponse(
                family_name=ctx.discovered_family.name,
                outcome=result.outcome.value,
                event_id=result.event_id,
                assigned_to=assignment.display_names,
                assigned_to_everyone=assignment.to_all,
                mismatched_fields=result.mismatched_fields,
            )
        except FamilyWallError as exc:
            return ErrorResponse(
                error_code=exc.info.code,
                error_message=exc.info.message,
                error_recovery=exc.info.recovery,
            )

    async def _resolve_assignment(
        self,
        principal: Principal,
        ctx: PrincipalContext,
        assigned_to: list[str] | None,
    ) -> tuple[Principal, PrincipalContext, ResolvedAssignment]:
        """Resolve ``assigned_to`` against the cached family, refreshing once.

        Shared by ``create_calendar_event`` and ``set_calendar_event_attendees``;
        callers must run this before any lookup, receipt or write. A name unknown
        to the cached family list triggers exactly one discovery refresh (the
        cached list may simply be stale) and a second resolution attempt; a
        second failure of any kind propagates. An ambiguous or otherwise invalid
        name never refreshes and makes zero upstream calls.

        Args:
            principal: The current principal, from ``context_resolver.resolve()``.
            ctx: The current principal's context, from the same call.
            assigned_to: Member names to resolve, or ``None``/``[]`` for everyone.

        Returns:
            The (possibly refreshed) principal, context and resolved assignment.

        Raises:
            MemberSelectionError: For an unknown name that a refresh still can't
                find, or immediately for an ambiguous or otherwise invalid name.
        """
        try:
            assignment = resolve_members(assigned_to, ctx.discovered_family)
        except MemberSelectionError as exc:
            if exc.info.code != "unknown_member":
                raise
            # The cached family list may simply be stale: refresh discovery
            # once and try again. A second failure (of any kind) propagates.
            principal, ctx = await self._context_resolver.refresh()
            assignment = resolve_members(assigned_to, ctx.discovered_family)
        return principal, ctx, assignment


def _invalid_event(detail: str) -> ErrorResponse:
    """Describe a rejected event request; nothing was sent upstream."""
    return ErrorResponse(
        error_code="invalid_event",
        error_message="The event details are not valid; no event was created.",
        error_recovery=f"Correct the request and try again ({detail}).",
    )


def _event_view(event: CalendarEvent, family: DiscoveredFamily) -> dict[str, object]:
    """Build one get_week_overview event entry, with assignment shown as names only."""
    assignment = describe_assignment(event.attendee_ids, event.to_all, family)
    return {
        "occurrence_id": event.occurrence_id,
        "title": event.title,
        "assigned_to": list(assignment.names),
        "assigned_to_everyone": assignment.everyone,
        "unresolved_members": assignment.unresolved,
    }


def _list_item_view(item: ListItem, family: DiscoveredFamily) -> ListItemResponse:
    """Build one get_list_items response item, with assignment shown as names only."""
    assignment = describe_assignment(item.assignee_ids, item.to_all, family)
    return ListItemResponse(
        item_id=item.item_id,
        text=item.text,
        completed=item.completed,
        description=item.description,
        assigned_to=assignment.names,
        assigned_to_everyone=assignment.everyone,
        unresolved_members=assignment.unresolved,
    )
