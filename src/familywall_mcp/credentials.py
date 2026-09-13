"""Credential provider from environment variables."""

from __future__ import annotations

from pydantic import SecretStr

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
