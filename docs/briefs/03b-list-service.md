# Task 03B — List selection and the three-state mutation service

Owner: delegated. **Lead reviews the receipt state machine and the membership
check before integration.**

Depends on 03A (`familywall/lists.py`, delivered and reviewed) and 02B
(`services/session.py`). Read both before starting and use their real APIs.

## File boundary — do not edit anything else

Create:
- `src/familywall_mcp/services/lists.py`
- `src/familywall_mcp/storage/__init__.py`
- `src/familywall_mcp/storage/memory.py`
- `tests/unit/test_list_service.py`
- `tests/unit/test_receipts.py`

**Do not edit** `pyproject.toml`, `uv.lock`, `models.py`, `config.py`,
`errors.py`, `interfaces.py`, `cli.py`, `familywall/*`, `services/ranges.py`,
`services/session.py`, any existing test, or any doc but your own handoff.

`familywall/lists.py` already provides `ShoppingList`, `ListItem`,
`ParsedItems`, `parse_list_summaries`, `parse_list_items`,
`build_get_lists_fields`, `build_get_list_fields`, `build_create_item_fields`
and `build_mark_item_fields`. Use them. Do not reimplement parsing or field
building, and do not change their behaviour from this task.

## Live-verified facts that drive the design

- `taskmark` sends **no list ID**. The wire request carries only `a00taskId`
  and `a00complete`. The server therefore cannot enforce that the item belongs
  to a list the caller may touch.
- `taskcreate` is **not idempotent**. Sending it twice adds two items.
- **No `quantity` is readable back.** 85 items across four lists returned no
  quantity-like key. A quantity that is written cannot be verified.
- Reads return bare arrays; item identity is `task/<id>` and an item's
  `taskListId` is the owning list's `taskList/<id>` metaId.

## What to build

### `storage/memory.py` — an in-process receipt repository

Implements the existing `ReceiptRepository` protocol in
`familywall_mcp.interfaces` over a dict, with an `asyncio.Lock`. SQLite arrives
in P5; this task must not add a database. The module docstring must say plainly
that receipts do **not** survive a restart here, and that the P5 SQLite
implementation is what makes the restart guarantee real — so nothing downstream
assumes durability it does not have.

### `services/lists.py` — selection and mutation

```python
class ListSelection(DomainModel):
    """Either a resolved list, or an ambiguity the caller must resolve."""

    resolved: ShoppingList | None
    candidates: tuple[ShoppingList, ...]
    reason: Literal["default", "only_eligible", "ambiguous", "none_eligible"]


class WriteOutcome(StrEnum):
    CONFIRMED = "confirmed"  # upstream acknowledged AND a readback shows it
    ACKNOWLEDGED = "acknowledged"  # upstream said ok, readback did not confirm
    UNKNOWN = "unknown"  # lost, timed out, or unparseable
```

Selection rules, in order, and none may be skipped:

1. An explicitly supplied list ID is used, **after** verifying it appears in the
   caller's own `taskgettasklists` result. An ID that does not appear is an
   error, never a silent redirect to another list.
2. Otherwise a saved default, **re-validated the same way** on every call. A
   deleted or inaccessible default is reported as such and does **not** silently
   redirect the write to a different list.
3. Otherwise, if exactly one list is eligible, use it.
4. Otherwise return `ambiguous` with the candidates. **Never pick the first.**

Eligibility for a shopping add is `known_type is ListType.SHOPPING`; if no
shopping list exists, fall back to offering all lists as candidates rather than
writing to a `TODOS` list by accident.

Mutation rules:

- `add_item(...)` builds fields with `build_create_item_fields`, sends one
  request, then **re-reads the list** and looks for the new item to decide
  `CONFIRMED` versus `ACKNOWLEDGED`.
- `set_item_checked(...)` **must** first read the item's list and verify the
  item's `list_id` matches a list the caller can access. Only then send
  `taskmark`. This is a security property, not a nicety — write a test that
  proves a foreign item ID is refused **before** any request is sent, by
  asserting the transport received zero calls.
- An explicit `checked=False` must be honoured, not treated as "no change".
- `UNKNOWN` is **never** retried automatically. A timeout, a transport error, or
  an unparseable response after the request left the process yields `UNKNOWN`
  and stops. Say so in the docstring.
- Every mutation carries an `operation_id` supplied by the caller. Before
  sending, consult the receipt repository:
  - same `operation_id` **and** same payload hash → return the stored result,
    send nothing
  - same `operation_id`, **different** payload → raise; do not send
  - no receipt → write a `pending` receipt, send, then update it
  - a `pending` receipt found on a later call (i.e. the process died mid-write)
    → resolve to `UNKNOWN`, never resend
- Receipts are scoped to subject **and** list, per the existing
  `OperationReceipt` model.
- A written quantity is reported as **unverified** in the result, because it
  cannot be read back. Do not imply it was stored.

## Tests — the acceptance criteria

No network. Use a fake client/transport that records calls so you can assert
what was and was not sent.

Selection:
1. One shopping list among several types → `only_eligible`.
2. Two shopping lists, no default → `ambiguous` with both candidates, and
   **no write is sent**.
3. Two shopping lists with a valid saved default → `default`, the default wins.
4. A saved default that no longer appears in the caller's lists → reported as
   unavailable; the write is **not** redirected to another list.
5. An explicitly supplied foreign list ID → error, zero requests sent.
6. Duplicate list names do not collapse; selection stays by ID.
7. No shopping list at all → candidates offered, nothing written.

Mutation:
8. Add succeeds and readback finds the item → `CONFIRMED`.
9. Add succeeds and readback does not find it → `ACKNOWLEDGED`.
10. Add times out → `UNKNOWN`, and exactly **one** request was sent.
11. Adding "bread" twice with two different `operation_id`s creates two items.
    There is no global de-duplication by item text — this is deliberate.
12. Replaying one `operation_id` with the same payload sends nothing and returns
    the first result.
13. Replaying one `operation_id` with a different payload raises and sends
    nothing.
14. A `pending` receipt from a previous process resolves to `UNKNOWN` and sends
    nothing.
15. `set_item_checked` on an item belonging to another list → refused with
    **zero** requests sent.
16. `set_item_checked(checked=False)` sends `a00complete="false"`.
17. Marking an already-completed item to the same state is safe and repeatable.
18. A Unicode item title round-trips.
19. A result that sets a quantity reports the quantity as unverified.

## Checks to run before handing back

```
uv run ruff check .
uv run ruff format .
uv run mypy src
uv run pytest -m 'not live'
```

`mypy` is strict. If a check fails in a file you did not create, report it and
leave it alone.

## Handoff

Write `docs/handoffs/03b-list-service.md` from `docs/templates/handoff.md`.
State explicitly that receipts are in-memory only and do not survive restart.
