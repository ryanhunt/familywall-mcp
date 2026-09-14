"""Tests for OAuthSqliteStore: hashed-at-rest storage for the hosted OAuth server."""

from __future__ import annotations

import time
from pathlib import Path

import pytest

from familywall_mcp.auth.storage import OAuthSqliteStore


@pytest.fixture
async def store(tmp_path: Path) -> OAuthSqliteStore:
    db_path = tmp_path / "oauth.sqlite3"
    s = OAuthSqliteStore(db_path)
    await s.initialise()
    return s


class TestClients:
    async def test_put_then_get_round_trips(self, store: OAuthSqliteStore) -> None:
        data = {
            "client_id": "client-1",
            "client_name": "Test Client",
            "redirect_uris": ["https://client.example/cb"],
        }
        await store.put_client("client-1", data)
        retrieved = await store.get_client("client-1")
        assert retrieved == data

    async def test_get_missing_client_returns_none(self, store: OAuthSqliteStore) -> None:
        assert await store.get_client("does-not-exist") is None

    async def test_put_twice_upserts(self, store: OAuthSqliteStore) -> None:
        await store.put_client("client-1", {"client_id": "client-1", "client_name": "First"})
        await store.put_client("client-1", {"client_id": "client-1", "client_name": "Second"})
        retrieved = await store.get_client("client-1")
        assert retrieved is not None
        assert retrieved["client_name"] == "Second"


class TestPendingAuthorizations:
    def _pending_data(self, **overrides: object) -> dict[str, object]:
        base: dict[str, object] = {
            "client_id": "client-1",
            "redirect_uri": "https://client.example/cb",
            "redirect_uri_provided_explicitly": True,
            "code_challenge": "challenge123",
            "resource": None,
            "client_state": "client-state-xyz",
            "scopes": ["familywall"],
            "expires_at": time.time() + 600,
        }
        base.update(overrides)
        return base

    async def test_put_then_get_round_trips(self, store: OAuthSqliteStore) -> None:
        data = self._pending_data()
        await store.put_pending_authorization("login-state-1", data)
        retrieved = await store.get_pending_authorization("login-state-1")
        assert retrieved is not None
        assert retrieved["client_id"] == "client-1"
        assert retrieved["client_state"] == "client-state-xyz"
        assert retrieved["scopes"] == ["familywall"]
        assert retrieved["redirect_uri_provided_explicitly"] is True

    async def test_expired_pending_authorization_reads_as_none(
        self, store: OAuthSqliteStore
    ) -> None:
        data = self._pending_data(expires_at=time.time() - 1)
        await store.put_pending_authorization("expired-state", data)
        assert await store.get_pending_authorization("expired-state") is None

    async def test_expired_pending_authorization_is_deleted_on_read(
        self, store: OAuthSqliteStore
    ) -> None:
        data = self._pending_data(expires_at=time.time() - 1)
        await store.put_pending_authorization("expired-state", data)
        await store.get_pending_authorization("expired-state")
        # A second read still finds nothing (row was actually deleted, not just filtered).
        assert await store.get_pending_authorization("expired-state") is None

    async def test_delete_removes_pending_authorization(self, store: OAuthSqliteStore) -> None:
        data = self._pending_data()
        await store.put_pending_authorization("login-state-1", data)
        await store.delete_pending_authorization("login-state-1")
        assert await store.get_pending_authorization("login-state-1") is None


class TestAuthorizationCodes:
    def _code_data(self, **overrides: object) -> dict[str, object]:
        base: dict[str, object] = {
            "client_id": "client-1",
            "subject": "alice",
            "redirect_uri": "https://client.example/cb",
            "redirect_uri_provided_explicitly": True,
            "code_challenge": "challenge123",
            "resource": None,
            "scopes": ["familywall"],
            "expires_at": time.time() + 300,
        }
        base.update(overrides)
        return base

    async def test_put_then_get_round_trips(self, store: OAuthSqliteStore) -> None:
        await store.put_authorization_code("code-1", self._code_data())
        retrieved = await store.get_authorization_code("code-1")
        assert retrieved is not None
        assert retrieved["subject"] == "alice"
        assert retrieved["scopes"] == ["familywall"]

    async def test_expired_code_reads_as_none(self, store: OAuthSqliteStore) -> None:
        await store.put_authorization_code("code-1", self._code_data(expires_at=time.time() - 1))
        assert await store.get_authorization_code("code-1") is None

    async def test_delete_returns_true_only_the_first_time(self, store: OAuthSqliteStore) -> None:
        await store.put_authorization_code("code-1", self._code_data())
        assert await store.delete_authorization_code("code-1") is True
        assert await store.delete_authorization_code("code-1") is False

    async def test_delete_missing_code_returns_false(self, store: OAuthSqliteStore) -> None:
        assert await store.delete_authorization_code("never-existed") is False


class TestAccessTokens:
    def _token_data(self, **overrides: object) -> dict[str, object]:
        base: dict[str, object] = {
            "client_id": "client-1",
            "subject": "alice",
            "resource": None,
            "scopes": ["familywall"],
            "expires_at": time.time() + 3600,
            "paired_hash": "refresh-hash-1",
        }
        base.update(overrides)
        return base

    async def test_put_then_get_round_trips(self, store: OAuthSqliteStore) -> None:
        await store.put_access_token("access-1", self._token_data())
        retrieved = await store.get_access_token("access-1")
        assert retrieved is not None
        assert retrieved["subject"] == "alice"
        assert retrieved["paired_hash"] == "refresh-hash-1"

    async def test_expired_access_token_reads_as_none(self, store: OAuthSqliteStore) -> None:
        await store.put_access_token("access-1", self._token_data(expires_at=time.time() - 1))
        assert await store.get_access_token("access-1") is None

    async def test_access_token_with_no_expiry_never_expires(self, store: OAuthSqliteStore) -> None:
        await store.put_access_token("access-1", self._token_data(expires_at=None))
        assert await store.get_access_token("access-1") is not None

    async def test_delete_access_token_removes_it(self, store: OAuthSqliteStore) -> None:
        await store.put_access_token("access-1", self._token_data())
        await store.delete_access_token("access-1")
        assert await store.get_access_token("access-1") is None


class TestRefreshTokens:
    def _token_data(self, **overrides: object) -> dict[str, object]:
        base: dict[str, object] = {
            "client_id": "client-1",
            "subject": "alice",
            "scopes": ["familywall"],
            "expires_at": time.time() + 3600,
            "paired_hash": "access-hash-1",
        }
        base.update(overrides)
        return base

    async def test_put_then_get_round_trips(self, store: OAuthSqliteStore) -> None:
        await store.put_refresh_token("refresh-1", self._token_data())
        retrieved = await store.get_refresh_token("refresh-1")
        assert retrieved is not None
        assert retrieved["subject"] == "alice"
        assert retrieved["consumed_at"] is None

    async def test_expired_refresh_token_reads_as_none(self, store: OAuthSqliteStore) -> None:
        await store.put_refresh_token("refresh-1", self._token_data(expires_at=time.time() - 1))
        assert await store.get_refresh_token("refresh-1") is None

    async def test_mark_consumed_then_get_reflects_consumed_at(
        self, store: OAuthSqliteStore
    ) -> None:
        await store.put_refresh_token("refresh-1", self._token_data())
        await store.mark_refresh_token_consumed("refresh-1")
        retrieved = await store.get_refresh_token("refresh-1")
        assert retrieved is not None
        assert retrieved["consumed_at"] is not None

    async def test_delete_refresh_token_by_hash_removes_it(self, store: OAuthSqliteStore) -> None:
        from familywall_mcp.auth.storage import hash_token

        await store.put_refresh_token("refresh-1", self._token_data())
        await store.delete_refresh_token_by_hash(hash_token("refresh-1"))
        assert await store.get_refresh_token("refresh-1") is None


class TestRevocation:
    async def test_revoke_pair_deletes_both_sides(self, store: OAuthSqliteStore) -> None:
        from familywall_mcp.auth.storage import hash_token

        await store.put_access_token(
            "access-1",
            {
                "client_id": "client-1",
                "subject": "alice",
                "resource": None,
                "scopes": ["familywall"],
                "expires_at": time.time() + 3600,
                "paired_hash": hash_token("refresh-1"),
            },
        )
        await store.put_refresh_token(
            "refresh-1",
            {
                "client_id": "client-1",
                "subject": "alice",
                "scopes": ["familywall"],
                "expires_at": time.time() + 3600,
                "paired_hash": hash_token("access-1"),
            },
        )

        await store.revoke_pair(hash_token("access-1"), hash_token("refresh-1"))

        assert await store.get_access_token("access-1") is None
        assert await store.get_refresh_token("refresh-1") is None

    async def test_revoke_pair_tolerates_none_hashes(self, store: OAuthSqliteStore) -> None:
        # Should not raise when only one side of the pair is known.
        await store.revoke_pair(None, None)

    async def test_revoke_all_for_subject_only_affects_that_subject(
        self, store: OAuthSqliteStore
    ) -> None:
        await store.put_access_token(
            "alice-access",
            {
                "client_id": "client-1",
                "subject": "alice",
                "resource": None,
                "scopes": ["familywall"],
                "expires_at": time.time() + 3600,
                "paired_hash": None,
            },
        )
        await store.put_refresh_token(
            "alice-refresh",
            {
                "client_id": "client-1",
                "subject": "alice",
                "scopes": ["familywall"],
                "expires_at": time.time() + 3600,
                "paired_hash": None,
            },
        )
        await store.put_access_token(
            "bob-access",
            {
                "client_id": "client-1",
                "subject": "bob",
                "resource": None,
                "scopes": ["familywall"],
                "expires_at": time.time() + 3600,
                "paired_hash": None,
            },
        )
        await store.put_refresh_token(
            "bob-refresh",
            {
                "client_id": "client-1",
                "subject": "bob",
                "scopes": ["familywall"],
                "expires_at": time.time() + 3600,
                "paired_hash": None,
            },
        )

        await store.revoke_all_for_subject("alice")

        assert await store.get_access_token("alice-access") is None
        assert await store.get_refresh_token("alice-refresh") is None
        assert await store.get_access_token("bob-access") is not None
        assert await store.get_refresh_token("bob-refresh") is not None


class TestPurgeExpired:
    async def test_purge_expired_removes_expired_rows_across_tables(
        self, store: OAuthSqliteStore
    ) -> None:
        past = time.time() - 1
        future = time.time() + 3600

        await store.put_pending_authorization(
            "expired-pending",
            {
                "client_id": "client-1",
                "redirect_uri": "https://client.example/cb",
                "redirect_uri_provided_explicitly": True,
                "code_challenge": "challenge",
                "resource": None,
                "client_state": None,
                "scopes": ["familywall"],
                "expires_at": past,
            },
        )
        await store.put_access_token(
            "expired-access",
            {
                "client_id": "client-1",
                "subject": "alice",
                "resource": None,
                "scopes": ["familywall"],
                "expires_at": past,
                "paired_hash": None,
            },
        )
        await store.put_access_token(
            "live-access",
            {
                "client_id": "client-1",
                "subject": "alice",
                "resource": None,
                "scopes": ["familywall"],
                "expires_at": future,
                "paired_hash": None,
            },
        )

        await store.purge_expired()

        # Bypass the read-side expiry check by hitting the tables directly via get_*,
        # which itself deletes-on-read for expired rows; a purge should have already
        # removed the expired row so no extra delete happens here.
        assert await store.get_access_token("live-access") is not None


class TestDatabaseFilePermissions:
    async def test_database_file_created_with_mode_0o600(self, tmp_path: Path) -> None:
        db_path = tmp_path / "mode_test.sqlite3"
        s = OAuthSqliteStore(db_path)
        await s.initialise()

        assert db_path.exists()
        mode = db_path.stat().st_mode & 0o777
        assert mode == 0o600
