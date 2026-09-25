"""Tests for ContextResolver.refresh() (C13): FixedContextResolver with and
without a session pool, and HostedContextResolver's invalidate-then-rebuild.
"""

from __future__ import annotations

import pytest
from mcp.server.auth.middleware.auth_context import auth_context_var
from mcp.server.auth.middleware.bearer_auth import AuthenticatedUser
from mcp.server.auth.provider import AccessToken

from familywall_mcp.familywall.discovery import parse_discovery
from familywall_mcp.models import Principal
from familywall_mcp.services.principal_context import (
    FixedContextResolver,
    HostedContextResolver,
    PrincipalContext,
)


def _synthetic_family_payload() -> dict[str, object]:
    """A minimal raw accgetallfamily payload with one authenticated member."""
    return {
        "family_id": "1",
        "metaId": "family/1",
        "name": "Synthetic Family",
        "members": [
            {
                "accountId": "acct-alex",
                "name": "Alex Doe",
                "firstName": "Alex",
                "timeZone": "Australia/Sydney",
                "isloggedaccount": "true",
            },
        ],
    }


def _synthetic_family_payload_with_robin() -> dict[str, object]:
    """The same family, with a second member ('Robin Doe') added."""
    payload = _synthetic_family_payload()
    members = list(payload["members"])  # type: ignore[arg-type]
    members.append(
        {
            "accountId": "acct-robin",
            "name": "Robin Doe",
            "firstName": "Robin",
            "timeZone": "Australia/Sydney",
            "isloggedaccount": "false",
        }
    )
    payload["members"] = members
    return payload


class _CountingDiscoveryPool:
    """A fake SessionPool whose accgetallfamily call is counted."""

    def __init__(self, payload: object) -> None:
        self.payload = payload
        self.calls = 0

    async def call(
        self, principal: Principal, endpoint: str, fields: dict[str, str], read_write: str
    ) -> object:
        assert endpoint == "accgetallfamily"
        assert read_write == "read"
        self.calls += 1
        return self.payload

    async def aclose(self) -> None:
        pass


def _context_for(payload: dict[str, object]) -> PrincipalContext:
    """A PrincipalContext built directly from a raw discovery payload, with no
    calendar service (never exercised by these tests)."""
    discovered = parse_discovery(payload)
    return PrincipalContext(
        family_context=discovered.to_context(),
        discovered_family=discovered,
        authenticated_member_timezone="Australia/Sydney",
        calendar_service=None,  # type: ignore[arg-type]
    )


class TestFixedContextResolverRefresh:
    """C13: FixedContextResolver.refresh() with and without a session_pool."""

    async def test_c13_without_a_pool_returns_the_same_pair_unchanged(self) -> None:
        principal = Principal(subject="subject-alex")
        context = _context_for(_synthetic_family_payload())
        resolver = FixedContextResolver(principal, context)

        refreshed_principal, refreshed_context = await resolver.refresh()

        assert refreshed_principal is principal
        assert refreshed_context is context

    async def test_c13_with_a_pool_rebuilds_via_one_more_discovery_call(self) -> None:
        principal = Principal(subject="subject-alex")
        initial_context = _context_for(_synthetic_family_payload())
        pool = _CountingDiscoveryPool(_synthetic_family_payload_with_robin())
        resolver = FixedContextResolver(principal, initial_context, pool)  # type: ignore[arg-type]

        refreshed_principal, refreshed_context = await resolver.refresh()

        assert refreshed_principal is principal
        assert pool.calls == 1
        assert refreshed_context is not initial_context
        assert [m.display_name for m in refreshed_context.discovered_family.members] == [
            "Alex Doe",
            "Robin Doe",
        ]
        # A second resolve() now returns the rebuilt context, not the original.
        _again_principal, again_context = await resolver.resolve()
        assert again_context is refreshed_context


class TestHostedContextResolverRefresh:
    """C13: HostedContextResolver.refresh() invalidates, then rebuilds."""

    async def test_c13_refresh_invalidates_then_rebuilds(self) -> None:
        """A fake discovery is called twice: once by the initial resolve(),
        once more by refresh()."""
        pool = _CountingDiscoveryPool(_synthetic_family_payload())
        resolver = HostedContextResolver(pool)  # type: ignore[arg-type]

        auth_user = AuthenticatedUser(
            auth_info=AccessToken(
                token="tok-1",
                client_id="client-1",
                scopes=["familywall"],
                subject="subject-alex",
            )
        )
        reset_token = auth_context_var.set(auth_user)
        try:
            principal1, context1 = await resolver.resolve()
            assert pool.calls == 1

            # A second resolve() is served from cache: no extra discovery call.
            _principal_again, context_again = await resolver.resolve()
            assert context_again is context1
            assert pool.calls == 1

            principal2, context2 = await resolver.refresh()
            assert pool.calls == 2
            assert principal2.subject == principal1.subject
            assert context2 is not context1
        finally:
            auth_context_var.reset(reset_token)

    async def test_c13_refresh_with_no_access_token_fails_closed(self) -> None:
        pool = _CountingDiscoveryPool(_synthetic_family_payload())
        resolver = HostedContextResolver(pool)  # type: ignore[arg-type]

        from familywall_mcp.errors import AuthenticationError

        with pytest.raises(AuthenticationError):
            await resolver.refresh()

        assert pool.calls == 0
