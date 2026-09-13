from __future__ import annotations

import pytest

from familywall_mcp.config import AppConfig, RuntimeMode
from familywall_mcp.errors import ConfigurationError


def test_stdio_requires_explicit_principal() -> None:
    with pytest.raises(ConfigurationError):
        AppConfig.from_env({"FAMILYWALL_MODE": "stdio"})


def test_stdio_principal_is_deterministic_and_not_environment_dumped() -> None:
    config = AppConfig.from_env(
        {
            "FAMILYWALL_MODE": "stdio",
            "FAMILYWALL_LOCAL_SUBJECT": "synthetic-user",
        }
    )
    assert config.mode is RuntimeMode.STDIO
    assert config.local_principal().subject == "synthetic-user"
    assert "FAMILYWALL_AUTH_SECRET_KEY" not in repr(config)


def test_hosted_fails_closed_for_required_settings() -> None:
    with pytest.raises(ConfigurationError) as exc_info:
        AppConfig.from_env({"FAMILYWALL_MODE": "hosted"})
    assert exc_info.value.info.code == "hosted_configuration_missing"
    assert "AUTH_SECRET_KEY" not in str(exc_info.value)


def test_hosted_accepts_valid_dummy_settings() -> None:
    config = AppConfig.from_env(
        {
            "FAMILYWALL_MODE": "hosted",
            "FAMILYWALL_PUBLIC_URL": "https://mcp.example.invalid/",
            "FAMILYWALL_AUTH_SECRET_KEY": "x" * 32,
        }
    )
    assert config.mode is RuntimeMode.HOSTED
    assert config.public_url == "https://mcp.example.invalid"
    assert config.auth_secret_key is not None
    assert "x" * 32 not in repr(config)


def test_hosted_rejects_short_auth_key() -> None:
    with pytest.raises(ConfigurationError) as exc_info:
        AppConfig.from_env(
            {
                "FAMILYWALL_MODE": "hosted",
                "FAMILYWALL_PUBLIC_URL": "https://mcp.example.invalid",
                "FAMILYWALL_AUTH_SECRET_KEY": "too-short",
            }
        )
    assert exc_info.value.info.code == "hosted_key_too_short"


@pytest.mark.parametrize("url", ["http://mcp.example.invalid", "mcp.example.invalid"])
def test_hosted_public_url_must_be_https(url: str) -> None:
    with pytest.raises(ConfigurationError):
        AppConfig.from_env(
            {
                "FAMILYWALL_MODE": "hosted",
                "FAMILYWALL_PUBLIC_URL": url,
                "FAMILYWALL_AUTH_SECRET_KEY": "x" * 32,
            }
        )
