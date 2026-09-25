# Task 09D — Generalized durable receipts and an in-place SQLite migration

Slice D of the [brief 09 implementation plan](09-implementation-plan.md).
Offline only: synthetic data, no live network, no credentials.

## Objective

Make the durable operation receipt independent of lists: rename `list_id` to
`resource_id`, add an `action` field, and add a `rejected` status, migrating
existing SQLite databases in place without losing a row. Calendar creation
starts recording definite refusals as `rejected`.

## Scope

- Files allowed to change:
  - `src/familywall_mcp/models.py` (`OperationReceipt`)
  - `src/familywall_mcp/interfaces.py` (only if a signature must change)
  - `src/familywall_mcp/storage/memory.py`, `src/familywall_mcp/storage/sqlite.py`
  - `src/familywall_mcp/services/lists.py` (receipt fields and replay action check only)
  - `src/familywall_mcp/services/calendar.py` (`create_event` receipt fields, action check, `rejected`)
  - tests: `tests/unit/test_receipts.py`, `tests/unit/test_sqlite_receipts.py`,
    `tests/unit/test_list_service.py`, `tests/unit/test_calendar_create.py`,
    `tests/unit/test_tools.py` and `tests/support/*`, only where receipt fields appear
  - docs: `README.md` and `docs/synology-nas.md` (one upgrade note each), `docs/PROGRESS.md`
    (one row), `docs/briefs/09-implementation-plan.md` (mark D done and record decision E2),
    new `docs/handoffs/09d-receipts-migration.md`
- Out of scope: changing any payload hash; adding `rejected` to the list flows; the OAuth store;
  tool arguments or outputs.

## Binding decisions

- **E1 — Model.** `OperationReceipt` fields: `subject`, `family_id`,
  `resource_id` (min length 1; replaces `list_id`),
  `action: Literal["list.add_item", "list.set_checked", "calendar.create_event", "legacy"]`,
  `operation_id`, `payload_hash`,
  `status: Literal["pending", "succeeded", "unknown", "rejected"]`, `upstream_id`, `expires_at`.
- **E2 — Schema detection, not `user_version`.** The database file is shared with
  `OAuthSqliteStore`: `server.py` passes `config.database_path` to both. A database-wide
  `PRAGMA user_version` would couple the two stores. Detect the receipts schema with
  `PRAGMA table_info(operation_receipts)`:
  - no table: create the new schema;
  - a `list_id` column: migrate;
  - a `resource_id` column: do nothing.
- **E3 — Migration.** Run it in one transaction (`BEGIN IMMEDIATE` ... `COMMIT`):
  1. Create `operation_receipts_new` with the new schema. Primary key `(subject, operation_id)`;
     status CHECK over the four values; `action TEXT NOT NULL`.
  2. `INSERT ... SELECT` from the old table, with `list_id` becoming `resource_id` and `action`
     set to `'legacy'`.
  3. `DROP TABLE operation_receipts`.
  4. `ALTER TABLE operation_receipts_new RENAME TO operation_receipts`.

  On any error, roll back and re-raise, so there is never a half-migrated table. A second
  `initialise()` is a no-op. File permissions keep today's behaviour: a new file is created
  0600, and an existing file is left as it is.
- **E4 — List service.** Receipts carry `resource_id=<list id>` and
  `action="list.add_item"` / `"list.set_checked"`. **Payload hashes do not change**, so receipts
  written before the upgrade (under 24 hours old) still replay. On replay, an existing receipt whose
  `action` is neither the expected action nor `"legacy"` is an `operation_id_conflict`, raised
  with zero upstream calls. The rest of the replay logic is unchanged.
- **E5 — Calendar create.**
  - **Fields:** receipts carry `resource_id=<family calendar id>` and
    `action="calendar.create_event"`. The hash is unchanged (still tagged with the endpoint).
  - **Action check on replay:** same as E4, so the action must be `"calendar.create_event"` or
    `"legacy"`.
  - **Refusals:** on `UpstreamRejectedError` or `AuthenticationError` from `evtcreate`, record
    `status="rejected"` with `upstream_id` set to JSON `{"code", "message", "recovery"}` taken from
    `exc.info`, then re-raise. This replaces today's "record unknown on refusal" behaviour, so update
    that test.
  - **Replaying a `rejected` receipt:** raise
    `FamilyWallError(ErrorInfo(code, message, recovery))` rebuilt from that JSON, or a plain
    `UpstreamRejectedError()` if the JSON is unreadable. Either way, zero upstream calls.
- **E6 — Docs.** Add one line to both `README.md` and `docs/synology-nas.md`: back up the data
  volume before upgrading; this release migrates the receipts table in place, and older images
  cannot read it.

## Required tests (each must exist and pass)

- **R1** A fresh database gets the new schema: `resource_id` and `action` columns, and the CHECK
  accepts `rejected`.
- **R2** Migration from a legacy database created with the **exact old DDL** (copy today's
  `CREATE TABLE` string into the test). Rows with statuses pending, succeeded and unknown are
  preserved field for field, with `list_id` becoming `resource_id` and `action` set to `legacy`.
- **R3** A second `initialise()` leaves the schema and row count unchanged.
- **R4** Failure mid-migration (for example, a pre-existing conflicting `operation_receipts_new`
  table) leaves the legacy table and its rows intact and raises.
- **R5** A new database file is created 0600.
- **R6** `rejected` round-trips in both the SQLite and in-memory stores.
- **R7** `(subject, operation_id)` isolation still holds after migration.
- **R8** List `add_item`: a migrated `legacy` receipt with the old hash replays its stored outcome
  with zero calls.
- **R9** List: an existing receipt with `action="calendar.create_event"` and the same operation ID
  is a conflict, with zero calls.
- **R10** List writes record the correct `action` and `resource_id`.
- **R11** Calendar: a refused create (both `UpstreamRejectedError` and `SessionExpiredError`) records
  `rejected` with the error JSON. A replay raises the same error code with zero calls.
- **R12** Calendar: an existing receipt with a `list.*` action is a conflict, with zero calls.
- **R13** Calendar: a `legacy` receipt with a matching hash replays its stored result.
- **R14** `purge_expired` still works after migration.

## Validation

Run from this worktree: `uv sync --frozen --group dev`, `uv run ruff check .`,
`uv run ruff format --check .`, `uv run mypy src`, `uv run pytest -m 'not live'`, `uv build`.
`scripts/check` may fail only at its detect-secrets step, on findings that exist before this
slice (fixed separately in PR #8); report that rather than editing `.secrets.baseline`.

## Security and privacy

Synthetic data only. Never read `.env*` files, credential stores or any real database file.
Test databases live in pytest `tmp_path`.

## Handoff target

Slice E, `set_calendar_event_attendees`. It uses `resource_id=<event id>` and the `rejected` status.
