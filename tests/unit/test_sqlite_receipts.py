"""Tests for the SQLite-backed receipt repository."""

from __future__ import annotations

import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from tests.support.doubles import FixedClock

from familywall_mcp.models import OperationReceipt, Principal
from familywall_mcp.storage.sqlite import SqliteReceiptRepository

# Copied verbatim from the pre-migration `SqliteReceiptRepository.initialise()`, so
# migration tests build a legacy database exactly as an older release would have.
_LEGACY_TABLE_DDL = """
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


def _build_legacy_database(db_path: Path, rows: list[tuple[object, ...]]) -> None:
    """Create a database on the exact pre-migration schema, populated with `rows`.

    Each row is (subject, operation_id, family_id, list_id, payload_hash, status,
    upstream_id, expires_at).
    """
    conn = sqlite3.connect(str(db_path))
    try:
        conn.execute(_LEGACY_TABLE_DDL)
        conn.executemany(
            """
            INSERT INTO operation_receipts
            (subject, operation_id, family_id, list_id, payload_hash,
             status, upstream_id, expires_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            rows,
        )
        conn.commit()
    finally:
        conn.close()


class TestSqliteReceiptRepository:
    """Tests for SqliteReceiptRepository."""

    @pytest.fixture
    def clock(self) -> FixedClock:
        """Fixed clock for testing."""
        return FixedClock(datetime(2026, 1, 2, 3, 4, 5, tzinfo=UTC))

    @pytest.fixture
    async def repository(self, tmp_path: Path, clock: FixedClock) -> SqliteReceiptRepository:
        """Create a fresh repository for each test."""
        db_path = tmp_path / "test.sqlite3"
        repo = SqliteReceiptRepository(db_path, clock=clock)
        await repo.initialise()
        yield repo
        await repo.aclose()

    @pytest.fixture
    def principal(self) -> Principal:
        return Principal(subject="user1")

    @pytest.fixture
    def receipt(self, clock: FixedClock) -> OperationReceipt:
        # Receipt that expires in the future
        return OperationReceipt(
            subject="user1",
            family_id="family123",
            resource_id="list456",
            action="list.add_item",
            operation_id="op001",
            payload_hash="hash123",
            status="pending",
            upstream_id=None,
            expires_at=clock.value + timedelta(hours=1),
        )

    # Criterion 1: put then get returns an equal receipt with timezone-aware expires_at
    async def test_put_then_get_returns_equal_receipt(
        self, repository: SqliteReceiptRepository, principal: Principal, receipt: OperationReceipt
    ) -> None:
        """Test that put and get work correctly."""
        await repository.put(receipt)
        retrieved = await repository.get(principal, receipt.operation_id)

        assert retrieved is not None
        assert retrieved.subject == receipt.subject
        assert retrieved.family_id == receipt.family_id
        assert retrieved.resource_id == receipt.resource_id
        assert retrieved.action == receipt.action
        assert retrieved.operation_id == receipt.operation_id
        assert retrieved.payload_hash == receipt.payload_hash
        assert retrieved.status == receipt.status
        assert retrieved.upstream_id == receipt.upstream_id
        assert retrieved.expires_at == receipt.expires_at

    async def test_expires_at_is_timezone_aware(
        self, repository: SqliteReceiptRepository, principal: Principal, receipt: OperationReceipt
    ) -> None:
        """Test that expires_at comes back as timezone-aware."""
        await repository.put(receipt)
        retrieved = await repository.get(principal, receipt.operation_id)

        assert retrieved is not None
        assert retrieved.expires_at.tzinfo is not None
        assert retrieved.expires_at.tzinfo == UTC

    # Criterion 2: Durability test - write, close, reopen, read back
    async def test_durability_write_close_reopen_read(
        self, tmp_path: Path, clock: FixedClock
    ) -> None:
        """Test that receipts survive a close and reopen (durability)."""
        db_path = tmp_path / "durable.sqlite3"

        # Create and write
        repo1 = SqliteReceiptRepository(db_path, clock=clock)
        await repo1.initialise()

        receipt = OperationReceipt(
            subject="user1",
            family_id="family123",
            resource_id="list456",
            action="list.add_item",
            operation_id="op001",
            payload_hash="hash123",
            status="pending",
            upstream_id=None,
            expires_at=clock.value + timedelta(hours=1),
        )
        await repo1.put(receipt)
        await repo1.aclose()

        # Reopen and read
        repo2 = SqliteReceiptRepository(db_path, clock=clock)
        await repo2.initialise()
        principal = Principal(subject="user1")
        retrieved = await repo2.get(principal, "op001")
        await repo2.aclose()

        assert retrieved is not None
        assert retrieved.operation_id == "op001"
        assert retrieved.subject == "user1"

    # Criterion 3: Tenant isolation - same operation_id under different subjects
    async def test_tenant_isolation_same_operation_id_different_subjects(
        self, repository: SqliteReceiptRepository, clock: FixedClock
    ) -> None:
        """Test that user A's operation_id does not resolve to user B's receipt."""
        receipt_user1 = OperationReceipt(
            subject="user1",
            family_id="family123",
            resource_id="list456",
            action="list.add_item",
            operation_id="op001",
            payload_hash="hash123",
            status="pending",
            upstream_id=None,
            expires_at=clock.value + timedelta(hours=1),
        )
        receipt_user2 = OperationReceipt(
            subject="user2",
            family_id="family456",
            resource_id="list789",
            action="list.add_item",
            operation_id="op001",  # Same operation_id
            payload_hash="hash456",
            status="succeeded",
            upstream_id="upstream123",
            expires_at=clock.value + timedelta(hours=2),
        )

        await repository.put(receipt_user1)
        await repository.put(receipt_user2)

        principal1 = Principal(subject="user1")
        principal2 = Principal(subject="user2")

        retrieved1 = await repository.get(principal1, "op001")
        retrieved2 = await repository.get(principal2, "op001")

        assert retrieved1 is not None
        assert retrieved2 is not None
        assert retrieved1.subject == "user1"
        assert retrieved2.subject == "user2"
        assert retrieved1.family_id == "family123"
        assert retrieved2.family_id == "family456"
        assert retrieved1.status == "pending"
        assert retrieved2.status == "succeeded"

    # Criterion 4: Upsert - put twice with same key
    async def test_upsert_same_key_updates(
        self, repository: SqliteReceiptRepository, principal: Principal, clock: FixedClock
    ) -> None:
        """Test that put twice with same key upserts."""
        receipt1 = OperationReceipt(
            subject="user1",
            family_id="family123",
            resource_id="list456",
            action="list.add_item",
            operation_id="op001",
            payload_hash="hash123",
            status="pending",
            upstream_id=None,
            expires_at=clock.value + timedelta(hours=1),
        )
        receipt2 = OperationReceipt(
            subject="user1",
            family_id="family123",
            resource_id="list456",
            action="list.add_item",
            operation_id="op001",
            payload_hash="hash456",  # Changed
            status="succeeded",  # Changed
            upstream_id="upstream123",  # Changed
            expires_at=clock.value + timedelta(hours=2),  # Changed
        )

        await repository.put(receipt1)
        await repository.put(receipt2)

        retrieved = await repository.get(principal, "op001")
        assert retrieved is not None
        assert retrieved.payload_hash == "hash456"
        assert retrieved.status == "succeeded"
        assert retrieved.upstream_id == "upstream123"

    # Criterion 5: Pending receipt readable after simulated crash
    async def test_pending_receipt_survives_restart(
        self, tmp_path: Path, clock: FixedClock
    ) -> None:
        """Test that a pending receipt is readable after a simulated crash."""
        db_path = tmp_path / "crash.sqlite3"

        repo1 = SqliteReceiptRepository(db_path, clock=clock)
        await repo1.initialise()

        receipt = OperationReceipt(
            subject="user1",
            family_id="family123",
            resource_id="list456",
            action="list.add_item",
            operation_id="op001",
            payload_hash="hash123",
            status="pending",
            upstream_id=None,
            expires_at=clock.value + timedelta(hours=1),
        )
        await repo1.put(receipt)
        await repo1.aclose()

        # Simulate crash by creating new repo
        repo2 = SqliteReceiptRepository(db_path, clock=clock)
        await repo2.initialise()
        principal = Principal(subject="user1")
        retrieved = await repo2.get(principal, "op001")
        await repo2.aclose()

        assert retrieved is not None
        assert retrieved.status == "pending"

    # Criterion 6: Expired receipt reads as None
    async def test_expired_receipt_returns_none(
        self, repository: SqliteReceiptRepository, principal: Principal, clock: FixedClock
    ) -> None:
        """Test that an expired receipt is treated as absent."""
        # Create a receipt that expires in the past
        receipt = OperationReceipt(
            subject="user1",
            family_id="family123",
            resource_id="list456",
            action="list.add_item",
            operation_id="op001",
            payload_hash="hash123",
            status="pending",
            upstream_id=None,
            expires_at=clock.value - timedelta(hours=1),  # Already expired
        )
        await repository.put(receipt)

        retrieved = await repository.get(principal, "op001")
        assert retrieved is None

    # Criterion 7: purge_expired removes expired rows and leaves live ones
    async def test_purge_expired_removes_expired_keeps_live(
        self, repository: SqliteReceiptRepository, principal: Principal, clock: FixedClock
    ) -> None:
        """Test that purge_expired removes expired rows and leaves live ones."""
        receipt_expired = OperationReceipt(
            subject="user1",
            family_id="family123",
            resource_id="list456",
            action="list.add_item",
            operation_id="op001",
            payload_hash="hash123",
            status="pending",
            upstream_id=None,
            expires_at=clock.value - timedelta(hours=1),  # Expired
        )
        receipt_live = OperationReceipt(
            subject="user1",
            family_id="family123",
            resource_id="list456",
            action="list.add_item",
            operation_id="op002",
            payload_hash="hash456",
            status="pending",
            upstream_id=None,
            expires_at=clock.value + timedelta(hours=1),  # Not expired
        )

        await repository.put(receipt_expired)
        await repository.put(receipt_live)

        # Purge expired
        await repository.purge_expired()

        # Expired should be gone
        retrieved_expired = await repository.get(principal, "op001")
        assert retrieved_expired is None

        # Live should still be there
        retrieved_live = await repository.get(principal, "op002")
        assert retrieved_live is not None
        assert retrieved_live.operation_id == "op002"

    # Criterion 8: initialise twice on existing file preserves rows
    async def test_initialise_twice_preserves_rows(self, tmp_path: Path, clock: FixedClock) -> None:
        """Test that initialise() is idempotent and preserves rows."""
        db_path = tmp_path / "idempotent.sqlite3"

        repo = SqliteReceiptRepository(db_path, clock=clock)
        await repo.initialise()

        receipt = OperationReceipt(
            subject="user1",
            family_id="family123",
            resource_id="list456",
            action="list.add_item",
            operation_id="op001",
            payload_hash="hash123",
            status="pending",
            upstream_id=None,
            expires_at=clock.value + timedelta(hours=1),
        )
        await repo.put(receipt)

        # Call initialise again
        await repo.initialise()

        # Row should still be there
        principal = Principal(subject="user1")
        retrieved = await repo.get(principal, "op001")
        assert retrieved is not None
        assert retrieved.operation_id == "op001"

    # Criterion 9: Concurrent puts for different operation_ids all land
    async def test_concurrent_puts_different_keys(
        self, repository: SqliteReceiptRepository, principal: Principal, clock: FixedClock
    ) -> None:
        """Test that concurrent puts for different keys all land."""
        import asyncio

        receipts = [
            OperationReceipt(
                subject="user1",
                family_id="family123",
                resource_id="list456",
                action="list.add_item",
                operation_id=f"op{i:03d}",
                payload_hash=f"hash{i}",
                status="pending",
                upstream_id=None,
                expires_at=clock.value + timedelta(hours=1),
            )
            for i in range(10)
        ]

        # Put all concurrently
        await asyncio.gather(*(repository.put(r) for r in receipts))

        # All should be readable
        for i in range(10):
            retrieved = await repository.get(principal, f"op{i:03d}")
            assert retrieved is not None
            assert retrieved.operation_id == f"op{i:03d}"

    # Criterion 10: Concurrent puts for same key do not raise and leave one row
    async def test_concurrent_puts_same_key_leaves_one_row(
        self, repository: SqliteReceiptRepository, principal: Principal, clock: FixedClock
    ) -> None:
        """Test that concurrent puts for same key do not raise and leave one row."""
        import asyncio

        receipts = [
            OperationReceipt(
                subject="user1",
                family_id="family123",
                resource_id="list456",
                action="list.add_item",
                operation_id="op001",
                payload_hash=f"hash{i}",
                status="pending",
                upstream_id=None,
                expires_at=clock.value + timedelta(hours=1),
            )
            for i in range(5)
        ]

        # Put all concurrently with same key
        await asyncio.gather(*(repository.put(r) for r in receipts))

        # Should have exactly one row (the last one wins, but we just care it's one)
        retrieved = await repository.get(principal, "op001")
        assert retrieved is not None
        assert retrieved.operation_id == "op001"

        # Verify by checking the database directly
        def _count_rows() -> int:
            conn = sqlite3.connect(str(repository._database_path))
            try:
                cursor = conn.execute(
                    """
                    SELECT COUNT(*)
                    FROM operation_receipts
                    WHERE subject = ? AND operation_id = ?
                    """,
                    ("user1", "op001"),
                )
                return cursor.fetchone()[0]
            finally:
                conn.close()

        count = await asyncio.to_thread(_count_rows)
        assert count == 1

    # Criterion 11: Invalid status cannot be written (CHECK constraint)
    async def test_invalid_status_raises(
        self, repository: SqliteReceiptRepository, clock: FixedClock
    ) -> None:
        """Test that an invalid status cannot be written (CHECK constraint)."""

        # Test by trying to insert directly into the database with an invalid status
        def _insert_invalid() -> None:
            conn = sqlite3.connect(str(repository._database_path))
            try:
                conn.execute(
                    """
                    INSERT INTO operation_receipts
                    (subject, operation_id, family_id, resource_id, action, payload_hash,
                     status, upstream_id, expires_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        "user1",
                        "op001",
                        "family123",
                        "list456",
                        "list.add_item",
                        "hash123",
                        "invalid_status",  # Invalid
                        None,
                        clock.value.isoformat(),
                    ),
                )
                conn.commit()
            finally:
                conn.close()

        import asyncio

        with pytest.raises(sqlite3.IntegrityError):
            await asyncio.to_thread(_insert_invalid)

    # Criterion 12, R5: Database file is created with mode 0o600
    async def test_database_file_mode_0o600(self, tmp_path: Path, clock: FixedClock) -> None:
        """R5: a new database file is created 0600."""
        db_path = tmp_path / "mode_test.sqlite3"
        repo = SqliteReceiptRepository(db_path, clock=clock)
        await repo.initialise()

        # Check file exists and has correct mode
        assert db_path.exists()
        mode = db_path.stat().st_mode & 0o777
        assert mode == 0o600

    # Criterion 13: Repository satisfies the ReceiptRepository protocol
    async def test_protocol_compliance(self, repository: SqliteReceiptRepository) -> None:
        """Test that the repository implements the ReceiptRepository protocol."""
        # Import the protocol
        from familywall_mcp.interfaces import ReceiptRepository

        # Check that the repository is assignable to the protocol
        _repo: ReceiptRepository = repository
        assert _repo is not None


class TestSchemaAndMigration:
    """Fresh-schema creation and in-place migration from the legacy `list_id` schema."""

    @pytest.fixture
    def clock(self) -> FixedClock:
        return FixedClock(datetime(2026, 1, 2, 3, 4, 5, tzinfo=UTC))

    async def test_fresh_database_has_new_schema(self, tmp_path: Path, clock: FixedClock) -> None:
        """R1: a fresh database gets `resource_id`/`action`, and the CHECK accepts 'rejected'."""
        import asyncio

        db_path = tmp_path / "fresh.sqlite3"
        repo = SqliteReceiptRepository(db_path, clock=clock)
        await repo.initialise()

        def _inspect_and_insert_rejected() -> set[str]:
            conn = sqlite3.connect(str(db_path))
            try:
                columns = {row[1] for row in conn.execute("PRAGMA table_info(operation_receipts)")}
                # The CHECK constraint must accept 'rejected' without raising.
                conn.execute(
                    """
                    INSERT INTO operation_receipts
                    (subject, operation_id, family_id, resource_id, action, payload_hash,
                     status, upstream_id, expires_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        "user1",
                        "op-rejected",
                        "family1",
                        "calendar/1",
                        "calendar.create_event",
                        "hash1",
                        "rejected",
                        None,
                        clock.value.isoformat(),
                    ),
                )
                conn.commit()
                return columns
            finally:
                conn.close()

        columns = await asyncio.to_thread(_inspect_and_insert_rejected)
        assert "resource_id" in columns
        assert "action" in columns
        assert "list_id" not in columns
        await repo.aclose()

    async def test_migration_preserves_rows_field_for_field(
        self, tmp_path: Path, clock: FixedClock
    ) -> None:
        """R2: migrating a legacy database keeps every row, `list_id` becomes
        `resource_id`, and `action` is set to 'legacy'."""
        import asyncio

        db_path = tmp_path / "legacy.sqlite3"
        rows = [
            (
                "user1",
                "op-pending",
                "family1",
                "taskList/1",
                "hash-pending",
                "pending",
                None,
                "2099-01-01T00:00:00+00:00",
            ),
            (
                "user1",
                "op-succeeded",
                "family1",
                "taskList/2",
                "hash-succeeded",
                "succeeded",
                '{"outcome": "confirmed"}',
                "2099-01-02T00:00:00+00:00",
            ),
            (
                "user2",
                "op-unknown",
                "family2",
                "calendar/2",
                "hash-unknown",
                "unknown",
                None,
                "2099-01-03T00:00:00+00:00",
            ),
        ]
        await asyncio.to_thread(_build_legacy_database, db_path, rows)

        repo = SqliteReceiptRepository(db_path, clock=clock)
        await repo.initialise()

        def _read_all() -> list[tuple[object, ...]]:
            conn = sqlite3.connect(str(db_path))
            conn.row_factory = sqlite3.Row
            try:
                cursor = conn.execute(
                    """
                    SELECT subject, operation_id, family_id, resource_id, action, payload_hash,
                           status, upstream_id, expires_at
                    FROM operation_receipts
                    ORDER BY operation_id
                    """
                )
                return [tuple(row) for row in cursor.fetchall()]
            finally:
                conn.close()

        migrated = await asyncio.to_thread(_read_all)
        assert len(migrated) == 3

        by_op_id = {row[1]: row for row in migrated}
        for legacy_row in rows:
            subject, operation_id, family_id, list_id, payload_hash, status = legacy_row[:6]
            upstream_id, expires_at = legacy_row[6:]
            row = by_op_id[operation_id]
            assert row == (
                subject,
                operation_id,
                family_id,
                list_id,  # list_id's value now lives in resource_id
                "legacy",  # action
                payload_hash,
                status,
                upstream_id,
                expires_at,
            )
        await repo.aclose()

    async def test_second_initialise_is_noop(self, tmp_path: Path, clock: FixedClock) -> None:
        """R3: a second `initialise()` leaves the schema and row count unchanged."""
        import asyncio

        db_path = tmp_path / "legacy.sqlite3"
        expires_at = "2099-01-01T00:00:00+00:00"
        rows = [("user1", "op1", "family1", "taskList/1", "hash1", "pending", None, expires_at)]
        await asyncio.to_thread(_build_legacy_database, db_path, rows)

        repo = SqliteReceiptRepository(db_path, clock=clock)
        await repo.initialise()
        await repo.initialise()  # second call: must be a no-op

        def _row_count_and_columns() -> tuple[int, set[str]]:
            conn = sqlite3.connect(str(db_path))
            try:
                columns = {row[1] for row in conn.execute("PRAGMA table_info(operation_receipts)")}
                (count,) = conn.execute("SELECT COUNT(*) FROM operation_receipts").fetchone()
                return count, columns
            finally:
                conn.close()

        count, columns = await asyncio.to_thread(_row_count_and_columns)
        assert count == 1
        assert "resource_id" in columns
        assert "list_id" not in columns
        await repo.aclose()

    async def test_migration_failure_leaves_legacy_table_intact(
        self, tmp_path: Path, clock: FixedClock
    ) -> None:
        """R4: a failure mid-migration leaves the legacy table and its rows intact,
        and raises. Forced here by a pre-existing conflicting
        `operation_receipts_new` table."""
        import asyncio

        db_path = tmp_path / "legacy.sqlite3"
        expires_at = "2099-01-01T00:00:00+00:00"
        original_row = (
            "user1",
            "op1",
            "family1",
            "taskList/1",
            "hash1",
            "pending",
            None,
            expires_at,
        )
        await asyncio.to_thread(_build_legacy_database, db_path, [original_row])

        def _plant_conflicting_table() -> None:
            conn = sqlite3.connect(str(db_path))
            try:
                conn.execute("CREATE TABLE operation_receipts_new (unrelated TEXT)")
                conn.commit()
            finally:
                conn.close()

        await asyncio.to_thread(_plant_conflicting_table)

        repo = SqliteReceiptRepository(db_path, clock=clock)
        with pytest.raises(sqlite3.OperationalError):
            await repo.initialise()

        def _read_legacy() -> list[tuple[object, ...]]:
            conn = sqlite3.connect(str(db_path))
            try:
                # The legacy table (with its original `list_id` column) must still
                # be readable under its original name and schema.
                cursor = conn.execute(
                    "SELECT subject, operation_id, family_id, list_id, payload_hash, "
                    "status, upstream_id, expires_at FROM operation_receipts"
                )
                return cursor.fetchall()
            finally:
                conn.close()

        surviving_rows = await asyncio.to_thread(_read_legacy)
        assert surviving_rows == [original_row]
        await repo.aclose()

    async def test_rejected_status_round_trips(self, tmp_path: Path, clock: FixedClock) -> None:
        """R6: 'rejected' round-trips through the SQLite store."""
        db_path = tmp_path / "rejected.sqlite3"
        repo = SqliteReceiptRepository(db_path, clock=clock)
        await repo.initialise()

        principal = Principal(subject="user1")
        rejected = OperationReceipt(
            subject="user1",
            family_id="family1",
            resource_id="calendar/1",
            action="calendar.create_event",
            operation_id="op-rejected",
            payload_hash="hash1",
            status="rejected",
            upstream_id='{"code": "upstream_rejected", "message": "no", "recovery": "retry"}',
            expires_at=clock.value + timedelta(hours=1),
        )
        await repo.put(rejected)
        retrieved = await repo.get(principal, "op-rejected")

        assert retrieved is not None
        assert retrieved.status == "rejected"
        assert retrieved.upstream_id == rejected.upstream_id
        await repo.aclose()

    async def test_tenant_isolation_after_migration(
        self, tmp_path: Path, clock: FixedClock
    ) -> None:
        """R7: (subject, operation_id) isolation still holds after migration."""
        import asyncio

        db_path = tmp_path / "legacy.sqlite3"
        rows = [
            (
                "user1",
                "op-shared",
                "family1",
                "taskList/1",
                "hash-user1",
                "succeeded",
                '{"outcome": "confirmed"}',
                "2099-01-01T00:00:00+00:00",
            ),
            (
                "user2",
                "op-shared",  # same operation_id, different subject
                "family2",
                "taskList/2",
                "hash-user2",
                "pending",
                None,
                "2099-01-02T00:00:00+00:00",
            ),
        ]
        await asyncio.to_thread(_build_legacy_database, db_path, rows)

        repo = SqliteReceiptRepository(db_path, clock=clock)
        await repo.initialise()

        retrieved1 = await repo.get(Principal(subject="user1"), "op-shared")
        retrieved2 = await repo.get(Principal(subject="user2"), "op-shared")

        assert retrieved1 is not None
        assert retrieved2 is not None
        assert retrieved1.resource_id == "taskList/1"
        assert retrieved1.status == "succeeded"
        assert retrieved2.resource_id == "taskList/2"
        assert retrieved2.status == "pending"
        await repo.aclose()

    async def test_purge_expired_after_migration(self, tmp_path: Path, clock: FixedClock) -> None:
        """R14: `purge_expired` still works after migration."""
        import asyncio

        db_path = tmp_path / "legacy.sqlite3"
        expired_at = (clock.value - timedelta(hours=1)).isoformat()
        live_at = (clock.value + timedelta(hours=1)).isoformat()
        rows = [
            ("user1", "op-expired", "family1", "taskList/1", "hash1", "pending", None, expired_at),
            ("user1", "op-live", "family1", "taskList/2", "hash2", "pending", None, live_at),
        ]
        await asyncio.to_thread(_build_legacy_database, db_path, rows)

        repo = SqliteReceiptRepository(db_path, clock=clock)
        await repo.initialise()
        await repo.purge_expired()

        principal = Principal(subject="user1")
        assert await repo.get(principal, "op-expired") is None
        live = await repo.get(principal, "op-live")
        assert live is not None
        assert live.operation_id == "op-live"
        await repo.aclose()


def test_migration_rechecks_schema_under_the_write_lock(tmp_path: Path) -> None:
    """A migration that loses the race to another process is a no-op, not an error.

    Both processes can see the legacy schema before either takes the write lock;
    the loser must re-check once it holds the lock instead of rebuilding a table
    that has already been migrated.
    """
    from familywall_mcp.storage.sqlite import _migrate_legacy_table

    db_path = tmp_path / "raced.sqlite3"
    _build_legacy_database(
        db_path,
        [
            (
                "user1",
                "op-1",
                "fam1",
                "list1",
                "hash1",
                "succeeded",
                None,
                "2099-01-01T00:00:00+00:00",
            )
        ],
    )
    winner = sqlite3.connect(str(db_path))
    try:
        _migrate_legacy_table(winner)
    finally:
        winner.close()

    loser = sqlite3.connect(str(db_path))
    try:
        _migrate_legacy_table(loser)
        columns = {row[1] for row in loser.execute("PRAGMA table_info(operation_receipts)")}
        rows = loser.execute("SELECT resource_id, action FROM operation_receipts").fetchall()
    finally:
        loser.close()

    assert "resource_id" in columns and "list_id" not in columns
    assert rows == [("list1", "legacy")]
