"""Tests for HostedCredentialProvider: per-subject FamilyWall credential lookup."""

from __future__ import annotations

import pytest

from familywall_mcp.config import AppConfig
from familywall_mcp.credentials import HostedCredentialProvider
from familywall_mcp.errors import AuthenticationError
from familywall_mcp.models import Principal


def _hosted_config() -> AppConfig:
    return AppConfig.from_env(
        {
            "FAMILYWALL_MODE": "hosted",
            "FAMILYWALL_PUBLIC_URL": "https://mcp.example.invalid",
            "FAMILYWALL_AUTH_SECRET_KEY": "x" * 32,
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


class TestHostedCredentialProvider:
    def test_repr_does_not_expose_passwords(self) -> None:
        provider = HostedCredentialProvider(_hosted_config())
        repr_str = repr(provider)
        assert "alice-fw-pass" not in repr_str
        assert "bob-fw-pass" not in repr_str

    @pytest.mark.asyncio
    async def test_returns_first_users_own_credentials(self) -> None:
        provider = HostedCredentialProvider(_hosted_config())
        creds = await provider.get_credentials(Principal(subject="alice"))
        assert creds.username == "alice@example.com"
        assert creds.password == "alice-fw-pass"

    @pytest.mark.asyncio
    async def test_returns_second_users_own_credentials(self) -> None:
        provider = HostedCredentialProvider(_hosted_config())
        creds = await provider.get_credentials(Principal(subject="bob"))
        assert creds.username == "bob@example.com"
        assert creds.password == "bob-fw-pass"

    @pytest.mark.asyncio
    async def test_users_are_isolated_from_each_other(self) -> None:
        provider = HostedCredentialProvider(_hosted_config())
        alice_creds = await provider.get_credentials(Principal(subject="alice"))
        bob_creds = await provider.get_credentials(Principal(subject="bob"))
        assert alice_creds.username != bob_creds.username
        assert alice_creds.password != bob_creds.password

    @pytest.mark.asyncio
    async def test_unrecognized_subject_raises_authentication_error(self) -> None:
        provider = HostedCredentialProvider(_hosted_config())
        with pytest.raises(AuthenticationError) as exc_info:
            await provider.get_credentials(Principal(subject="mallory"))
        assert exc_info.value.info.code == "hosted_user_not_configured"
