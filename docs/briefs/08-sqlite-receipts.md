# Task 08 — Durable receipts in SQLite

Owner: delegated. **Lead reviews durability and concurrency, then runs a restart
check.**

## Why this exists

`storage/memory.py` holds receipts in a dict, so they die with the process. That
is not a theoretical gap: during the live write check an agent's harness spawned
a fresh server per tool call, each got a fresh repository, and **five duplicate
items were created in a real family's list** because replay protection could not
see the earlier receipts.

Receipts exist to make a non-idempotent `taskcreate` safe. They are only worth
anything if they survive a restart.

## File boundary

Create:
- `src/familywall_mcp/storage/sqlite.py`
- `tests/unit/test_sqlite_receipts.py`

Edit:
- `src/familywall_mcp/server.py` — choose the repository at startup
- `src/familywall_mcp/storage/__init__.py` — export the new class

**Do not edit** `pyproject.toml`, `uv.lock` (add no dependency — Python ships
`sqlite3`), `models.py`, `errors.py`, `interfaces.py`, `config.py`,
`storage/memory.py`, anything under `familywall/`, `services/`, `tools/`,
`tests/conftest.py`, existing tests, or any doc but your handoff.

`storage/memory.py` stays exactly as it is — unit tests elsewhere depend on it.

## What to build

`SqliteReceiptRepository`, implementing the existing `ReceiptRepository` protocol
in `interfaces.py`. Do not change that protocol.

```python
class SqliteReceiptRepository:
    def __init__(self, database_path: str | Path) -> None: ...
    async def initialise(self) -> None: ...        # create schema, idempotent
    async def get(self, principal: Principal, operation_id: str) -> OperationReceipt | None: ...
    async def put(self, receipt: OperationReceipt) -> None: ...
    async def aclose(self) -> None: ...
```

Schema, matching the existing `OperationReceipt` model exactly — do not invent
fields and do not widen `status`:

```sql
CREATE TABLE IF NOT EXISTS operation_receipts (
    subject       TEXT NOT NULL,
    operation_id  TEXT NOT NULL,
    family_id     TEXT NOT NULL,
    list_id       TEXT NOT NULL,
    payload_hash  TEXT NOT NULL,
    status        TEXT NOT NULL CHECK (status IN ('pending','succeeded','unknown')),
    upstream_id   TEXT,
    expires_at    TEXT NOT NULL,
    PRIMARY KEY (subject, operation_id)
);
```

Requirements:

- **`(subject, operation_id)` is the primary key.** Receipts are scoped per
  subject: user A's operation id must never resolve to user B's receipt. Test it.
- Enable **WAL** (`PRAGMA journal_mode=WAL`) and **foreign keys**
  (`PRAGMA foreign_keys=ON`) on every connection.
- `put` is an **upsert** (`INSERT ... ON CONFLICT(subject, operation_id) DO
  UPDATE`), because the flow writes `pending` and then updates it.
- `initialise` is idempotent: running it twice on an existing database is safe
  and preserves rows.
- Store `expires_at` as an ISO-8601 UTC string; read it back as a
  **timezone-aware** datetime. A naive datetime coming back out is a bug.
- **`sqlite3` is blocking.** Do not call it directly on the event loop — wrap
  every database call with `asyncio.to_thread` (or an equivalent executor). Hold
  an `asyncio.Lock` around write paths so concurrent `put`s cannot interleave.
- Create the database file's parent directory if missing, and set the file mode
  to `0o600` — it holds operation metadata for a real family.
- Never log or stringify receipt contents.

### Expiry

`OperationReceipt` already carries `expires_at`. `get` must treat an expired
receipt as **absent** (return `None`), so a stale receipt cannot suppress a
legitimate new write. Do not delete on read; add a `purge_expired()` method the
server may call at startup, and test it separately. Take the current time from
an injected clock, not `datetime.now` inside the query — the existing tests use
a fake clock and yours must be able to as well.

## Server wiring

In `server.py`, build `SqliteReceiptRepository` from
`config.database_path`, call `initialise()` and `purge_expired()` at startup,
and `aclose()` in the existing shutdown `finally` block. `config.database_path`
already exists with a default — do not add a setting.

## Tests — the acceptance criteria

No network. Use `tmp_path` for real database files; do **not** use `:memory:`
for the durability tests, since the whole point is that data outlives a process.

1. `put` then `get` returns an equal receipt, with a timezone-aware `expires_at`.
2. **Durability: write a receipt, `aclose()`, construct a NEW repository over the
   same file, and read the receipt back.** This is the test that would have
   prevented the duplicate items.
3. Same `operation_id` under two different subjects returns two distinct
   receipts; subject A never sees subject B's.
4. `put` twice with the same key upserts — one row, latest values, no error.
5. A `pending` receipt is readable after a simulated crash (new repository over
   the same file) and still reports `pending`.
6. An expired receipt reads as `None`.
7. `purge_expired()` removes expired rows and leaves live ones.
8. `initialise()` twice on an existing file preserves existing rows.
9. Concurrent `put`s for different operation ids all land — run them with
   `asyncio.gather` and assert every row is present.
10. Concurrent `put`s for the **same** key do not raise and leave exactly one row.
11. An invalid status cannot be written (the CHECK constraint holds).
12. The database file is created with mode `0o600`.
13. The repository satisfies the `ReceiptRepository` protocol — assert with an
    explicit type annotation so mypy checks it.

## Checks

```
uv run ruff check .
uv run ruff format .
uv run mypy src
uv run pytest -m 'not live'
scripts/check
```

The suite is at 286 tests and must stay green. `mypy` is strict, no `Any` in
public signatures. Add no dependency. Do NOT run anything against the live API.

## Handoff

Write `docs/handoffs/08-sqlite-receipts.md` from `docs/templates/handoff.md`.
State plainly any criterion you did not cover with a test and why.
