"""Safe errors shared by the future transport, services, and MCP adapters.

Error messages are intentionally static and never include upstream bodies,
credentials, cookies, or environment values.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class ErrorInfo:
    code: str
    message: str
    recovery: str
    endpoint: str | None = None


class FamilyWallError(Exception):
    """Base exception with a safe, serialisable error description."""

    default = ErrorInfo("internal_error", "The request could not be completed.", "Try again later.")

    def __init__(self, info: ErrorInfo | None = None) -> None:
        self.info = info or self.default
        super().__init__(self.info.message)

    def as_dict(self) -> dict[str, str]:
        """Return only fields suitable for an MCP error response or log."""
        result = {
            "code": self.info.code,
            "message": self.info.message,
            "recovery": self.info.recovery,
        }
        if self.info.endpoint:
            result["endpoint"] = self.info.endpoint
        return result


class ConfigurationError(FamilyWallError):
    default = ErrorInfo(
        "invalid_configuration",
        "The server configuration is invalid.",
        "Set the required configuration and try again.",
    )


class InvalidEnvelopeError(FamilyWallError):
    default = ErrorInfo(
        "invalid_upstream_envelope",
        "The upstream response was not a recognised envelope.",
        "Reconnect the account; if it persists, inspect the endpoint contract.",
    )


class AuthenticationError(FamilyWallError):
    default = ErrorInfo(
        "authentication_failed",
        "The account could not be authenticated.",
        "Reconnect the FamilyWall account.",
    )


class TransportError(FamilyWallError):
    default = ErrorInfo(
        "upstream_unavailable",
        "The upstream service could not be reached.",
        "Try again later.",
    )


class UnknownFamilyContextError(FamilyWallError):
    default = ErrorInfo(
        "unknown_family_context",
        "The family context has not been verified.",
        "Select a verified family before continuing.",
    )


class UnexpectedRequestError(FamilyWallError):
    default = ErrorInfo(
        "unexpected_request",
        "The requested upstream operation is not allowed by this test harness.",
        "Update the synthetic contract before making this request.",
    )
