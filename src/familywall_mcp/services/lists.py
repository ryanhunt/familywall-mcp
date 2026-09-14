"""Shopping list selection and mutation service.

Provides three-state mutation tracking and list selection with ambiguity resolution.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from typing import TYPE_CHECKING, Literal, Protocol

from familywall_mcp.errors import (
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
    build_create_item_fields,
    build_get_list_fields,
    build_get_lists_fields,
    build_mark_item_fields,
    build_move_item_fields,
    parse_list_items,
    parse_list_summaries,
)
from familywall_mcp.models import DomainModel, OperationReceipt, Principal

if TYPE_CHECKING:
    from familywall_mcp.interfaces import ReceiptRepository


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
    """Four-state outcome of a mutation operation."""

    CONFIRMED = "confirmed"  # upstream acknowledged AND a readback shows it
    ACKNOWLEDGED = "acknowledged"  # upstream said ok, readback did not confirm
    MISFILED = "misfiled"  # created, but it is in the wrong list
    UNKNOWN = "unknown"  # lost, timed out, or unparseable


class ListSelection(DomainModel):
    """Result of list selection, with resolved list or candidates to choose from."""

    resolved: ShoppingList | None
    candidates: tuple[ShoppingList, ...]
    reason: Literal["default", "only_eligible", "ambiguous", "none_eligible"]


class AddItemResult(DomainModel):
    """Result of adding an item to a list."""

    outcome: WriteOutcome
    item_id: str | None = None  # populated if confirmed, acknowledged, or misfiled
    # Populated if and only if outcome is misfiled:
    actual_list_id: str | None = None  # the list it actually landed in
    # Populated if and only if outcome is misfiled:
    requested_list_id: str | None = None  # the list that was requested


class SetItemCheckedResult(DomainModel):
    """Result of marking an item as checked/unchecked."""

    outcome: WriteOutcome


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
        operation_id: str,
        receipt_repository: ReceiptRepository,
        family_id: str,
    ) -> tuple[AddItemResult, tuple[ListItem, ...]]:
        """Add an item to a list via create-then-move.

        Implements receipt-based idempotency for deduplication and crash recovery.
        Creates in the default list, then moves to the requested list if necessary.

        The operation is non-atomic: if create succeeds but move fails, the item exists
        in the default list and a MISFILED outcome is returned. No automatic retry or
        delete is performed. See ADR 0002.

        Args:
            principal: The authenticated subject.
            list_id: The requested list metaId (taskList/...)
            text: The item text.
            operation_id: Operation ID supplied by caller; must not be empty.
            receipt_repository: Repository for receipt tracking and idempotency.
            family_id: Family ID for receipt scoping.

        Returns:
            Tuple of (AddItemResult with outcome, updated items in requested list or empty).

        Raises:
            FamilyWallError: If operation_id conflicts with existing receipt.
            ValueError: If operation_id is empty.
        """
        if not operation_id or not operation_id.strip():
            raise ValueError("operation_id must not be empty")

        # Build create fields (text only, no list ID, no quantity)
        create_fields = build_create_item_fields(text)
        payload_hash = self._compute_payload_hash(create_fields)

        # Check receipt repository for existing operation
        existing_receipt = await receipt_repository.get(principal, operation_id)
        if existing_receipt is not None:
            # If status is pending (previous process crashed mid-write), resolve to UNKNOWN
            if existing_receipt.status == "pending":
                return (
                    AddItemResult(outcome=WriteOutcome.UNKNOWN),
                    (),
                )
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
            # Return the stored result from upstream_id (contains outcome as JSON)
            if existing_receipt.status == "succeeded" and existing_receipt.upstream_id:
                try:
                    result_data = json.loads(existing_receipt.upstream_id)
                    stored_outcome = WriteOutcome(result_data.get("outcome", "acknowledged"))
                    stored_item_id = result_data.get("item_id")
                    stored_actual_list_id = result_data.get("actual_list_id")
                    stored_requested_list_id = result_data.get("requested_list_id")
                    return (
                        AddItemResult(
                            outcome=stored_outcome,
                            item_id=stored_item_id,
                            actual_list_id=stored_actual_list_id,
                            requested_list_id=stored_requested_list_id,
                        ),
                        (),
                    )
                except (json.JSONDecodeError, ValueError):
                    pass
            # Fallback: if status is succeeded, return acknowledged
            return (
                AddItemResult(outcome=WriteOutcome.ACKNOWLEDGED),
                (),
            )

        # New operation; create a pending receipt
        pending_receipt = OperationReceipt(
            subject=principal.subject,
            family_id=family_id,
            list_id=list_id,
            operation_id=operation_id,
            payload_hash=payload_hash,
            status="pending",
            upstream_id=None,
            expires_at=datetime.now(UTC) + timedelta(hours=24),
        )
        await receipt_repository.put(pending_receipt)

        # Step 1: Create the item (always in default list)
        try:
            create_response = await self.transport.call("taskcreate", create_fields)
        except (TransportError, RateLimitedError, InvalidEnvelopeError, MalformedPayloadError):
            # Create failed indeterminately. Outcome is unknown.
            unknown_receipt = OperationReceipt(
                subject=principal.subject,
                family_id=family_id,
                list_id=list_id,
                operation_id=operation_id,
                payload_hash=payload_hash,
                status="unknown",
                upstream_id=None,
                expires_at=datetime.now(UTC) + timedelta(hours=24),
            )
            await receipt_repository.put(unknown_receipt)
            return (
                AddItemResult(outcome=WriteOutcome.UNKNOWN),
                (),
            )

        # Parse create response: expecting a full task object with metaId and taskListId
        if not isinstance(create_response, dict):
            unknown_receipt = OperationReceipt(
                subject=principal.subject,
                family_id=family_id,
                list_id=list_id,
                operation_id=operation_id,
                payload_hash=payload_hash,
                status="unknown",
                upstream_id=None,
                expires_at=datetime.now(UTC) + timedelta(hours=24),
            )
            await receipt_repository.put(unknown_receipt)
            return (
                AddItemResult(outcome=WriteOutcome.UNKNOWN),
                (),
            )

        # Extract item ID and actual list from response
        created_item_id = create_response.get("metaId") or create_response.get("taskId")
        created_list_id = create_response.get("taskListId")

        if not created_item_id or not created_list_id:
            unknown_receipt = OperationReceipt(
                subject=principal.subject,
                family_id=family_id,
                list_id=list_id,
                operation_id=operation_id,
                payload_hash=payload_hash,
                status="unknown",
                upstream_id=None,
                expires_at=datetime.now(UTC) + timedelta(hours=24),
            )
            await receipt_repository.put(unknown_receipt)
            return (
                AddItemResult(outcome=WriteOutcome.UNKNOWN),
                (),
            )

        # Step 2: Decide if we need to move
        # If it's already in the requested list, skip the move entirely
        if created_list_id == list_id:
            # Item is already in the right list; no move needed
            result_data = {
                "outcome": WriteOutcome.CONFIRMED.value,
                "item_id": created_item_id,
            }
            final_receipt = OperationReceipt(
                subject=principal.subject,
                family_id=family_id,
                list_id=list_id,
                operation_id=operation_id,
                payload_hash=payload_hash,
                status="succeeded",
                upstream_id=json.dumps(result_data),
                expires_at=datetime.now(UTC) + timedelta(hours=24),
            )
            await receipt_repository.put(final_receipt)

            # Read back to confirm
            items = await self.get_list_items(principal, list_id)
            found = any(item.item_id == created_item_id for item in items)
            outcome = WriteOutcome.CONFIRMED if found else WriteOutcome.ACKNOWLEDGED

            return (
                AddItemResult(
                    outcome=outcome,
                    item_id=created_item_id,
                    actual_list_id=None,
                    requested_list_id=None,
                ),
                items,
            )

        # Step 3: Move is needed
        move_fields = build_move_item_fields(created_item_id, list_id)

        try:
            await self.transport.call("taskmove", move_fields)
        except UpstreamRejectedError:
            # Definite failure: move was rejected by the server
            # The item is definitely in created_list_id (the default list)
            result_data = {
                "outcome": WriteOutcome.MISFILED.value,
                "item_id": created_item_id,
                "actual_list_id": created_list_id,
                "requested_list_id": list_id,
            }
            misfiled_receipt = OperationReceipt(
                subject=principal.subject,
                family_id=family_id,
                list_id=list_id,
                operation_id=operation_id,
                payload_hash=payload_hash,
                status="succeeded",
                upstream_id=json.dumps(result_data),
                expires_at=datetime.now(UTC) + timedelta(hours=24),
            )
            await receipt_repository.put(misfiled_receipt)
            return (
                AddItemResult(
                    outcome=WriteOutcome.MISFILED,
                    item_id=created_item_id,
                    actual_list_id=created_list_id,
                    requested_list_id=list_id,
                ),
                (),
            )
        except (TransportError, RateLimitedError, InvalidEnvelopeError, MalformedPayloadError):
            # Indeterminate failure: we don't know if the move happened
            # The create definitely happened, so we know the item's id and location
            # No retry, no delete. Report misfiled.
            result_data = {
                "outcome": WriteOutcome.MISFILED.value,
                "item_id": created_item_id,
                "actual_list_id": created_list_id,
                "requested_list_id": list_id,
            }
            misfiled_receipt = OperationReceipt(
                subject=principal.subject,
                family_id=family_id,
                list_id=list_id,
                operation_id=operation_id,
                payload_hash=payload_hash,
                status="succeeded",
                upstream_id=json.dumps(result_data),
                expires_at=datetime.now(UTC) + timedelta(hours=24),
            )
            await receipt_repository.put(misfiled_receipt)
            return (
                AddItemResult(
                    outcome=WriteOutcome.MISFILED,
                    item_id=created_item_id,
                    actual_list_id=created_list_id,
                    requested_list_id=list_id,
                ),
                (),
            )

        # Move succeeded. Now try to read back to confirm
        try:
            items = await self.get_list_items(principal, list_id)
        except (TransportError, RateLimitedError, InvalidEnvelopeError, MalformedPayloadError):
            # Readback failed; we know the move was sent and no definite rejection occurred
            # so it may have succeeded. Report as acknowledged.
            result_data = {
                "outcome": WriteOutcome.ACKNOWLEDGED.value,
                "item_id": created_item_id,
            }
            final_receipt = OperationReceipt(
                subject=principal.subject,
                family_id=family_id,
                list_id=list_id,
                operation_id=operation_id,
                payload_hash=payload_hash,
                status="succeeded",
                upstream_id=json.dumps(result_data),
                expires_at=datetime.now(UTC) + timedelta(hours=24),
            )
            await receipt_repository.put(final_receipt)
            return (
                AddItemResult(
                    outcome=WriteOutcome.ACKNOWLEDGED,
                    item_id=created_item_id,
                    actual_list_id=None,
                    requested_list_id=None,
                ),
                (),
            )

        # Look for the newly moved item in the target list
        found = any(item.item_id == created_item_id for item in items)
        outcome = WriteOutcome.CONFIRMED if found else WriteOutcome.ACKNOWLEDGED

        # Update receipt with final status
        result_data = {
            "outcome": outcome.value,
            "item_id": created_item_id,
        }
        final_receipt = OperationReceipt(
            subject=principal.subject,
            family_id=family_id,
            list_id=list_id,
            operation_id=operation_id,
            payload_hash=payload_hash,
            status="succeeded",
            upstream_id=json.dumps(result_data),
            expires_at=datetime.now(UTC) + timedelta(hours=24),
        )
        await receipt_repository.put(final_receipt)

        return (
            AddItemResult(
                outcome=outcome,
                item_id=created_item_id,
                actual_list_id=None,
                requested_list_id=None,
            ),
            items,
        )

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
            list_id=item_list_id,
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
                list_id=item_list_id,
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
            list_id=item_list_id,
            operation_id=operation_id,
            payload_hash=payload_hash,
            status="succeeded",
            upstream_id=json.dumps(result_data),
            expires_at=datetime.now(UTC) + timedelta(hours=24),
        )
        await receipt_repository.put(final_receipt)

        return SetItemCheckedResult(outcome=outcome)
