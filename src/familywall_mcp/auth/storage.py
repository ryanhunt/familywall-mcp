"""SQLite storage for the hosted OAuth authorization server.

Same style as ``storage/sqlite.py``'s ``SqliteReceiptRepository``: one
connection per call wrapped in ``asyncio.to_thread``, WAL mode, a new
database file created with ``0o600``. Authorization codes and access/refresh
tokens are stored by their SHA-256 hash, never in cleartext, so a copy of
the database file alone does not hand out live bearer credentials.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import sqlite3
import time
from pathlib import Path
from typing import Any


def hash_token(value: str) -> str:
    """Return the lookup key stored for a secret token/code value."""
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


class OAuthSqliteStore:
    """Durable storage for OAuth clients, pending logins, codes, and tokens."""

    def __init__(self, database_path: str | Path) -> None:
        self._database_path = Path(database_path)
        self._lock = asyncio.Lock()

    async def initialise(self) -> None:
        def _init_db() -> None:
            self._database_path.parent.mkdir(parents=True, exist_ok=True)
            is_new = not self._database_path.exists()

            conn = sqlite3.connect(str(self._database_path))
            try:
                conn.execute("PRAGMA journal_mode=WAL")
                conn.execute("PRAGMA foreign_keys=ON")
                conn.execute(
                    """
                    CREATE TABLE IF NOT EXISTS oauth_clients (
                        client_id   TEXT PRIMARY KEY,
                        client_data TEXT NOT NULL
                    )
                    """
                )
                conn.execute(
                    """
                    CREATE TABLE IF NOT EXISTS oauth_pending_authorizations (
                        state                          TEXT PRIMARY KEY,
                        client_id                      TEXT NOT NULL,
                        redirect_uri                   TEXT NOT NULL,
                        redirect_uri_provided_explicitly INTEGER NOT NULL,
                        code_challenge                TEXT NOT NULL,
                        resource                       TEXT,
                        client_state                   TEXT,
                        scopes                         TEXT NOT NULL,
                        expires_at                     REAL NOT NULL
                    )
                    """
                )
                conn.execute(
                    """
                    CREATE TABLE IF NOT EXISTS oauth_authorization_codes (
                        code_hash                     TEXT PRIMARY KEY,
                        client_id                      TEXT NOT NULL,
                        subject                        TEXT NOT NULL,
                        redirect_uri                   TEXT NOT NULL,
                        redirect_uri_provided_explicitly INTEGER NOT NULL,
                        code_challenge                TEXT NOT NULL,
                        resource                       TEXT,
                        scopes                         TEXT NOT NULL,
                        expires_at                     REAL NOT NULL
                    )
                    """
                )
                conn.execute(
                    """
                    CREATE TABLE IF NOT EXISTS oauth_access_tokens (
                        token_hash   TEXT PRIMARY KEY,
                        client_id    TEXT NOT NULL,
                        subject      TEXT NOT NULL,
                        resource     TEXT,
                        scopes       TEXT NOT NULL,
                        expires_at   REAL,
                        paired_hash  TEXT
                    )
                    """
                )
                conn.execute(
                    """
                    CREATE TABLE IF NOT EXISTS oauth_refresh_tokens (
                        token_hash   TEXT PRIMARY KEY,
                        client_id    TEXT NOT NULL,
                        subject      TEXT NOT NULL,
                        scopes       TEXT NOT NULL,
                        expires_at   REAL,
                        paired_hash  TEXT,
                        consumed_at  REAL
                    )
                    """
                )
                conn.execute(
                    "CREATE INDEX IF NOT EXISTS idx_access_tokens_subject "
                    "ON oauth_access_tokens(subject)"
                )
                conn.execute(
                    "CREATE INDEX IF NOT EXISTS idx_refresh_tokens_subject "
                    "ON oauth_refresh_tokens(subject)"
                )
                conn.commit()
            finally:
                conn.close()

            if is_new:
                os.chmod(str(self._database_path), 0o600)

        await asyncio.to_thread(_init_db)

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self._database_path))
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA foreign_keys=ON")
        return conn

    # -- Clients --------------------------------------------------------

    async def get_client(self, client_id: str) -> dict[str, Any] | None:
        def _get() -> dict[str, Any] | None:
            conn = self._connect()
            try:
                row = conn.execute(
                    "SELECT client_data FROM oauth_clients WHERE client_id = ?",
                    (client_id,),
                ).fetchone()
                return json.loads(row["client_data"]) if row else None
            finally:
                conn.close()

        return await asyncio.to_thread(_get)

    async def put_client(self, client_id: str, client_data: dict[str, Any]) -> None:
        def _put() -> None:
            conn = self._connect()
            try:
                conn.execute(
                    """
                    INSERT INTO oauth_clients (client_id, client_data) VALUES (?, ?)
                    ON CONFLICT(client_id) DO UPDATE SET client_data = excluded.client_data
                    """,
                    (client_id, json.dumps(client_data)),
                )
                conn.commit()
            finally:
                conn.close()

        async with self._lock:
            await asyncio.to_thread(_put)

    # -- Pending authorizations (the /authorize -> /login hop) ----------

    async def put_pending_authorization(self, state: str, data: dict[str, Any]) -> None:
        def _put() -> None:
            conn = self._connect()
            try:
                conn.execute(
                    """
                    INSERT INTO oauth_pending_authorizations
                        (state, client_id, redirect_uri, redirect_uri_provided_explicitly,
                         code_challenge, resource, client_state, scopes, expires_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        state,
                        data["client_id"],
                        data["redirect_uri"],
                        int(data["redirect_uri_provided_explicitly"]),
                        data["code_challenge"],
                        data.get("resource"),
                        data.get("client_state"),
                        json.dumps(data["scopes"]),
                        data["expires_at"],
                    ),
                )
                conn.commit()
            finally:
                conn.close()

        async with self._lock:
            await asyncio.to_thread(_put)

    async def get_pending_authorization(self, state: str) -> dict[str, Any] | None:
        def _get() -> dict[str, Any] | None:
            conn = self._connect()
            try:
                row = conn.execute(
                    "SELECT * FROM oauth_pending_authorizations WHERE state = ?",
                    (state,),
                ).fetchone()
                if row is None:
                    return None
                result = dict(row)
                result["scopes"] = json.loads(result["scopes"])
                result["redirect_uri_provided_explicitly"] = bool(
                    result["redirect_uri_provided_explicitly"]
                )
                return result
            finally:
                conn.close()

        result = await asyncio.to_thread(_get)
        if result is not None and result["expires_at"] < time.time():
            await self.delete_pending_authorization(state)
            return None
        return result

    async def delete_pending_authorization(self, state: str) -> None:
        def _delete() -> None:
            conn = self._connect()
            try:
                conn.execute("DELETE FROM oauth_pending_authorizations WHERE state = ?", (state,))
                conn.commit()
            finally:
                conn.close()

        async with self._lock:
            await asyncio.to_thread(_delete)

    # -- Authorization codes ---------------------------------------------

    async def put_authorization_code(self, code: str, data: dict[str, Any]) -> None:
        code_hash = hash_token(code)

        def _put() -> None:
            conn = self._connect()
            try:
                conn.execute(
                    """
                    INSERT INTO oauth_authorization_codes
                        (code_hash, client_id, subject, redirect_uri,
                         redirect_uri_provided_explicitly, code_challenge, resource,
                         scopes, expires_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        code_hash,
                        data["client_id"],
                        data["subject"],
                        data["redirect_uri"],
                        int(data["redirect_uri_provided_explicitly"]),
                        data["code_challenge"],
                        data.get("resource"),
                        json.dumps(data["scopes"]),
                        data["expires_at"],
                    ),
                )
                conn.commit()
            finally:
                conn.close()

        async with self._lock:
            await asyncio.to_thread(_put)

    async def get_authorization_code(self, code: str) -> dict[str, Any] | None:
        code_hash = hash_token(code)

        def _get() -> dict[str, Any] | None:
            conn = self._connect()
            try:
                row = conn.execute(
                    "SELECT * FROM oauth_authorization_codes WHERE code_hash = ?",
                    (code_hash,),
                ).fetchone()
                if row is None:
                    return None
                result = dict(row)
                result["scopes"] = json.loads(result["scopes"])
                result["redirect_uri_provided_explicitly"] = bool(
                    result["redirect_uri_provided_explicitly"]
                )
                return result
            finally:
                conn.close()

        result = await asyncio.to_thread(_get)
        if result is not None and result["expires_at"] < time.time():
            await self.delete_authorization_code(code)
            return None
        return result

    async def delete_authorization_code(self, code: str) -> bool:
        """Delete a code (single-use). Returns True iff a row was actually deleted,
        so callers can detect a replayed/already-consumed code."""
        code_hash = hash_token(code)

        def _delete() -> bool:
            conn = self._connect()
            try:
                cursor = conn.execute(
                    "DELETE FROM oauth_authorization_codes WHERE code_hash = ?", (code_hash,)
                )
                conn.commit()
                return cursor.rowcount > 0
            finally:
                conn.close()

        async with self._lock:
            return await asyncio.to_thread(_delete)

    # -- Access tokens ----------------------------------------------------

    async def put_access_token(self, token: str, data: dict[str, Any]) -> None:
        token_hash = hash_token(token)

        def _put() -> None:
            conn = self._connect()
            try:
                conn.execute(
                    """
                    INSERT INTO oauth_access_tokens
                        (token_hash, client_id, subject, resource, scopes, expires_at,
                         paired_hash)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        token_hash,
                        data["client_id"],
                        data["subject"],
                        data.get("resource"),
                        json.dumps(data["scopes"]),
                        data.get("expires_at"),
                        data.get("paired_hash"),
                    ),
                )
                conn.commit()
            finally:
                conn.close()

        async with self._lock:
            await asyncio.to_thread(_put)

    async def get_access_token(self, token: str) -> dict[str, Any] | None:
        token_hash = hash_token(token)

        def _get() -> dict[str, Any] | None:
            conn = self._connect()
            try:
                row = conn.execute(
                    "SELECT * FROM oauth_access_tokens WHERE token_hash = ?",
                    (token_hash,),
                ).fetchone()
                if row is None:
                    return None
                result = dict(row)
                result["scopes"] = json.loads(result["scopes"])
                return result
            finally:
                conn.close()

        result = await asyncio.to_thread(_get)
        if (
            result is not None
            and result["expires_at"] is not None
            and result["expires_at"] < time.time()
        ):
            await self.delete_access_token(token)
            return None
        return result

    async def delete_access_token(self, token: str) -> None:
        token_hash = hash_token(token)
        await self.delete_access_token_by_hash(token_hash)

    async def delete_access_token_by_hash(self, token_hash: str) -> None:
        def _delete() -> None:
            conn = self._connect()
            try:
                conn.execute("DELETE FROM oauth_access_tokens WHERE token_hash = ?", (token_hash,))
                conn.commit()
            finally:
                conn.close()

        async with self._lock:
            await asyncio.to_thread(_delete)

    # -- Refresh tokens -----------------------------------------------------

    async def put_refresh_token(self, token: str, data: dict[str, Any]) -> None:
        token_hash = hash_token(token)

        def _put() -> None:
            conn = self._connect()
            try:
                conn.execute(
                    """
                    INSERT INTO oauth_refresh_tokens
                        (token_hash, client_id, subject, scopes, expires_at, paired_hash,
                         consumed_at)
                    VALUES (?, ?, ?, ?, ?, ?, NULL)
                    """,
                    (
                        token_hash,
                        data["client_id"],
                        data["subject"],
                        json.dumps(data["scopes"]),
                        data.get("expires_at"),
                        data.get("paired_hash"),
                    ),
                )
                conn.commit()
            finally:
                conn.close()

        async with self._lock:
            await asyncio.to_thread(_put)

    async def get_refresh_token(self, token: str) -> dict[str, Any] | None:
        token_hash = hash_token(token)

        def _get() -> dict[str, Any] | None:
            conn = self._connect()
            try:
                row = conn.execute(
                    "SELECT * FROM oauth_refresh_tokens WHERE token_hash = ?",
                    (token_hash,),
                ).fetchone()
                if row is None:
                    return None
                result = dict(row)
                result["scopes"] = json.loads(result["scopes"])
                return result
            finally:
                conn.close()

        result = await asyncio.to_thread(_get)
        if (
            result is not None
            and result["expires_at"] is not None
            and result["expires_at"] < time.time()
        ):
            await self.delete_refresh_token_by_hash(token_hash)
            return None
        return result

    async def mark_refresh_token_consumed(self, token: str) -> None:
        token_hash = hash_token(token)

        def _mark() -> None:
            conn = self._connect()
            try:
                conn.execute(
                    "UPDATE oauth_refresh_tokens SET consumed_at = ? WHERE token_hash = ?",
                    (time.time(), token_hash),
                )
                conn.commit()
            finally:
                conn.close()

        async with self._lock:
            await asyncio.to_thread(_mark)

    async def delete_refresh_token_by_hash(self, token_hash: str) -> None:
        def _delete() -> None:
            conn = self._connect()
            try:
                conn.execute("DELETE FROM oauth_refresh_tokens WHERE token_hash = ?", (token_hash,))
                conn.commit()
            finally:
                conn.close()

        async with self._lock:
            await asyncio.to_thread(_delete)

    # -- Revocation -----------------------------------------------------

    async def revoke_pair(
        self, access_token_hash: str | None, refresh_token_hash: str | None
    ) -> None:
        """Delete an access/refresh token pair by hash (either side may be None)."""

        def _revoke() -> None:
            conn = self._connect()
            try:
                if access_token_hash:
                    conn.execute(
                        "DELETE FROM oauth_access_tokens WHERE token_hash = ?",
                        (access_token_hash,),
                    )
                if refresh_token_hash:
                    conn.execute(
                        "DELETE FROM oauth_refresh_tokens WHERE token_hash = ?",
                        (refresh_token_hash,),
                    )
                conn.commit()
            finally:
                conn.close()

        async with self._lock:
            await asyncio.to_thread(_revoke)

    async def revoke_all_for_subject(self, subject: str) -> None:
        """Revoke every outstanding access/refresh token for one MCP username.

        Used when a refresh token is presented for reuse after already being
        rotated once — treated as a signal of possible token theft.
        """

        def _revoke() -> None:
            conn = self._connect()
            try:
                conn.execute("DELETE FROM oauth_access_tokens WHERE subject = ?", (subject,))
                conn.execute("DELETE FROM oauth_refresh_tokens WHERE subject = ?", (subject,))
                conn.commit()
            finally:
                conn.close()

        async with self._lock:
            await asyncio.to_thread(_revoke)

    async def purge_expired(self) -> None:
        now = time.time()

        def _purge() -> None:
            conn = self._connect()
            try:
                conn.execute(
                    "DELETE FROM oauth_pending_authorizations WHERE expires_at <= ?", (now,)
                )
                conn.execute("DELETE FROM oauth_authorization_codes WHERE expires_at <= ?", (now,))
                conn.execute(
                    "DELETE FROM oauth_access_tokens WHERE expires_at IS NOT NULL "
                    "AND expires_at <= ?",
                    (now,),
                )
                conn.execute(
                    "DELETE FROM oauth_refresh_tokens WHERE expires_at IS NOT NULL "
                    "AND expires_at <= ?",
                    (now,),
                )
                conn.commit()
            finally:
                conn.close()

        async with self._lock:
            await asyncio.to_thread(_purge)

    async def aclose(self) -> None:
        """No-op: connections are opened and closed per operation."""
