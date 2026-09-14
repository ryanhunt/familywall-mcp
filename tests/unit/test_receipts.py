"""Tests for the in-memory receipt repository."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from familywall_mcp.models import OperationReceipt, Principal
from familywall_mcp.storage.memory import InMemoryReceiptRepository


class TestInMemoryReceiptRepository:
    """Tests for InMemoryReceiptRepository."""

    @pytest.fixture
    def repository(self) -> InMemoryReceiptRepository:
        return InMemoryReceiptRepository()

    @pytest.fixture
    def principal(self) -> Principal:
        return Principal(subject="user1")

    @pytest.fixture
    def receipt(self) -> OperationReceipt:
        return OperationReceipt(
            subject="user1",
            family_id="family123",
            list_id="list456",
            operation_id="op001",
            payload_hash="hash123",
            status="pending",
            upstream_id=None,
            expires_at=datetime(2026, 12, 31, 23, 59, 59, tzinfo=UTC),
        )

    async def test_get_returns_none_when_not_found(
        self, repository: InMemoryReceiptRepository, principal: Principal
    ) -> None:
        result = await repository.get(principal, "nonexistent")
        assert result is None

    async def test_put_and_get(
        self, repository: InMemoryReceiptRepository, principal: Principal, receipt: OperationReceipt
    ) -> None:
        await repository.put(receipt)
        retrieved = await repository.get(principal, receipt.operation_id)
        assert retrieved is receipt

    async def test_get_returns_matching_receipt_only(
        self, repository: InMemoryReceiptRepository, principal: Principal, receipt: OperationReceipt
    ) -> None:
        await repository.put(receipt)
        # Different operation_id should not be found
        result = await repository.get(principal, "different_op")
        assert result is None

    async def test_get_scoped_by_subject(
        self, repository: InMemoryReceiptRepository, receipt: OperationReceipt
    ) -> None:
        await repository.put(receipt)
        # Same operation_id but different subject should not be found
        other_principal = Principal(subject="user2")
        result = await repository.get(other_principal, receipt.operation_id)
        assert result is None

    async def test_put_updates_existing_receipt(
        self, repository: InMemoryReceiptRepository, principal: Principal, receipt: OperationReceipt
    ) -> None:
        await repository.put(receipt)
        updated_receipt = OperationReceipt(
            subject=receipt.subject,
            family_id=receipt.family_id,
            list_id=receipt.list_id,
            operation_id=receipt.operation_id,
            payload_hash=receipt.payload_hash,
            status="succeeded",
            upstream_id="id123",
            expires_at=receipt.expires_at,
        )
        await repository.put(updated_receipt)
        retrieved = await repository.get(principal, receipt.operation_id)
        assert retrieved is updated_receipt
        assert retrieved.status == "succeeded"
        assert retrieved.upstream_id == "id123"

    async def test_multiple_subjects_are_independent(
        self, repository: InMemoryReceiptRepository
    ) -> None:
        user1 = Principal(subject="user1")
        user2 = Principal(subject="user2")
        receipt1 = OperationReceipt(
            subject="user1",
            family_id="fam1",
            list_id="list1",
            operation_id="op1",
            payload_hash="hash1",
            status="pending",
            upstream_id=None,
            expires_at=datetime(2026, 12, 31, 23, 59, 59, tzinfo=UTC),
        )
        receipt2 = OperationReceipt(
            subject="user2",
            family_id="fam2",
            list_id="list2",
            operation_id="op1",
            payload_hash="hash2",
            status="pending",
            upstream_id=None,
            expires_at=datetime(2026, 12, 31, 23, 59, 59, tzinfo=UTC),
        )
        await repository.put(receipt1)
        await repository.put(receipt2)
        # Each subject should get their own receipt
        retrieved1 = await repository.get(user1, "op1")
        retrieved2 = await repository.get(user2, "op1")
        assert retrieved1 is receipt1
        assert retrieved2 is receipt2
        assert retrieved1.subject == "user1"
        assert retrieved2.subject == "user2"
