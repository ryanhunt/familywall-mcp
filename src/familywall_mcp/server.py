"""MCP server lifecycle and initialization."""

from __future__ import annotations

import sys
from datetime import datetime

import httpx
from mcp.server import MCPServer

from familywall_mcp.config import AppConfig
from familywall_mcp.errors import FamilyWallError
from familywall_mcp.familywall.client import FamilyWallSession
from familywall_mcp.familywall.discovery import build_discovery_fields, parse_discovery
from familywall_mcp.models import Principal
from familywall_mcp.services.calendar import CalendarService
from familywall_mcp.services.session import SessionPool
from familywall_mcp.services.transport import read_transport
from familywall_mcp.storage.memory import InMemoryReceiptRepository
from familywall_mcp.tools.registry import ToolRegistry


class SimpleClock:
    """Simple clock for SessionPool."""

    def now(self) -> datetime:
        """Return the current datetime."""
        return datetime.now()


async def run_server() -> int:
    """Build and run the MCP server.

    Returns:
        Exit code (0 on normal shutdown, 1 on error).
    """
    config = None
    http_client = None
    session_pool = None

    try:
        # Load configuration once
        config = AppConfig.from_env()
        email, password = config.require_familywall_credentials()
        principal = config.local_principal()

        # Create httpx client
        http_client = httpx.AsyncClient()

        # Create client factory for SessionPool
        class ClientFactory:
            """Factory to create authenticated FamilyWall sessions."""

            def __init__(
                self, base_url: str, http_client: httpx.AsyncClient, email: str, password: str
            ) -> None:
                self.base_url = base_url
                self.http_client = http_client
                self.email = email
                self.password = password

            async def create_client(self, principal: Principal) -> FamilyWallSession:
                """Create and authenticate a FamilyWallSession."""
                session = FamilyWallSession(
                    base_url=self.base_url,
                    http_client=self.http_client,
                )
                await session.login(self.email, self.password)
                return session

        # Create SessionPool
        factory = ClientFactory(config.familywall_base_url, http_client, email, password)
        session_pool = SessionPool(
            client_factory=factory,
            clock=SimpleClock(),
        )

        # Run discovery to get FamilyContext and DiscoveredFamily
        discovery_fields = build_discovery_fields()
        discovery_payload = await session_pool.call(
            principal,
            "accgetallfamily",
            discovery_fields,
            read_write="read",
        )
        discovered = parse_discovery(discovery_payload)
        family_context = discovered.to_context()

        # Find authenticated member's timezone for fallback
        authenticated_member = next(
            (m for m in discovered.members if m.is_authenticated_member),
            None,
        )
        auth_member_timezone = (
            authenticated_member.timezone if authenticated_member else None
        ) or "UTC"

        # Create services
        read_xport = read_transport(session_pool, principal)
        calendar_service = CalendarService(read_xport)

        # Create receipt repository for the entire server lifetime
        receipt_repository = InMemoryReceiptRepository()

        # Create tool registry with real discovered context
        registry = ToolRegistry(
            config=config,
            session_pool=session_pool,
            principal=principal,
            family_context=family_context,
            discovered_family=discovered,
            authenticated_member_timezone=auth_member_timezone,
            calendar_service=calendar_service,
            receipt_repository=receipt_repository,
        )

        # Create MCP server and register tools
        server = MCPServer(name="familywall", version="0.1.0")
        registry.register_tools(server)

        # Run the stdio server
        await server.run_stdio_async()
        return 0

    except FamilyWallError as exc:
        print(f"configuration error: {exc.info.message}", file=sys.stderr)
        return 2
    except Exception as exc:
        print(f"fatal error: {exc}", file=sys.stderr)
        return 1
    finally:
        # Clean up resources
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
