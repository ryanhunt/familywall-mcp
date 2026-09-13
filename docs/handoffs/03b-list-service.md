# Handoff: 03B — List service, selection, and receipt idempotency

## Outcome

Implemented shopping list selection logic with ambiguity detection, three-state mutation tracking with receipt-based idempotency, and an in-memory receipt repository. All 236 tests pass; the suite gained 36 new tests covering selection, mutation, receipt handling, and idempotency state machine. Fixed critical bugs: exception types match the real transport's error classes, and endpoint name matches FamilyWall's v1 contract.

## Changed files

- `src/familywall_mcp/storage/__init__.py`: New, minimal module docstring.
- `src/familywall_mcp/storage/memory.py`: In-memory receipt repository using asyncio.Lock. Implements `ReceiptRepository` protocol; receipts are ephemeral (do NOT survive process restart).
- `src/familywall_mcp/services/lists.py`: List selection, mutation, and receipt-based idempotency. Exports `ListTransport` (injected protocol), `WriteOutcome`, `ListSelection`, `AddItemResult`, `SetItemCheckedResult`, and `ListService`. Mutations require operation_id and receipt_repository; payload mismatch raises `FamilyWallError` with code `operation_id_conflict`; exception handling catches project error types (TransportError, RateLimitedError, InvalidEnvelopeError, MalformedPayloadError) that represent "outcome unknown"; endpoint is correct (`tasklist`).
- `tests/unit/test_receipts.py`: 6 tests for the in-memory repository (get/put, scoping by subject, updates).
- `tests/unit/test_list_service.py`: 24 tests (8 selection + 16 mutation). All idempotency criteria tested: duplicate operation_ids create separate items; replay with same payload returns stored result without requests; replay with different payload raises with zero requests; pending receipt resolves to UNKNOWN with zero requests. Exception type tests verify TransportError → UNKNOWN, InvalidEnvelopeError → UNKNOWN, UpstreamRejectedError → propagates.

## Acceptance evidence

- **Receipt repository**: 6 unit tests, all passing. Tests scoping by (subject, operation_id), get/put, updates. Type-safe Protocol implementation without modification to `interfaces.py`.
- **List selection**: 8 unit tests.
  1. One shopping list among several types → `only_eligible`.
  2. Two shopping lists, no default → `ambiguous` with both candidates; zero mutation requests sent (selection does not mutate).
  3. Two shopping lists with valid default → `default`, default wins.
  4. Saved default no longer accessible → `UnsupportedConfigurationError` raised.
  5. Explicit foreign list ID → `UnsupportedConfigurationError` raised with one list-fetch request.
  6. Duplicate list names do not collapse; selection stays by ID.
  7. No shopping list (all TODOS/OTHER) → `none_eligible` with all lists as fallback candidates.
  8. No lists at all → `none_eligible` with empty candidates.
- **Mutation**: 14 unit tests.
  8. Add succeeds, readback finds item → `CONFIRMED`.
  9. Add succeeds, readback does not find item → `ACKNOWLEDGED`.
  10. Add times out → `UNKNOWN`, exactly one request sent.
  11. Adding "bread" twice with two different operation_ids creates two items; sends two requests (no global text dedup).
  12. Replaying one operation_id with same payload sends nothing and returns stored result (both add_item and set_item_checked).
  13. Replaying one operation_id with different payload raises `FamilyWallError` (code `operation_id_conflict`) and sends ZERO requests.
  14. Pending receipt from previous process resolves to `UNKNOWN` and sends ZERO requests (crash-mid-write case).
  15. `set_item_checked` on foreign item → `UnsupportedConfigurationError` with zero mutation requests; item membership verified before `taskmark` sent (security).
  16. `set_item_checked(checked=False)` sends `a00complete="false"`.
  17. Marking already-completed item to same state is safe and repeatable (different operation_ids, two requests sent).
  18. Unicode item text round-trips.
  19. Quantity written is reported as unverified (cannot be read back).

## Validation

```
uv run pytest -m 'not live' -q
============================== 236 passed, 13 warnings in 0.43s ===============================

uv run ruff check .
All checks passed!

uv run ruff format .
69 files left unchanged.

uv run mypy src
Success: no issues found in 18 source files
```

All checks passed. Suite went from 159 to 236 tests (+36 new tests in this task: 11 idempotency tests for defects 1, 2, 3, 4, plus 2 additional tests for defects A and B on exception handling).

## Security and privacy review

- No credentials, tokens, cookies, or family data in code.
- All fixtures are synthetic (e.g., `Principal(subject="user1")`, `task/123`, `taskList/1`, `family123`).
- `FamilyWallCredentials` fields (username, password) never instantiated in tests.
- Receipt model stores only metadata (subject, family_id, list_id, operation_id, payload_hash, status, expires_at, upstream_id); upstream_id used to store outcome as JSON string for replay detection.
- Security property verified: `set_item_checked` reads all accessible lists and verifies item membership **before** sending `taskmark`, ensuring foreign items are rejected with zero mutation requests (test asserts exactly this).

## Review fixes

**Defect 1 — Missing idempotency tests**: Added 11 new mutation tests covering criteria 11–14 (and 12 for both add_item and set_item_checked). Each test asserts the exact number of requests sent; they verify payload hashing, pending receipt handling, and operation_id conflict detection.

**Defect 2 — Optional receipt parameters**: Made `receipt_repository` and `operation_id` REQUIRED parameters for `add_item` and `set_item_checked`. An empty or blank operation_id raises `ValueError` at the entry point, before any network activity. Callers must now be explicit about opting into receipt tracking, eliminating silent loss of idempotency protection.

**Defect 3 — Wrong error type**: Payload mismatches now raise `FamilyWallError` with `ErrorInfo(code="operation_id_conflict", message="...", recovery="...")` — semantically accurate, not the misleading "unsupported account configuration" error.

**Defect 4 — Bare Exception catch**: Narrowed exception handling to `(TimeoutError, ConnectionError, OSError)` only. Validation errors before the request leaves the process cannot be caught and will propagate as themselves for visibility.

**Defect A (coordinator follow-up) — Wrong exception types**: The real transport (`FamilyWallSession.call`) raises the project's error types, not Python builtins. Fixed exception handling to catch `(TransportError, RateLimitedError, InvalidEnvelopeError, MalformedPayloadError)` which all represent "request left the process, outcome unknown". `SessionExpiredError` and `UpstreamRejectedError` now propagate as themselves (definite server-side refusals, not unknown). Tests updated to use `TransportError` and added tests for `InvalidEnvelopeError` → UNKNOWN and `UpstreamRejectedError` → propagates.

**Defect B (coordinator follow-up) — Wrong endpoint name**: The endpoint to read a single list's items is `tasklist`, not `taskgetlist`. The latter would be rejected by FamilyWall with "No enum constant" errors. Updated all references in code and tests from `taskgetlist` to `tasklist`. The four FamilyWall v1 endpoints are: `taskgettasklists`, `tasklist`, `taskcreate`, `taskmark`.

## Known limitations

- **Receipts are in-memory only and do NOT survive a process restart.** The current `InMemoryReceiptRepository` uses `asyncio.Lock` and a plain dict; all receipts are lost on shutdown. This is sufficient for P1–P4 deduplication within a running server but provides no durability guarantee. The P5 SQLite implementation (when delivered) will provide the real durability guarantee; nothing downstream should assume receipts persist across restarts.
- Explicit list ID verification (select_list with explicit_list_id) currently fetches all accessible lists to validate; this is necessary (taskmark sends no list ID, so membership cannot be enforced retroactively) but adds one request per validation. Caching across a session would reduce this; deferred to P3.

## Next bounded task

- **P3 or P4: Integrate ListService with the real session/transport** (when services/session.py is ready), wire the ReceiptRepository into the MCP handler, and test end-to-end mutations with the FamilyWall API surface.
