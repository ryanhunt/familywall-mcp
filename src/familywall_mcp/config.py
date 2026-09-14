"""Explicit local/hosted configuration with fail-closed hosted startup."""

from __future__ import annotations

import os
import re
from collections.abc import Mapping
from enum import StrEnum
from urllib.parse import urlparse

from pydantic import BaseModel, ConfigDict, Field, SecretStr, field_validator, model_validator

from .errors import ConfigurationError, ErrorInfo
from .models import Principal

# Hard cap on numbered FAMILYWALL_USER_<N>_* env vars scanned by from_env.
# This deployment is for a small, operator-configured household, not a
# general multi-tenant service; a generous-but-bounded cap keeps a typo'd
# gap-free sequence from scanning indefinitely.
MAX_HOSTED_USERS = 20

_USERNAME_RE = re.compile(r"^[A-Za-z0-9._-]{1,100}$")


class RuntimeMode(StrEnum):
    STDIO = "stdio"
    HOSTED = "hosted"


class HostedUser(BaseModel):
    """One operator-configured hosted-mode account: an MCP login mapped to
    that person's own FamilyWall credentials.

    Both credential pairs are supplied directly via environment variables
    (FAMILYWALL_USER_<N>_*) by the operator for a small, trusted group of
    invited users. There is no self-service signup, invitation flow, or
    encrypted-at-rest credential database; see
    docs/decisions/0002-simplified-hosted-auth.md for the rationale.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    mcp_username: str = Field(min_length=1, max_length=100)
    mcp_password: SecretStr = Field(repr=False)
    familywall_email: str = Field(min_length=1, max_length=320)
    familywall_password: SecretStr = Field(repr=False)

    @field_validator("mcp_username")
    @classmethod
    def mcp_username_is_safe(cls, value: str) -> str:
        if not _USERNAME_RE.match(value):
            raise ValueError(
                "mcp_username must be 1-100 characters of letters, digits, '.', '_' or '-'"
            )
        return value


class AppConfig(BaseModel):
    """Application settings. Construct with ``from_env`` to load environment safely."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    mode: RuntimeMode = RuntimeMode.STDIO
    public_url: str | None = None
    port: int = 8000
    auth_secret_key: SecretStr | None = Field(default=None, repr=False)
    local_subject: str | None = None
    database_path: str = "data/familywall.sqlite3"
    familywall_base_url: str = "https://familywall.example.invalid"
    familywall_email: str | None = None
    familywall_password: SecretStr | None = Field(default=None, repr=False)
    enable_writes: bool = False
    hosted_users: tuple[HostedUser, ...] = ()
    allowed_redirect_uri_hosts: frozenset[str] = frozenset()

    @field_validator("public_url")
    @classmethod
    def public_url_is_absolute_https(cls, value: str | None) -> str | None:
        if value is None:
            return value
        parsed = urlparse(value)
        if parsed.scheme != "https" or not parsed.netloc:
            raise ValueError("public_url must be an absolute HTTPS URL")
        return value.rstrip("/")

    @field_validator("enable_writes", mode="before")
    @classmethod
    def parse_enable_writes(cls, value: object) -> bool:
        """Parse enable_writes from string environment variable.

        Only the exact strings 'true' and 'false' (case-insensitive) are valid.
        Anything else — including '1', 'yes', 'TRUE ' (with whitespace), empty
        string, or 'maybe' — raises ValueError.

        Args:
            value: The value from the environment or test (already a bool if set
                programmatically, or a string if from environment).

        Returns:
            The parsed boolean value.

        Raises:
            ValueError: If the string is not exactly 'true' or 'false'.
        """
        if isinstance(value, bool):
            return value
        if not isinstance(value, str):
            raise ValueError(f"enable_writes must be a string or bool, not {type(value).__name__}")
        lower = value.lower()
        if lower == "true":
            return True
        if lower == "false":
            return False
        raise ValueError(
            f"enable_writes must be exactly 'true' or 'false' (case-insensitive); got {value!r}"
        )

    @model_validator(mode="after")
    def validate_mode(self) -> AppConfig:
        if self.mode is RuntimeMode.HOSTED:
            missing = [
                name
                for name, value in (
                    ("PUBLIC_URL", self.public_url),
                    ("AUTH_SECRET_KEY", self.auth_secret_key),
                )
                if value is None or (isinstance(value, SecretStr) and not value.get_secret_value())
            ]
            if missing:
                raise ConfigurationError(
                    ErrorInfo(
                        "hosted_configuration_missing",
                        "Hosted mode is missing required configuration.",
                        "Set the required hosted settings and try again.",
                    )
                )
            assert self.auth_secret_key is not None
            if len(self.auth_secret_key.get_secret_value()) < 32:
                raise ConfigurationError(
                    ErrorInfo(
                        "hosted_key_too_short",
                        "Hosted authentication key does not meet the minimum length.",
                        "Use a randomly generated key of at least 32 characters.",
                    )
                )
            if not self.hosted_users:
                raise ConfigurationError(
                    ErrorInfo(
                        "hosted_users_missing",
                        "Hosted mode requires at least one configured user.",
                        "Set FAMILYWALL_USER_1_MCP_USERNAME and related variables.",
                    )
                )
            seen: set[str] = set()
            for user in self.hosted_users:
                normalized = user.mcp_username.lower()
                if normalized in seen:
                    raise ConfigurationError(
                        ErrorInfo(
                            "hosted_users_duplicate_username",
                            "Two configured hosted users share the same MCP username.",
                            "Give each FAMILYWALL_USER_<N>_MCP_USERNAME a unique value.",
                        )
                    )
                seen.add(normalized)
        elif self.local_subject is None or not self.local_subject.strip():
            raise ConfigurationError(
                ErrorInfo(
                    "stdio_principal_missing",
                    "Stdio mode requires an explicit local principal.",
                    "Set LOCAL_SUBJECT before starting the local process.",
                )
            )
        return self

    @classmethod
    def from_env(cls, environ: Mapping[str, str] | None = None) -> AppConfig:
        """Load only documented ``FAMILYWALL_`` variables; values are never logged."""
        values = os.environ if environ is None else environ
        raw: dict[str, object] = {
            "mode": values.get("FAMILYWALL_MODE", RuntimeMode.STDIO),
            "public_url": values.get("FAMILYWALL_PUBLIC_URL"),
            "port": values.get("FAMILYWALL_PORT", "8000"),
            "auth_secret_key": values.get("FAMILYWALL_AUTH_SECRET_KEY"),
            "local_subject": values.get("FAMILYWALL_LOCAL_SUBJECT"),
            "database_path": values.get("FAMILYWALL_DATABASE_PATH", "data/familywall.sqlite3"),
            "familywall_base_url": values.get(
                "FAMILYWALL_BASE_URL", "https://familywall.example.invalid"
            ),
            "familywall_email": values.get("FAMILYWALL_EMAIL"),
            "familywall_password": values.get("FAMILYWALL_PASSWORD"),
            "enable_writes": values.get("FAMILYWALL_ENABLE_WRITES", "false"),
            "hosted_users": _parse_hosted_users(values),
            "allowed_redirect_uri_hosts": _parse_allowed_redirect_uri_hosts(values),
        }
        try:
            return cls.model_validate(raw)
        except ConfigurationError:
            raise
        except ValueError as exc:
            raise ConfigurationError() from exc

    def require_familywall_credentials(self) -> tuple[str, str]:
        """Return the FamilyWall email and password, or raise ConfigurationError.

        Returns:
            A tuple of (email, password).

        Raises:
            ConfigurationError: If either email or password is missing.
        """
        if not self.familywall_email:
            raise ConfigurationError(
                ErrorInfo(
                    "familywall_email_missing",
                    "FAMILYWALL_EMAIL is required.",
                    "Set FAMILYWALL_EMAIL and try again.",
                )
            )
        if not self.familywall_password:
            raise ConfigurationError(
                ErrorInfo(
                    "familywall_password_missing",
                    "FAMILYWALL_PASSWORD is required.",
                    "Set FAMILYWALL_PASSWORD and try again.",
                )
            )
        return self.familywall_email, self.familywall_password.get_secret_value()

    def local_principal(self) -> Principal:
        if self.mode is not RuntimeMode.STDIO or self.local_subject is None:
            raise ConfigurationError()
        return Principal(subject=self.local_subject)

    def find_hosted_user(self, mcp_username: str) -> HostedUser | None:
        """Return the configured hosted user matching ``mcp_username`` (case-insensitive)."""
        normalized = mcp_username.lower()
        for user in self.hosted_users:
            if user.mcp_username.lower() == normalized:
                return user
        return None


def _parse_hosted_users(values: Mapping[str, str]) -> tuple[dict[str, object], ...]:
    """Parse ``FAMILYWALL_USER_<N>_*`` variables for N = 1.. up to MAX_HOSTED_USERS.

    Numbering must be contiguous starting at 1; the scan stops at the first
    gap. A partially-configured slot (some but not all four fields set)
    raises ConfigurationError rather than silently skipping it.
    """
    users: list[dict[str, object]] = []
    for index in range(1, MAX_HOSTED_USERS + 1):
        prefix = f"FAMILYWALL_USER_{index}_"
        fields: dict[str, object] = {
            "mcp_username": values.get(f"{prefix}MCP_USERNAME"),
            "mcp_password": values.get(f"{prefix}MCP_PASSWORD"),
            "familywall_email": values.get(f"{prefix}FW_EMAIL"),
            "familywall_password": values.get(f"{prefix}FW_PASSWORD"),
        }
        present = [name for name, value in fields.items() if value]
        if not present:
            break
        if len(present) != len(fields):
            missing = sorted(set(fields) - set(present))
            raise ConfigurationError(
                ErrorInfo(
                    "hosted_user_incomplete",
                    f"{prefix}* is missing required fields: {', '.join(missing)}.",
                    f"Set all of {prefix}MCP_USERNAME, {prefix}MCP_PASSWORD, "
                    f"{prefix}FW_EMAIL, {prefix}FW_PASSWORD.",
                )
            )
        users.append(fields)
    return tuple(users)


def _parse_allowed_redirect_uri_hosts(values: Mapping[str, str]) -> frozenset[str]:
    raw = values.get("FAMILYWALL_ALLOWED_REDIRECT_URI_HOSTS", "")
    return frozenset(host.strip() for host in raw.split(",") if host.strip())
