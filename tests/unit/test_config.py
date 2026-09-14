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
            "FAMILYWALL_USER_1_MCP_USERNAME": "alice",
            "FAMILYWALL_USER_1_MCP_PASSWORD": "mcp-pass",
            "FAMILYWALL_USER_1_FW_EMAIL": "alice@example.com",
            "FAMILYWALL_USER_1_FW_PASSWORD": "fw-pass",
        }
    )
    assert config.mode is RuntimeMode.HOSTED
    assert config.public_url == "https://mcp.example.invalid"
    assert config.auth_secret_key is not None
    assert "x" * 32 not in repr(config)
    assert [u.mcp_username for u in config.hosted_users] == ["alice"]


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


def _hosted_base_env(**overrides: str) -> dict[str, str]:
    env = {
        "FAMILYWALL_MODE": "hosted",
        "FAMILYWALL_PUBLIC_URL": "https://mcp.example.invalid",
        "FAMILYWALL_AUTH_SECRET_KEY": "x" * 32,
    }
    env.update(overrides)
    return env


def test_hosted_parses_two_users() -> None:
    config = AppConfig.from_env(
        _hosted_base_env(
            FAMILYWALL_USER_1_MCP_USERNAME="alice",
            FAMILYWALL_USER_1_MCP_PASSWORD="alice-mcp-pass",
            FAMILYWALL_USER_1_FW_EMAIL="alice@example.com",
            FAMILYWALL_USER_1_FW_PASSWORD="alice-fw-pass",
            FAMILYWALL_USER_2_MCP_USERNAME="bob",
            FAMILYWALL_USER_2_MCP_PASSWORD="bob-mcp-pass",
            FAMILYWALL_USER_2_FW_EMAIL="bob@example.com",
            FAMILYWALL_USER_2_FW_PASSWORD="bob-fw-pass",
        )
    )
    assert [u.mcp_username for u in config.hosted_users] == ["alice", "bob"]
    alice, bob = config.hosted_users
    assert alice.familywall_email == "alice@example.com"
    assert alice.mcp_password.get_secret_value() == "alice-mcp-pass"
    assert bob.familywall_email == "bob@example.com"
    assert bob.mcp_password.get_secret_value() == "bob-mcp-pass"


def test_hosted_partially_configured_user_slot_raises() -> None:
    with pytest.raises(ConfigurationError) as exc_info:
        AppConfig.from_env(
            _hosted_base_env(
                FAMILYWALL_USER_1_MCP_USERNAME="alice",
                FAMILYWALL_USER_1_MCP_PASSWORD="alice-mcp-pass",
                FAMILYWALL_USER_1_FW_EMAIL="alice@example.com",
                # FAMILYWALL_USER_1_FW_PASSWORD deliberately missing
            )
        )
    assert exc_info.value.info.code == "hosted_user_incomplete"


def test_hosted_duplicate_username_case_insensitive_raises() -> None:
    with pytest.raises(ConfigurationError) as exc_info:
        AppConfig.from_env(
            _hosted_base_env(
                FAMILYWALL_USER_1_MCP_USERNAME="alice",
                FAMILYWALL_USER_1_MCP_PASSWORD="alice-mcp-pass",
                FAMILYWALL_USER_1_FW_EMAIL="alice@example.com",
                FAMILYWALL_USER_1_FW_PASSWORD="alice-fw-pass",
                FAMILYWALL_USER_2_MCP_USERNAME="Alice",
                FAMILYWALL_USER_2_MCP_PASSWORD="other-mcp-pass",
                FAMILYWALL_USER_2_FW_EMAIL="alice2@example.com",
                FAMILYWALL_USER_2_FW_PASSWORD="other-fw-pass",
            )
        )
    assert exc_info.value.info.code == "hosted_users_duplicate_username"


def test_hosted_zero_users_raises() -> None:
    with pytest.raises(ConfigurationError) as exc_info:
        AppConfig.from_env(_hosted_base_env())
    assert exc_info.value.info.code == "hosted_users_missing"


def test_allowed_redirect_uri_hosts_parses_comma_separated_and_trims() -> None:
    config = AppConfig.from_env(
        _hosted_base_env(
            FAMILYWALL_USER_1_MCP_USERNAME="alice",
            FAMILYWALL_USER_1_MCP_PASSWORD="alice-mcp-pass",
            FAMILYWALL_USER_1_FW_EMAIL="alice@example.com",
            FAMILYWALL_USER_1_FW_PASSWORD="alice-fw-pass",
            FAMILYWALL_ALLOWED_REDIRECT_URI_HOSTS=" host1.example ,host2.example,, ",
        )
    )
    assert config.allowed_redirect_uri_hosts == frozenset({"host1.example", "host2.example"})


def test_allowed_redirect_uri_hosts_empty_means_no_restriction() -> None:
    config = AppConfig.from_env(
        _hosted_base_env(
            FAMILYWALL_USER_1_MCP_USERNAME="alice",
            FAMILYWALL_USER_1_MCP_PASSWORD="alice-mcp-pass",
            FAMILYWALL_USER_1_FW_EMAIL="alice@example.com",
            FAMILYWALL_USER_1_FW_PASSWORD="alice-fw-pass",
        )
    )
    assert config.allowed_redirect_uri_hosts == frozenset()


def test_find_hosted_user_is_case_insensitive_and_none_for_unknown() -> None:
    config = AppConfig.from_env(
        _hosted_base_env(
            FAMILYWALL_USER_1_MCP_USERNAME="alice",
            FAMILYWALL_USER_1_MCP_PASSWORD="alice-mcp-pass",
            FAMILYWALL_USER_1_FW_EMAIL="alice@example.com",
            FAMILYWALL_USER_1_FW_PASSWORD="alice-fw-pass",
        )
    )
    found = config.find_hosted_user("ALICE")
    assert found is not None
    assert found.mcp_username == "alice"
    assert config.find_hosted_user("nobody") is None
