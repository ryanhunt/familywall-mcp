"""Per-principal session pool with isolation and lifecycle management."""

from __future__ import annotations

import asyncio
import contextlib
from datetime import datetime
from typing import Any, Literal, Protocol

from familywall_mcp.errors import SessionExpiredError
from familywall_mcp.familywall.discovery import build_discovery_fields, parse_discovery
from familywall_mcp.models import FamilyContext, Principal


class Clock(Protocol):
    """Minimal clock interface for testing."""

    def now(self) -> datetime: ...


class ClientFactory(Protocol):
    """Creates httpx clients for a principal."""

    async def create_client(self, principal: Principal) -> Any: ...


class AuthenticatedSession:
    """A validated session with cached discovery context."""

    def __init__(
        self,
        client: Any,
        context: FamilyContext,
    ) -> None:
        """Initialize an authenticated session.

        Args:
            client: The FamilyWallSession client.
            context: The cached FamilyContext from discovery.
        """
        self._client = client
        self._context = context

    @property
    def client(self) -> Any:
        """Return the underlying client."""
        return self._client

    @property
    def context(self) -> FamilyContext:
        """Return the cached family context."""
        return self._context


class _PooledSession:
    """Internal session holder with tracking."""

    def __init__(
        self,
        principal: Principal,
        session: AuthenticatedSession,
        credential_generation: int,
        clock: Clock,
    ) -> None:
        self.principal = principal
        self.session = session
        self.credential_generation = credential_generation
        self.clock = clock
        self.last_accessed = clock.now()

    def mark_accessed(self) -> None:
        """Update last access time."""
        self.last_accessed = self.clock.now()

    def is_idle(self, max_idle_seconds: float) -> bool:
        """Check if this session is idle."""
        elapsed = (self.clock.now() - self.last_accessed).total_seconds()
        return elapsed > max_idle_seconds

    def __repr__(self) -> str:
        """Safe repr that does not expose credentials."""
        return (
            f"_PooledSession(principal={self.principal.subject!r}, accessed={self.last_accessed!r})"
        )


class SessionPool:
    """Per-principal session pool with concurrent access isolation."""

    def __init__(
        self,
        *,
        client_factory: ClientFactory,
        clock: Clock,
        max_idle_seconds: float = 900,
        max_sessions: int = 32,
    ) -> None:
        """Initialize the session pool.

        Args:
            client_factory: Factory for creating httpx clients.
            clock: Clock for idle tracking.
            max_idle_seconds: Idle timeout in seconds (default 900).
            max_sessions: Maximum sessions to hold (default 32).
        """
        self._client_factory = client_factory
        self._clock = clock
        self._max_idle_seconds = max_idle_seconds
        self._max_sessions = max_sessions

        # Sessions by principal subject
        self._sessions: dict[str, _PooledSession] = {}

        # Login lock per principal (one asyncio.Lock per subject)
        self._login_locks: dict[str, asyncio.Lock] = {}

        # Credential generation per principal for invalidation tracking
        self._credential_generations: dict[str, int] = {}

        # Overall lock for session dict mutations
        self._lock = asyncio.Lock()

    async def acquire(
        self,
        principal: Principal,
        credential_generation: int | None = None,
    ) -> AuthenticatedSession:
        """Acquire or create a session for a principal.

        Args:
            principal: The principal requesting a session.
            credential_generation: Current credential generation number.
                If provided and differs from the stored generation,
                the cached session is invalidated.

        Returns:
            An authenticated session with cached context.

        Raises:
            AuthenticationError: If login fails.
            TransportError: If the request fails.
            Any error from discovery or validation.
        """
        subject = principal.subject

        async with self._lock:
            # Check if credentials have changed
            if credential_generation is not None:
                if subject in self._credential_generations:
                    if self._credential_generations[subject] != credential_generation:
                        # Generation mismatch: invalidate this principal's session
                        if subject in self._sessions:
                            old_session = self._sessions.pop(subject)
                            await self._close_session(old_session)
                        self._credential_generations[subject] = credential_generation
                else:
                    self._credential_generations[subject] = credential_generation

            # Check if we have a valid cached session (and not idle)
            if subject in self._sessions:
                pooled = self._sessions[subject]
                if not pooled.is_idle(self._max_idle_seconds):
                    pooled.mark_accessed()
                    return pooled.session
                else:
                    # Session is idle; close and remove it
                    self._sessions.pop(subject)
                    await self._close_session(pooled)

            # No cached session; get/create a login lock for this principal
            if subject not in self._login_locks:
                self._login_locks[subject] = asyncio.Lock()

        # Acquire the login lock for this principal (outside the session lock)
        async with self._login_locks[subject], self._lock:
            # Double-check: another coroutine may have created a session while we waited
            if subject in self._sessions:
                pooled = self._sessions[subject]
                pooled.mark_accessed()
                return pooled.session

            # Still no session; need to create one
            # First, create the client
            client = await self._client_factory.create_client(principal)

            # Run login (this should set credentials on the client)
            # We assume the client is a FamilyWallSession with a login() method
            # and a call() method for making authenticated calls.
            # The credential_provider is expected to be injected elsewhere.
            # For now, we assume login has already happened or we discover it.

            # Discover family to build context
            discovery_fields = build_discovery_fields()
            try:
                discovery_payload = await client.call(
                    "accgetallfamily",
                    discovery_fields,
                )
            except SessionExpiredError:
                # Should not happen on fresh login
                raise

            discovered = parse_discovery(discovery_payload)
            context = discovered.to_context()

            # Create authenticated session
            session = AuthenticatedSession(client, context)
            pooled = _PooledSession(
                principal,
                session,
                credential_generation or 0,
                self._clock,
            )

            # Store and manage size
            self._sessions[subject] = pooled
            await self._evict_lru_if_needed()

            return session

    async def invalidate(self, principal: Principal) -> None:
        """Invalidate a principal's session (e.g., after logout or credential change).

        Args:
            principal: The principal whose session to invalidate.
        """
        subject = principal.subject

        async with self._lock:
            if subject in self._sessions:
                pooled = self._sessions.pop(subject)
                await self._close_session(pooled)

    async def aclose(self) -> None:
        """Close all sessions and clean up resources.

        Safe to call multiple times.
        """
        async with self._lock:
            for pooled in list(self._sessions.values()):
                await self._close_session(pooled)
            self._sessions.clear()
            self._login_locks.clear()
            self._credential_generations.clear()

    async def _close_session(self, pooled: _PooledSession) -> None:
        """Close a single pooled session's client.

        Args:
            pooled: The session to close.
        """
        client = pooled.session.client
        if hasattr(client, "aclose") and callable(client.aclose):
            with contextlib.suppress(Exception):
                await client.aclose()

    async def _evict_lru_if_needed(self) -> None:
        """Evict the least recently used session if we exceed max_sessions.

        Assumes the session lock is already held.
        """
        if len(self._sessions) <= self._max_sessions:
            return

        # Find the LRU session
        lru_pooled = min(self._sessions.values(), key=lambda p: p.last_accessed)
        lru_subject = lru_pooled.principal.subject

        self._sessions.pop(lru_subject)
        await self._close_session(lru_pooled)

    async def call(
        self,
        principal: Principal,
        endpoint: str,
        fields: dict[str, str],
        read_write: Literal["read", "write"],
    ) -> object:
        """Perform an authenticated API call on behalf of a principal.

        Implements bounded reauth for reads (at most one re-login) and
        immediate propagation for writes (no re-login, no resend).

        Args:
            principal: The principal making the request.
            endpoint: The API endpoint name.
            fields: Form fields to send.
            read_write: "read" or "write" - must be explicit.

        Returns:
            The unwrapped result from the endpoint.

        Raises:
            SessionExpiredError: Session is invalid and cannot be recovered
                (after bounded reauth for reads, or immediately for writes).
            AuthenticationError: If login fails.
            TransportError: If the request fails at the transport level.
            Any error from the upstream endpoint.
        """
        session = await self.acquire(principal)

        try:
            return await session.client.call(endpoint, fields)
        except SessionExpiredError:
            # Writes never retry or re-login
            if read_write == "write":
                raise

            # Reads: attempt exactly one re-login and one retry
            # Invalidate and re-acquire
            await self.invalidate(principal)
            session = await self.acquire(principal)

            try:
                return await session.client.call(endpoint, fields)
            except SessionExpiredError:
                # Second expiry on read: propagate
                raise
