"""End-to-end tests for the hosted ASGI app: OAuth routes, health, and per-user
tool-call isolation.

The FamilyWall upstream itself is faked with respx (matching
tests/unit/test_client.py's pattern) for the OAuth/login half of the flow.

``test_hosted_app_tool_call_isolation_by_subject`` drives a real tool call
through the mounted ``/mcp`` Streamable HTTP endpoint end to end (DCR,
login, token exchange, MCP `initialize`, then `tools/call`). An earlier
version of this suite could not do this: wrapping the app
``MCPServer.streamable_http_app()`` builds inside a second, outer
``Starlette`` (mounted via ``Mount("/", app=mcp_app)``) meant the outer
app's ASGI lifespan ran but the inner app's own lifespan -- which starts the
Streamable HTTP session manager's task group -- never did, so every real
tool call failed with "Task group is not initialized." ``build_hosted_app``
now registers extra routes via ``MCPServer.custom_route`` directly on the
one app the SDK builds instead, and ``_connection_status_for_token`` (used by
the second isolation test below) is kept as a fast, complementary check of
the same isolation property at the ``HostedContextResolver`` level.
"""

from __future__ import annotations

import base64
import hashlib
import json
import re
import secrets
import string
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import httpx
import pytest
import respx
from mcp.server.auth.middleware.auth_context import auth_context_var
from mcp.server.auth.middleware.bearer_auth import AuthenticatedUser
from mcp.server.auth.provider import AccessToken
from tests.support.discovery_fixtures import normal_discovery_payload

from familywall_mcp.auth.storage import OAuthSqliteStore
from familywall_mcp.config import AppConfig
from familywall_mcp.server import build_hosted_app
from familywall_mcp.services.principal_context import HostedContextResolver
from familywall_mcp.storage.memory import InMemoryReceiptRepository
from familywall_mcp.tools.registry import ToolRegistry

REDIRECT_URI = "https://client.example/callback"


def _hosted_config(database_path: Path) -> AppConfig:
    return AppConfig.from_env(
        {
            "FAMILYWALL_MODE": "hosted",
            "FAMILYWALL_PUBLIC_URL": "https://mcp.example.invalid",
            "FAMILYWALL_AUTH_SECRET_KEY": "x" * 32,
            "FAMILYWALL_DATABASE_PATH": str(database_path),
            "FAMILYWALL_ALLOWED_REDIRECT_URI_HOSTS": "client.example",
            "FAMILYWALL_BASE_URL": "https://familywall.example.invalid",
            "FAMILYWALL_USER_1_MCP_USERNAME": "alice",
            "FAMILYWALL_USER_1_MCP_PASSWORD": "alice-mcp-pass",
            "FAMILYWALL_USER_1_FW_EMAIL": "alice@example.com",
            "FAMILYWALL_USER_1_FW_PASSWORD": "alice-fw-pass",
            "FAMILYWALL_USER_2_MCP_USERNAME": "bob",
            "FAMILYWALL_USER_2_MCP_PASSWORD": "bob-mcp-pass",
            "FAMILYWALL_USER_2_FW_EMAIL": "bob@example.com",
            "FAMILYWALL_USER_2_FW_PASSWORD": "bob-fw-pass",
        }
    )


def _discovery_payload_for(email: str) -> dict[str, object]:
    """A discovery payload distinct per simulated user, in the real wire shape."""
    payload = normal_discovery_payload()
    local_part = email.split("@")[0]
    payload["name"] = f"{local_part.title()}'s Family"
    payload["family_id"] = f"family-{local_part}"
    return payload


def _make_pkce() -> tuple[str, str]:
    alphabet = string.ascii_letters + string.digits + "-._~"
    verifier = "".join(secrets.choice(alphabet) for _ in range(64))
    digest = hashlib.sha256(verifier.encode()).digest()
    challenge = base64.urlsafe_b64encode(digest).decode().rstrip("=")
    return verifier, challenge


def _extract_csrf_token(html: str) -> str:
    match = re.search(r'name="csrf_token" value="([0-9a-f]+)"', html)
    assert match is not None
    return match.group(1)


def _cookie_response(
    request: httpx.Request, body: dict[str, object], cookies: dict[str, str]
) -> httpx.Response:
    header_list = [("set-cookie", f"{name}={value}; Path=/") for name, value in cookies.items()]
    response = httpx.Response(200, text=json.dumps(body), headers=header_list)
    response._request = request
    _ = response.cookies
    return response


class _FakeFamilyWallUpstream:
    """Fakes FamilyWall's log2in + accgetallfamily so two simulated hosted users
    (identified by their own JSESSIONID) never see each other's discovery data."""

    def __init__(self) -> None:
        self._email_by_jsessionid: dict[str, str] = {}

    def login_callback(self, request: httpx.Request) -> httpx.Response:
        content = request.content.decode()
        match = re.search(r"a00identifier=([^&]+)", content)
        assert match is not None
        email = _url_decode(match.group(1))
        jsessionid = f"session-{hashlib.sha256(email.encode()).hexdigest()[:16]}"
        self._email_by_jsessionid[jsessionid] = email
        body = {
            "a00": {
                "r": {"r": {"accountId": f"acc-{email}", "tokenCsrf": "abcd" * 8}},
                "cn": "log2in",
            }
        }
        return _cookie_response(request, body, {"JSESSIONID": jsessionid})

    def accgetallfamily_callback(self, request: httpx.Request) -> httpx.Response:
        jsessionid = _cookie_value(request.headers.get("cookie", ""), "JSESSIONID")
        email = self._email_by_jsessionid[jsessionid]
        payload = _discovery_payload_for(email)
        body = {"a00": {"r": {"r": payload}, "cn": "accgetallfamily"}}
        return httpx.Response(200, text=json.dumps(body))


def _url_decode(value: str) -> str:
    from urllib.parse import unquote_plus

    return unquote_plus(value)


def _cookie_value(cookie_header: str, name: str) -> str | None:
    for part in cookie_header.split(";"):
        key, _, value = part.strip().partition("=")
        if key == name:
            return value
    return None


async def _register_client(http: httpx.AsyncClient, redirect_uri: str = REDIRECT_URI) -> str:
    response = await http.post(
        "/register",
        json={
            "redirect_uris": [redirect_uri],
            "client_name": "Test Client",
            "grant_types": ["authorization_code", "refresh_token"],
            "response_types": ["code"],
            "token_endpoint_auth_method": "none",
        },
    )
    assert response.status_code == 201
    client_id = response.json()["client_id"]
    assert isinstance(client_id, str)
    return client_id


async def _obtain_token(
    http: httpx.AsyncClient, client_id: str, username: str, password: str
) -> dict[str, object]:
    verifier, challenge = _make_pkce()
    state = secrets.token_hex(8)

    authorize_resp = await http.get(
        "/authorize",
        params={
            "response_type": "code",
            "client_id": client_id,
            "redirect_uri": REDIRECT_URI,
            "code_challenge": challenge,
            "code_challenge_method": "S256",
            "state": state,
            "scope": "familywall",
            "resource": f"{str(http.base_url).rstrip('/')}/mcp",
        },
    )
    assert authorize_resp.status_code == 302
    login_url = authorize_resp.headers["location"]
    login_state = parse_qs(urlparse(login_url).query)["state"][0]

    login_page = await http.get(f"/login?state={login_state}")
    assert login_page.status_code == 200
    csrf_token = _extract_csrf_token(login_page.text)

    callback_resp = await http.post(
        "/login/callback",
        data={
            "username": username,
            "password": password,
            "state": login_state,
            "csrf_token": csrf_token,
        },
    )
    assert callback_resp.status_code == 302
    redirect_qs = parse_qs(urlparse(callback_resp.headers["location"]).query)
    assert redirect_qs["state"][0] == state
    code = redirect_qs["code"][0]

    token_resp = await http.post(
        "/token",
        data={
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": REDIRECT_URI,
            "client_id": client_id,
            "code_verifier": verifier,
        },
    )
    assert token_resp.status_code == 200
    payload = token_resp.json()
    assert isinstance(payload, dict)
    return payload


class _FakeDiscoverySessionPool:
    """A minimal SessionPool double: returns a fixed discovery payload for
    whichever principal it was built for, matching tests/unit/test_tools.py's
    FakeSessionPool pattern."""

    def __init__(self, discovery_payload: dict[str, object]) -> None:
        self._discovery_payload = discovery_payload

    async def call(
        self,
        principal: object,
        endpoint: str,
        fields: dict[str, str],
        read_write: str,
    ) -> object:
        assert endpoint == "accgetallfamily"
        assert read_write == "read"
        return self._discovery_payload

    async def aclose(self) -> None:
        pass


async def _connection_status_for_token(
    config: AppConfig, store: OAuthSqliteStore, access_token: str
) -> dict[str, object]:
    """Resolve a tool call's response for a genuinely-issued bearer token.

    Looks up the token's real ``subject`` from the same durable store the
    hosted app itself uses, then drives the real ``HostedContextResolver`` and
    ``ToolRegistry`` the same way the hosted app wires them, with
    ``auth_context_var`` set the same way ``AuthContextMiddleware`` sets it for
    an authenticated request.
    """
    token_row = await store.get_access_token(access_token)
    assert token_row is not None
    subject = token_row["subject"]

    hosted_user = config.find_hosted_user(subject)
    assert hosted_user is not None

    session_pool = _FakeDiscoverySessionPool(_discovery_payload_for(hosted_user.familywall_email))
    context_resolver = HostedContextResolver(session_pool)  # type: ignore[arg-type]
    registry = ToolRegistry(
        config=config,
        session_pool=session_pool,  # type: ignore[arg-type]
        context_resolver=context_resolver,
        receipt_repository=InMemoryReceiptRepository(),
    )

    auth_user = AuthenticatedUser(
        auth_info=AccessToken(
            token=access_token,
            client_id=token_row["client_id"],
            scopes=token_row["scopes"],
            subject=subject,
        )
    )
    reset_token = auth_context_var.set(auth_user)
    try:
        result = await registry._get_connection_status()
    finally:
        auth_context_var.reset(reset_token)

    return result.model_dump()


@pytest.mark.asyncio
async def test_hosted_app_health_metadata_dcr_and_unauthenticated_mcp(tmp_path: Path) -> None:
    config = _hosted_config(tmp_path / "hosted.sqlite3")
    hosted = build_hosted_app(config)
    await hosted.startup()

    async with hosted.app.router.lifespan_context(hosted.app):
        transport = httpx.ASGITransport(app=hosted.app)
        async with httpx.AsyncClient(transport=transport, base_url=config.public_url) as http:
            health = await http.get("/health")
            assert health.status_code == 200

            metadata = await http.get("/.well-known/oauth-authorization-server")
            assert metadata.status_code == 200
            assert metadata.json()["issuer"].rstrip("/") == config.public_url

            await _register_client(http)

            bad_registration = await http.post(
                "/register",
                json={
                    "redirect_uris": ["https://evil.example/callback"],
                    "client_name": "Bad Client",
                },
            )
            assert bad_registration.status_code == 400

            unauthenticated = await http.post(
                "/mcp", json={"jsonrpc": "2.0", "id": 1, "method": "ping"}
            )
            assert unauthenticated.status_code == 401

    await hosted.cleanup()


@pytest.mark.asyncio
async def test_hosted_app_two_users_get_distinct_bearer_tokens(tmp_path: Path) -> None:
    database_path = tmp_path / "hosted.sqlite3"
    config = _hosted_config(database_path)
    hosted = build_hosted_app(config)
    await hosted.startup()
    upstream = _FakeFamilyWallUpstream()

    async with hosted.app.router.lifespan_context(hosted.app):
        transport = httpx.ASGITransport(app=hosted.app)
        async with httpx.AsyncClient(transport=transport, base_url=config.public_url) as http:
            with respx.mock:
                respx.post(f"{config.familywall_base_url}/api/log2in").mock(
                    side_effect=upstream.login_callback
                )
                respx.post(f"{config.familywall_base_url}/api/accgetallfamily").mock(
                    side_effect=upstream.accgetallfamily_callback
                )

                client_id = await _register_client(http)
                token_alice = await _obtain_token(http, client_id, "alice", "alice-mcp-pass")
                token_bob = await _obtain_token(http, client_id, "bob", "bob-mcp-pass")

    await hosted.cleanup()

    assert token_alice["access_token"] != token_bob["access_token"]
    assert token_alice["refresh_token"] != token_bob["refresh_token"]

    store = OAuthSqliteStore(database_path)
    await store.initialise()
    alice_row = await store.get_access_token(token_alice["access_token"])
    bob_row = await store.get_access_token(token_bob["access_token"])
    assert alice_row is not None
    assert bob_row is not None
    assert alice_row["subject"] == "alice"
    assert bob_row["subject"] == "bob"


def _sse_json_bodies(text: str) -> list[dict[str, object]]:
    """Parse `data: {...}` lines out of a text/event-stream response body."""
    bodies = []
    for line in text.splitlines():
        if line.startswith("data:"):
            bodies.append(json.loads(line[len("data:") :].strip()))
    return bodies


async def _call_tool_via_mcp(
    http: httpx.AsyncClient, access_token: str, tool_name: str, arguments: dict[str, object]
) -> dict[str, object]:
    """Drive a real tool call through the mounted ``/mcp`` Streamable HTTP
    endpoint: `initialize`, the `notifications/initialized` handshake, then
    `tools/call`, reusing the `mcp-session-id` the server assigns."""
    headers = {
        "Authorization": f"Bearer {access_token}",
        "Accept": "application/json, text/event-stream",
    }

    init_resp = await http.post(
        "/mcp",
        json={
            "jsonrpc": "2.0",
            "id": 0,
            "method": "initialize",
            "params": {
                "protocolVersion": "2025-06-18",
                "capabilities": {},
                "clientInfo": {"name": "test-client", "version": "0.0.1"},
            },
        },
        headers=headers,
    )
    assert init_resp.status_code == 200, init_resp.text
    session_id = init_resp.headers.get("mcp-session-id")
    assert session_id
    headers["mcp-session-id"] = session_id

    initialized_resp = await http.post(
        "/mcp",
        json={"jsonrpc": "2.0", "method": "notifications/initialized"},
        headers=headers,
    )
    assert initialized_resp.status_code == 202, initialized_resp.text

    call_resp = await http.post(
        "/mcp",
        json={
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/call",
            "params": {"name": tool_name, "arguments": arguments},
        },
        headers=headers,
    )
    assert call_resp.status_code == 200, call_resp.text
    (body,) = _sse_json_bodies(call_resp.text)
    result = body["result"]
    assert isinstance(result, dict)
    assert result.get("isError") is not True, result
    structured = result["structuredContent"]["result"]
    assert isinstance(structured, dict)
    return structured


@pytest.mark.asyncio
async def test_hosted_app_tool_call_isolation_via_mcp_endpoint(tmp_path: Path) -> None:
    """Two hosted users' tool calls, driven through the real mounted ``/mcp``
    Streamable HTTP endpoint end to end, resolve to their own isolated
    family data -- the regression test for the lifespan-wiring bug described
    in this module's docstring."""
    database_path = tmp_path / "hosted.sqlite3"
    config = _hosted_config(database_path)
    hosted = build_hosted_app(config)
    await hosted.startup()
    upstream = _FakeFamilyWallUpstream()

    async with hosted.app.router.lifespan_context(hosted.app):
        transport = httpx.ASGITransport(app=hosted.app)
        async with httpx.AsyncClient(transport=transport, base_url=config.public_url) as http:
            with respx.mock:
                respx.post(f"{config.familywall_base_url}/api/log2in").mock(
                    side_effect=upstream.login_callback
                )
                respx.post(f"{config.familywall_base_url}/api/accgetallfamily").mock(
                    side_effect=upstream.accgetallfamily_callback
                )

                client_id = await _register_client(http)
                token_alice = await _obtain_token(http, client_id, "alice", "alice-mcp-pass")
                token_bob = await _obtain_token(http, client_id, "bob", "bob-mcp-pass")

                status_alice = await _call_tool_via_mcp(
                    http, token_alice["access_token"], "get_connection_status", {}
                )
                status_bob = await _call_tool_via_mcp(
                    http, token_bob["access_token"], "get_connection_status", {}
                )

    await hosted.cleanup()

    assert status_alice["family_name"] == "Alice's Family"
    assert status_bob["family_name"] == "Bob's Family"
    assert status_alice["family_name"] != status_bob["family_name"]


@pytest.mark.asyncio
async def test_hosted_app_tool_call_isolation_by_subject(tmp_path: Path) -> None:
    """Complementary, faster check of the same isolation property directly at
    the ``HostedContextResolver``/``ToolRegistry`` level (no SSE/session
    handshake), keyed by the real ``subject`` recorded against a
    genuinely-issued bearer token."""
    database_path = tmp_path / "hosted.sqlite3"
    config = _hosted_config(database_path)
    hosted = build_hosted_app(config)
    await hosted.startup()
    upstream = _FakeFamilyWallUpstream()

    async with hosted.app.router.lifespan_context(hosted.app):
        transport = httpx.ASGITransport(app=hosted.app)
        async with httpx.AsyncClient(transport=transport, base_url=config.public_url) as http:
            with respx.mock:
                respx.post(f"{config.familywall_base_url}/api/log2in").mock(
                    side_effect=upstream.login_callback
                )
                respx.post(f"{config.familywall_base_url}/api/accgetallfamily").mock(
                    side_effect=upstream.accgetallfamily_callback
                )

                client_id = await _register_client(http)
                token_alice = await _obtain_token(http, client_id, "alice", "alice-mcp-pass")
                token_bob = await _obtain_token(http, client_id, "bob", "bob-mcp-pass")

        store = OAuthSqliteStore(database_path)
        await store.initialise()

        status_alice = await _connection_status_for_token(
            config, store, token_alice["access_token"]
        )
        status_bob = await _connection_status_for_token(config, store, token_bob["access_token"])

    await hosted.cleanup()

    assert status_alice["family_name"] == "Alice's Family"
    assert status_bob["family_name"] == "Bob's Family"
    assert status_alice["family_name"] != status_bob["family_name"]
