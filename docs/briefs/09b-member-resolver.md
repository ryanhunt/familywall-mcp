# Task 09B — Member resolver, `list_family_members`, assignment read models

Slice B of the [brief 09 implementation plan](09-implementation-plan.md).
Offline only: synthetic fixtures, no live network, no credentials.

## Objective

Resolve family-member names to FamilyWall account IDs locally, add a read-only
`list_family_members` tool, and show who events and list items are assigned to
(as names) in the existing read tools. No write path changes in this slice.

## Scope

- In scope: the resolver, the new read tool, assignment fields on the calendar
  and list read models, and assignment output on `get_week_overview` and
  `get_list_items`.
- Files allowed to change:
  - new `src/familywall_mcp/services/members.py`
  - `src/familywall_mcp/familywall/calendar.py` (`CalendarEvent` and its parser only)
  - `src/familywall_mcp/familywall/lists.py` (`ListItem` and its parser only)
  - `src/familywall_mcp/tools/registry.py`
  - tests: new `tests/unit/test_member_selection.py`, `tests/unit/test_calendar_adapter.py`,
    `tests/unit/test_lists_adapter.py`, `tests/unit/test_tools.py`, `tests/support/*` (synthetic data only)
  - docs: `README.md` (tool table and count), `AGENTS.md` (tool count sentence only),
    `docs/PROGRESS.md` (one row), `docs/briefs/09-implementation-plan.md` (mark B done),
    new `docs/handoffs/09b-member-resolver.md`
- Out of scope: any write tool or its arguments (`create_calendar_event`,
  `add_list_item`), receipts, wire builders, and refreshing hosted discovery on
  an unknown name (that belongs to slice C, where names are first used for writes).

## Binding decisions

1. **`ResolvedAssignment`** (frozen `DomainModel`, in `services/members.py`):
   `to_all: bool`, `account_ids: tuple[str, ...]` (non-empty),
   `display_names: tuple[str, ...]` (same length and order as `account_ids`).
2. **`resolve_members(names: Sequence[str] | None, family: DiscoveredFamily) -> ResolvedAssignment`**
   - `None` or an empty sequence means everyone: `to_all=True`, and every member in
     discovery order (owner decision 1 in the plan).
   - Normalise each name with `unicodedata.normalize("NFKC", s).casefold()`, collapse runs
     of whitespace to one space, and strip. An entry that is empty after normalising is an
     `invalid_member_name` error.
   - Match in this order: (a) members whose normalised `display_name` equals the name: one
     match wins, more than one is `ambiguous_member`; (b) otherwise members whose normalised
     first name equals it, where first name is `first_name` if set, else the first
     whitespace-separated token of `display_name`: one match wins, more than one is
     `ambiguous_member`, none is `unknown_member`.
   - No substring, prefix or fuzzy matching.
   - De-duplicate by `account_id`, keeping first-seen order.
   - A named list is always `to_all=False`, even if it names every member.
   - Errors: a `MemberSelectionError(FamilyWallError)` defined in `services/members.py`, with
     codes `invalid_member_name`, `unknown_member` and `ambiguous_member`. Messages and recovery
     text are **static**: they never include the input or any member name. Recovery:
     "Call list_family_members for the exact names."
3. **`describe_assignment(account_ids, to_all, family) -> AssignmentView`**: `names` for the IDs
   found in the family (in the given order), `everyone` = `to_all` as reported (may be `None`),
   and `unresolved` = the count of IDs not in the family. It never returns IDs.
4. **`CalendarEvent`** gains `attendee_ids: tuple[str, ...] = ()` (from `attendeeIds`),
   `to_all: bool | None = None` (from `toAll`, via `coerce_bool`) and `editable: bool | None = None`
   (from `editable`). An absent field takes the default. A present but malformed one (for example
   `attendeeIds` not a list of strings, or `toAll: "maybe"`) makes the event malformed under the
   existing skip-and-count policy.
5. **`ListItem`** gains `assignee_ids: tuple[str, ...] = ()` (from `assigneeIds`) and
   `to_all: bool | None = None` (from `toAll`). The absent and malformed policy is the same as
   for events.
6. **`list_family_members`**: no arguments, `read_only_hint=True`, and **zero upstream calls** (it uses
   the cached discovery). Returns `family_name` and `members: [{display_name, first_name | null, is_you}]`
   in discovery order. It never returns account IDs.
7. **`get_week_overview`** event entries and **`get_list_items`** items each gain `assigned_to`
   (list of names), `assigned_to_everyone` (`bool | null`) and `unresolved_members` (int),
   built with `describe_assignment`. Existing fields are unchanged. No account ID appears in any
   tool output.

Evidence for these shapes (live-verified 2026-09-25): events read back "everyone" as
`toAll:"true"` with an empty `attendeeIds`; tasks read back "everyone" as `toAll:"true"` with
every member in `assigneeIds`, in a different order. See `docs/contracts/calendar.md#mutations`
and `docs/contracts/familywall.md#probe-a2--list-assignment-and-the-2-endpoints-2026-09-25`.

## Required tests (each must exist and pass)

- **T1** Normalisation: `" rObIn  SMITH "` and a fullwidth-Unicode spelling both match the
  member displayed as `Robin Smith`.
- **T2** An exact full display name wins over a first-name match. With members displayed as
  `Sam` and `Sam Lee` (first name `Sam`), the name `Sam` resolves to `Sam` and is not ambiguous.
- **T3** A unique first name resolves.
- **T4** Two members with the same first name: that first name is `ambiguous_member`.
- **T5** Two members with the same display name: the full name is `ambiguous_member`.
- **T6** An unknown name is `unknown_member`.
- **T7** A whitespace-only entry is `invalid_member_name`.
- **T8** `None` and `[]` both give `to_all=True` with every member, in discovery order.
- **T9** `["Robin", "robin smith"]` gives one ID. The order of several distinct names is preserved.
- **T10** Naming every member individually gives `to_all=False`.
- **T11** Isolation: a name that exists only in a different `DiscoveredFamily` is `unknown_member`.
- **T12** No substring matching: `"Rob"` is `unknown_member`.
- **T13** Error messages and recovery text contain neither the input nor any member name.
- **T14** `describe_assignment`: known plus unknown IDs give names and an unresolved count, and
  `to_all` passes through, including `None`.
- **T15** Calendar adapter: the three fields parse; absent fields give defaults; each malformed
  case skips the event and increments `skipped`.
- **T16** List adapter: the two fields parse; absent fields give defaults; each malformed case
  skips the item.
- **T17** `list_family_members`: zero pool calls, `is_you` correct, and no synthetic account ID
  in the serialised response.
- **T18** `get_week_overview` and `get_list_items`: names mapped, the everyone flag, the
  unresolved count, and no synthetic account ID in the serialised output.

## Validation

Run from this worktree: `uv sync --frozen --group dev`, `uv run ruff check .`,
`uv run ruff format --check .`, `uv run mypy src`, `uv run pytest -m 'not live'`, `uv build`.
`scripts/check` may fail only at its detect-secrets step, on findings that exist before this
slice (fixed separately in PR #8); report that rather than editing `.secrets.baseline`.

## Security and privacy

Synthetic names and IDs only. Never read `.env*` files or credential stores, and never make
network calls. No account ID in tool output, logs or error text.

## Handoff target

Slice C, attendees on `create_calendar_event`. It needs this resolver plus a one-time hosted
discovery refresh on an unknown name.
