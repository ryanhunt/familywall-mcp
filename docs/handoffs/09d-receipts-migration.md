# Handoff — 09D generalized receipts and in-place SQLite migration

## Outcome

`OperationReceipt` is no longer list-shaped: `list_id` is now `resource_id`
(any mutation's subject, not just a list), and a new `action` field
(`list.add_item` / `list.set_checked` / `calendar.create_event` / `legacy`)
records which tool wrote the receipt, so a key reused across tools is a
detected conflict rather than a silent replay. A new `rejected` status
records a definite upstream refusal so a replay raises the original error
instead of reporting `unknown`.

Existing SQLite databases (the pre-migration `list_id` schema) migrate to the
new schema in place, in a single transaction that leaves the legacy table and
its rows completely untouched on any failure. `create_calendar_event` now
records `rejected` (with the refusal's code/message/recovery) on
`UpstreamRejectedError` or `AuthenticationError`, instead of the previous
"record unknown on refusal" behaviour.

This is brief 09 slice D, done offline (synthetic data only, no live calls).
It unblocks slice E (`set_calendar_event_attendees`), which needs
`resource_id` and `rejected`.

## Changed files

- `src/familywall_mcp/models.py`: `OperationReceipt.list_id` → `resource_id`;
  added `action: Literal["list.add_item", "list.set_checked",
  "calendar.create_event", "legacy"]`; `status` gained `"rejected"`.
- `src/familywall_mcp/storage/sqlite.py`: schema detection via
  `PRAGMA table_info(operation_receipts)` (decision E2, see below) instead of
  `PRAGMA user_version`; `_migrate_legacy_table` rebuilds the table in one
  `BEGIN IMMEDIATE` ... `COMMIT` transaction, rolling back and re-raising on
  any error; `get`/`put` updated for the renamed/added columns.
- `src/familywall_mcp/services/lists.py`: `add_item` and `set_item_checked`
  write `resource_id`/`action` instead of `list_id`; both now reject a replay
  whose existing receipt has an action other than their own or `"legacy"`
  with `operation_id_conflict`, before any upstream call. Payload hashes are
  unchanged.
- `src/familywall_mcp/services/calendar.py`: `create_event` writes
  `resource_id`/`action="calendar.create_event"`; on `UpstreamRejectedError`
  or `AuthenticationError` it now records `status="rejected"` with
  `upstream_id` set to JSON `{"code", "message", "recovery"}` from
  `exc.info`, then re-raises (previously recorded `unknown`). `_replay_create`
  checks the receipt's `action` before its hash, and a `rejected` replay
  rebuilds and raises the original `FamilyWallError` (or a plain
  `UpstreamRejectedError` if the stored JSON is unreadable).
- `tests/unit/test_receipts.py`, `tests/unit/test_sqlite_receipts.py`,
  `tests/unit/test_list_service.py`, `tests/unit/test_calendar_create.py`:
  field renames throughout, plus the R1–R14 tests below (13 net new test
  cases: 423 → 436).
- `README.md`, `docs/synology-nas.md`: one-line "back up the data volume
  before upgrading" note each, right before the deploy command.
- `docs/PROGRESS.md`: one row recording this slice as done, offline.
- `docs/briefs/09-implementation-plan.md`: slice D marked done; decision E2
  recorded (see below), including in the "brief 09 stands" summary table.
- `docs/handoffs/09d-receipts-migration.md`: this file.

## Binding decision E2, as implemented

The brief's original plan (decision 2 in `09-implementation-plan.md`) said
migration would be "keyed on `PRAGMA user_version`". The task brief
(`docs/briefs/09d-receipts-migration.md`, decision E2) supersedes that: the
database file is shared with `OAuthSqliteStore` (`server.py` passes
`config.database_path` to both), so a database-wide `PRAGMA user_version`
would couple the two stores' schema histories. `initialise()` instead
inspects `PRAGMA table_info(operation_receipts)`: no table creates the new
schema directly; a `list_id` column present means the legacy schema, so it
migrates; a `resource_id` column present means nothing to do. This is
recorded in `docs/briefs/09-implementation-plan.md` next to the old text so
the discrepancy is not silently lost.

## Final `operation_receipts` schema

```sql
CREATE TABLE operation_receipts (
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
)
```

## Acceptance evidence (R1–R14)

- **R1** fresh schema has `resource_id`/`action`, CHECK accepts `rejected`:
  `tests/unit/test_sqlite_receipts.py::TestSchemaAndMigration::test_fresh_database_has_new_schema`.
- **R2** migration from the exact legacy DDL preserves every row field for
  field, `list_id` → `resource_id`, `action` → `"legacy"`:
  `TestSchemaAndMigration::test_migration_preserves_rows_field_for_field`.
- **R3** a second `initialise()` is a no-op:
  `TestSchemaAndMigration::test_second_initialise_is_noop`.
- **R4** a failure mid-migration (a planted conflicting
  `operation_receipts_new` table) leaves the legacy table and rows intact and
  raises: `TestSchemaAndMigration::test_migration_failure_leaves_legacy_table_intact`.
- **R5** a new database file is created `0600`:
  `tests/unit/test_sqlite_receipts.py::TestSqliteReceiptRepository::test_database_file_mode_0o600`.
- **R6** `rejected` round-trips in both stores:
  `tests/unit/test_receipts.py::TestInMemoryReceiptRepository::test_rejected_status_round_trips`
  and
  `tests/unit/test_sqlite_receipts.py::TestSchemaAndMigration::test_rejected_status_round_trips`.
- **R7** `(subject, operation_id)` isolation holds after migration:
  `TestSchemaAndMigration::test_tenant_isolation_after_migration`.
- **R8** list `add_item`: a migrated `legacy` receipt with the old hash
  replays its stored outcome, zero calls:
  `tests/unit/test_list_service.py::TestListServiceMutation::test_add_item_legacy_receipt_replays_stored_outcome`.
- **R9** list: a receipt with `action="calendar.create_event"` and the same
  operation ID is a conflict, zero calls:
  `TestListServiceMutation::test_add_item_receipt_from_another_action_conflicts`.
- **R10** list writes record the correct `action`/`resource_id`:
  `TestListServiceMutation::test_add_item_records_action_and_resource_id`
  (add_item) and the assertion added at the end of
  `TestListServiceMutation::test_set_item_checked_success` (set_checked).
- **R11** calendar: a refused create (`UpstreamRejectedError` and
  `SessionExpiredError`, parametrized) records `rejected` with the error
  JSON; a replay raises the same error code, zero calls:
  `tests/unit/test_calendar_create.py::TestReceipts::test_refused_create_records_rejected_and_replays_the_same_error`.
- **R12** calendar: a receipt with a `list.*` action is a conflict, zero
  calls: `TestReceipts::test_key_used_by_a_list_write_conflicts`.
- **R13** calendar: a `legacy` receipt with a matching hash replays its
  stored result: `TestReceipts::test_legacy_receipt_with_matching_hash_replays_stored_result`.
- **R14** `purge_expired` still works after migration:
  `tests/unit/test_sqlite_receipts.py::TestSchemaAndMigration::test_purge_expired_after_migration`.

## Validation

- `uv sync --frozen --group dev`: ok.
- `uv run ruff check .`: All checks passed.
- `uv run ruff format --check .`: 106 files already formatted (one file
  reformatted during implementation, then re-checked clean).
- `uv run mypy src`: Success, no issues found in 29 source files.
- `uv run pytest -m 'not live'`: 436 passed (423 before this slice; 13 net
  new test cases).
- `uv build`: both sdist and wheel built successfully.
- `scripts/check`: every step above passes; the final detect-secrets step
  fails, but only on findings that pre-date this slice (tracked separately in
  PR #8 per the task brief) — the baseline file is unchanged by this work
  (`git status` shows no diff to `.secrets.baseline`).

## Security and privacy review

- All test data is synthetic (`user1`/`user2`, `taskList/1`, `calendar/1`,
  fake hashes). No credentials, tokens, cookies, or real account/family data
  anywhere in the diff.
- No `.env*` file or credential store was read. No network calls were made;
  every test uses `tmp_path` SQLite databases and fake transports.
- No environment variables were printed.

## Known limitations

- `interfaces.py`'s `ReceiptRepository` protocol still does not declare
  `purge_expired`, matching its pre-existing shape; out of this slice's file
  boundary.
- The migration path is exercised only against the exact legacy DDL captured
  in the tests; it has not been run against a real production database file
  (no live check for an offline slice).
- `set_calendar_event_attendees` (slice E) and list assignment (slice F)
  still need building; this slice only prepares the receipt shape and
  migration they depend on.

## Next bounded task

Slice E, `set_calendar_event_attendees`: uses `resource_id=<event id>` and the
`rejected` status this slice added. See
`docs/briefs/09-implementation-plan.md`'s Slice E section.

## Lead review (2026-09-25)

- The migration now re-checks the schema after taking the write lock, so a
  process that loses a start-up race to another one no-ops instead of failing.
  Covered by `test_migration_rechecks_schema_under_the_write_lock`; the suite
  is now 437 passed.
- Upgrade replay: a database built by the pre-slice code (the five OAuth tables
  plus legacy receipts in one file) was opened with this slice's code. The OAuth
  table definitions were unchanged, all three receipts were preserved with
  `action='legacy'`, and a second `initialise()` was a no-op.
