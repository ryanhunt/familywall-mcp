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
        """Create the database schema if it does not exist (idempotent).

        This method is safe to call multiple times. It enables WAL and foreign
        key constraints, then creates the operation_receipts table if needed.
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

                # Create table if it doesn't exist
                conn.execute(
                    """
                    CREATE TABLE IF NOT EXISTS operation_receipts (
                        subject       TEXT NOT NULL,
                        operation_id  TEXT NOT NULL,
                        family_id     TEXT NOT NULL,
                        list_id       TEXT NOT NULL,
                        payload_hash  TEXT NOT NULL,
                        status        TEXT NOT NULL CHECK (
                            status IN ('pending', 'succeeded', 'unknown')
                        ),
                        upstream_id   TEXT,
                        expires_at    TEXT NOT NULL,
                        PRIMARY KEY (subject, operation_id)
                    )
                    """
                )
                conn.commit()
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
                    SELECT subject, operation_id, family_id, list_id, payload_hash,
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
            list_id=str(row["list_id"]),
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
                    (subject, operation_id, family_id, list_id, payload_hash,
                     status, upstream_id, expires_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(subject, operation_id) DO UPDATE SET
                        family_id = excluded.family_id,
                        list_id = excluded.list_id,
                        payload_hash = excluded.payload_hash,
                        status = excluded.status,
                        upstream_id = excluded.upstream_id,
                        expires_at = excluded.expires_at
                    """,
                    (
                        receipt.subject,
                        receipt.operation_id,
                        receipt.family_id,
                        receipt.list_id,
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


class _DefaultClock:
    """Default clock using datetime.utcnow() for production use."""

    def now(self) -> datetime:
        """Return the current UTC datetime as a timezone-aware datetime."""
        return datetime.now(UTC)
