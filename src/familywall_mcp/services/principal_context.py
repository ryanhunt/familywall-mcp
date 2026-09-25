"""Per-principal discovery context, and how tool handlers resolve it.

Every tool call needs a ``Principal`` plus the FamilyWall discovery data tied
to that principal's own login (family context, member list, timezone) and a
``CalendarService`` bound to that principal's session. In stdio mode there is
exactly one principal for the whole process lifetime, discovered once at
startup. In hosted mode there are several concurrently-logged-in MCP users
sharing one process, and ADR 0001 is explicit that tools must resolve
identity per request from the access token, never from a server-lifetime
variable, so that one user's family/list data can never leak into another
user's tool call.

``ContextResolver`` is the seam between those two cases: ``FixedContextResolver``
returns the one context built at startup (stdio); ``HostedContextResolver``
reads ``get_access_token()`` per call and lazily builds/caches a
``PrincipalContext`` per subject (hosted). Both also implement ``refresh()``,
which rebuilds the context from one more discovery call: a tool calls it, at
most once, when a member name it resolved against the cached family list
turns out to be unknown, in case that list is simply stale.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Protocol

from mcp.server.auth.middleware.auth_context import get_access_token

from familywall_mcp.errors import AuthenticationError, ErrorInfo
from familywall_mcp.familywall.discovery import (
    DiscoveredFamily,
    build_discovery_fields,
    parse_discovery,
)
from familywall_mcp.models import FamilyContext, Principal
from familywall_mcp.services.calendar import CalendarService
from familywall_mcp.services.session import SessionPool
from familywall_mcp.services.transport import read_transport


@dataclass(frozen=True)
class PrincipalContext:
    """Everything derived from one principal's FamilyWall discovery."""

    family_context: FamilyContext
    discovered_family: DiscoveredFamily
    authenticated_member_timezone: str
    calendar_service: CalendarService


async def build_principal_context(
    principal: Principal, session_pool: SessionPool
) -> PrincipalContext:
    """Run discovery for ``principal`` and assemble its ``PrincipalContext``.

    This is the same discovery sequence ``server.py`` runs once at stdio
    startup, extracted so hosted mode can run it per subject instead.
    """
    discovery_fields = build_discovery_fields()
    discovery_payload = await session_pool.call(
        principal,
        "accgetallfamily",
        discovery_fields,
        read_write="read",
    )
    discovered = parse_discovery(discovery_payload)
    family_context = discovered.to_context()

    authenticated_member = next(
        (m for m in discovered.members if m.is_authenticated_member),
        None,
    )
    timezone = (authenticated_member.timezone if authenticated_member else None) or "UTC"

    read_xport = read_transport(session_pool, principal)
    calendar_service = CalendarService(read_xport)

    return PrincipalContext(
        family_context=family_context,
        discovered_family=discovered,
        authenticated_member_timezone=timezone,
        calendar_service=calendar_service,
    )


class ContextResolver(Protocol):
    async def resolve(self) -> tuple[Principal, PrincipalContext]: ...

    async def refresh(self) -> tuple[Principal, PrincipalContext]:
        """Rebuild the current principal's context from a fresh discovery call.

        Used exactly once, when a tool's member-name resolution fails with
        ``unknown_member``: the cached family list may simply be stale.
        """
        ...


class FixedContextResolver:
    """Always returns the one (principal, context) pair built at startup.

    Used for stdio, where there is exactly one FamilyWall login for the
    whole process lifetime.
    """

    def __init__(
        self,
        principal: Principal,
        context: PrincipalContext,
        session_pool: SessionPool | None = None,
    ) -> None:
        self._principal = principal
        self._context = context
        self._session_pool = session_pool

    async def resolve(self) -> tuple[Principal, PrincipalContext]:
        return self._principal, self._context

    async def refresh(self) -> tuple[Principal, PrincipalContext]:
        """Rebuild the context from one more discovery call, if possible.

        Without a ``session_pool`` there is nothing to refresh against (e.g.
        a fixed context built for a test), so this returns the current pair
        unchanged.
        """
        if self._session_pool is None:
            return self._principal, self._context
        self._context = await build_principal_context(self._principal, self._session_pool)
        return self._principal, self._context


class HostedContextResolver:
    """Resolves the current principal from the request's access token and
    lazily builds/caches its ``PrincipalContext`` per subject.

    Uses the same double-checked-locking shape as ``SessionPool.acquire``:
    a per-subject build lock so concurrent calls for the *same* subject wait
    for one discovery run instead of racing, while different subjects never
    block each other.
    """

    def __init__(self, session_pool: SessionPool) -> None:
        self._session_pool = session_pool
        self._cache: dict[str, PrincipalContext] = {}
        self._build_locks: dict[str, asyncio.Lock] = {}
        self._lock = asyncio.Lock()

    async def resolve(self) -> tuple[Principal, PrincipalContext]:
        access_token = get_access_token()
        if access_token is None or not access_token.subject:
            # RequireAuthMiddleware gates /mcp on a valid bearer token before
            # a tool handler ever runs, so this should be unreachable; fail
            # closed rather than falling back to any shared identity.
            raise AuthenticationError(
                ErrorInfo(
                    "missing_access_token",
                    "No authenticated subject for this request.",
                    "Reconnect and sign in again.",
                )
            )
        subject = access_token.subject
        principal = Principal(subject=subject, scopes=frozenset(access_token.scopes))

        async with self._lock:
            cached = self._cache.get(subject)
            if cached is not None:
                return principal, cached
            build_lock = self._build_locks.setdefault(subject, asyncio.Lock())

        async with build_lock:
            async with self._lock:
                cached = self._cache.get(subject)
                if cached is not None:
                    return principal, cached

            context = await build_principal_context(principal, self._session_pool)

            async with self._lock:
                self._cache[subject] = context
            return principal, context

    async def invalidate(self, subject: str) -> None:
        """Drop a cached context (e.g. after a credential/session change)."""
        async with self._lock:
            self._cache.pop(subject, None)

    async def refresh(self) -> tuple[Principal, PrincipalContext]:
        """Invalidate the current subject's cached context, then rebuild it.

        Reads the same per-request access token ``resolve()`` does, so this
        always refreshes the caller's own subject, never another one's.
        """
        access_token = get_access_token()
        if access_token is None or not access_token.subject:
            raise AuthenticationError(
                ErrorInfo(
                    "missing_access_token",
                    "No authenticated subject for this request.",
                    "Reconnect and sign in again.",
                )
            )
        await self.invalidate(access_token.subject)
        return await self.resolve()
