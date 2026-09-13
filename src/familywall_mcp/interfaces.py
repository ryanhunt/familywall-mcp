"""Dependency-injection protocols for later FamilyWall and storage work."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Protocol

from .models import (
    FamilyContext,
    FamilyWallCredentials,
    OperationReceipt,
    Principal,
    ResponseEnvelope,
)


class Clock(Protocol):
    def now(self) -> datetime: ...


class CredentialProvider(Protocol):
    async def get_credentials(self, principal: Principal) -> FamilyWallCredentials: ...


class Transport(Protocol):
    async def request(self, method: str, url: str, **kwargs: Any) -> ResponseEnvelope: ...


class FamilyWallClient(Protocol):
    async def connection_status(self, principal: Principal) -> ResponseEnvelope: ...

    async def list_families(self, principal: Principal) -> ResponseEnvelope: ...

    async def with_context(self, context: FamilyContext) -> FamilyWallClient: ...


class ReceiptRepository(Protocol):
    async def get(self, principal: Principal, operation_id: str) -> OperationReceipt | None: ...

    async def put(self, receipt: OperationReceipt) -> None: ...
