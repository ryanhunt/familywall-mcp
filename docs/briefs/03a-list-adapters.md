# Task 03A — Shopping-list wire adapters

Owner: delegated. Lead reviews before integration. Pure parsing only: this task
performs **no I/O and no HTTP**.

## File boundary — do not edit anything else

Create:
- `src/familywall_mcp/familywall/lists.py`
- `tests/unit/test_lists_adapter.py`
- `tests/support/list_fixtures.py`

**Do not edit** `pyproject.toml`, `uv.lock`, `models.py`, `config.py`,
`errors.py`, `interfaces.py`, `cli.py`, `tests/support/doubles.py`,
`tests/conftest.py`, any existing test, or any doc. Another agent is
concurrently creating `familywall/client.py` and `familywall/wire.py` and
`familywall/__init__.py` — **do not create or import those files**; if you need a
helper they own, write your own private copy in `lists.py`.

## Facts (live-verified 2026-09-13 — this is what the server actually returns)

All booleans arrive as the **strings** `"true"` / `"false"`. All numbers may
arrive as strings. Identifiers are prefix-typed.

### `taskgettasklists` → a **bare JSON array** of list objects

Observed keys on every list:
`accountId`, `alexa`, `bestMoment`, `clientOpId`, `color`, `comments`,
`completedHidden`, `creationDate`, `emoji`, `familyId`, `lastAction`,
`lastActionAuthor`, `lastActionDate`, `medias`, `metaId`, `moodMap`,
`moodStarShortcut`, `name`, `remainingTaskNumber`, `rights`, `sharedMemberIds`,
`sharedToAll`, `sortingIndex`, `taskCategoriesHidden`, `taskListType`,
`taskSorting`, `totalTaskNumber`. System lists add `systemId` and
`pinSortingIndex`, and may omit `color`.

- identity is `metaId`, of the form `taskList/<digits>`
- `taskListType` observed values: `SHOPPING_LIST`, `TODOS`, `OTHER`
- counts are `totalTaskNumber` and `remainingTaskNumber` (remaining, **not**
  checked). There is no `itemCount` and no `checkedCount`
- there is **no** `taskListId`, `listId` or `id` alias

### `tasklist` → a **bare JSON array** of item objects

Observed keys:
`accountId`, `assignee`, `assigneeIds`, `bestMoment`, `categories`,
`clientOpId`, `comments`, `complete`, `completedDate`, `creationDate`,
`description`, `editable`, `familyId`, `lastAction`, `lastActionAuthor`,
`lastActionDate`, `medias`, `metaId`, `modifDate`, `moodMap`,
`moodStarShortcut`, `recurrency`, `recurrencyDeletedOccurence`, `reminder`,
`sortingIndex`, `taskCategoryId`, `taskId`, `taskListId`, `text`, `toAll`.

- identity is `metaId`, of the form `task/<digits>`
- `taskId` is the bare numeric form; `taskListId` is the **owning list's
  `metaId`** and is the field that lets a service verify membership
- `complete` is `"true"` / `"false"`
- `categories` are objects with `name` and a `system` string flag
- **there is no `quantity` field on any read item.** Do not invent one, do not
  parse one out of `text`, and do not model quantity as readable

## What to build in `lists.py`

Frozen pydantic models (mirror the style of `src/familywall_mcp/models.py`:
`extra="forbid"`, `frozen=True`), plus pure adapter functions.

```python
class ListType(StrEnum):
    SHOPPING = "SHOPPING_LIST"
    TODOS = "TODOS"
    OTHER = "OTHER"
```

but an **unknown** `taskListType` must be preserved verbatim, never coerced to
`OTHER`. Model it as `type_raw: str` plus a `known_type: ListType | None`, or an
equivalent that keeps the original string. This is a hard requirement.

```python
class ShoppingList(DomainModel):
    list_id: str          # the full "taskList/<digits>" metaId
    name: str
    type_raw: str
    known_type: ListType | None
    total_items: int | None
    remaining_items: int | None
    color: str | None
    system_id: str | None

class ListItem(DomainModel):
    item_id: str          # the full "task/<digits>" metaId
    list_id: str          # taskListId — the owning list's metaId
    text: str
    completed: bool
    description: str | None
    author_account_id: str | None
    category_names: tuple[str, ...]
    created_at: datetime | None
    completed_at: datetime | None
```

Adapters:
- `parse_list_summaries(payload: object) -> tuple[ShoppingList, ...]`
- `parse_list_items(payload: object) -> tuple[ListItem, ...]`

Rules, all of which must be tested:
- Accept a bare array (the real shape). Also accept an object wrapping the array
  under `lists`, `taskLists`, `results` (lists) or `items`, `tasks`,
  `listItems` (items) — the reference client claimed those and tolerance is
  cheap. Anything else raises `MalformedPayloadError` from
  `familywall_mcp.errors`.
- A missing or empty `metaId`, `name` or `text` raises `MalformedPayloadError`.
  A wrong prefix (`task/…` where `taskList/…` is required, or vice versa) also
  raises it.
- Numbers may be ints or numeric strings; a non-numeric string for a count
  yields `None` rather than an exception, because counts are cosmetic.
- Booleans use the string form. An unrecognised value raises
  `MalformedPayloadError`.
- Dates are ISO-8601 with a `Z` suffix (e.g. `2026-09-13T13:34:16.302Z`). Parse
  to timezone-aware UTC `datetime`. An unparseable date yields `None`, never a
  naive datetime.
- **One malformed item must not discard the whole list.** `parse_list_items`
  skips unparseable entries and the function returns what it could parse; expose
  the skipped count via a returned pair or a dedicated result model so a caller
  can report partial data honestly. Decide the shape, document it in the
  docstring, and test it. (A malformed *envelope* still raises.)
- No function may accept or return `Any` in its public signature.

Also build the request-field builders, so the exact wire fields live next to the
contract they came from and can be asserted in tests:
- `build_get_lists_fields() -> dict[str, str]` → `{"partnerScope": "Family"}`
- `build_get_list_fields(list_id: str) -> dict[str, str]` →
  `{"partnerScope": "Family", "a00listId": list_id}`, validating the prefix
- `build_create_item_fields(list_id: str, text: str, quantity: str | None) -> dict[str, str]`
  → `a00taskListId`, `a00text`, and `a00quantity` **only when quantity is not
  None**. Quantity is passed through verbatim as a string; no parsing,
  normalisation, unit handling or trimming beyond rejecting an empty string.
- `build_mark_item_fields(item_id: str, completed: bool) -> dict[str, str]`
  → `a00taskId`, `a00complete` as the string `"true"`/`"false"`. Note this call
  carries **no list ID**, which is why membership must be checked by a service
  before it is ever sent. Say so in the docstring.

## Fixtures

`tests/support/list_fixtures.py` holds **synthetic** payloads shaped like the
real ones. Invent names (`Weekly shop`, `Hardware`, `Müsli`), invent numeric
IDs, and include: a shopping list, a todos list, an `OTHER` list, and a list
with an unknown type such as `MEALPLAN`. No real family data exists in this
repository and none may be added.

## Tests — the acceptance criteria

1. A bare array of four lists parses, preserving the unknown `MEALPLAN` type
   verbatim with `known_type is None`.
2. Each of the three wrapper-object shapes parses.
3. A non-array, non-wrapper payload raises `MalformedPayloadError`.
4. A list with `metaId: "task/1"` raises (wrong prefix).
5. `totalTaskNumber: "45"` and `totalTaskNumber: 45` both yield `45`;
   `"many"` yields `None`.
6. Items parse, `complete: "true"` yields `True`, `"false"` yields `False`,
   `"yes"` raises.
7. `list_id` on an item comes from `taskListId` and keeps the `taskList/` prefix.
8. A Unicode item text round-trips unchanged.
9. One malformed item among four is skipped, the other three are returned, and
   the skipped count is reported.
10. An unparseable `creationDate` yields `None` and does not raise.
11. `build_create_item_fields` omits `a00quantity` when quantity is `None`,
    includes it verbatim (including spaces and Unicode) when given, and rejects
    an empty string.
12. `build_mark_item_fields(..., completed=False)` produces the **string**
    `"false"`, and the field set contains no list ID.
13. `build_get_list_fields` rejects an ID without the `taskList/` prefix.

## Checks to run before handing back

```
uv run ruff check .
uv run ruff format .
uv run mypy src
uv run pytest -m 'not live'
```

`mypy` is strict. Other agents are working in this repo concurrently — if a
check fails in a file you did not create, say so in the handoff and do not
"fix" it.

## Handoff

Write `docs/handoffs/03a-list-adapters.md` from `docs/templates/handoff.md`.
