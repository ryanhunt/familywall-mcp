"""Adapter bridging SessionPool and service transport protocols."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Literal

from familywall_mcp.models import Principal
from familywall_mcp.services.session import SessionPool


class PooledTransport:
    """SessionPool adapter with fixed read/write mode.

    Bridges the SessionPool's per-principal call interface (which requires
    explicit read_write mode and a principal) with the ListTransport and
    CalendarTransport protocols (which operate on (endpoint, fields) only).

    The read_write mode is fixed at construction, so a read transport can never
    send a write request.
    """

    def __init__(
        self,
        pool: SessionPool,
        principal: Principal,
        read_write: Literal["read", "write"],
    ) -> None:
        """Initialize the adapter.

        Args:
            pool: The session pool to delegate calls to.
            principal: The principal making requests.
            read_write: "read" or "write" - the fixed mode for all calls.
        """
        self._pool = pool
        self._principal = principal
        self._read_write = read_write

    async def call(self, endpoint: str, fields: Mapping[str, str]) -> object:
        """Call an endpoint with the fixed read_write mode.

        Args:
            endpoint: The API endpoint name (e.g., "taskgettasklists").
            fields: Request fields (wire protocol dict).

        Returns:
            The unwrapped response from the endpoint.

        Raises:
            Any exception from SessionPool.call (auth, transport, etc.).
        """
        return await self._pool.call(
            self._principal,
            endpoint,
            dict(fields),
            self._read_write,
        )


def read_transport(
    pool: SessionPool,
    principal: Principal,
) -> PooledTransport:
    """Factory for a read-mode transport.

    Args:
        pool: The session pool.
        principal: The principal making requests.

    Returns:
        A PooledTransport with read_write="read".
    """
    return PooledTransport(pool, principal, "read")


def write_transport(
    pool: SessionPool,
    principal: Principal,
) -> PooledTransport:
    """Factory for a write-mode transport.

    Args:
        pool: The session pool.
        principal: The principal making requests.

    Returns:
        A PooledTransport with read_write="write".
    """
    return PooledTransport(pool, principal, "write")
