"""Durable receipt repository using SQLite for mutation operation tracking."""

from __future__ import annotations

import asyncio
import os
import sqlite3
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING

from familywall_mcp.models import Principal

if TYPE_CHECKING:
    from familywall_mcp.interfaces import Clock
    from familywall_mcp.models import OperationReceipt

_SCHEMA_COLUMNS = """
        subject       TEXT NOT NULL,
        operation_id  TEXT NOT NULL,
        family_id     TEXT NOT NULL,
        resource_id   TEXT NOT NULL,
        action        TEXT NOT NULL,
        payload_hash  TEXT NOT NULL,
        status        TEXT NOT NULL CHECK (
            status IN ('pending', 'succeeded', 'unknown', 'rejected')
        ),
        upstream_id   TEXT,
        expires_at    TEXT NOT NULL,
        PRIMARY KEY (subject, operation_id)
"""

_FRESH_TABLE_DDL = f"CREATE TABLE IF NOT EXISTS operation_receipts (\n{_SCHEMA_COLUMNS})"

_MIGRATION_TABLE_DDL = f"CREATE TABLE operation_receipts_new (\n{_SCHEMA_COLUMNS})"
"""Deliberately lacks ``IF NOT EXISTS``: a pre-existing ``operation_receipts_new``
(e.g. left over from a previous failed migration) must make the migration fail
loudly rather than silently reuse or clobber it."""


class SqliteReceiptRepository:
    """Receipt repository using SQLite for durability across process restarts.

    Receipts are stored in a SQLite database with (subject, operation_id) as
    the primary key, ensuring tenant isolation. All database operations are
    wrapped in asyncio.to_thread to avoid blocking the event loop.
    """

    def __init__(self, database_path: str | Path, clock: Clock | None = None) -> None:
        """Initialize the SQLite receipt repository.

        Args:
            database_path: Path to the SQLite database file. Parent directory
                          will be created if missing. File will be created with
                          mode 0o600.
            clock: Optional Clock implementation for testing. If not provided,
                   a default clock using datetime.utcnow() is used.
        """
        self._database_path = Path(database_path)
        self._clock = clock or _DefaultClock()
        self._lock = asyncio.Lock()

    async def initialise(self) -> None:
        """Create or migrate the database schema (idempotent).

        This method is safe to call multiple times. It enables WAL and foreign
        key constraints, then creates the ``operation_receipts`` table if it
        does not exist, or migrates it in place if it is still on the legacy
        ``list_id`` schema.

        The database file is shared with ``OAuthSqliteStore`` (see
        ``server.py``), so this deliberately does not use a database-wide
        ``PRAGMA user_version`` to detect whether it has run before — that
        would couple the two stores' schema histories. Instead, the receipts
        schema itself is inspected with ``PRAGMA table_info(operation_receipts)``.
        """

        def _init_db() -> None:
            # Create parent directory if missing
            self._database_path.parent.mkdir(parents=True, exist_ok=True)

            # If creating a new file, set mode to 0o600
            is_new = not self._database_path.exists()

            conn = sqlite3.connect(str(self._database_path))
            try:
                # Enable WAL mode for better concurrency
                conn.execute("PRAGMA journal_mode=WAL")
                # Enable foreign key constraints
                conn.execute("PRAGMA foreign_keys=ON")

                columns = {row[1] for row in conn.execute("PRAGMA table_info(operation_receipts)")}

                if not columns:
                    # No table yet: create the current schema directly.
                    conn.execute(_FRESH_TABLE_DDL)
                    conn.commit()
                elif "list_id" in columns:
                    # Legacy schema: migrate in place, in one transaction.
                    _migrate_legacy_table(conn)
                # else: "resource_id" already present, nothing to do.
            finally:
                conn.close()

            # Set file mode to 0o600 if it's a new file
            if is_new:
                os.chmod(str(self._database_path), 0o600)

        await asyncio.to_thread(_init_db)

    async def get(self, principal: Principal, operation_id: str) -> OperationReceipt | None:
        """Retrieve a receipt by principal and operation_id, or None if not found or expired.

        An expired receipt is treated as absent and returns None.

        Args:
            principal: The authenticated subject making the request.
            operation_id: The operation identifier supplied by the caller.

        Returns:
            The OperationReceipt if found and not expired, None otherwise.
        """

        def _get_receipt() -> tuple[dict[str, object], datetime] | None:
            conn = sqlite3.connect(str(self._database_path))
            conn.row_factory = sqlite3.Row
            try:
                # Enable pragmas
                conn.execute("PRAGMA journal_mode=WAL")
                conn.execute("PRAGMA foreign_keys=ON")

                cursor = conn.execute(
                    """
                    SELECT subject, operation_id, family_id, resource_id, action, payload_hash,
                           status, upstream_id, expires_at
                    FROM operation_receipts
                    WHERE subject = ? AND operation_id = ?
                    """,
                    (principal.subject, operation_id),
                )
                row = cursor.fetchone()
                if row is None:
                    return None

                return (dict(row), self._clock.now())
            finally:
                conn.close()

        result = await asyncio.to_thread(_get_receipt)
        if result is None:
            return None

        row, now = result

        # Check expiry
        expires_at_str = str(row["expires_at"])
        expires_at = datetime.fromisoformat(expires_at_str)
        if expires_at <= now:
            return None

        # Import here to avoid circular imports
        from familywall_mcp.models import OperationReceipt

        return OperationReceipt(
            subject=str(row["subject"]),
            family_id=str(row["family_id"]),
            resource_id=str(row["resource_id"]),
            action=row["action"],  # type: ignore[arg-type]
            operation_id=str(row["operation_id"]),
            payload_hash=str(row["payload_hash"]),
            status=row["status"],  # type: ignore[arg-type]
            upstream_id=str(row["upstream_id"]) if row["upstream_id"] else None,
            expires_at=expires_at,
        )

    async def put(self, receipt: OperationReceipt) -> None:
        """Store or update a receipt (upsert).

        If a receipt with the same (subject, operation_id) key already exists,
        it is replaced with the new values.

        Args:
            receipt: The OperationReceipt to store.
        """

        def _put_receipt() -> None:
            # Store expires_at as ISO-8601 UTC string
            expires_at_str = receipt.expires_at.isoformat()

            conn = sqlite3.connect(str(self._database_path))
            try:
                # Enable pragmas
                conn.execute("PRAGMA journal_mode=WAL")
                conn.execute("PRAGMA foreign_keys=ON")

                conn.execute(
                    """
                    INSERT INTO operation_receipts
                    (subject, operation_id, family_id, resource_id, action, payload_hash,
                     status, upstream_id, expires_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(subject, operation_id) DO UPDATE SET
                        family_id = excluded.family_id,
                        resource_id = excluded.resource_id,
                        action = excluded.action,
                        payload_hash = excluded.payload_hash,
                        status = excluded.status,
                        upstream_id = excluded.upstream_id,
                        expires_at = excluded.expires_at
                    """,
                    (
                        receipt.subject,
                        receipt.operation_id,
                        receipt.family_id,
                        receipt.resource_id,
                        receipt.action,
                        receipt.payload_hash,
                        receipt.status,
                        receipt.upstream_id,
                        expires_at_str,
                    ),
                )
                conn.commit()
            finally:
                conn.close()

        async with self._lock:
            await asyncio.to_thread(_put_receipt)

    async def purge_expired(self) -> None:
        """Remove all expired receipts from the database.

        This method deletes rows where expires_at is in the past relative to
        the current time from the injected clock.
        """

        def _purge() -> None:
            now = self._clock.now()
            now_str = now.isoformat()

            conn = sqlite3.connect(str(self._database_path))
            try:
                # Enable pragmas
                conn.execute("PRAGMA journal_mode=WAL")
                conn.execute("PRAGMA foreign_keys=ON")

                conn.execute(
                    """
                    DELETE FROM operation_receipts
                    WHERE expires_at <= ?
                    """,
                    (now_str,),
                )
                conn.commit()
            finally:
                conn.close()

        async with self._lock:
            await asyncio.to_thread(_purge)

    async def aclose(self) -> None:
        """Close the repository and release any resources.

        For SQLite, this is a no-op since connections are opened and closed
        per operation. Included for protocol compatibility.
        """
        pass


def _migrate_legacy_table(conn: sqlite3.Connection) -> None:
    """Rebuild ``operation_receipts`` onto the new schema, in one transaction.

    Runs as ``BEGIN IMMEDIATE`` ... ``COMMIT``: create the new table, copy every
    row across (``list_id`` becomes ``resource_id``, ``action`` is set to
    ``'legacy'``), drop the old table, then rename. On any error the whole
    transaction is rolled back and the exception re-raised, so the legacy
    table and its rows are left exactly as they were.
    """
    conn.execute("BEGIN IMMEDIATE")
    try:
        # Re-check under the write lock: another process may have migrated the
        # table between the caller's schema check and this BEGIN.
        columns = {row[1] for row in conn.execute("PRAGMA table_info(operation_receipts)")}
        if "list_id" not in columns:
            conn.commit()
            return
        conn.execute(_MIGRATION_TABLE_DDL)
        conn.execute(
            """
            INSERT INTO operation_receipts_new
            (subject, operation_id, family_id, resource_id, action, payload_hash,
             status, upstream_id, expires_at)
            SELECT subject, operation_id, family_id, list_id, 'legacy', payload_hash,
                   status, upstream_id, expires_at
            FROM operation_receipts
            """
        )
        conn.execute("DROP TABLE operation_receipts")
        conn.execute("ALTER TABLE operation_receipts_new RENAME TO operation_receipts")
        conn.commit()
    except BaseException:
        conn.rollback()
        raise


class _DefaultClock:
    """Default clock using datetime.utcnow() for production use."""

    def now(self) -> datetime:
        """Return the current UTC datetime as a timezone-aware datetime."""
        return datetime.now(UTC)
