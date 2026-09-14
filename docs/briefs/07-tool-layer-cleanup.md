# Task 07 — Two tool-layer defects found by the live check

Owner: delegated. **Lead reviews and re-runs the live check.**

Both defects were found on 2026-09-14: the first by reading the raw response of a
successful live write, the second while investigating why a stale test double
never caused a failure.

## File boundary

Edit:
- `src/familywall_mcp/services/lists.py`
- `src/familywall_mcp/tools/registry.py`
- `src/familywall_mcp/server.py` (only to drop one constructor argument)
- `tests/unit/test_list_service.py`, `tests/unit/test_tools.py`

**Do not edit** `pyproject.toml`, `uv.lock`, `models.py`, `errors.py`,
`interfaces.py`, `config.py`, `credentials.py`, anything under `familywall/`,
`services/session.py`, `services/calendar.py`, `services/transport.py`,
`services/ranges.py`, `storage/`, `tests/conftest.py`, or any doc but your
handoff. Do not change the four write outcomes or ADR 0002 behaviour.

## Defect 1 — a `confirmed` add reports the wrong list

A live add through the real server returned:

```json
{"outcome": "confirmed",
 "item_id": "task/<id>",
 "actual_list_id": "taskList/<the DEFAULT list>",
 "requested_list_id": "taskList/<the requested list>"}
```

The outcome is `confirmed` and the item genuinely **is** in the requested list —
a readback proved it. But `actual_list_id` is populated from the `taskcreate`
response, which always names the default list, and is never updated after the
move succeeds. A model relaying this would tell the user "added, but it is in
your TODOS list", which is false. Telling a user something untrue about where
their data went is the exact failure class this project exists to avoid.

**Fix:** `actual_list_id` and `requested_list_id` are meaningful **only** for the
`MISFILED` outcome. For `CONFIRMED`, `ACKNOWLEDGED` and `UNKNOWN` they must be
`None`. Do not "fix" it by setting `actual_list_id` to the requested list on
confirm — that invents a fact; absence is the honest representation.

Document the rule on both `AddItemResult` and `AddListItemResponse`: these two
fields are populated if and only if the outcome is `misfiled`.

Tests:
1. A confirmed add has `actual_list_id is None` and `requested_list_id is None`.
2. An add where no move was needed (created item already in the requested list)
   is confirmed with both fields `None`.
3. An acknowledged add has both fields `None`.
4. An unknown add has both fields `None`.
5. A misfiled add has BOTH fields populated, with the actual list being the
   default list and the requested list being the one asked for. Keep this test —
   it is the only case where the fields carry meaning.

## Defect 2 — `ToolRegistry.list_service` is injected and never used

`ToolRegistry.__init__` takes `list_service`, assigns `self._list_service`, and
nothing ever reads it. All four list tools build their own service per call:

```python
transport = write_transport(self._session_pool, self._principal)
service = ListService(transport)
```

That per-call construction is **correct and must stay** — read and write tools
need different transports, which is exactly what `services/transport.py` is for.
The defect is the dead constructor argument and what it does to the tests.

Because tests inject a `FakeListService` that is never consulted, four tool tests
assert almost nothing. Proven: `test_add_list_item_with_idempotency_uses_receipt_repo`
passes while the add actually returns `outcome='unknown'` — it would pass if the
entire add path were broken. The same dead injection hid a `FakeListService.add_item`
stub whose signature had not matched the real service for some time.

**Fix:**
- Remove the `list_service` parameter from `ToolRegistry.__init__` and the
  `self._list_service` attribute. Update the call site in `server.py`.
- Keep `calendar_service` — it IS used by `get_week_overview`.
- Delete the `FakeListService` double from `tests/unit/test_tools.py`.
- Rewrite the affected tool tests to drive through `FakeSessionPool`, which is
  what actually determines behaviour, and **assert on the calls it records**:
  the endpoint names, the request fields, and the read/write mode each call was
  issued with.

Tests, each asserting on recorded pool calls, not just the returned model:
6. `list_shopping_lists` issues `taskgettasklists` with `read` mode.
7. `get_list_items` issues `tasklist` with `a00listId` and `read` mode.
8. `add_list_item` issues `taskcreate` then `taskmove` with `write` mode, and a
   `tasklist` readback.
9. `set_list_item_checked` verifies membership before `taskmark`, and the
   `taskmark` call carries `a00complete` as the string `"true"`/`"false"`.
10. The write gate still refuses both write tools with **zero** recorded pool
    calls when `enable_writes` is false. This must keep passing unchanged.
11. A tool test that would pass against a broken add path must now fail. Sanity
    check your own work: temporarily break the add flow, confirm a test fails,
    then restore it. Say in the handoff that you did this and which test caught
    it.

## Checks

```
uv run ruff check .
uv run ruff format .
uv run mypy src
uv run pytest -m 'not live'
scripts/check
```

The suite is at 285 tests; the count will change as tests are rewritten. Do not
delete a test to make a failure disappear, and do not weaken an assertion to make
one pass. Do NOT run anything against the live API — the lead does that.

## Handoff

Write `docs/handoffs/07-tool-layer-cleanup.md` from `docs/templates/handoff.md`.
Report the criterion-11 sanity check explicitly, and state plainly any criterion
you did not cover with a test and why.
