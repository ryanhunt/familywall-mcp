"""Credential providers: single-user (stdio) and multi-user (hosted)."""

from __future__ import annotations

from pydantic import SecretStr

from familywall_mcp.config import AppConfig
from familywall_mcp.errors import AuthenticationError, ErrorInfo
from familywall_mcp.models import FamilyWallCredentials, Principal


class EnvCredentialProvider:
    """Credential provider initialized with username and password from config.

    The credentials are passed in at construction, not read from os.environ
    directly, keeping the process boundary in config.py.

    The provider does not expose the password in __repr__.
    """

    def __init__(self, username: str, password: SecretStr) -> None:
        """Initialize with credentials.

        Args:
            username: The FamilyWall email/username.
            password: A SecretStr containing the FamilyWall password.
        """
        self._username = username
        self._password = password

    def __repr__(self) -> str:
        """Return a safe representation without exposing the password."""
        return f"EnvCredentialProvider(username={self._username!r})"

    async def get_credentials(self, principal: Principal) -> FamilyWallCredentials:
        """Return the configured credentials for any principal.

        In a future implementation, this would be per-principal (e.g., per OAuth
        subject). For now, all requests use the same credential pair.

        Args:
            principal: The requesting principal (currently unused).

        Returns:
            FamilyWallCredentials with the configured username and password.
        """
        return FamilyWallCredentials(
            username=self._username,
            password=self._password.get_secret_value(),
        )


class HostedCredentialProvider:
    """Credential provider for hosted mode: looks up FamilyWall credentials by
    the authenticated MCP username (``principal.subject``).

    Each hosted user's own FamilyWall email/password is configured statically
    by the operator via ``FAMILYWALL_USER_<N>_FW_EMAIL``/``FW_PASSWORD`` (see
    docs/decisions/0002-simplified-hosted-auth.md). ``principal.subject`` is
    only ever set by this service's own OAuth login flow to a value it just
    validated against a configured user, so an unrecognised subject here
    indicates an internal inconsistency rather than a real login attempt.
    """

    def __init__(self, config: AppConfig) -> None:
        self._config = config

    def __repr__(self) -> str:
        return f"HostedCredentialProvider(users={len(self._config.hosted_users)})"

    async def get_credentials(self, principal: Principal) -> FamilyWallCredentials:
        user = self._config.find_hosted_user(principal.subject)
        if user is None:
            raise AuthenticationError(
                ErrorInfo(
                    "hosted_user_not_configured",
                    "The authenticated MCP user has no configured FamilyWall credentials.",
                    "Check FAMILYWALL_USER_<N>_* configuration for this username.",
                )
            )
        return FamilyWallCredentials(
            username=user.familywall_email,
            password=user.familywall_password.get_secret_value(),
        )
