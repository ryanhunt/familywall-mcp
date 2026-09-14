# Handoff: Task 08 — Durable receipts in SQLite

## Outcome

The `SqliteReceiptRepository` class is now implemented, tested, and wired into the server. Receipts are now durably stored in SQLite with (subject, operation_id) as the primary key, ensuring tenant isolation. The in-memory repository remains untouched for backwards compatibility and existing tests.

The implementation includes:
- Durable receipt storage with WAL mode enabled
- Replay protection that survives process restarts
- Proper timezone handling (ISO-8601 storage, timezone-aware retrieval)
- Concurrent write safety with asyncio.Lock
- Tenant isolation (user A's operation_id never resolves to user B's receipt)
- Expiry handling via injected clock
- Database file permission enforcement (mode 0o600)

## Changed files

- `src/familywall_mcp/storage/sqlite.py`: New `SqliteReceiptRepository` class implementing the `ReceiptRepository` protocol. Wraps all database calls in `asyncio.to_thread` to avoid blocking the event loop. Holds an `asyncio.Lock` around write paths. Stores `expires_at` as ISO-8601 UTC strings and reads them back as timezone-aware datetimes.
- `tests/unit/test_sqlite_receipts.py`: New comprehensive test suite with 14 tests covering all 13 acceptance criteria plus protocol compliance.
- `src/familywall_mcp/storage/__init__.py`: Added exports for `InMemoryReceiptRepository` and `SqliteReceiptRepository`.
- `src/familywall_mcp/server.py`: Updated to use `SqliteReceiptRepository` instead of `InMemoryReceiptRepository`. Initializes the repository and purges expired receipts at startup. Closes the repository in the shutdown finally block.

## Acceptance evidence

1. **Put then get returns equal receipt with timezone-aware expires_at**: `test_put_then_get_returns_equal_receipt` and `test_expires_at_is_timezone_aware` verify this. Retrieved receipts match stored ones and `expires_at` is timezone-aware (tzinfo == UTC).

2. **Durability: write, close, reopen, read back**: `test_durability_write_close_reopen_read` demonstrates that a receipt written to disk survives the repository being closed and a new repository being created over the same file. This is the critical test that would have prevented the five duplicate items during the live write check.

3. **Tenant isolation**: `test_tenant_isolation_same_operation_id_different_subjects` verifies that the same `operation_id` under two different subjects returns two distinct receipts. User A never sees user B's receipt.

4. **Upsert behavior**: `test_upsert_same_key_updates` confirms that `put` with the same (subject, operation_id) key updates the row in place (using `INSERT ... ON CONFLICT ... DO UPDATE`).

5. **Pending receipt survives restart**: `test_pending_receipt_survives_restart` writes a `pending` receipt, restarts, and verifies it still reports `pending`. Matches the real flow where a status transitions from pending → succeeded/unknown.

6. **Expired receipt returns None**: `test_expired_receipt_returns_none` creates a receipt with `expires_at` in the past and confirms `get` returns `None`.

7. **Purge expired removes expired, keeps live**: `test_purge_expired_removes_expired_keeps_live` creates both expired and live receipts, calls `purge_expired()`, and confirms only the live one remains.

8. **Initialise is idempotent**: `test_initialise_twice_preserves_rows` calls `initialise()` twice on the same file and verifies rows are preserved.

9. **Concurrent puts for different keys all land**: `test_concurrent_puts_different_keys` uses `asyncio.gather` to put 10 receipts concurrently and verifies all are readable.

10. **Concurrent puts for same key leave one row**: `test_concurrent_puts_same_key_leaves_one_row` puts 5 receipts concurrently with the same (subject, operation_id) key and confirms exactly one row exists in the database.

11. **Invalid status raises**: `test_invalid_status_raises` attempts a direct SQL insert with an invalid status and confirms the CHECK constraint raises `sqlite3.IntegrityError`.

12. **Database file mode 0o600**: `test_database_file_mode_0o600` creates a database and confirms its file mode is exactly `0o600`.

13. **Protocol compliance**: `test_protocol_compliance` assigns the repository to a `ReceiptRepository` type annotation, forcing mypy strict mode to verify protocol compliance.

## Validation

All checks passed:

```
$ uv run ruff check .
All checks passed!

$ uv run ruff format .
90 files left unchanged

$ uv run mypy src
Success: no issues found in 25 source files

$ uv run pytest -m 'not live'
============================== 300 passed, 13 warnings in 1.11s =======================

$ scripts/check
All checks passed!
[... full output includes ruff, mypy, pytest, and uv build ...]
```

Test count: 300 tests (14 new SQLite receipt tests + 286 existing tests).

## Security and privacy review

- No credentials, tokens, cookies, real family data, or generated state in diffs, logs, or fixtures.
- Fixtures use synthetic data: `user1`, `user2`, `family123`, `list456`, `op001`, etc.
- Database file is created with mode `0o600` to protect receipt metadata.
- Receipt contents are never logged or stringified in code.
- Clock is injected for testing; the implementation never calls `datetime.now()` directly inside queries.

## Known limitations

None. All 13 criteria are covered by tests.

## Next bounded task

None. Task complete.
