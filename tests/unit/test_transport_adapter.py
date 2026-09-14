"""Tests for the PooledTransport adapter."""

from __future__ import annotations

import pytest

from familywall_mcp.models import Principal
from familywall_mcp.services.transport import PooledTransport, read_transport, write_transport


class FakeSessionPool:
    """Minimal fake SessionPool that records calls."""

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
        return {"synthetic": "result"}


@pytest.mark.asyncio
async def test_read_transport_forwards_read_mode() -> None:
    """A read transport forwards read_write='read'."""
    pool = FakeSessionPool()
    principal = Principal(subject="test-subject")
    transport = read_transport(pool, principal)

    result = await transport.call("taskgettasklists", {"partnerScope": "Family"})

    assert result == {"synthetic": "result"}
    assert len(pool.calls) == 1
    subject, endpoint, fields, mode = pool.calls[0]
    assert subject == "test-subject"
    assert endpoint == "taskgettasklists"
    assert fields == {"partnerScope": "Family"}
    assert mode == "read"


@pytest.mark.asyncio
async def test_write_transport_forwards_write_mode() -> None:
    """A write transport forwards read_write='write'."""
    pool = FakeSessionPool()
    principal = Principal(subject="test-subject")
    transport = write_transport(pool, principal)

    result = await transport.call("taskcreate", {"a00taskListId": "list/1", "a00text": "Item"})

    assert result == {"synthetic": "result"}
    assert len(pool.calls) == 1
    subject, endpoint, fields, mode = pool.calls[0]
    assert subject == "test-subject"
    assert endpoint == "taskcreate"
    assert fields == {"a00taskListId": "list/1", "a00text": "Item"}
    assert mode == "write"


@pytest.mark.asyncio
async def test_fields_pass_through_unchanged() -> None:
    """Fields are passed through to the pool unchanged."""
    pool = FakeSessionPool()
    principal = Principal(subject="test-subject")
    transport = PooledTransport(pool, principal, "read")

    input_fields = {"a": "1", "b": "2", "partnerScope": "Family"}
    await transport.call("evtlistinterval", input_fields)

    assert pool.calls[0][2] == input_fields


@pytest.mark.asyncio
async def test_endpoint_passes_through_unchanged() -> None:
    """Endpoint names are passed through unchanged."""
    pool = FakeSessionPool()
    principal = Principal(subject="test-subject")
    transport = PooledTransport(pool, principal, "write")

    await transport.call("taskmark", {})

    assert pool.calls[0][1] == "taskmark"
