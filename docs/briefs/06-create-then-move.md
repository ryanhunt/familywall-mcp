# Task 06 — Rework `add_list_item` into create-then-move

Owner: delegated. **Lead reviews the outcome machine and runs the live check.**

Read [ADR 0002](../decisions/0002-non-atomic-add.md) first. It is the governing
decision for this task and its reasoning is binding, not advisory.

## Why this exists

`taskcreate` has **no list parameter**. The web client's own declared signature
is `["text","dueDate","assignee","reminder"]`. Placing an item in a chosen list
needs a second call: `taskmove(["taskId","taskListId","prevTaskId","taskCategoryId"])`.

A live check confirmed the consequence: four different list-id field spellings
were all accepted and all landed the item in the family's **default** list.

## File boundary

Edit:
- `src/familywall_mcp/familywall/lists.py` — field builders
- `src/familywall_mcp/services/lists.py` — the add flow and outcomes
- `src/familywall_mcp/tools/registry.py` — the `add_list_item` tool contract
- `tests/unit/test_lists_adapter.py`, `tests/unit/test_list_service.py`,
  `tests/unit/test_tools.py` — update the tests these changes invalidate

**Do not edit** `pyproject.toml`, `uv.lock`, `models.py`, `errors.py`,
`interfaces.py`, `config.py`, `server.py`, `credentials.py`, anything else under
`familywall/` or `services/`, `storage/`, `tests/conftest.py`, or any doc but
your handoff. **Do not widen `OperationReceipt.status`** — ADR 0002 explains why
the new state does not belong there.

## 1. Field builders (`familywall/lists.py`)

`build_create_item_fields` currently sends `a00taskListId`, `a00text` and an
optional `a00quantity`. Two of those are wrong:

- **Remove the list id.** `taskcreate` has no such parameter; sending it is
  misleading, because it reads like it works.
- **Remove quantity entirely.** It is not a parameter of `taskcreate`, a live
  check confirmed it is silently dropped, and no read ever returns it. Delete
  the parameter from the function signature — do not keep it as an ignored
  argument.

New signature: `build_create_item_fields(text: str) -> dict[str, str]` producing
`partnerScope=Family` and `a00text`.

Add `build_move_item_fields(item_id: str, list_id: str) -> dict[str, str]`
producing `partnerScope=Family`, `a00taskId`, `a00taskListId`. Validate the
`task/` and `taskList/` prefixes as the existing builders do. `prevTaskId` and
`taskCategoryId` exist in the real signature but are **not** sent by v1 — say so
in the docstring.

Update the existing builder tests. Several currently assert quantity behaviour
and the list id in the create fields; those assertions are now wrong and must be
replaced, not deleted wholesale — assert the new exact field sets.

## 2. The add flow (`services/lists.py`)

`WriteOutcome` gains a fourth member:

```python
MISFILED = "misfiled"  # created, but it is in the wrong list
```

New `add_item` flow:

1. Resolve the target list as today (selection logic is unchanged and correct).
2. Receipt check as today — unchanged. A replay with the same operation id and
   payload returns the stored result and sends nothing.
3. `taskcreate` with the text only. Parse the response: it is a **full task
   object**, not a bare id. Read `metaId`/`taskId` for the new item's id and
   **`taskListId` for where it actually landed**.
4. **If the created item's `taskListId` already equals the requested list, do
   not call `taskmove` at all.** The orphan case cannot arise on that path.
5. Otherwise call `taskmove`. Then:
   - move succeeds and a readback of the target list finds the item →
     `CONFIRMED`
   - move succeeds but readback does not find it → `ACKNOWLEDGED`
   - move **definitely fails** (`UpstreamRejectedError`) → `MISFILED`
   - move fails indeterminately (`TransportError`, `RateLimitedError`, a payload
     error) → `MISFILED`, because the create definitely happened and we know the
     item's id and location
   - the **create** itself fails indeterminately → `UNKNOWN`, exactly as today
6. **Never delete the orphan. Never retry the move. Never retry the create.**
   ADR 0002 explains why; do not add a compensating delete or a retry loop.

The result model must carry, for a `MISFILED` outcome: the created item id, the
list id it actually landed in, and the list id that was requested. A caller that
cannot say *where the item went* has not solved the user's problem.

Receipt status for a successful create stays `succeeded` with the item id, even
when the outcome is `MISFILED` — a create did happen, and the receipt exists to
stop a replay creating a second item.

## 3. The tool (`tools/registry.py`)

- **Remove the `quantity` parameter** from `add_list_item`'s schema entirely.
- The tool description must state that an add is two steps and may leave the
  item in the default list, and that calling again creates a **second** item —
  so a `misfiled` result must never be retried by the caller.
- A `misfiled` response must say plainly, in text a person can act on: the item
  was created, it is in the default list, it is **not** in the list that was
  requested, and here is its id. Do not phrase it as a success.
- Do not add a delete or move tool. Out of scope.

## Tests — the acceptance criteria

No network. Assert on the fake transport's recorded calls throughout.

1. `build_create_item_fields` emits exactly `partnerScope` and `a00text`, with
   no list id and no quantity, and no longer accepts a quantity argument.
2. `build_move_item_fields` emits exactly `partnerScope`, `a00taskId`,
   `a00taskListId`, and rejects wrong prefixes.
3. A successful create-then-move with a confirming readback → `CONFIRMED`, and
   exactly three upstream calls in order: `taskcreate`, `taskmove`, `tasklist`.
4. Create succeeds, response's `taskListId` already equals the requested list →
   **no `taskmove` is sent at all**; assert the call list contains no move.
5. Create succeeds, move raises `UpstreamRejectedError` → `MISFILED`, and the
   result carries the item id, the actual list and the requested list.
6. Create succeeds, move raises `TransportError` → `MISFILED`, not `UNKNOWN`.
7. Create raises `TransportError` → `UNKNOWN`, and **no `taskmove` is sent**.
8. A `MISFILED` result triggers **no delete and no retry** — assert no
   `taskdelete` call and exactly one `taskcreate`.
9. Move succeeds but readback does not show the item → `ACKNOWLEDGED`.
10. Replaying one operation id after a `MISFILED` result returns the stored
    result and sends **zero** upstream calls — it must not create a second item.
11. The `add_list_item` tool schema contains no `quantity` property.
12. A `misfiled` tool response names the actual list and the requested list.

## Checks

```
uv run ruff check .
uv run ruff format .
uv run mypy src
uv run pytest -m 'not live'
scripts/check
```

The suite is at 279 tests. Some existing tests assert the old quantity and
list-id behaviour and **must** be updated — that is expected. Do not delete a
test to make a failure go away; rewrite it to assert the new contract.

Do NOT run anything against the live API. The lead does that.

## Handoff

Write `docs/handoffs/06-create-then-move.md` from `docs/templates/handoff.md`.
State plainly any acceptance criterion you did not cover with a test and why.
