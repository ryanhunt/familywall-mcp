"""Self-hosted OAuth 2.1 authorization server for the hosted MCP transport.

Implements ``mcp.server.auth.provider.OAuthAuthorizationServerProvider`` per
docs/decisions/0001-auth-and-sdk.md (embed the provider, no Authlib; DCR and
revocation enabled; ``AccessToken.subject`` is the per-user key) with the
scope reduced per docs/decisions/0002-simplified-hosted-auth.md: "signing in"
checks a small, operator-configured `.env` table of MCP username/password
pairs (``AppConfig.hosted_users``), not a self-service account system.

Adapted from the pattern used by the sibling ``halaxy-mcp`` project's
``_SimpleOAuthProvider`` (itself derived from the MCP Python SDK's own
simple-auth example) — including its documented, previously-reported-and-fixed
security properties, carried forward deliberately:
  - the pending-authorization lookup key is a value only this server chose
    (``login_state``), never the client-supplied ``state``;
  - the login page HTML-escapes the requesting client's name and redirect
    target, and sends CSP/X-Frame-Options/nosniff headers;
  - login attempts are throttled per source IP (5 failures / 15 minute
    lockout);
  - refresh tokens rotate on every use.
This implementation adds two properties beyond that reference: refresh-token
reuse detection (a rotated-then-replayed refresh token revokes every
outstanding token for that subject) and a signed CSRF double-submit cookie on
the login form (using the existing required ``FAMILYWALL_AUTH_SECRET_KEY``),
and stores everything hashed in SQLite rather than a plaintext JSON file.
"""

from __future__ import annotations

import hashlib
import hmac
import html
import secrets
import time
from typing import TYPE_CHECKING
from urllib.parse import urlparse

from mcp.server.auth.provider import (
    AccessToken,
    AuthorizationCode,
    AuthorizationParams,
    OAuthAuthorizationServerProvider,
    RefreshToken,
    RegistrationError,
    TokenError,
    construct_redirect_uri,
)
from mcp.shared.auth import OAuthClientInformationFull, OAuthToken
from pydantic import AnyUrl
from starlette.exceptions import HTTPException
from starlette.requests import Request
from starlette.responses import HTMLResponse, RedirectResponse, Response

from familywall_mcp.auth.storage import OAuthSqliteStore, hash_token

if TYPE_CHECKING:
    from familywall_mcp.config import AppConfig

DEFAULT_SCOPE = "familywall"

ACCESS_TOKEN_TTL_SECONDS = 60 * 60
REFRESH_TOKEN_TTL_SECONDS = 30 * 24 * 60 * 60
AUTHORIZATION_CODE_TTL_SECONDS = 5 * 60
LOGIN_STATE_TTL_SECONDS = 10 * 60

LOGIN_MAX_ATTEMPTS = 5
LOGIN_LOCKOUT_SECONDS = 15 * 60

_LOGIN_PAGE_HEADERS = {
    "Content-Security-Policy": "default-src 'self'; frame-ancestors 'none'",
    "X-Frame-Options": "DENY",
    "X-Content-Type-Options": "nosniff",
}

_LOGIN_PAGE_TEMPLATE = """\
<!DOCTYPE html>
<html>
<head><title>FamilyWall MCP - Sign in</title>
<style>
    body {{
        font-family: system-ui, sans-serif; max-width: 420px;
        margin: 80px auto; padding: 0 20px;
    }}
    .form-group {{ margin-bottom: 15px; }}
    input {{ width: 100%; padding: 8px; margin-top: 5px; box-sizing: border-box; }}
    button {{
        background-color: #4CAF50; color: white;
        padding: 10px 15px; border: none; cursor: pointer;
    }}
    .consent {{
        background: #f4f4f4; border-radius: 6px; padding: 12px 16px;
        margin-bottom: 20px; font-size: 14px; word-break: break-all;
    }}
</style>
</head>
<body>
    <h2>FamilyWall MCP</h2>
    <div class="consent">
        <strong>{client_name}</strong> is requesting access.<br>
        After signing in, you'll be sent to:<br>{redirect_uri}
    </div>
    <form action="{public_url}/login/callback" method="post">
        <input type="hidden" name="state" value="{state}">
        <input type="hidden" name="csrf_token" value="{csrf_token}">
        <div class="form-group">
            <label>Username</label>
            <input type="text" name="username" required autofocus>
        </div>
        <div class="form-group">
            <label>Password</label>
            <input type="password" name="password" required>
        </div>
        <button type="submit">Sign in &amp; authorize</button>
    </form>
</body>
</html>
"""


class FamilyWallAuthProvider(
    OAuthAuthorizationServerProvider[AuthorizationCode, RefreshToken, AccessToken]
):
    """OAuth authorization server backed by ``OAuthSqliteStore`` and
    ``AppConfig.hosted_users``."""

    def __init__(self, config: AppConfig, store: OAuthSqliteStore, public_url: str) -> None:
        self._config = config
        self._store = store
        self._public_url = public_url.rstrip("/")
        # Per-source-IP login throttle. Deliberately in-memory/per-process
        # (not persisted): it only needs to bound brute-force attempts within
        # this server's uptime, matching the "one worker" deployment model.
        self._login_attempts: dict[str, list[float]] = {}

    # -- OAuthAuthorizationServerProvider ---------------------------------

    async def get_client(self, client_id: str) -> OAuthClientInformationFull | None:
        data = await self._store.get_client(client_id)
        return OAuthClientInformationFull.model_validate(data) if data else None

    async def register_client(self, client_info: OAuthClientInformationFull) -> None:
        if not client_info.client_id:
            raise RegistrationError(
                error="invalid_client_metadata", error_description="client_id is required"
            )
        allowed_hosts = self._config.allowed_redirect_uri_hosts
        if allowed_hosts:
            for redirect_uri in client_info.redirect_uris or []:
                host = urlparse(str(redirect_uri)).hostname
                if host not in allowed_hosts:
                    raise RegistrationError(
                        error="invalid_client_metadata",
                        error_description=(
                            f"redirect_uri host {host!r} is not in "
                            "FAMILYWALL_ALLOWED_REDIRECT_URI_HOSTS"
                        ),
                    )
        await self._store.put_client(client_info.client_id, client_info.model_dump(mode="json"))

    async def authorize(
        self, client: OAuthClientInformationFull, params: AuthorizationParams
    ) -> str:
        # The pending-authorization key MUST be a value only this server
        # chose, never the client-supplied `params.state` -- otherwise an
        # attacker could pre-seed an entry a victim's flow might inherit.
        # The client's own `state` (echoed back verbatim in the final
        # redirect, per spec) is carried through separately as
        # `client_state`.
        login_state = secrets.token_hex(16)
        await self._store.put_pending_authorization(
            login_state,
            {
                "client_id": client.client_id,
                "redirect_uri": str(params.redirect_uri),
                "redirect_uri_provided_explicitly": params.redirect_uri_provided_explicitly,
                "code_challenge": params.code_challenge,
                "resource": params.resource,
                "client_state": params.state,
                "scopes": params.scopes or [DEFAULT_SCOPE],
                "expires_at": time.time() + LOGIN_STATE_TTL_SECONDS,
            },
        )
        return f"{self._public_url}/login?state={login_state}"

    async def load_authorization_code(
        self, client: OAuthClientInformationFull, authorization_code: str
    ) -> AuthorizationCode | None:
        row = await self._store.get_authorization_code(authorization_code)
        if row is None or row["client_id"] != client.client_id:
            return None
        return AuthorizationCode(
            code=authorization_code,
            client_id=row["client_id"],
            redirect_uri=AnyUrl(row["redirect_uri"]),
            redirect_uri_provided_explicitly=row["redirect_uri_provided_explicitly"],
            expires_at=row["expires_at"],
            scopes=row["scopes"],
            code_challenge=row["code_challenge"],
            resource=row["resource"],
            subject=row["subject"],
        )

    async def exchange_authorization_code(
        self, client: OAuthClientInformationFull, authorization_code: AuthorizationCode
    ) -> OAuthToken:
        # Single-use: only proceed if this exact code hadn't already been
        # consumed by a concurrent/replayed request.
        consumed = await self._store.delete_authorization_code(authorization_code.code)
        if not consumed:
            raise TokenError(
                error="invalid_grant", error_description="Authorization code already used"
            )

        subject = authorization_code.subject
        if subject is None:
            raise TokenError(
                error="invalid_grant", error_description="Authorization code has no subject"
            )

        access_token, refresh_token = await self._issue_token_pair(
            client_id=client.client_id,
            subject=subject,
            scopes=authorization_code.scopes,
            resource=authorization_code.resource,
        )
        return OAuthToken(
            access_token=access_token,
            token_type="Bearer",
            expires_in=ACCESS_TOKEN_TTL_SECONDS,
            scope=" ".join(authorization_code.scopes),
            refresh_token=refresh_token,
        )

    async def load_refresh_token(
        self, client: OAuthClientInformationFull, refresh_token: str
    ) -> RefreshToken | None:
        row = await self._store.get_refresh_token(refresh_token)
        if row is None or row["client_id"] != client.client_id:
            return None
        if row["consumed_at"] is not None:
            # This exact refresh token was already rotated once before and
            # is being presented again -- treat as a signal of possible
            # token theft and revoke everything issued to this subject.
            await self._store.revoke_all_for_subject(row["subject"])
            return None
        return RefreshToken(
            token=refresh_token,
            client_id=row["client_id"],
            scopes=row["scopes"],
            expires_at=int(row["expires_at"]) if row["expires_at"] else None,
            subject=row["subject"],
        )

    async def exchange_refresh_token(
        self,
        client: OAuthClientInformationFull,
        refresh_token: RefreshToken,
        scopes: list[str],
    ) -> OAuthToken:
        # Rotate on every use (OAuth 2.1 requirement for public clients,
        # applied uniformly here). The old token becomes a replay signal
        # rather than being deleted outright, so load_refresh_token can
        # detect reuse above.
        await self._store.mark_refresh_token_consumed(refresh_token.token)

        if refresh_token.subject is None:
            raise TokenError(
                error="invalid_grant", error_description="Refresh token has no subject"
            )

        new_scopes = scopes or refresh_token.scopes
        access_token, new_refresh_token = await self._issue_token_pair(
            client_id=client.client_id,
            subject=refresh_token.subject,
            scopes=new_scopes,
            resource=None,
        )
        return OAuthToken(
            access_token=access_token,
            token_type="Bearer",
            expires_in=ACCESS_TOKEN_TTL_SECONDS,
            scope=" ".join(new_scopes),
            refresh_token=new_refresh_token,
        )

    async def load_access_token(self, token: str) -> AccessToken | None:
        row = await self._store.get_access_token(token)
        if row is None:
            return None
        return AccessToken(
            token=token,
            client_id=row["client_id"],
            scopes=row["scopes"],
            expires_at=int(row["expires_at"]) if row["expires_at"] else None,
            resource=row["resource"],
            subject=row["subject"],
        )

    async def revoke_token(self, token: AccessToken | RefreshToken) -> None:
        # Revoke both the access token and its paired refresh token
        # regardless of which one was handed to us, per the SDK's own
        # docstring guidance.
        if isinstance(token, RefreshToken):
            row = await self._store.get_refresh_token(token.token)
            refresh_hash = hash_token(token.token)
            paired_hash = row["paired_hash"] if row else None
            await self._store.revoke_pair(paired_hash, refresh_hash)
        else:
            row = await self._store.get_access_token(token.token)
            access_hash = hash_token(token.token)
            paired_hash = row["paired_hash"] if row else None
            await self._store.revoke_pair(access_hash, paired_hash)

    async def _issue_token_pair(
        self,
        *,
        client_id: str,
        subject: str,
        scopes: list[str],
        resource: str | None,
    ) -> tuple[str, str]:
        access_token = f"fw_{secrets.token_hex(32)}"
        refresh_token = f"fw_refresh_{secrets.token_hex(32)}"
        access_hash = hash_token(access_token)
        refresh_hash = hash_token(refresh_token)
        now = time.time()
        await self._store.put_access_token(
            access_token,
            {
                "client_id": client_id,
                "subject": subject,
                "resource": resource,
                "scopes": scopes,
                "expires_at": now + ACCESS_TOKEN_TTL_SECONDS,
                "paired_hash": refresh_hash,
            },
        )
        await self._store.put_refresh_token(
            refresh_token,
            {
                "client_id": client_id,
                "subject": subject,
                "scopes": scopes,
                "expires_at": now + REFRESH_TOKEN_TTL_SECONDS,
                "paired_hash": access_hash,
            },
        )
        return access_token, refresh_token

    # -- Login page / callback (not part of OAuthAuthorizationServerProvider,
    # wired in as plain Starlette routes by server.py) --------------------

    def _client_ip(self, request: Request) -> str:
        """Best-effort source IP for login-attempt throttling.

        Trusts the first hop of X-Forwarded-For because Caddy is the sole
        ingress in the documented deployment topology (docker-compose.prod.yml
        never publishes this app's port directly) and sets this header on
        every request it proxies; falls back to the raw socket peer for
        local/non-Docker use where nothing sits in front at all.
        """
        forwarded = request.headers.get("x-forwarded-for")
        if forwarded:
            return forwarded.split(",")[0].strip()
        return request.client.host if request.client else "unknown"

    def _check_login_rate_limit(self, ip: str) -> None:
        attempts = self._login_attempts.get(ip)
        if not attempts:
            return
        now = time.time()
        recent = [t for t in attempts if now - t < LOGIN_LOCKOUT_SECONDS]
        self._login_attempts[ip] = recent
        if len(recent) >= LOGIN_MAX_ATTEMPTS:
            raise HTTPException(
                429,
                f"Too many failed sign-in attempts; try again in "
                f"{LOGIN_LOCKOUT_SECONDS // 60} minutes",
            )

    def _record_login_failure(self, ip: str) -> None:
        self._login_attempts.setdefault(ip, []).append(time.time())

    def _csrf_signature(self, csrf_token: str) -> str:
        key = (
            self._config.auth_secret_key.get_secret_value().encode("utf-8")
            if self._config.auth_secret_key
            else b""
        )
        return hmac.new(key, csrf_token.encode("utf-8"), hashlib.sha256).hexdigest()

    async def handle_login_page(self, request: Request) -> Response:
        state = request.query_params.get("state")
        if not state:
            raise HTTPException(400, "Missing state parameter")

        pending = await self._store.get_pending_authorization(state)
        if pending is None:
            # Deliberately rejected before rendering anything: an
            # unrecognised `state` must never still produce a normal-looking
            # login page reflecting attacker-controlled query parameters.
            raise HTTPException(400, "Invalid or expired state parameter; try connecting again")

        client_data = await self._store.get_client(pending["client_id"])
        client_name = (client_data or {}).get("client_name") or pending["client_id"]

        csrf_token = secrets.token_hex(16)
        signature = self._csrf_signature(csrf_token)

        body = _LOGIN_PAGE_TEMPLATE.format(
            client_name=html.escape(str(client_name)),
            redirect_uri=html.escape(pending["redirect_uri"]),
            state=html.escape(state),
            csrf_token=html.escape(csrf_token),
            public_url=html.escape(self._public_url),
        )
        response = HTMLResponse(body, headers=_LOGIN_PAGE_HEADERS)
        response.set_cookie(
            "fw_csrf",
            f"{csrf_token}.{signature}",
            httponly=True,
            samesite="lax",
            secure=self._public_url.startswith("https://"),
            max_age=LOGIN_STATE_TTL_SECONDS,
        )
        return response

    async def handle_login_callback(self, request: Request) -> Response:
        ip = self._client_ip(request)
        self._check_login_rate_limit(ip)

        form = await request.form()
        username = form.get("username")
        password = form.get("password")
        state = form.get("state")
        csrf_token = form.get("csrf_token")
        if not (
            isinstance(username, str)
            and isinstance(password, str)
            and isinstance(state, str)
            and isinstance(csrf_token, str)
        ):
            raise HTTPException(400, "Missing username, password, state, or csrf_token")

        cookie_value = request.cookies.get("fw_csrf", "")
        cookie_token, _, cookie_signature = cookie_value.partition(".")
        expected_signature = self._csrf_signature(cookie_token) if cookie_token else ""
        if (
            not cookie_token
            or not hmac.compare_digest(cookie_signature, expected_signature)
            or not hmac.compare_digest(cookie_token, csrf_token)
        ):
            raise HTTPException(400, "Invalid or missing anti-forgery token; try connecting again")

        pending = await self._store.get_pending_authorization(state)
        if pending is None:
            raise HTTPException(400, "Invalid or expired state parameter; try connecting again")

        user = self._config.find_hosted_user(username)
        if user is None or not secrets.compare_digest(
            password, user.mcp_password.get_secret_value()
        ):
            self._record_login_failure(ip)
            raise HTTPException(401, "Invalid credentials")
        self._login_attempts.pop(ip, None)

        new_code = f"fw_{secrets.token_hex(16)}"
        await self._store.put_authorization_code(
            new_code,
            {
                "client_id": pending["client_id"],
                "subject": user.mcp_username,
                "redirect_uri": pending["redirect_uri"],
                "redirect_uri_provided_explicitly": pending["redirect_uri_provided_explicitly"],
                "code_challenge": pending["code_challenge"],
                "resource": pending["resource"],
                "scopes": pending["scopes"],
                "expires_at": time.time() + AUTHORIZATION_CODE_TTL_SECONDS,
            },
        )
        await self._store.delete_pending_authorization(state)

        # `state` echoed back here MUST be the client's own original value
        # (`client_state`), never our internal pending-authorization key.
        redirect_url = construct_redirect_uri(
            pending["redirect_uri"], code=new_code, state=pending["client_state"]
        )
        response = RedirectResponse(url=redirect_url, status_code=302)
        response.delete_cookie("fw_csrf")
        return response
