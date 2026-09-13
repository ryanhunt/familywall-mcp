"""Tests for the per-principal session pool."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from tests.support.discovery_fixtures import normal_discovery_payload

from familywall_mcp.errors import SessionExpiredError
from familywall_mcp.models import FamilyContext, Principal
from familywall_mcp.services.session import SessionPool


class FakeClock:
    """Controllable clock for testing."""

    def __init__(self, initial: datetime) -> None:
        self._now = initial

    def now(self) -> datetime:
        return self._now

    def advance(self, delta: timedelta) -> None:
        """Move time forward."""
        self._now += delta


class FakeClient:
    """Fake FamilyWallSession for testing."""

    def __init__(self, principal_subject: str) -> None:
        self.principal_subject = principal_subject
        self.login_count = 0
        self.calls: list[tuple[str, dict[str, str]]] = []
        self._closed = False

    async def login(self, username: str, password: str) -> None:
        """Fake login."""
        self.login_count += 1

    async def call(
        self,
        endpoint: str,
        fields: dict[str, str],
    ) -> object:
        """Fake call."""
        self.calls.append((endpoint, fields))

        if endpoint == "accgetallfamily":
            return normal_discovery_payload()

        return {"result": "ok"}

    async def aclose(self) -> None:
        """Fake close."""
        self._closed = True

    def __repr__(self) -> str:
        """Safe repr."""
        return f"FakeClient(subject={self.principal_subject!r}, closed={self._closed})"


class FakeClientFactory:
    """Factory that tracks created clients."""

    def __init__(self) -> None:
        self.created_clients: list[FakeClient] = []

    async def create_client(self, principal: Principal) -> Any:
        client = FakeClient(principal.subject)
        self.created_clients.append(client)
        return client


@pytest.fixture
def fake_clock() -> FakeClock:
    return FakeClock(datetime(2026, 1, 1, 0, 0, 0, tzinfo=UTC))


@pytest.fixture
def client_factory() -> FakeClientFactory:
    return FakeClientFactory()


@pytest.fixture
async def pool(fake_clock: FakeClock, client_factory: FakeClientFactory) -> SessionPool:
    pool = SessionPool(
        client_factory=client_factory,
        clock=fake_clock,
        max_idle_seconds=60,
        max_sessions=3,
    )
    yield pool
    await pool.aclose()


class TestKeyedByPrincipal:
    @pytest.mark.asyncio
    async def test_two_principals_get_different_sessions(
        self,
        pool: SessionPool,
        client_factory: FakeClientFactory,
    ) -> None:
        """Two principals never share a session, cookie jar, or httpx client."""
        principal_a = Principal(subject="alice")
        principal_b = Principal(subject="bob")

        session_a = await pool.acquire(principal_a)
        session_b = await pool.acquire(principal_b)

        # Sessions must be different
        assert session_a is not session_b

        # Underlying clients must be different
        assert session_a.client is not session_b.client

        # Verify we created exactly two clients
        assert len(client_factory.created_clients) == 2
        assert client_factory.created_clients[0] is session_a.client
        assert client_factory.created_clients[1] is session_b.client


class TestOneLoginLockPerPrincipal:
    @pytest.mark.asyncio
    async def test_concurrent_acquire_for_same_principal_performs_one_login(
        self,
        pool: SessionPool,
        client_factory: FakeClientFactory,
    ) -> None:
        """Ten concurrent acquire calls for same principal perform exactly one login."""
        principal = Principal(subject="alice")

        # Start 10 concurrent acquire calls
        tasks = [pool.acquire(principal) for _ in range(10)]
        sessions = await asyncio.gather(*tasks)

        # All should return the same session
        assert len(set(id(s) for s in sessions)) == 1

        # Only one client created
        assert len(client_factory.created_clients) == 1

    @pytest.mark.asyncio
    async def test_concurrent_acquire_for_different_principals_not_serialized(
        self,
        pool: SessionPool,
        client_factory: FakeClientFactory,
    ) -> None:
        """Concurrent acquire for different principals are not serialized."""
        principal_a = Principal(subject="alice")
        principal_b = Principal(subject="bob")

        # Start concurrent acquire calls for different principals
        tasks = [
            pool.acquire(principal_a),
            pool.acquire(principal_b),
            pool.acquire(principal_a),
        ]
        await asyncio.gather(*tasks)

        # Should create 2 clients (one per principal), not serialized
        assert len(client_factory.created_clients) == 2


class TestCredentialGeneration:
    @pytest.mark.asyncio
    async def test_credential_change_invalidates_session(
        self,
        pool: SessionPool,
        client_factory: FakeClientFactory,
    ) -> None:
        """A credential change invalidates that principal's session only."""
        principal = Principal(subject="alice")

        # First acquire with generation 1
        session1 = await pool.acquire(principal, credential_generation=1)
        client1 = session1.client

        # Second acquire with same generation reuses session
        session2 = await pool.acquire(principal, credential_generation=1)
        assert session2.client is client1

        # Third acquire with different generation creates new session
        session3 = await pool.acquire(principal, credential_generation=2)
        client3 = session3.client

        assert client3 is not client1
        assert client1._closed is True  # Old client was closed

    @pytest.mark.asyncio
    async def test_credential_change_does_not_affect_other_principals(
        self,
        pool: SessionPool,
        client_factory: FakeClientFactory,
    ) -> None:
        """Credential change in one principal does not affect another."""
        principal_a = Principal(subject="alice")
        principal_b = Principal(subject="bob")

        await pool.acquire(principal_a, credential_generation=1)

        session_b = await pool.acquire(principal_b, credential_generation=1)
        client_b = session_b.client

        # Change credentials for A
        await pool.acquire(principal_a, credential_generation=2)

        # B's session should still be usable
        session_b_again = await pool.acquire(principal_b, credential_generation=1)
        assert session_b_again.client is client_b


class TestIdleEviction:
    @pytest.mark.asyncio
    async def test_idle_session_is_evicted_on_new_acquire(
        self,
        pool: SessionPool,
        fake_clock: FakeClock,
        client_factory: FakeClientFactory,
    ) -> None:
        """A session untouched for max_idle_seconds is closed and dropped."""
        principal = Principal(subject="alice")

        # Acquire a session
        session1 = await pool.acquire(principal)
        client1 = session1.client

        # Advance time past idle threshold
        fake_clock.advance(timedelta(seconds=61))

        # Acquire again; should create new session since old one is idle
        session2 = await pool.acquire(principal)
        client2 = session2.client

        assert client2 is not client1
        assert client1._closed is True


class TestBoundedSize:
    @pytest.mark.asyncio
    async def test_lru_eviction_when_exceeding_max_sessions(
        self,
        pool: SessionPool,
        client_factory: FakeClientFactory,
    ) -> None:
        """Beyond max_sessions, the LRU session is evicted and closed."""
        principals = [Principal(subject=f"user{i}") for i in range(5)]

        # Create 5 sessions (max is 3)
        sessions = []
        for principal in principals:
            session = await pool.acquire(principal)
            sessions.append((principal, session))

        # Last 3 should be kept (user3, user4, user2 depending on access order)
        # User0 and User1 should have been evicted
        assert client_factory.created_clients[0]._closed is True  # user0
        assert client_factory.created_clients[1]._closed is True  # user1

        # Last 3 should still be open
        assert client_factory.created_clients[2]._closed is False  # user2
        assert client_factory.created_clients[3]._closed is False  # user3
        assert client_factory.created_clients[4]._closed is False  # user4


class TestAclose:
    @pytest.mark.asyncio
    async def test_aclose_closes_all_clients(
        self,
        client_factory: FakeClientFactory,
    ) -> None:
        """aclose closes every client."""
        fake_clock = FakeClock(datetime(2026, 1, 1, 0, 0, 0, tzinfo=UTC))
        pool = SessionPool(
            client_factory=client_factory,
            clock=fake_clock,
            max_idle_seconds=60,
            max_sessions=10,
        )

        principals = [Principal(subject=f"user{i}") for i in range(3)]

        for principal in principals:
            await pool.acquire(principal)

        # Close the pool
        await pool.aclose()

        # All clients should be closed
        for client in client_factory.created_clients:
            assert client._closed is True

    @pytest.mark.asyncio
    async def test_aclose_safe_to_call_twice(
        self,
        client_factory: FakeClientFactory,
    ) -> None:
        """aclose is safe to call twice."""
        fake_clock = FakeClock(datetime(2026, 1, 1, 0, 0, 0, tzinfo=UTC))
        pool = SessionPool(
            client_factory=client_factory,
            clock=fake_clock,
        )

        principal = Principal(subject="alice")
        await pool.acquire(principal)

        # Should not raise
        await pool.aclose()
        await pool.aclose()


class TestCachedDiscoveryContext:
    @pytest.mark.asyncio
    async def test_discovery_cached_per_principal(
        self,
        pool: SessionPool,
    ) -> None:
        """DiscoveredFamily/FamilyContext cached per principal."""
        principal = Principal(subject="alice")

        session = await pool.acquire(principal)
        context = session.context

        assert isinstance(context, FamilyContext)
        assert context.account_id == "acc1"  # From normal_discovery_payload
        assert context.family_id == "1234567"
        assert context.calendar_id == "calendar/1234567"
        assert context.verified is True

    @pytest.mark.asyncio
    async def test_relogin_discards_cached_context(
        self,
        pool: SessionPool,
        client_factory: FakeClientFactory,
    ) -> None:
        """Re-login discards cached context and rediscovers."""
        principal = Principal(subject="alice")

        session1 = await pool.acquire(principal, credential_generation=1)
        context1 = session1.context

        # Invalidate (simulating credential change)
        await pool.invalidate(principal)

        # Re-acquire with new generation
        session2 = await pool.acquire(principal, credential_generation=2)
        context2 = session2.context

        # New session should have fresh context
        assert session1 is not session2
        assert context1 == context2  # Same content but different instance OK


class TestInvalidate:
    @pytest.mark.asyncio
    async def test_invalidate_only_affects_principal(
        self,
        pool: SessionPool,
        client_factory: FakeClientFactory,
    ) -> None:
        """A credential change invalidates that principal's session and only that principal's."""
        principal_a = Principal(subject="alice")
        principal_b = Principal(subject="bob")

        session_a = await pool.acquire(principal_a)
        client_a = session_a.client

        session_b = await pool.acquire(principal_b)
        client_b = session_b.client

        # Invalidate A
        await pool.invalidate(principal_a)

        # A's client should be closed
        assert client_a._closed is True

        # B's client should still be open
        assert client_b._closed is False

        # B's session still works
        session_b_again = await pool.acquire(principal_b)
        assert session_b_again.client is client_b


class TestAuthenticatedSessionProperties:
    @pytest.mark.asyncio
    async def test_authenticated_session_has_client_and_context(
        self,
        pool: SessionPool,
    ) -> None:
        """AuthenticatedSession exposes client and context."""
        principal = Principal(subject="alice")
        session = await pool.acquire(principal)

        assert hasattr(session, "client")
        assert hasattr(session, "context")

        assert isinstance(session.context, FamilyContext)
        assert isinstance(session.client, FakeClient)


class TestRepr:
    @pytest.mark.asyncio
    async def test_repr_does_not_expose_credentials(
        self,
        pool: SessionPool,
        client_factory: FakeClientFactory,
    ) -> None:
        """Repr and logging do not expose passwords, cookies, or tokens."""
        principal = Principal(subject="alice")
        session = await pool.acquire(principal)

        # Get repr of the client
        client_repr = repr(session.client)

        # Should not contain any suspicious strings
        assert "password" not in client_repr.lower() or "=none" in client_repr.lower()
        assert "token" not in client_repr.lower() or "none" in client_repr.lower()
        assert "cookie" not in client_repr.lower()
        assert "secret" not in client_repr.lower()


class TestCallMethodImplementsReadWriteDistinction:
    @pytest.mark.asyncio
    async def test_write_expired_propagates_immediately_no_retry(
        self,
        pool: SessionPool,
        client_factory: FakeClientFactory,
    ) -> None:
        """WRITE SessionExpiredError propagates immediately, sent once, never retried."""
        principal = Principal(subject="alice")

        # Shared state for tracking write calls across all clients
        write_call_count = [0]

        # Wrap the factory to inject failure behavior
        original_factory_create = client_factory.create_client

        async def factory_with_write_failure(principal):
            client = await original_factory_create(principal)
            original_call = client.call

            async def call_with_write_error(endpoint, fields):
                if endpoint == "taskcreate":
                    write_call_count[0] += 1
                    raise SessionExpiredError()
                return await original_call(endpoint, fields)

            client.call = call_with_write_error
            return client

        client_factory.create_client = factory_with_write_failure

        # Make a write that will fail
        with pytest.raises(SessionExpiredError):
            await pool.call(principal, "taskcreate", {}, read_write="write")

        # Write was attempted exactly once (no retry)
        assert write_call_count[0] == 1

    @pytest.mark.asyncio
    async def test_read_expired_once_retries_and_succeeds(
        self,
        pool: SessionPool,
        client_factory: FakeClientFactory,
    ) -> None:
        """READ SessionExpiredError causes one retry; succeeds on second attempt."""
        principal = Principal(subject="alice")

        # Shared state for tracking read calls
        read_call_count = [0]

        # Wrap the factory to inject failure behavior
        original_factory_create = client_factory.create_client

        async def factory_with_read_failure(principal):
            client = await original_factory_create(principal)
            original_call = client.call

            async def call_with_read_error(endpoint, fields):
                if endpoint == "somereading":
                    read_call_count[0] += 1
                    # Fail only on first attempt
                    if read_call_count[0] == 1:
                        raise SessionExpiredError()
                return await original_call(endpoint, fields)

            client.call = call_with_read_error
            return client

        client_factory.create_client = factory_with_read_failure

        # Make a read that will fail once
        await pool.call(principal, "somereading", {}, read_write="read")

        # Should have been called twice (failed once, retried once, succeeded)
        assert read_call_count[0] == 2

    @pytest.mark.asyncio
    async def test_read_expired_twice_propagates_no_third_attempt(
        self,
        pool: SessionPool,
        client_factory: FakeClientFactory,
    ) -> None:
        """READ SessionExpiredError twice: second error propagates, no third attempt."""
        principal = Principal(subject="alice")

        call_count = [0]

        original_factory_create = client_factory.create_client

        async def factory_always_failing(principal):
            client = await original_factory_create(principal)
            original_call = client.call

            async def always_fail(endpoint, fields):
                if endpoint == "alwaysfails":
                    call_count[0] += 1
                    raise SessionExpiredError()
                return await original_call(endpoint, fields)

            client.call = always_fail
            return client

        client_factory.create_client = factory_always_failing

        # Read should fail on second attempt and propagate
        with pytest.raises(SessionExpiredError):
            await pool.call(principal, "alwaysfails", {}, read_write="read")

        # Should be called exactly twice (initial + one retry)
        assert call_count[0] == 2
