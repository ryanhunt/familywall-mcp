"""Stable domain values; upstream wire formats do not belong here."""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class DomainModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, str_strip_whitespace=True)


class Principal(DomainModel):
    """An already-authenticated local subject, never a model-supplied username."""

    subject: str = Field(min_length=1, max_length=200)
    scopes: frozenset[str] = frozenset()

    @field_validator("subject")
    @classmethod
    def subject_is_not_secret_like(cls, value: str) -> str:
        if any(character.isspace() for character in value):
            raise ValueError("subject must not contain whitespace")
        return value


class FamilyContext(DomainModel):
    """A family/calendar mapping observed and validated for one account."""

    account_id: str = Field(min_length=1, max_length=200)
    family_id: str = Field(min_length=1, max_length=200)
    calendar_id: str = Field(min_length=1, max_length=200)
    verified: Literal[True] = True


class UnknownFamilyContext(DomainModel):
    """Untrusted discovery data that cannot be passed where FamilyContext is required."""

    account_id: str | None = None
    family_id: str | None = None
    calendar_id: str | None = None
    verified: Literal[False] = False


class FamilyWallCredentials(DomainModel):
    """Short-lived credential material; keep it out of logs and persisted models."""

    username: str = Field(min_length=1, max_length=320)
    password: str = Field(min_length=1, max_length=1024, repr=False)


class ResponseEnvelope(DomainModel):
    """Small synthetic envelope used until P0 freezes real wire contracts."""

    ok: bool
    data: object | None = None
    error_code: str | None = None
    error_message: str | None = None

    @model_validator(mode="after")
    def validate_shape(self) -> ResponseEnvelope:
        if self.ok and (self.error_code is not None or self.error_message is not None):
            raise ValueError("successful envelopes cannot contain an error")
        if not self.ok and not self.error_code:
            raise ValueError("failed envelopes require an error code")
        if self.error_message is not None and not self.error_message.strip():
            raise ValueError("error messages cannot be empty")
        return self


class OperationReceipt(DomainModel):
    """A local mutation receipt, with no promise of remote exactly-once delivery."""

    subject: str = Field(min_length=1)
    family_id: str = Field(min_length=1)
    resource_id: str = Field(min_length=1)
    action: Literal[
        "list.add_item",
        "list.set_checked",
        "list.set_assignees",
        "calendar.create_event",
        "calendar.set_attendees",
        "legacy",
    ]
    operation_id: str = Field(min_length=1, max_length=200)
    payload_hash: str = Field(min_length=1, max_length=128)
    status: Literal["pending", "succeeded", "unknown", "rejected"]
    upstream_id: str | None = None
    expires_at: datetime
