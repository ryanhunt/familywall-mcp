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


class SessionExpiredError(AuthenticationError):
    """The upstream session is no longer accepted.

    Observed live as HTTP 200 with ``a00.un.un`` / ``501`` / ``NOAUTHENT``.
    """

    default = ErrorInfo(
        "session_expired",
        "The FamilyWall session is no longer valid.",
        "The server will sign in again; if this persists, reconnect the account.",
    )


class UpstreamRejectedError(FamilyWallError):
    """The upstream accepted the request and refused it (``un``/``ex`` envelope)."""

    default = ErrorInfo(
        "upstream_rejected",
        "FamilyWall rejected the request.",
        "Check the request details; if it persists, the endpoint contract may have changed.",
    )


class MalformedPayloadError(FamilyWallError):
    """A parsed envelope did not contain the shape this endpoint's contract requires."""

    default = ErrorInfo(
        "malformed_upstream_payload",
        "FamilyWall returned data this server could not interpret.",
        "Report the endpoint; the upstream contract may have changed.",
    )


class UnsupportedConfigurationError(FamilyWallError):
    """A real account state this project deliberately refuses to guess about."""

    default = ErrorInfo(
        "unsupported_configuration",
        "This account is in a configuration this server does not support.",
        "See the documented limitations; no action was taken.",
    )


class RateLimitedError(FamilyWallError):
    default = ErrorInfo(
        "upstream_rate_limited",
        "FamilyWall is rate limiting this account.",
        "Wait before retrying; no automatic retry was attempted.",
    )
