"""Editing and deleting list items, and deleting whole lists.

Every operation first establishes that its target belongs to one of the caller's
accessible lists: taskupdate2, taskdelete and taskdeletelist carry no family
scope of their own, so that check cannot be left to the server. FamilyWall's two
built-in lists (shopping and to-do) are never deleted. Deletes are immediate
with no undo, so each one also requires the name of its target as confirmation
and is refused unless that matches the current name.

Receipts follow the calendar writes: a receipt is written before the request
leaves the process, an uncertain outcome is recorded as UNKNOWN and never
retried, and a replayed operation ID returns the stored outcome without
contacting FamilyWall again.
"""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime, timedelta

from familywall_mcp.errors import (
    ErrorInfo,
    FamilyWallError,
    InvalidEnvelopeError,
    MalformedPayloadError,
    RateLimitedError,
    TransportError,
    UnsupportedConfigurationError,
)
from familywall_mcp.familywall.lists import (
    build_delete_item_fields,
    build_delete_list_fields,
    build_update_item_fields,
)
from familywall_mcp.interfaces import ReceiptRepository
from familywall_mcp.models import DomainModel, OperationReceipt, Principal, ReceiptAction
from familywall_mcp.services.lists import ListService, ListTransport, WriteOutcome

MAX_ITEM_TEXT_LENGTH = 500
RECEIPT_TTL = timedelta(hours=24)

# The request left the process but its effect is unknowable from here.
_UNCERTAIN_ERRORS = (TransportError, RateLimitedError, InvalidEnvelopeError, MalformedPayloadError)


class ListWriteResult(DomainModel):
    """Outcome of a list edit or delete."""

    outcome: WriteOutcome


def _invalid(message: str) -> FamilyWallError:
    return FamilyWallError(
        ErrorInfo(
            code="invalid_item",
            message=message,
            recovery="Check the identifier or text and try again.",
        )
    )


def _hash(fields: dict[str, str]) -> str:
    return hashlib.sha256(json.dumps(fields, sort_keys=True).encode()).hexdigest()


class ListEditService:
    """Edits and deletes list items and whole lists."""

    def __init__(self, transport: ListTransport) -> None:
        self.transport = transport
        self._lists = ListService(transport)

    async def update_item_text(
        self,
        principal: Principal,
        item_id: str,
        text: str,
        operation_id: str,
        receipt_repository: ReceiptRepository,
        family_id: str,
    ) -> ListWriteResult:
        """Change an item's text, leaving everything else about it as it was.

        Returns:
            CONFIRMED when a re-read shows the new text, UNKNOWN when it does not
            or the request's effect is unknowable, ACKNOWLEDGED when the re-read
            failed.

        Raises:
            FamilyWallError: `invalid_item` (no request sent) or
                `operation_id_conflict`.
            UnsupportedConfigurationError: If the item is not in an accessible
                list (no write sent).
        """
        _require_operation_id(operation_id)
        text = text.strip()
        if not text:
            raise _invalid("The item needs text.")
        if len(text) > MAX_ITEM_TEXT_LENGTH:
            raise _invalid(f"The text is longer than {MAX_ITEM_TEXT_LENGTH} characters.")
        try:
            fields = build_update_item_fields(item_id, text)
        except ValueError as exc:
            raise _invalid("That is not a list item identifier.") from exc

        payload_hash = _hash(fields)
        replayed = await _replay(
            "list.update_item", principal, operation_id, payload_hash, receipt_repository
        )
        if replayed is not None:
            return replayed

        list_id, _text = await self._locate(principal, item_id)
        await _record(
            "list.update_item",
            receipt_repository,
            principal,
            family_id,
            list_id,
            operation_id,
            payload_hash,
            "pending",
        )
        try:
            await self.transport.call("taskupdate2", fields)
        except _UNCERTAIN_ERRORS:
            await _record(
                "list.update_item",
                receipt_repository,
                principal,
                family_id,
                list_id,
                operation_id,
                payload_hash,
                "unknown",
            )
            return ListWriteResult(outcome=WriteOutcome.UNKNOWN)

        try:
            items = await self._lists.get_list_items(principal, list_id)
        except FamilyWallError:
            outcome = WriteOutcome.ACKNOWLEDGED
        else:
            shown = any(item.item_id == item_id and item.text == text for item in items)
            outcome = WriteOutcome.CONFIRMED if shown else WriteOutcome.UNKNOWN
        return await _finish(
            "list.update_item",
            receipt_repository,
            principal,
            family_id,
            list_id,
            operation_id,
            payload_hash,
            outcome,
        )

    async def delete_item(
        self,
        principal: Principal,
        item_id: str,
        expected_text: str,
        operation_id: str,
        receipt_repository: ReceiptRepository,
        family_id: str,
    ) -> ListWriteResult:
        """Delete one list item.

        Returns:
            CONFIRMED when a re-read no longer shows the item, UNKNOWN when the
            server did not confirm the delete or the item is still there,
            ACKNOWLEDGED when the re-read failed.

        Raises:
            FamilyWallError: `invalid_item` (no request sent) or
                `operation_id_conflict`.
            UnsupportedConfigurationError: If the item is not in an accessible
                list (no write sent).
        """
        _require_operation_id(operation_id)
        expected = _confirmation(expected_text)
        try:
            fields = build_delete_item_fields(item_id)
        except ValueError as exc:
            raise _invalid("That is not a list item identifier.") from exc

        payload_hash = _hash({**fields, "confirm": expected})
        replayed = await _replay(
            "list.delete_item", principal, operation_id, payload_hash, receipt_repository
        )
        if replayed is not None:
            return replayed

        list_id, item_text = await self._locate(principal, item_id)
        _require_match("item", item_text, expected)
        await _record(
            "list.delete_item",
            receipt_repository,
            principal,
            family_id,
            list_id,
            operation_id,
            payload_hash,
            "pending",
        )
        try:
            response = await self.transport.call("taskdelete", fields)
        except _UNCERTAIN_ERRORS:
            await _record(
                "list.delete_item",
                receipt_repository,
                principal,
                family_id,
                list_id,
                operation_id,
                payload_hash,
                "unknown",
            )
            return ListWriteResult(outcome=WriteOutcome.UNKNOWN)

        if response not in ("true", True):
            outcome = WriteOutcome.UNKNOWN
        else:
            try:
                items = await self._lists.get_list_items(principal, list_id)
            except FamilyWallError:
                outcome = WriteOutcome.ACKNOWLEDGED
            else:
                present = any(item.item_id == item_id for item in items)
                outcome = WriteOutcome.UNKNOWN if present else WriteOutcome.CONFIRMED
        return await _finish(
            "list.delete_item",
            receipt_repository,
            principal,
            family_id,
            list_id,
            operation_id,
            payload_hash,
            outcome,
        )

    async def delete_list(
        self,
        principal: Principal,
        list_id: str,
        expected_name: str,
        operation_id: str,
        receipt_repository: ReceiptRepository,
        family_id: str,
    ) -> ListWriteResult:
        """Delete a whole list with its items. Built-in lists are refused.

        Returns:
            CONFIRMED when a re-read no longer shows the list, UNKNOWN when the
            server did not confirm the delete or the list is still there,
            ACKNOWLEDGED when the re-read failed.

        Raises:
            FamilyWallError: `invalid_item` or `default_list_delete_unsupported`
                (no delete sent), or `operation_id_conflict`.
            UnsupportedConfigurationError: If the list is not accessible (no
                delete sent).
        """
        _require_operation_id(operation_id)
        expected = _confirmation(expected_name)
        try:
            fields = build_delete_list_fields(list_id)
        except ValueError as exc:
            raise _invalid("That is not a list identifier.") from exc

        payload_hash = _hash({**fields, "confirm": expected})
        replayed = await _replay(
            "list.delete_list", principal, operation_id, payload_hash, receipt_repository
        )
        if replayed is not None:
            return replayed

        target = next(
            (
                lst
                for lst in await self._lists.list_accessible_lists(principal)
                if lst.list_id == list_id
            ),
            None,
        )
        if target is None:
            raise UnsupportedConfigurationError()
        if target.system_id is not None:
            raise FamilyWallError(
                ErrorInfo(
                    code="default_list_delete_unsupported",
                    message="This is one of FamilyWall's built-in lists and is never deleted here.",
                    recovery="Delete individual items from it instead.",
                )
            )
        _require_match("list", target.name, expected)

        await _record(
            "list.delete_list",
            receipt_repository,
            principal,
            family_id,
            list_id,
            operation_id,
            payload_hash,
            "pending",
        )
        try:
            response = await self.transport.call("taskdeletelist", fields)
        except _UNCERTAIN_ERRORS:
            await _record(
                "list.delete_list",
                receipt_repository,
                principal,
                family_id,
                list_id,
                operation_id,
                payload_hash,
                "unknown",
            )
            return ListWriteResult(outcome=WriteOutcome.UNKNOWN)

        if response not in ("true", True):
            outcome = WriteOutcome.UNKNOWN
        else:
            try:
                remaining = await self._lists.list_accessible_lists(principal)
            except FamilyWallError:
                outcome = WriteOutcome.ACKNOWLEDGED
            else:
                present = any(lst.list_id == list_id for lst in remaining)
                outcome = WriteOutcome.UNKNOWN if present else WriteOutcome.CONFIRMED
        return await _finish(
            "list.delete_list",
            receipt_repository,
            principal,
            family_id,
            list_id,
            operation_id,
            payload_hash,
            outcome,
        )

    async def _locate(self, principal: Principal, item_id: str) -> tuple[str, str]:
        """The accessible list holding `item_id` and the item's current text.

        Raises:
            UnsupportedConfigurationError: If no accessible list holds the item.
        """
        for lst in await self._lists.list_accessible_lists(principal):
            for item in await self._lists.get_list_items(principal, lst.list_id):
                if item.item_id == item_id:
                    return lst.list_id, item.text
        raise UnsupportedConfigurationError()


def _confirmation(value: str) -> str:
    """Normalise a caller's confirmation text; blank is never a confirmation."""
    normalised = value.strip().casefold()
    if not normalised:
        raise _invalid("A delete needs the name of what is being deleted, as confirmation.")
    return normalised


def _require_match(kind: str, actual: str, expected: str) -> None:
    """Refuse a delete whose confirmation does not name the actual target.

    Deletes are immediate and FamilyWall has no undo, so an identifier alone,
    which is easy to mix up, is never enough to delete something.
    """
    if actual.strip().casefold() != expected:
        raise FamilyWallError(
            ErrorInfo(
                code="confirmation_mismatch",
                message=(
                    f'That {kind} is called "{actual}", which does not match; nothing was deleted.'
                ),
                recovery=f"Check the {kind} and repeat the delete with its exact name.",
            )
        )


def _require_operation_id(operation_id: str) -> None:
    if not operation_id or not operation_id.strip():
        raise ValueError("operation_id must not be empty")


async def _replay(
    action: ReceiptAction,
    principal: Principal,
    operation_id: str,
    payload_hash: str,
    receipt_repository: ReceiptRepository,
) -> ListWriteResult | None:
    """Return the stored outcome for a known operation ID, or None if new."""
    receipt = await receipt_repository.get(principal, operation_id)
    if receipt is None:
        return None
    if receipt.action not in (action, "legacy") or receipt.payload_hash != payload_hash:
        raise FamilyWallError(
            ErrorInfo(
                code="operation_id_conflict",
                message="An operation with this ID already exists with different content.",
                recovery="Use a different operation ID for this request.",
            )
        )
    if receipt.upstream_id:
        try:
            return ListWriteResult.model_validate_json(receipt.upstream_id)
        except ValueError:
            pass
    # A pending receipt means a process died mid-request; an unknown one means
    # the request's effect was never established. Neither is success.
    return ListWriteResult(outcome=WriteOutcome.UNKNOWN)


async def _record(
    action: ReceiptAction,
    receipt_repository: ReceiptRepository,
    principal: Principal,
    family_id: str,
    list_id: str,
    operation_id: str,
    payload_hash: str,
    status: str,
    result: ListWriteResult | None = None,
) -> None:
    await receipt_repository.put(
        OperationReceipt(
            subject=principal.subject,
            family_id=family_id,
            resource_id=list_id,
            action=action,
            operation_id=operation_id,
            payload_hash=payload_hash,
            status=status,  # type: ignore[arg-type]
            upstream_id=result.model_dump_json() if result is not None else None,
            expires_at=datetime.now(UTC) + RECEIPT_TTL,
        )
    )


async def _finish(
    action: ReceiptAction,
    receipt_repository: ReceiptRepository,
    principal: Principal,
    family_id: str,
    list_id: str,
    operation_id: str,
    payload_hash: str,
    outcome: WriteOutcome,
) -> ListWriteResult:
    result = ListWriteResult(outcome=outcome)
    status = "unknown" if outcome is WriteOutcome.UNKNOWN else "succeeded"
    await _record(
        action,
        receipt_repository,
        principal,
        family_id,
        list_id,
        operation_id,
        payload_hash,
        status,
        result,
    )
    return result
