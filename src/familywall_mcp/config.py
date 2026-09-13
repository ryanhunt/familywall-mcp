"""Explicit local/hosted configuration with fail-closed hosted startup."""

from __future__ import annotations

import os
from collections.abc import Mapping
from enum import StrEnum
from urllib.parse import urlparse

from pydantic import BaseModel, ConfigDict, Field, SecretStr, field_validator, model_validator

from .errors import ConfigurationError, ErrorInfo
from .models import Principal


class RuntimeMode(StrEnum):
    STDIO = "stdio"
    HOSTED = "hosted"


class AppConfig(BaseModel):
    """Application settings. Construct with ``from_env`` to load environment safely."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    mode: RuntimeMode = RuntimeMode.STDIO
    public_url: str | None = None
    auth_secret_key: SecretStr | None = Field(default=None, repr=False)
    local_subject: str | None = None
    database_path: str = "data/familywall.sqlite3"
    familywall_base_url: str = "https://familywall.example.invalid"

    @field_validator("public_url")
    @classmethod
    def public_url_is_absolute_https(cls, value: str | None) -> str | None:
        if value is None:
            return value
        parsed = urlparse(value)
        if parsed.scheme != "https" or not parsed.netloc:
            raise ValueError("public_url must be an absolute HTTPS URL")
        return value.rstrip("/")

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
            "auth_secret_key": values.get("FAMILYWALL_AUTH_SECRET_KEY"),
            "local_subject": values.get("FAMILYWALL_LOCAL_SUBJECT"),
            "database_path": values.get("FAMILYWALL_DATABASE_PATH", "data/familywall.sqlite3"),
            "familywall_base_url": values.get(
                "FAMILYWALL_BASE_URL", "https://familywall.example.invalid"
            ),
        }
        try:
            return cls.model_validate(raw)
        except ConfigurationError:
            raise
        except ValueError as exc:
            raise ConfigurationError() from exc

    def local_principal(self) -> Principal:
        if self.mode is not RuntimeMode.STDIO or self.local_subject is None:
            raise ConfigurationError()
        return Principal(subject=self.local_subject)
