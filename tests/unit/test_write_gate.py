"""Tests for write gate configuration and enforcement."""

from __future__ import annotations

import pytest
from pydantic import SecretStr

from familywall_mcp.config import AppConfig
from familywall_mcp.credentials import EnvCredentialProvider
from familywall_mcp.errors import ConfigurationError


class TestEnableWritesParsing:
    """Test strict parsing of enable_writes configuration."""

    def _base_env(self, **overrides: str) -> dict[str, str]:
        """Build a test environment with required config."""
        env = {
            "FAMILYWALL_LOCAL_SUBJECT": "test-local-user",
            "FAMILYWALL_MODE": "stdio",
        }
        env.update(overrides)
        return env

    def test_default_is_false(self) -> None:
        """enable_writes defaults to False."""
        config = AppConfig.from_env(self._base_env())
        assert config.enable_writes is False

    def test_accepts_true_lowercase(self) -> None:
        """Accepts 'true' in lowercase."""
        config = AppConfig.from_env(self._base_env(FAMILYWALL_ENABLE_WRITES="true"))
        assert config.enable_writes is True

    def test_accepts_true_uppercase(self) -> None:
        """Accepts 'TRUE' in uppercase."""
        config = AppConfig.from_env(self._base_env(FAMILYWALL_ENABLE_WRITES="TRUE"))
        assert config.enable_writes is True

    def test_accepts_true_mixed_case(self) -> None:
        """Accepts 'True' in mixed case."""
        config = AppConfig.from_env(self._base_env(FAMILYWALL_ENABLE_WRITES="True"))
        assert config.enable_writes is True

    def test_accepts_false_lowercase(self) -> None:
        """Accepts 'false' in lowercase."""
        config = AppConfig.from_env(self._base_env(FAMILYWALL_ENABLE_WRITES="false"))
        assert config.enable_writes is False

    def test_accepts_false_uppercase(self) -> None:
        """Accepts 'FALSE' in uppercase."""
        config = AppConfig.from_env(self._base_env(FAMILYWALL_ENABLE_WRITES="FALSE"))
        assert config.enable_writes is False

    def test_rejects_numeric_1(self) -> None:
        """Rejects '1' (common boolean representation)."""
        with pytest.raises(ConfigurationError):
            AppConfig.from_env(self._base_env(FAMILYWALL_ENABLE_WRITES="1"))

    def test_rejects_numeric_0(self) -> None:
        """Rejects '0'."""
        with pytest.raises(ConfigurationError):
            AppConfig.from_env(self._base_env(FAMILYWALL_ENABLE_WRITES="0"))

    def test_rejects_yes(self) -> None:
        """Rejects 'yes'."""
        with pytest.raises(ConfigurationError):
            AppConfig.from_env(self._base_env(FAMILYWALL_ENABLE_WRITES="yes"))

    def test_rejects_no(self) -> None:
        """Rejects 'no'."""
        with pytest.raises(ConfigurationError):
            AppConfig.from_env(self._base_env(FAMILYWALL_ENABLE_WRITES="no"))

    def test_rejects_true_with_whitespace(self) -> None:
        """Rejects 'true ' with trailing whitespace."""
        with pytest.raises(ConfigurationError):
            AppConfig.from_env(self._base_env(FAMILYWALL_ENABLE_WRITES="true "))

    def test_rejects_empty_string(self) -> None:
        """Rejects empty string."""
        with pytest.raises(ConfigurationError):
            AppConfig.from_env(self._base_env(FAMILYWALL_ENABLE_WRITES=""))

    def test_rejects_maybe(self) -> None:
        """Rejects 'maybe'."""
        with pytest.raises(ConfigurationError):
            AppConfig.from_env(self._base_env(FAMILYWALL_ENABLE_WRITES="maybe"))


class TestEnvCredentialProvider:
    """Test the EnvCredentialProvider."""

    def test_repr_does_not_expose_password(self) -> None:
        """__repr__ does not expose the password."""
        provider = EnvCredentialProvider(
            username="test@example.com",
            password=SecretStr("my-secret-password"),
        )
        repr_str = repr(provider)
        assert "my-secret-password" not in repr_str
        assert "test@example.com" in repr_str

    @pytest.mark.asyncio
    async def test_get_credentials_returns_pair(self) -> None:
        """get_credentials returns the configured username and password."""
        from familywall_mcp.models import Principal

        provider = EnvCredentialProvider(
            username="test@example.com",
            password=SecretStr("my-secret-password"),
        )
        principal = Principal(subject="test-subject")
        creds = await provider.get_credentials(principal)
        assert creds.username == "test@example.com"
        assert creds.password == "my-secret-password"
