"""Pure transport envelope parsing and form encoding."""

from __future__ import annotations

import json
from typing import Any

from familywall_mcp.errors import (
    AuthenticationError,
    ErrorInfo,
    InvalidEnvelopeError,
    MalformedPayloadError,
    SessionExpiredError,
    UpstreamRejectedError,
)


def parse_envelope(
    body: str | bytes,
    endpoint: str,
    key: str = "a00",
) -> object:
    """Parse a FamilyWall API response envelope.

    Args:
        body: The response body as string or bytes.
        endpoint: The API endpoint name (for error context).
        key: The envelope key (default "a00").

    Returns:
        The unwrapped result from the envelope's success path.

    Raises:
        InvalidEnvelopeError: For malformed JSON or missing structure.
        SessionExpiredError: For authentication/NOAUTHENT errors.
        UpstreamRejectedError: For request rejection (502, ex).
        AuthenticationError: For login endpoint (log2in) with ex envelope.
    """
    # Convert bytes to string if needed
    body_str = body.decode("utf-8") if isinstance(body, bytes) else body

    # Check for HTML response (login page redirect, etc.)
    if body_str.lstrip().startswith("<"):
        raise SessionExpiredError(
            ErrorInfo(
                code="session_expired",
                message="The FamilyWall session is no longer valid.",
                recovery="The server will sign in again; if this persists, reconnect the account.",
                endpoint=endpoint,
            )
        )

    # Parse JSON
    try:
        data = json.loads(body_str)
    except (json.JSONDecodeError, ValueError) as exc:
        raise InvalidEnvelopeError(
            ErrorInfo(
                code="invalid_upstream_envelope",
                message="The upstream response was not a recognised envelope.",
                recovery="Reconnect the account; if it persists, inspect the endpoint contract.",
                endpoint=endpoint,
            )
        ) from exc

    # Check that top-level is an object and has the key
    if not isinstance(data, dict):
        raise InvalidEnvelopeError(
            ErrorInfo(
                code="invalid_upstream_envelope",
                message="The upstream response was not a recognised envelope.",
                recovery="Reconnect the account; if it persists, inspect the endpoint contract.",
                endpoint=endpoint,
            )
        )

    if key not in data:
        raise InvalidEnvelopeError(
            ErrorInfo(
                code="invalid_upstream_envelope",
                message="The upstream response was not a recognised envelope.",
                recovery="Reconnect the account; if it persists, inspect the endpoint contract.",
                endpoint=endpoint,
            )
        )

    envelope = data[key]
    if not isinstance(envelope, dict):
        raise InvalidEnvelopeError(
            ErrorInfo(
                code="invalid_upstream_envelope",
                message="The upstream response was not a recognised envelope.",
                recovery="Reconnect the account; if it persists, inspect the endpoint contract.",
                endpoint=endpoint,
            )
        )

    # Check for auth failure (un) envelope
    if "un" in envelope:
        un = envelope["un"]
        if isinstance(un, dict) and "un" in un:
            error_info = un["un"]
            if isinstance(error_info, dict):
                fiz_class = error_info.get("FiZClassId")
                message = error_info.get("message", "")
                # Coerce message to string to avoid TypeError if it's JSON null
                if not isinstance(message, str):
                    message = ""

                # Check for NOAUTHENT or 501 - both are session expired
                if fiz_class == "501" or "NOAUTHENT" in message:
                    raise SessionExpiredError(
                        ErrorInfo(
                            code="session_expired",
                            message="The FamilyWall session is no longer valid.",
                            recovery=(
                                "The server will sign in again; if this persists, "
                                "reconnect the account."
                            ),
                            endpoint=endpoint,
                        )
                    )

                # 502 is upstream rejected
                if fiz_class == "502":
                    raise UpstreamRejectedError(
                        ErrorInfo(
                            code="upstream_rejected",
                            message="FamilyWall rejected the request.",
                            recovery=(
                                "Check the request details; if it persists, the "
                                "endpoint contract may have changed."
                            ),
                            endpoint=endpoint,
                        )
                    )

        # Default to upstream rejected if un is present but doesn't match above
        raise UpstreamRejectedError(
            ErrorInfo(
                code="upstream_rejected",
                message="FamilyWall rejected the request.",
                recovery=(
                    "Check the request details; if it persists, the endpoint "
                    "contract may have changed."
                ),
                endpoint=endpoint,
            )
        )

    # Check for exception (ex) envelope
    if "ex" in envelope:
        ex = envelope["ex"]
        if isinstance(ex, dict) and "ex" in ex:
            # For log2in endpoint, ex is authentication error
            if endpoint == "log2in":
                raise AuthenticationError(
                    ErrorInfo(
                        code="authentication_failed",
                        message="The account could not be authenticated.",
                        recovery="Reconnect the FamilyWall account.",
                        endpoint=endpoint,
                    )
                )
            # For other endpoints, ex is upstream rejected
            raise UpstreamRejectedError(
                ErrorInfo(
                    code="upstream_rejected",
                    message="FamilyWall rejected the request.",
                    recovery=(
                        "Check the request details; if it persists, the endpoint "
                        "contract may have changed."
                    ),
                    endpoint=endpoint,
                )
            )

        # Default to upstream rejected
        raise UpstreamRejectedError(
            ErrorInfo(
                code="upstream_rejected",
                message="FamilyWall rejected the request.",
                recovery=(
                    "Check the request details; if it persists, the endpoint "
                    "contract may have changed."
                ),
                endpoint=endpoint,
            )
        )

    # Check for success (r) envelope
    if "r" in envelope:
        r = envelope["r"]
        if not isinstance(r, dict) or "r" not in r:
            raise InvalidEnvelopeError(
                ErrorInfo(
                    code="invalid_upstream_envelope",
                    message="The upstream response was not a recognised envelope.",
                    recovery=(
                        "Reconnect the account; if it persists, inspect the endpoint contract."
                    ),
                    endpoint=endpoint,
                )
            )
        return r["r"]

    # No recognized envelope type
    raise InvalidEnvelopeError(
        ErrorInfo(
            code="invalid_upstream_envelope",
            message="The upstream response was not a recognised envelope.",
            recovery="Reconnect the account; if it persists, inspect the endpoint contract.",
            endpoint=endpoint,
        )
    )


def coerce_bool(value: Any) -> bool:
    """Coerce a value to boolean.

    Accepts string "true"/"false" (case-sensitive) and real booleans.

    Args:
        value: The value to coerce.

    Returns:
        The boolean value.

    Raises:
        MalformedPayloadError: For invalid types or string values.
    """
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        if value == "true":
            return True
        if value == "false":
            return False
        raise MalformedPayloadError(
            ErrorInfo(
                code="malformed_upstream_payload",
                message="FamilyWall returned data this server could not interpret.",
                recovery="Report the endpoint; the upstream contract may have changed.",
            )
        )
    raise MalformedPayloadError(
        ErrorInfo(
            code="malformed_upstream_payload",
            message="FamilyWall returned data this server could not interpret.",
            recovery="Report the endpoint; the upstream contract may have changed.",
        )
    )
