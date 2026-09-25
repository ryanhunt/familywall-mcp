"""MCP server lifecycle and initialization for both stdio and hosted modes."""

from __future__ import annotations

import contextlib
import sys
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from typing import TYPE_CHECKING
from urllib.parse import urlparse

import httpx
from mcp.server import MCPServer
from mcp.server.auth.settings import AuthSettings, ClientRegistrationOptions, RevocationOptions
from mcp.server.transport_security import TransportSecuritySettings
from pydantic import AnyHttpUrl, SecretStr
from starlette.responses import PlainTextResponse

if TYPE_CHECKING:
    from starlette.applications import Starlette
    from starlette.requests import Request
    from starlette.responses import Response

from familywall_mcp.auth.provider import DEFAULT_SCOPE, FamilyWallAuthProvider
from familywall_mcp.auth.storage import OAuthSqliteStore
from familywall_mcp.config import AppConfig, RuntimeMode
from familywall_mcp.credentials import EnvCredentialProvider, HostedCredentialProvider
from familywall_mcp.errors import FamilyWallError
from familywall_mcp.familywall.client import FamilyWallSession
from familywall_mcp.interfaces import CredentialProvider
from familywall_mcp.models import Principal
from familywall_mcp.services.principal_context import (
    FixedContextResolver,
    HostedContextResolver,
    build_principal_context,
)
from familywall_mcp.services.session import SessionPool
from familywall_mcp.storage.sqlite import SqliteReceiptRepository
from familywall_mcp.tools.registry import ToolRegistry


class SimpleClock:
    """Simple clock for SessionPool."""

    def now(self) -> datetime:
        """Return the current UTC datetime as timezone-aware."""
        return datetime.now(UTC)


class ClientFactory:
    """Factory to create authenticated FamilyWall sessions.

    Credentials are resolved per-principal via an injected ``CredentialProvider``
    rather than being fixed at construction: for stdio that provider always
    returns the same single-user pair, and for hosted mode it looks up the
    calling principal's own configured FamilyWall credentials, which is what
    keeps two hosted users' sessions from ever sharing an upstream login.
    """

    def __init__(
        self,
        base_url: str,
        http_client: httpx.AsyncClient,
        credential_provider: CredentialProvider,
    ) -> None:
        self.base_url = base_url
        self.http_client = http_client
        self.credential_provider = credential_provider

    async def create_client(self, principal: Principal) -> FamilyWallSession:
        """Create and authenticate a FamilyWallSession."""
        credentials = await self.credential_provider.get_credentials(principal)
        session = FamilyWallSession(
            base_url=self.base_url,
            http_client=self.http_client,
        )
        await session.login(credentials.username, credentials.password)
        return session


async def run_server() -> int:
    """Build and run the MCP server (stdio or hosted, per configuration).

    Returns:
        Exit code (0 on normal shutdown, 1 on error, 2 on configuration error).
    """
    try:
        config = AppConfig.from_env()
    except FamilyWallError as exc:
        print(f"configuration error: {exc.info.message}", file=sys.stderr)
        return 2

    if config.mode is RuntimeMode.HOSTED:
        return await _run_hosted(config)
    return await _run_stdio(config)


async def _run_stdio(config: AppConfig) -> int:
    http_client: httpx.AsyncClient | None = None
    session_pool: SessionPool | None = None
    receipt_repository: SqliteReceiptRepository | None = None

    try:
        email, password = config.require_familywall_credentials()
        principal = config.local_principal()

        http_client = httpx.AsyncClient()
        credential_provider = EnvCredentialProvider(email, SecretStr(password))
        factory = ClientFactory(config.familywall_base_url, http_client, credential_provider)
        session_pool = SessionPool(client_factory=factory, clock=SimpleClock())

        context = await build_principal_context(principal, session_pool)
        context_resolver = FixedContextResolver(principal, context, session_pool)

        receipt_repository = SqliteReceiptRepository(config.database_path, clock=SimpleClock())
        await receipt_repository.initialise()
        await receipt_repository.purge_expired()

        registry = ToolRegistry(
            config=config,
            session_pool=session_pool,
            context_resolver=context_resolver,
            receipt_repository=receipt_repository,
        )

        server = MCPServer(name="familywall", version="0.1.0")
        registry.register_tools(server)

        await server.run_stdio_async()
        return 0

    except FamilyWallError as exc:
        print(f"configuration error: {exc.info.message}", file=sys.stderr)
        return 2
    except Exception as exc:
        print(f"fatal error: {exc}", file=sys.stderr)
        return 1
    finally:
        try:
            if receipt_repository is not None:
                await receipt_repository.aclose()
        except Exception:
            pass
        try:
            if session_pool is not None:
                await session_pool.aclose()
        except Exception:
            pass
        try:
            if http_client is not None:
                await http_client.aclose()
        except Exception:
            pass


class HostedApp:
    """The hosted-mode ASGI app plus the resources ``_run_hosted`` must close.

    ``MCPServer.streamable_http_app()`` returns a single ``Starlette``
    instance whose *own* lifespan starts the Streamable HTTP session
    manager's task group (``lifespan=lambda app: session_manager.run()`` in
    the SDK's low-level ``Server.streamable_http_app``). That lifespan is
    fixed by the SDK and cannot be composed with a second one: wrapping this
    app inside another ``Starlette`` via ``Mount("/", app=mcp_app)`` and
    attaching our own startup/shutdown to the *outer* app's lifespan looks
    reasonable but is wrong — an ASGI server only drives the lifespan of the
    app it was actually given, so the outer app's lifespan runs while the
    inner (real) app's never does, and every tool call then fails with
    "Task group is not initialized." So extra routes are registered via
    ``MCPServer.custom_route`` instead, which the SDK folds into the *same*
    Starlette instance (see ``mcp.server.lowlevel.server.Server.streamable_http_app``),
    and our own resource setup/teardown is run directly by ``_run_hosted``
    around ``uvicorn.Server.serve()`` rather than via ASGI lifespan at all —
    the same shape ``_run_stdio`` already uses.
    """

    def __init__(
        self,
        app: Starlette,
        startup: Callable[[], Awaitable[None]],
        cleanup: Callable[[], Awaitable[None]],
    ) -> None:
        self.app = app
        self.startup = startup
        self.cleanup = cleanup


def build_hosted_app(config: AppConfig) -> HostedApp:
    """Build the hosted-mode ASGI app: OAuth login/authorization routes,
    a health check, and the MCP Streamable HTTP endpoint, all on one
    Starlette app suitable for serving under uvicorn (and, in front of
    that, Caddy)."""
    if config.public_url is None:
        raise FamilyWallError()  # unreachable: AppConfig validates this for hosted mode

    oauth_store = OAuthSqliteStore(config.database_path)
    auth_provider = FamilyWallAuthProvider(config, oauth_store, config.public_url)

    http_client = httpx.AsyncClient()
    credential_provider = HostedCredentialProvider(config)
    factory = ClientFactory(config.familywall_base_url, http_client, credential_provider)
    session_pool = SessionPool(client_factory=factory, clock=SimpleClock())
    context_resolver = HostedContextResolver(session_pool)

    receipt_repository = SqliteReceiptRepository(config.database_path, clock=SimpleClock())

    registry = ToolRegistry(
        config=config,
        session_pool=session_pool,
        context_resolver=context_resolver,
        receipt_repository=receipt_repository,
    )

    mcp_server = MCPServer(
        name="familywall",
        version="0.1.0",
        auth_server_provider=auth_provider,
        auth=AuthSettings(
            issuer_url=AnyHttpUrl(config.public_url),
            # The canonical resource clients bind their tokens to (RFC 8707)
            # is the MCP endpoint itself, not the bare domain -- matching
            # architecture.md's documented convention of `https://<domain>/mcp`.
            # With validate_token_resource=True, an access token whose
            # `resource` claim doesn't match this exactly is rejected, so this
            # must agree with the `resource` a client actually requests.
            resource_server_url=AnyHttpUrl(f"{config.public_url}/mcp"),
            validate_token_resource=True,
            client_registration_options=ClientRegistrationOptions(
                enabled=True,
                valid_scopes=[DEFAULT_SCOPE],
                default_scopes=[DEFAULT_SCOPE],
            ),
            revocation_options=RevocationOptions(enabled=True),
            required_scopes=[DEFAULT_SCOPE],
        ),
    )
    registry.register_tools(mcp_server)

    @mcp_server.custom_route("/login", methods=["GET"])  # type: ignore[untyped-decorator]
    async def login_page(request: Request) -> Response:
        return await auth_provider.handle_login_page(request)

    @mcp_server.custom_route("/login/callback", methods=["POST"])  # type: ignore[untyped-decorator]
    async def login_callback(request: Request) -> Response:
        return await auth_provider.handle_login_callback(request)

    @mcp_server.custom_route("/health", methods=["GET"])  # type: ignore[untyped-decorator]
    async def health(_request: Request) -> Response:
        return PlainTextResponse("ok")

    # Without an explicit transport_security, the SDK auto-enables DNS-rebinding
    # protection scoped to localhost only (its `streamable_http_app` default
    # `host="127.0.0.1"`), which would reject every real request in
    # production: Caddy forwards the original public Host header, not
    # "127.0.0.1". Scope it to the actual public host instead.
    public_host = urlparse(config.public_url).netloc
    app = mcp_server.streamable_http_app(
        transport_security=TransportSecuritySettings(
            enable_dns_rebinding_protection=True,
            allowed_hosts=[public_host],
            allowed_origins=[config.public_url],
        ),
    )

    async def startup() -> None:
        await oauth_store.initialise()
        await oauth_store.purge_expired()
        await receipt_repository.initialise()
        await receipt_repository.purge_expired()

    async def cleanup() -> None:
        with contextlib.suppress(Exception):
            await receipt_repository.aclose()
        with contextlib.suppress(Exception):
            await session_pool.aclose()
        with contextlib.suppress(Exception):
            await http_client.aclose()

    return HostedApp(app, startup, cleanup)


async def _run_hosted(config: AppConfig) -> int:
    import uvicorn

    try:
        hosted_app = build_hosted_app(config)
    except FamilyWallError as exc:
        print(f"configuration error: {exc.info.message}", file=sys.stderr)
        return 2

    await hosted_app.startup()
    try:
        # Bind 0.0.0.0 inside the container/process regardless of the public
        # HTTPS domain: the externally visible name is a separate concern
        # (Caddy's reverse_proxy target), matching the halaxy-mcp precedent.
        uvicorn_config = uvicorn.Config(
            hosted_app.app,
            host="0.0.0.0",
            port=config.port,
            log_level="info",  # noqa: S104
        )
        server = uvicorn.Server(uvicorn_config)
        await server.serve()
        return 0
    except Exception as exc:
        print(f"fatal error: {exc}", file=sys.stderr)
        return 1
    finally:
        await hosted_app.cleanup()
