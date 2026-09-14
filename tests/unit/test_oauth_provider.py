"""Tests for FamilyWallAuthProvider against real SDK types and a real sqlite store."""

from __future__ import annotations

import re
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import httpx
import pytest
from mcp.server.auth.provider import (
    AuthorizationParams,
    RegistrationError,
    TokenError,
)
from mcp.shared.auth import OAuthClientInformationFull, OAuthToken
from pydantic import AnyUrl
from starlette.applications import Starlette
from starlette.routing import Route

from familywall_mcp.auth.provider import LOGIN_MAX_ATTEMPTS, FamilyWallAuthProvider
from familywall_mcp.auth.storage import OAuthSqliteStore
from familywall_mcp.config import AppConfig

REDIRECT_URI = "https://client.example/callback"


def _hosted_config(**overrides: str) -> AppConfig:
    env = {
        "FAMILYWALL_MODE": "hosted",
        "FAMILYWALL_PUBLIC_URL": "https://mcp.example.invalid",
        "FAMILYWALL_AUTH_SECRET_KEY": "x" * 32,
        "FAMILYWALL_ALLOWED_REDIRECT_URI_HOSTS": "client.example",
        "FAMILYWALL_USER_1_MCP_USERNAME": "alice",
        "FAMILYWALL_USER_1_MCP_PASSWORD": "alice-mcp-pass",
        "FAMILYWALL_USER_1_FW_EMAIL": "alice@example.com",
        "FAMILYWALL_USER_1_FW_PASSWORD": "alice-fw-pass",
        "FAMILYWALL_USER_2_MCP_USERNAME": "bob",
        "FAMILYWALL_USER_2_MCP_PASSWORD": "bob-mcp-pass",
        "FAMILYWALL_USER_2_FW_EMAIL": "bob@example.com",
        "FAMILYWALL_USER_2_FW_PASSWORD": "bob-fw-pass",
    }
    env.update(overrides)
    return AppConfig.from_env(env)


@pytest.fixture
async def provider(tmp_path: Path) -> FamilyWallAuthProvider:
    config = _hosted_config()
    store = OAuthSqliteStore(tmp_path / "oauth.sqlite3")
    await store.initialise()
    assert config.public_url is not None
    return FamilyWallAuthProvider(config, store, config.public_url)


async def _register(
    provider: FamilyWallAuthProvider,
    client_id: str = "client-1",
    redirect_uri: str = REDIRECT_URI,
) -> OAuthClientInformationFull:
    client_info = OAuthClientInformationFull(
        client_id=client_id,
        client_name="Test Client",
        redirect_uris=[AnyUrl(redirect_uri)],
        grant_types=["authorization_code", "refresh_token"],
        response_types=["code"],
    )
    await provider.register_client(client_info)
    registered = await provider.get_client(client_id)
    assert registered is not None
    return registered


def _login_app(provider: FamilyWallAuthProvider) -> Starlette:
    return Starlette(
        routes=[
            Route("/login", provider.handle_login_page, methods=["GET"]),
            Route("/login/callback", provider.handle_login_callback, methods=["POST"]),
        ]
    )


def _extract_csrf_token(html: str) -> str:
    match = re.search(r'name="csrf_token" value="([0-9a-f]+)"', html)
    assert match is not None
    return match.group(1)


async def _full_login(
    provider: FamilyWallAuthProvider,
    client: OAuthClientInformationFull,
    username: str,
    password: str,
    client_state: str = "client-state-xyz",
) -> OAuthToken:
    """Drive authorize -> login page -> callback -> code exchange, over real HTTP."""
    params = AuthorizationParams(
        state=client_state,
        scopes=["familywall"],
        code_challenge="challenge123",
        redirect_uri=AnyUrl(REDIRECT_URI),
        redirect_uri_provided_explicitly=True,
    )
    login_url = await provider.authorize(client, params)
    login_state = parse_qs(urlparse(login_url).query)["state"][0]

    app = _login_app(provider)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="https://testserver") as http:
        login_page = await http.get(f"/login?state={login_state}")
        assert login_page.status_code == 200
        csrf_token = _extract_csrf_token(login_page.text)

        result = await http.post(
            "/login/callback",
            data={
                "username": username,
                "password": password,
                "state": login_state,
                "csrf_token": csrf_token,
            },
        )
        assert result.status_code == 302
        redirect_qs = parse_qs(urlparse(result.headers["location"]).query)
        code = redirect_qs["code"][0]

    auth_code = await provider.load_authorization_code(client, code)
    assert auth_code is not None
    return await provider.exchange_authorization_code(client, auth_code)


class TestRegisterClient:
    @pytest.mark.asyncio
    async def test_register_client_happy_path(self, provider: FamilyWallAuthProvider) -> None:
        registered = await _register(provider)
        assert registered.client_id == "client-1"
        assert registered.client_name == "Test Client"

    @pytest.mark.asyncio
    async def test_register_client_rejects_disallowed_redirect_host(
        self, provider: FamilyWallAuthProvider
    ) -> None:
        client_info = OAuthClientInformationFull(
            client_id="client-evil",
            client_name="Evil Client",
            redirect_uris=[AnyUrl("https://evil.example/callback")],
        )
        with pytest.raises(RegistrationError):
            await provider.register_client(client_info)
        assert await provider.get_client("client-evil") is None


class TestAuthorize:
    @pytest.mark.asyncio
    async def test_pending_authorization_is_keyed_by_server_state_not_client_state(
        self, provider: FamilyWallAuthProvider
    ) -> None:
        registered = await _register(provider)
        params = AuthorizationParams(
            state="client-state-xyz",
            scopes=["familywall"],
            code_challenge="challenge123",
            redirect_uri=AnyUrl(REDIRECT_URI),
            redirect_uri_provided_explicitly=True,
        )
        login_url = await provider.authorize(registered, params)
        login_state = parse_qs(urlparse(login_url).query)["state"][0]

        assert login_state != "client-state-xyz"

        pending = await provider._store.get_pending_authorization(login_state)
        assert pending is not None
        assert pending["client_state"] == "client-state-xyz"
        assert pending["client_id"] == "client-1"

        # The client's own state must never itself be a valid pending-authorization key.
        assert await provider._store.get_pending_authorization("client-state-xyz") is None


class TestLoginFlow:
    @pytest.mark.asyncio
    async def test_full_login_issues_a_working_token_pair(
        self, provider: FamilyWallAuthProvider
    ) -> None:
        registered = await _register(provider)
        token = await _full_login(provider, registered, "alice", "alice-mcp-pass")

        assert token.access_token.startswith("fw_")
        assert token.refresh_token is not None
        assert token.refresh_token.startswith("fw_refresh_")

        access = await provider.load_access_token(token.access_token)
        assert access is not None
        assert access.subject == "alice"

    @pytest.mark.asyncio
    async def test_wrong_password_returns_401(self, provider: FamilyWallAuthProvider) -> None:
        registered = await _register(provider)
        params = AuthorizationParams(
            state="client-state-xyz",
            scopes=["familywall"],
            code_challenge="challenge123",
            redirect_uri=AnyUrl(REDIRECT_URI),
            redirect_uri_provided_explicitly=True,
        )
        login_url = await provider.authorize(registered, params)
        login_state = parse_qs(urlparse(login_url).query)["state"][0]

        app = _login_app(provider)
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="https://testserver"
        ) as http:
            login_page = await http.get(f"/login?state={login_state}")
            csrf_token = _extract_csrf_token(login_page.text)

            result = await http.post(
                "/login/callback",
                data={
                    "username": "alice",
                    "password": "wrong-password",
                    "state": login_state,
                    "csrf_token": csrf_token,
                },
            )
            assert result.status_code == 401

    @pytest.mark.asyncio
    async def test_lockout_after_max_attempts(self, provider: FamilyWallAuthProvider) -> None:
        registered = await _register(provider)
        params = AuthorizationParams(
            state="client-state-xyz",
            scopes=["familywall"],
            code_challenge="challenge123",
            redirect_uri=AnyUrl(REDIRECT_URI),
            redirect_uri_provided_explicitly=True,
        )
        login_url = await provider.authorize(registered, params)
        login_state = parse_qs(urlparse(login_url).query)["state"][0]

        app = _login_app(provider)
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="https://testserver"
        ) as http:
            login_page = await http.get(f"/login?state={login_state}")
            csrf_token = _extract_csrf_token(login_page.text)

            for _ in range(LOGIN_MAX_ATTEMPTS):
                result = await http.post(
                    "/login/callback",
                    data={
                        "username": "alice",
                        "password": "wrong-password",
                        "state": login_state,
                        "csrf_token": csrf_token,
                    },
                )
                assert result.status_code == 401

            # The next attempt is locked out even with the correct password.
            locked = await http.post(
                "/login/callback",
                data={
                    "username": "alice",
                    "password": "alice-mcp-pass",
                    "state": login_state,
                    "csrf_token": csrf_token,
                },
            )
            assert locked.status_code == 429

    @pytest.mark.asyncio
    async def test_csrf_mismatch_returns_400(self, provider: FamilyWallAuthProvider) -> None:
        registered = await _register(provider)
        params = AuthorizationParams(
            state="client-state-xyz",
            scopes=["familywall"],
            code_challenge="challenge123",
            redirect_uri=AnyUrl(REDIRECT_URI),
            redirect_uri_provided_explicitly=True,
        )
        login_url = await provider.authorize(registered, params)
        login_state = parse_qs(urlparse(login_url).query)["state"][0]

        app = _login_app(provider)
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="https://testserver"
        ) as http:
            login_page = await http.get(f"/login?state={login_state}")
            assert "fw_csrf" in login_page.cookies
            # Use a csrf_token that does not match the signed cookie.
            result = await http.post(
                "/login/callback",
                data={
                    "username": "alice",
                    "password": "alice-mcp-pass",
                    "state": login_state,
                    "csrf_token": "0" * 32,
                },
            )
            assert result.status_code == 400


class TestAuthorizationCodeExchange:
    @pytest.mark.asyncio
    async def test_code_replay_is_rejected(self, provider: FamilyWallAuthProvider) -> None:
        registered = await _register(provider)
        params = AuthorizationParams(
            state="client-state-xyz",
            scopes=["familywall"],
            code_challenge="challenge123",
            redirect_uri=AnyUrl(REDIRECT_URI),
            redirect_uri_provided_explicitly=True,
        )
        login_url = await provider.authorize(registered, params)
        login_state = parse_qs(urlparse(login_url).query)["state"][0]

        app = _login_app(provider)
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="https://testserver"
        ) as http:
            login_page = await http.get(f"/login?state={login_state}")
            csrf_token = _extract_csrf_token(login_page.text)
            result = await http.post(
                "/login/callback",
                data={
                    "username": "alice",
                    "password": "alice-mcp-pass",
                    "state": login_state,
                    "csrf_token": csrf_token,
                },
            )
            code = parse_qs(urlparse(result.headers["location"]).query)["code"][0]

        auth_code = await provider.load_authorization_code(registered, code)
        assert auth_code is not None
        await provider.exchange_authorization_code(registered, auth_code)

        with pytest.raises(TokenError):
            await provider.exchange_authorization_code(registered, auth_code)


class TestRefreshTokenRotation:
    @pytest.mark.asyncio
    async def test_rotation_issues_new_token_while_old_access_token_still_works(
        self, provider: FamilyWallAuthProvider
    ) -> None:
        registered = await _register(provider)
        token = await _full_login(provider, registered, "alice", "alice-mcp-pass")
        assert token.refresh_token is not None

        refresh = await provider.load_refresh_token(registered, token.refresh_token)
        assert refresh is not None

        rotated = await provider.exchange_refresh_token(registered, refresh, scopes=["familywall"])
        assert rotated.access_token != token.access_token
        assert rotated.refresh_token != token.refresh_token

        # The old access token is untouched by rotation; it remains valid until its
        # own expiry, and the newly minted one is valid too.
        old_access = await provider.load_access_token(token.access_token)
        assert old_access is not None
        new_access = await provider.load_access_token(rotated.access_token)
        assert new_access is not None

    @pytest.mark.asyncio
    async def test_refresh_token_reuse_revokes_every_token_for_the_subject(
        self, provider: FamilyWallAuthProvider
    ) -> None:
        registered = await _register(provider)
        token = await _full_login(provider, registered, "alice", "alice-mcp-pass")
        assert token.refresh_token is not None

        refresh = await provider.load_refresh_token(registered, token.refresh_token)
        assert refresh is not None
        rotated = await provider.exchange_refresh_token(registered, refresh, scopes=["familywall"])
        assert rotated.refresh_token is not None

        # Reuse: present the already-rotated (now consumed) refresh token again.
        reused = await provider.load_refresh_token(registered, token.refresh_token)
        assert reused is None

        # Every token issued to this subject -- including the ones minted by the
        # rotation that just happened -- must now be dead.
        assert await provider.load_access_token(token.access_token) is None
        assert await provider.load_access_token(rotated.access_token) is None
        assert await provider.load_refresh_token(registered, rotated.refresh_token) is None


class TestRevokeToken:
    @pytest.mark.asyncio
    async def test_revoking_access_token_also_revokes_paired_refresh_token(
        self, provider: FamilyWallAuthProvider
    ) -> None:
        registered = await _register(provider)
        token = await _full_login(provider, registered, "bob", "bob-mcp-pass")
        assert token.refresh_token is not None

        access = await provider.load_access_token(token.access_token)
        assert access is not None

        await provider.revoke_token(access)

        assert await provider.load_access_token(token.access_token) is None
        assert await provider.load_refresh_token(registered, token.refresh_token) is None

    @pytest.mark.asyncio
    async def test_revoking_refresh_token_also_revokes_paired_access_token(
        self, provider: FamilyWallAuthProvider
    ) -> None:
        registered = await _register(provider)
        token = await _full_login(provider, registered, "bob", "bob-mcp-pass")
        assert token.refresh_token is not None

        refresh = await provider.load_refresh_token(registered, token.refresh_token)
        assert refresh is not None

        await provider.revoke_token(refresh)

        assert await provider.load_refresh_token(registered, token.refresh_token) is None
        assert await provider.load_access_token(token.access_token) is None
