"""FamilyWall API session and client."""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence

import httpx

from familywall_mcp.errors import (
    AuthenticationError,
    ErrorInfo,
    InvalidEnvelopeError,
    RateLimitedError,
    TransportError,
)
from familywall_mcp.familywall.wire import parse_envelope


class FamilyWallSession:
    """Async client for FamilyWall API with session management.

    Holds a reference to an httpx.AsyncClient for making authenticated requests.
    Session credentials (JSESSIONID and tokenCsrf) are stored on the instance.
    """

    def __init__(
        self,
        base_url: str,
        http_client: httpx.AsyncClient,
        *,
        connect_timeout: float = 15.0,
        read_timeout: float = 15.0,
    ) -> None:
        """Initialize a FamilyWall session.

        Args:
            base_url: The base URL for FamilyWall API (e.g., https://api.familywall.com).
            http_client: An injected httpx.AsyncClient instance.
            connect_timeout: Connection timeout in seconds (default 15s).
            read_timeout: Read timeout in seconds (default 15s).
        """
        self._base_url = base_url.rstrip("/")
        self._http_client = http_client
        self._connect_timeout = connect_timeout
        self._read_timeout = read_timeout
        self._jsessionid: str | None = None
        self._token_csrf: str | None = None

    def __repr__(self) -> str:
        """Return a safe representation without exposing session credentials."""
        return (
            f"{self.__class__.__name__}("
            f"base_url={self._base_url!r}, "
            f"authenticated={self._jsessionid is not None})"
        )

    async def login(self, username: str, password: str) -> None:
        """Authenticate with FamilyWall and store session credentials.

        Args:
            username: The email address or username.
            password: The account password.

        Raises:
            AuthenticationError: If login fails (bad password or no JSESSIONID in response).
            TransportError: If the request fails at the transport level.
            InvalidEnvelopeError: If the response envelope is malformed.
        """
        fields = {
            "partnerScope": "Family",
            "transactional": "true",
            "a00identifier": username,
            "a00password": password,
            "a00generateAutologinToken": "true",
            "a01call": "log2get",
        }

        url = f"{self._base_url}/api/log2in"
        timeout = httpx.Timeout(
            timeout=self._read_timeout,
            connect=self._connect_timeout,
        )

        try:
            response = await self._http_client.post(
                url,
                content=self._encode_form(fields),
                headers={"Content-Type": "application/x-www-form-urlencoded; charset=UTF-8"},
                timeout=timeout,
            )
        except httpx.TimeoutException as exc:
            raise TransportError(
                ErrorInfo(
                    code="upstream_unavailable",
                    message="The upstream service could not be reached.",
                    recovery="Try again later.",
                    endpoint="log2in",
                )
            ) from exc
        except httpx.TransportError as exc:
            raise TransportError(
                ErrorInfo(
                    code="upstream_unavailable",
                    message="The upstream service could not be reached.",
                    recovery="Try again later.",
                    endpoint="log2in",
                )
            ) from exc

        # Handle non-200 status codes
        if response.status_code != 200:
            raise TransportError(
                ErrorInfo(
                    code="upstream_unavailable",
                    message="The upstream service could not be reached.",
                    recovery="Try again later.",
                    endpoint="log2in",
                )
            )

        # Parse the envelope; will raise AuthenticationError for bad password
        result = parse_envelope(response.text, endpoint="log2in")

        # Verify result is a dict and contains tokenCsrf
        if not isinstance(result, dict):
            raise AuthenticationError(
                ErrorInfo(
                    code="authentication_failed",
                    message="The account could not be authenticated.",
                    recovery="Reconnect the FamilyWall account.",
                    endpoint="log2in",
                )
            )

        token_csrf = result.get("tokenCsrf")
        if not isinstance(token_csrf, str) or not token_csrf:
            raise AuthenticationError(
                ErrorInfo(
                    code="authentication_failed",
                    message="The account could not be authenticated.",
                    recovery="Reconnect the FamilyWall account.",
                    endpoint="log2in",
                )
            )

        # Extract JSESSIONID from cookies
        jsessionid = response.cookies.get("JSESSIONID")
        if not jsessionid:
            raise AuthenticationError(
                ErrorInfo(
                    code="authentication_failed",
                    message="The account could not be authenticated.",
                    recovery="Reconnect the FamilyWall account.",
                    endpoint="log2in",
                )
            )

        # Store credentials
        self._jsessionid = jsessionid
        self._token_csrf = token_csrf

    async def call(
        self,
        endpoint: str,
        fields: Mapping[str, str],
        *,
        extra_keys: Sequence[str] = (),
    ) -> object:
        """Make an authenticated API call.

        Args:
            endpoint: The API endpoint name (e.g., "log2get", "calget").
            fields: Form fields to send in the request.
            extra_keys: Additional envelope keys to extract from response (default empty).

        Returns:
            The unwrapped result from the success envelope.

        Raises:
            AuthenticationError: If not logged in.
            TransportError: If the request fails at the transport level.
            RateLimitedError: If rate limited (HTTP 429).
            InvalidEnvelopeError: If the response envelope is malformed.
            UpstreamRejectedError: If the upstream rejects the request.
            SessionExpiredError: If the session is no longer valid.
        """
        if not self._jsessionid or not self._token_csrf:
            raise AuthenticationError(
                ErrorInfo(
                    code="authentication_failed",
                    message="The account could not be authenticated.",
                    recovery="Reconnect the FamilyWall account.",
                    endpoint=endpoint,
                )
            )

        # Add required fields
        form_fields = dict(fields)
        form_fields["partnerScope"] = "Family"

        url = f"{self._base_url}/api/{endpoint}"
        timeout = httpx.Timeout(
            timeout=self._read_timeout,
            connect=self._connect_timeout,
        )

        # Request carries JSESSIONID; the live probe confirmed JSESSIONID alone
        # is sufficient (AWSALB/AWSALBCORS from login are harmless if present in
        # the client's cookie jar).
        cookies = {"JSESSIONID": self._jsessionid}

        try:
            response = await self._http_client.post(
                url,
                content=self._encode_form(form_fields),
                headers={
                    "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
                    "tokencsrf": self._token_csrf,
                },
                cookies=cookies,
                timeout=timeout,
            )
        except httpx.TimeoutException as exc:
            raise TransportError(
                ErrorInfo(
                    code="upstream_unavailable",
                    message="The upstream service could not be reached.",
                    recovery="Try again later.",
                    endpoint=endpoint,
                )
            ) from exc
        except httpx.TransportError as exc:
            raise TransportError(
                ErrorInfo(
                    code="upstream_unavailable",
                    message="The upstream service could not be reached.",
                    recovery="Try again later.",
                    endpoint=endpoint,
                )
            ) from exc

        # Handle rate limiting
        if response.status_code == 429:
            raise RateLimitedError(
                ErrorInfo(
                    code="upstream_rate_limited",
                    message="FamilyWall is rate limiting this account.",
                    recovery="Wait before retrying; no automatic retry was attempted.",
                    endpoint=endpoint,
                )
            )

        # Handle non-200 status codes
        if response.status_code != 200:
            raise TransportError(
                ErrorInfo(
                    code="upstream_unavailable",
                    message="The upstream service could not be reached.",
                    recovery="Try again later.",
                    endpoint=endpoint,
                )
            )

        # Parse the main envelope
        result = parse_envelope(response.text, endpoint=endpoint)

        # If extra_keys is specified, parse each key and return a dict with all results.
        # Absent keys are omitted from the result; malformed or errored keys raise
        # the same typed errors as any other envelope.
        if extra_keys:
            response_data: dict[str, object] = {"a00": result}
            data = json.loads(response.text)
            for key in extra_keys:
                try:
                    response_data[key] = parse_envelope(response.text, endpoint=endpoint, key=key)
                except InvalidEnvelopeError:
                    # If the key is absent, omit it; if malformed, raise
                    # Check if the key is actually missing from the response
                    if isinstance(data, dict) and key not in data:
                        # Key is absent; omit it from the result
                        continue
                    # Key is present but malformed; re-raise the error
                    raise
            return response_data

        return result

    def _encode_form(self, fields: Mapping[str, str]) -> str:
        """Encode form fields as application/x-www-form-urlencoded.

        Args:
            fields: Dictionary of field name/value pairs.

        Returns:
            The encoded form string.
        """
        # Use httpx's native form handling via QueryParams
        from httpx import QueryParams

        # QueryParams properly handles UTF-8 encoding
        return str(QueryParams(fields))
