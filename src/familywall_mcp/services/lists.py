"""Shopping list selection and mutation service.

Provides multi-state mutation tracking and list selection with ambiguity resolution.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from typing import TYPE_CHECKING, Literal, Protocol

from pydantic import ValidationError

from familywall_mcp.errors import (
    AuthenticationError,
    ErrorInfo,
    FamilyWallError,
    InvalidEnvelopeError,
    MalformedPayloadError,
    RateLimitedError,
    TransportError,
    UnsupportedConfigurationError,
    UpstreamRejectedError,
)
from familywall_mcp.familywall.lists import (
    ListItem,
    ListType,
    ShoppingList,
    build_create2_item_fields,
    build_get_list_fields,
    build_get_lists_fields,
    build_mark_item_fields,
    build_update2_assignees_fields,
    parse_list_items,
    parse_list_summaries,
)
from familywall_mcp.models import DomainModel, OperationReceipt, Principal
from familywall_mcp.services.members import ResolvedAssignment

if TYPE_CHECKING:
    from familywall_mcp.interfaces import ReceiptRepository

_INDETERMINATE_ERRORS = (
    TransportError,
    RateLimitedError,
    InvalidEnvelopeError,
    MalformedPayloadError,
)
"""Errors caught as UNKNOWN: the request may or may not have reached FamilyWall."""

RECEIPT_TTL = timedelta(hours=24)


class ListTransport(Protocol):
    """Narrow injected protocol for upstream communication.

    The real implementation is wired during integration (from services.session
    or its replacement). Tests inject a fake that records calls.
    """

    async def call(self, endpoint: str, fields: Mapping[str, str]) -> object:
        """Call an endpoint with request fields.

        Args:
            endpoint: The FamilyWall endpoint name (e.g. "taskgettasklists")
            fields: Wire protocol request fields

        Returns:
            The parsed response (dict, list, or other object from JSON).

        Raises:
            Various exceptions for network errors, invalid responses, etc.
        """
        ...


class WriteOutcome(StrEnum):
    """Five-state outcome of a mutation operation."""

    CONFIRMED = "confirmed"  # upstream acknowledged AND a readback shows it
    ACKNOWLEDGED = "acknowledged"  # upstream said ok, readback did not confirm
    MISFILED = "misfiled"  # created, but it is in a list other than the one requested
    MISMATCHED = "mismatched"  # readback shows the write took effect, but differs
    UNKNOWN = "unknown"  # lost, timed out, or unparseable


class ListSelection(DomainModel):
    """Result of list selection, with resolved list or candidates to choose from."""

    resolved: ShoppingList | None
    candidates: tuple[ShoppingList, ...]
    reason: Literal["default", "only_eligible", "ambiguous", "none_eligible"]


class AddItemResult(DomainModel):
    """Result of adding an item to a list."""

    outcome: WriteOutcome
    item_id: str | None = None  # populated if confirmed, acknowledged, misfiled or mismatched
    # Populated if and only if outcome is misfiled:
    actual_list_id: str | None = None  # the list it actually landed in
    # Populated if and only if outcome is misfiled:
    requested_list_id: str | None = None  # the list that was requested
    # Populated if and only if outcome is mismatched:
    mismatched_fields: tuple[str, ...] = ()


class SetItemCheckedResult(DomainModel):
    """Result of marking an item as checked/unchecked."""

    outcome: WriteOutcome


class SetItemAssigneesResult(DomainModel):
    """Result of changing who a list item is assigned to."""

    outcome: WriteOutcome
    # Populated if and only if outcome is mismatched:
    mismatched_fields: tuple[str, ...] = ()


class ListService:
    """Shopping list selection, reading, and mutation operations.

    Implements list selection with ambiguity detection and three-state mutation
    tracking using a receipt repository for idempotency and crash recovery.
    """

    def __init__(self, transport: ListTransport) -> None:
        """Initialize the service with a transport.

        Args:
            transport: The injected upstream communication handler.
        """
        self.transport = transport

    async def list_accessible_lists(self, principal: Principal) -> tuple[ShoppingList, ...]:
        """Fetch the lists accessible to the principal.

        Args:
            principal: The authenticated subject.

        Returns:
            Tuple of accessible shopping lists.

        Raises:
            MalformedPayloadError: If the response shape is invalid.
        """
        fields = build_get_lists_fields()
        response = await self.transport.call("taskgettasklists", fields)
        return parse_list_summaries(response)

    async def get_list_items(self, principal: Principal, list_id: str) -> tuple[ListItem, ...]:
        """Fetch items in a list.

        Args:
            principal: The authenticated subject.
            list_id: The list metaId (taskList/...)

        Returns:
            Tuple of items in the list.

        Raises:
            MalformedPayloadError: If the response shape is invalid.
        """
        fields = build_get_list_fields(list_id)
        response = await self.transport.call("tasklist", fields)
        parsed = parse_list_items(response)
        return parsed.items

    async def select_list(
        self,
        principal: Principal,
        explicit_list_id: str | None = None,
        default_list_id: str | None = None,
    ) -> ListSelection:
        """Select a list for an operation, with ambiguity detection.

        Selection rules in order:
        1. Explicit list ID: verify it exists in caller's lists, error if not
        2. Default list ID: re-validate on every call, error if not accessible
        3. Only eligible list: if exactly one shopping list exists, use it
        4. Ambiguous: return candidates

        Eligibility for shopping add is known_type == ListType.SHOPPING.
        If no shopping list exists, offer all lists as fallback.

        Args:
            principal: The authenticated subject.
            explicit_list_id: A list ID supplied by caller, if any.
            default_list_id: A saved default list ID, if any.

        Returns:
            ListSelection with resolved list or candidates to choose from.

        Raises:
            UnsupportedConfigurationError: If explicit or default list is not in
                caller's accessible lists.
        """
        accessible = await self.list_accessible_lists(principal)

        # Rule 1: explicit list ID
        if explicit_list_id is not None:
            for lst in accessible:
                if lst.list_id == explicit_list_id:
                    return ListSelection(resolved=lst, candidates=(), reason="default")
            raise UnsupportedConfigurationError()

        # Rule 2: default list ID (re-validated)
        if default_list_id is not None:
            for lst in accessible:
                if lst.list_id == default_list_id:
                    return ListSelection(resolved=lst, candidates=(), reason="default")
            raise UnsupportedConfigurationError()

        # Rule 3 & 4: find eligible shopping lists
        shopping_lists = tuple(lst for lst in accessible if lst.known_type == ListType.SHOPPING)

        if len(shopping_lists) == 1:
            return ListSelection(resolved=shopping_lists[0], candidates=(), reason="only_eligible")

        if len(shopping_lists) > 1:
            return ListSelection(resolved=None, candidates=shopping_lists, reason="ambiguous")

        # No shopping lists: offer all as fallback
        if accessible:
            return ListSelection(resolved=None, candidates=accessible, reason="none_eligible")

        # No lists at all
        return ListSelection(resolved=None, candidates=(), reason="none_eligible")

    def _compute_payload_hash(self, fields: dict[str, str]) -> str:
        """Compute a hash of request fields for idempotency detection.

        Args:
            fields: The request fields dict.

        Returns:
            SHA256 hex digest of the sorted JSON representation.
        """
        # Sort fields for consistent hashing
        sorted_json = json.dumps(dict(sorted(fields.items())), sort_keys=True)
        return hashlib.sha256(sorted_json.encode()).hexdigest()

    async def add_item(
        self,
        principal: Principal,
        list_id: str,
        text: str,
        assignment: ResolvedAssignment,
        operation_id: str,
        receipt_repository: ReceiptRepository,
        family_id: str,
    ) -> tuple[AddItemResult, tuple[ListItem, ...]]:
        """Add an item directly to ``list_id`` in one taskcreate2 call.

        Superseded create-then-move (ADR 0002): a single taskcreate2 carries the
        target list and the assignment (probe A2, ADR 0003). No taskmove is ever
        sent, so the create-then-move partial-failure state no longer exists;
        ``misfiled`` remains only as a **detected** outcome, when the create
        response itself names a list other than the one requested — there is no
        move to fail and no compensation.

        ``assignment`` is already resolved (names to account IDs) by the caller;
        no member name ever reaches this method or the wire, only account IDs.
        Everyone is sent as ``to_all=True`` plus every member's account ID
        (extrapolated from the taskupdate2 encoding verified by probe A2, not
        directly observed on taskcreate2); named members are sent as
        ``to_all=False`` with just those IDs.

        taskcreate2 is sent at most once and never retried. The outcome is
        ``confirmed`` only when a readback of the requested list finds the
        created item with exactly the requested assignment (decision 5:
        everyone needs ``to_all is True``; named members need
        ``to_all is False`` and the same account ID set, order-insensitive).

        Receipts carry ``resource_id=list_id`` and ``action="list.add_item"``;
        the payload hash is computed over the complete taskcreate2 fields, so it
        naturally covers the assignment too, and a pre-upgrade receipt (the old
        ``taskcreate`` hash) conflicts rather than replaying — safe, since no
        duplicate is created. A definite refusal (``UpstreamRejectedError`` or
        ``AuthenticationError``) is recorded as ``status="rejected"`` with the
        error's code, message and recovery so a replay raises the same error
        rather than reporting ``unknown`` (the calendar pattern from slice D).

        Args:
            principal: The authenticated subject.
            list_id: The requested list metaId (taskList/...).
            text: The item text.
            assignment: The resolved assignment (everyone, or named members).
            operation_id: Operation ID supplied by caller; must not be empty.
            receipt_repository: Repository for receipt tracking and idempotency.
            family_id: Family ID for receipt scoping.

        Returns:
            Tuple of (AddItemResult with outcome, items in the requested list —
            populated only when a readback actually ran and returned a list).

        Raises:
            FamilyWallError: If operation_id conflicts with existing receipt.
            UpstreamRejectedError: If FamilyWall refused the create.
            AuthenticationError: If the session was not accepted for the create.
            ValueError: If operation_id is empty.
        """
        if not operation_id or not operation_id.strip():
            raise ValueError("operation_id must not be empty")

        fields = build_create2_item_fields(
            list_id=list_id,
            text=text,
            to_all=assignment.to_all,
            assignee_account_ids=assignment.account_ids,
        )
        payload_hash = self._compute_payload_hash(fields)

        existing_receipt = await receipt_repository.get(principal, operation_id)
        if existing_receipt is not None:
            return _replay_add_item(existing_receipt, payload_hash), ()

        async def record(
            status: Literal["pending", "succeeded", "unknown", "rejected"],
            upstream_id: str | None = None,
        ) -> None:
            await receipt_repository.put(
                OperationReceipt(
                    subject=principal.subject,
                    family_id=family_id,
                    resource_id=list_id,
                    action="list.add_item",
                    operation_id=operation_id,
                    payload_hash=payload_hash,
                    status=status,
                    upstream_id=upstream_id,
                    expires_at=datetime.now(UTC) + RECEIPT_TTL,
                )
            )

        await record("pending")

        try:
            create_response = await self.transport.call("taskcreate2", fields)
        except (UpstreamRejectedError, AuthenticationError) as exc:
            # A definite refusal: nothing was created. Record it as rejected so a
            # replay of this key raises the same refusal instead of resending.
            info = exc.info
            await record(
                "rejected",
                json.dumps({"code": info.code, "message": info.message, "recovery": info.recovery}),
            )
            raise
        except _INDETERMINATE_ERRORS:
            await record("unknown")
            return AddItemResult(outcome=WriteOutcome.UNKNOWN), ()

        if not isinstance(create_response, dict):
            await record("unknown")
            return AddItemResult(outcome=WriteOutcome.UNKNOWN), ()

        created_item_id = create_response.get("metaId") or create_response.get("taskId")
        created_list_id = create_response.get("taskListId")

        if not created_item_id:
            await record("unknown")
            return AddItemResult(outcome=WriteOutcome.UNKNOWN), ()

        if not created_list_id:
            # An ID is present but the response named no list at all.
            result = AddItemResult(outcome=WriteOutcome.ACKNOWLEDGED, item_id=created_item_id)
            await record("succeeded", result.model_dump_json())
            return result, ()

        if created_list_id != list_id:
            # Detected, not caused: no move was sent and none is sent now.
            result = AddItemResult(
                outcome=WriteOutcome.MISFILED,
                item_id=created_item_id,
                actual_list_id=created_list_id,
                requested_list_id=list_id,
            )
            await record("succeeded", result.model_dump_json())
            return result, ()

        # Record the acknowledgement before the readback, so a crash replays as
        # acknowledged rather than unknown.
        acknowledged = AddItemResult(outcome=WriteOutcome.ACKNOWLEDGED, item_id=created_item_id)
        await record("succeeded", acknowledged.model_dump_json())
        result, items = await self._confirm_added(principal, list_id, created_item_id, assignment)
        await record("succeeded", result.model_dump_json())
        return result, items

    async def _confirm_added(
        self,
        principal: Principal,
        list_id: str,
        created_item_id: str,
        assignment: ResolvedAssignment,
    ) -> tuple[AddItemResult, tuple[ListItem, ...]]:
        """Read ``list_id`` back and compare the created item's assignment."""
        try:
            items = await self.get_list_items(principal, list_id)
        except _INDETERMINATE_ERRORS:
            return AddItemResult(outcome=WriteOutcome.ACKNOWLEDGED, item_id=created_item_id), ()

        found = next((item for item in items if item.item_id == created_item_id), None)
        if found is None:
            return AddItemResult(outcome=WriteOutcome.ACKNOWLEDGED, item_id=created_item_id), ()

        if not _assignment_matches(found, assignment):
            return (
                AddItemResult(
                    outcome=WriteOutcome.MISMATCHED,
                    item_id=created_item_id,
                    mismatched_fields=("assignees",),
                ),
                items,
            )

        return AddItemResult(outcome=WriteOutcome.CONFIRMED, item_id=created_item_id), items

    async def set_item_assignees(
        self,
        principal: Principal,
        item_id: str,
        assignment: ResolvedAssignment,
        operation_id: str,
        receipt_repository: ReceiptRepository,
        family_id: str,
    ) -> SetItemAssigneesResult:
        """Change only who ``item_id`` is assigned to, via one partial taskupdate2.

        SECURITY: this first reads every accessible list to verify the item
        belongs to one of them, exactly as ``set_item_checked`` does. The
        taskupdate2 endpoint sends no list ID, so server enforcement is
        impossible; verification MUST happen before sending the request, or a
        foreign item's assignment could be changed. A foreign item raises
        ``UnsupportedConfigurationError`` with zero writes and no receipt.

        ``assignment`` is already resolved by the caller; no member name ever
        reaches this method or the wire. taskupdate2 is sent at most once and
        never retried. The outcome is ``confirmed`` only when a readback shows
        the new assignment and nothing else about the item changed (text,
        description, completed, list, due date and reminder all compared
        against the item as read immediately before the write); any other
        difference is ``mismatched``, naming every differing field.

        Receipts carry ``resource_id=item_id`` and ``action="list.set_assignees"``,
        following the same pending/succeeded/rejected/replay pattern as
        ``add_item`` (the calendar pattern from slice D).

        Args:
            principal: The authenticated subject.
            item_id: The item metaId (task/...).
            assignment: The resolved assignment (everyone, or named members).
            operation_id: Operation ID supplied by caller; must not be empty.
            receipt_repository: Repository for receipt tracking and idempotency.
            family_id: Family ID for receipt scoping.

        Returns:
            SetItemAssigneesResult with the outcome.

        Raises:
            UnsupportedConfigurationError: If the item belongs to an
                inaccessible list (zero requests sent, no receipt written).
            FamilyWallError: If operation_id conflicts with existing receipt.
            UpstreamRejectedError: If FamilyWall refused the update.
            AuthenticationError: If the session was not accepted for the update.
            ValueError: If operation_id is empty.
        """
        if not operation_id or not operation_id.strip():
            raise ValueError("operation_id must not be empty")

        fields = build_update2_assignees_fields(
            item_id=item_id,
            to_all=assignment.to_all,
            assignee_account_ids=assignment.account_ids,
        )
        payload_hash = self._compute_payload_hash(fields)

        existing_receipt = await receipt_repository.get(principal, operation_id)
        if existing_receipt is not None:
            return _replay_set_assignees(existing_receipt, payload_hash)

        # Verify the item belongs to an accessible list BEFORE sending taskupdate2.
        # This is a security requirement; foreign items must be rejected with zero
        # requests and zero receipts.
        item_list_id: str | None = None
        before: ListItem | None = None
        accessible = await self.list_accessible_lists(principal)
        for lst in accessible:
            items = await self.get_list_items(principal, lst.list_id)
            for item in items:
                if item.item_id == item_id:
                    item_list_id = lst.list_id
                    before = item
                    break
            if item_list_id is not None:
                break

        if item_list_id is None or before is None:
            raise UnsupportedConfigurationError()

        async def record(
            status: Literal["pending", "succeeded", "unknown", "rejected"],
            upstream_id: str | None = None,
        ) -> None:
            await receipt_repository.put(
                OperationReceipt(
                    subject=principal.subject,
                    family_id=family_id,
                    resource_id=item_id,
                    action="list.set_assignees",
                    operation_id=operation_id,
                    payload_hash=payload_hash,
                    status=status,
                    upstream_id=upstream_id,
                    expires_at=datetime.now(UTC) + RECEIPT_TTL,
                )
            )

        await record("pending")

        try:
            await self.transport.call("taskupdate2", fields)
        except (UpstreamRejectedError, AuthenticationError) as exc:
            info = exc.info
            await record(
                "rejected",
                json.dumps({"code": info.code, "message": info.message, "recovery": info.recovery}),
            )
            raise
        except _INDETERMINATE_ERRORS:
            await record("unknown")
            return SetItemAssigneesResult(outcome=WriteOutcome.UNKNOWN)

        # Record the acknowledgement before the readback, so a crash replays as
        # acknowledged rather than unknown.
        acknowledged = SetItemAssigneesResult(outcome=WriteOutcome.ACKNOWLEDGED)
        await record("succeeded", acknowledged.model_dump_json())
        result = await self._confirm_set_assignees(
            principal, item_list_id, item_id, before, assignment
        )
        await record("succeeded", result.model_dump_json())
        return result

    async def _confirm_set_assignees(
        self,
        principal: Principal,
        list_id: str,
        item_id: str,
        before: ListItem,
        assignment: ResolvedAssignment,
    ) -> SetItemAssigneesResult:
        """Read ``list_id`` back and compare the item against ``before`` and ``assignment``."""
        try:
            items = await self.get_list_items(principal, list_id)
        except _INDETERMINATE_ERRORS:
            return SetItemAssigneesResult(outcome=WriteOutcome.ACKNOWLEDGED)

        found = next((item for item in items if item.item_id == item_id), None)
        if found is None:
            return SetItemAssigneesResult(outcome=WriteOutcome.ACKNOWLEDGED)

        mismatched = _set_assignees_mismatches(found, before, assignment)
        if mismatched:
            return SetItemAssigneesResult(
                outcome=WriteOutcome.MISMATCHED, mismatched_fields=mismatched
            )
        return SetItemAssigneesResult(outcome=WriteOutcome.CONFIRMED)

    async def set_item_checked(
        self,
        principal: Principal,
        item_id: str,
        checked: bool,
        operation_id: str,
        receipt_repository: ReceiptRepository,
        family_id: str,
    ) -> SetItemCheckedResult:
        """Mark an item as checked or unchecked.

        SECURITY: This operation first reads the list to verify the item belongs
        to an accessible list. The taskmark endpoint sends no list ID, so server
        enforcement is impossible. Verification MUST happen before sending the
        request, or a foreign item could be marked complete.

        Implements receipt-based idempotency for deduplication and crash recovery.
        UNKNOWN outcomes are never retried automatically.

        Args:
            principal: The authenticated subject.
            item_id: The item metaId (task/...)
            checked: Whether the item is complete.
            operation_id: Operation ID supplied by caller; must not be empty.
            receipt_repository: Repository for receipt tracking and idempotency.
            family_id: Family ID for receipt scoping.

        Returns:
            SetItemCheckedResult with outcome.

        Raises:
            UnsupportedConfigurationError: If the item belongs to an inaccessible list
                (zero requests sent).
            FamilyWallError: If operation_id conflicts with existing receipt.
            ValueError: If operation_id is empty.
        """
        if not operation_id or not operation_id.strip():
            raise ValueError("operation_id must not be empty")

        fields = build_mark_item_fields(item_id, checked)
        payload_hash = self._compute_payload_hash(fields)

        # Check receipt repository for existing operation (before any item verification)
        existing_receipt = await receipt_repository.get(principal, operation_id)
        if existing_receipt is not None:
            # A receipt written by a different tool (or reused across tools) never
            # replays as this one; a migrated "legacy" receipt is accepted either way.
            if existing_receipt.action not in ("list.set_checked", "legacy"):
                raise FamilyWallError(
                    ErrorInfo(
                        code="operation_id_conflict",
                        message="An operation with this ID already exists with different content.",
                        recovery="Use a different operation ID for this request.",
                    )
                )
            # If status is pending (previous process crashed mid-write), resolve to UNKNOWN
            if existing_receipt.status == "pending":
                return SetItemCheckedResult(outcome=WriteOutcome.UNKNOWN)
            # Check if payload matches (for succeeded/unknown receipts)
            if existing_receipt.payload_hash != payload_hash:
                # Payload mismatch: caller is reusing operation_id with different content
                raise FamilyWallError(
                    ErrorInfo(
                        code="operation_id_conflict",
                        message="An operation with this ID already exists with different content.",
                        recovery="Use a different operation ID for this request.",
                    )
                )
            # Return the stored result from upstream_id
            if existing_receipt.status == "succeeded" and existing_receipt.upstream_id:
                try:
                    result_data = json.loads(existing_receipt.upstream_id)
                    stored_outcome = WriteOutcome(result_data.get("outcome", "confirmed"))
                    return SetItemCheckedResult(outcome=stored_outcome)
                except (json.JSONDecodeError, ValueError):
                    pass
            # Fallback: if status is succeeded, return confirmed
            return SetItemCheckedResult(outcome=WriteOutcome.CONFIRMED)

        # Verify the item belongs to an accessible list BEFORE sending taskmark
        # This is a security requirement; foreign items must be rejected with zero requests
        item_list_id: str | None = None
        accessible = await self.list_accessible_lists(principal)
        for lst in accessible:
            items = await self.get_list_items(principal, lst.list_id)
            for item in items:
                if item.item_id == item_id:
                    # Item found in an accessible list; safe to mark
                    item_list_id = lst.list_id
                    break
            if item_list_id is not None:
                break

        if item_list_id is None:
            # Item not found in any accessible list
            raise UnsupportedConfigurationError()

        # Create a pending receipt
        pending_receipt = OperationReceipt(
            subject=principal.subject,
            family_id=family_id,
            resource_id=item_list_id,
            action="list.set_checked",
            operation_id=operation_id,
            payload_hash=payload_hash,
            status="pending",
            upstream_id=None,
            expires_at=datetime.now(UTC) + timedelta(hours=24),
        )
        await receipt_repository.put(pending_receipt)

        # Send the request
        try:
            await self.transport.call("taskmark", fields)
            outcome = WriteOutcome.CONFIRMED
        except (TransportError, RateLimitedError, InvalidEnvelopeError, MalformedPayloadError):
            # Request left the process; outcome is unknown (network error, rate limit,
            # unparseable response). Do not retry. Programming errors propagate as themselves.
            outcome = WriteOutcome.UNKNOWN
            unknown_receipt = OperationReceipt(
                subject=principal.subject,
                family_id=family_id,
                resource_id=item_list_id,
                action="list.set_checked",
                operation_id=operation_id,
                payload_hash=payload_hash,
                status="unknown",
                upstream_id=None,
                expires_at=datetime.now(UTC) + timedelta(hours=24),
            )
            await receipt_repository.put(unknown_receipt)
            return SetItemCheckedResult(outcome=outcome)

        # Update receipt with final status
        result_data = {"outcome": outcome.value}
        final_receipt = OperationReceipt(
            subject=principal.subject,
            family_id=family_id,
            resource_id=item_list_id,
            action="list.set_checked",
            operation_id=operation_id,
            payload_hash=payload_hash,
            status="succeeded",
            upstream_id=json.dumps(result_data),
            expires_at=datetime.now(UTC) + timedelta(hours=24),
        )
        await receipt_repository.put(final_receipt)

        return SetItemCheckedResult(outcome=outcome)


def _assignment_matches(item: ListItem, assignment: ResolvedAssignment) -> bool:
    """Decision 5's assignment match rule.

    Everyone means ``item.to_all is True``. Named members means
    ``item.to_all is False`` and the same account ID set (order-insensitive).
    """
    if assignment.to_all:
        return item.to_all is True
    return item.to_all is False and set(item.assignee_ids) == set(assignment.account_ids)


def _set_assignees_mismatches(
    found: ListItem, before: ListItem, assignment: ResolvedAssignment
) -> tuple[str, ...]:
    """Name every field where the read-back item differs from ``before`` or ``assignment``."""
    mismatched: list[str] = []
    if not _assignment_matches(found, assignment):
        mismatched.append("assignees")
    if found.text != before.text:
        mismatched.append("text")
    if found.description != before.description:
        mismatched.append("description")
    if found.completed != before.completed:
        mismatched.append("completed")
    if found.list_id != before.list_id:
        mismatched.append("list_id")
    if found.due_date != before.due_date:
        mismatched.append("due_date")
    if found.reminder != before.reminder:
        mismatched.append("reminder")
    return tuple(mismatched)


def _conflict_error() -> FamilyWallError:
    """The stable operation_id_conflict error, shared by both replay helpers."""
    return FamilyWallError(
        ErrorInfo(
            code="operation_id_conflict",
            message="An operation with this ID already exists with different content.",
            recovery="Use a different operation ID for this request.",
        )
    )


def _rebuild_rejection(upstream_id: str | None) -> FamilyWallError:
    """Rebuild the original refusal from its stored JSON, for a rejected replay.

    Falls back to a plain ``UpstreamRejectedError`` if the JSON is missing or
    unreadable, so a replay never raises anything but a ``FamilyWallError``.
    """
    if upstream_id:
        try:
            info = json.loads(upstream_id)
            return FamilyWallError(
                ErrorInfo(code=info["code"], message=info["message"], recovery=info["recovery"])
            )
        except (json.JSONDecodeError, KeyError, TypeError):
            pass
    return UpstreamRejectedError()


def _replay_add_item(existing: OperationReceipt, payload_hash: str) -> AddItemResult:
    """Resolve a repeated add_item operation ID from its stored receipt, with no upstream call."""
    # A receipt written by a different tool (or reused across tools) never replays
    # as this one; a migrated "legacy" receipt is accepted either way. A
    # pre-upgrade add_list_item receipt (the old taskcreate hash) falls through
    # to the payload_hash check below and conflicts, since the fields changed.
    if existing.action not in ("list.add_item", "legacy"):
        raise _conflict_error()
    if existing.payload_hash != payload_hash:
        raise _conflict_error()
    if existing.status == "rejected":
        raise _rebuild_rejection(existing.upstream_id)
    if existing.status == "succeeded" and existing.upstream_id:
        try:
            return AddItemResult.model_validate_json(existing.upstream_id)
        except ValidationError:
            return AddItemResult(outcome=WriteOutcome.ACKNOWLEDGED)
    # pending (a crash mid-write) and unknown both resolve to unknown.
    return AddItemResult(outcome=WriteOutcome.UNKNOWN)


def _replay_set_assignees(existing: OperationReceipt, payload_hash: str) -> SetItemAssigneesResult:
    """Resolve a repeated set_item_assignees operation ID from its stored receipt."""
    if existing.action not in ("list.set_assignees", "legacy"):
        raise _conflict_error()
    if existing.payload_hash != payload_hash:
        raise _conflict_error()
    if existing.status == "rejected":
        raise _rebuild_rejection(existing.upstream_id)
    if existing.status == "succeeded" and existing.upstream_id:
        try:
            return SetItemAssigneesResult.model_validate_json(existing.upstream_id)
        except ValidationError:
            return SetItemAssigneesResult(outcome=WriteOutcome.ACKNOWLEDGED)
    # pending (a crash mid-write) and unknown both resolve to unknown.
    return SetItemAssigneesResult(outcome=WriteOutcome.UNKNOWN)
