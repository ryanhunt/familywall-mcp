"""In-memory receipt repository for mutation operation tracking.

IMPORTANT: Receipts stored here are EPHEMERAL and do NOT survive a process
restart. They are lost immediately when the server stops, and have no
durability guarantee.

The P5 SQLite implementation (when delivered) will provide the actual
durability guarantee. Nothing downstream should assume durability of receipts
stored here. This is storage for deduplication within a running process only.
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

from familywall_mcp.models import Principal

if TYPE_CHECKING:
    from familywall_mcp.models import OperationReceipt


class InMemoryReceiptRepository:
    """Receipt repository using a process-local dict and asyncio.Lock.

    Receipts are indexed by (subject, operation_id) and are completely lost
    on process restart. Use only for deduplication within a running instance.
    """

    def __init__(self) -> None:
        self._receipts: dict[tuple[str, str], OperationReceipt] = {}
        self._lock = asyncio.Lock()

    async def get(self, principal: Principal, operation_id: str) -> OperationReceipt | None:
        """Retrieve a receipt by principal and operation_id, or None if not found.

        Args:
            principal: The authenticated subject making the request.
            operation_id: The operation identifier supplied by the caller.

        Returns:
            The OperationReceipt if found, None otherwise.
        """
        async with self._lock:
            key = (principal.subject, operation_id)
            return self._receipts.get(key)

    async def put(self, receipt: OperationReceipt) -> None:
        """Store or update a receipt.

        Args:
            receipt: The OperationReceipt to store. Must have subject and
                     operation_id set.
        """
        async with self._lock:
            key = (receipt.subject, receipt.operation_id)
            self._receipts[key] = receipt
