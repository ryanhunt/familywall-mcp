# ADR 0003 — Single-call add supersedes create-then-move

Date: 2026-09-25. Status: accepted. Supersedes
[ADR 0002](0002-non-atomic-add.md).

## Context

ADR 0002 recorded that `taskcreate` has no list parameter, so placing an item
in a chosen list required two calls: `taskcreate`, then
`taskmove(taskId, taskListId, ...)`. That pair was not atomic: a create
succeeding while a move failed left the item in the default list, and ADR
0002 introduced the `misfiled` outcome to report that half-right state
precisely, deliberately without an automatic compensating delete or an
automatic retry.

Probe A2 (2026-09-25) found that the web app itself does not use
`taskcreate`/`taskupdate` at all. It uses `taskcreate2`/`taskupdate2`, and
`taskcreate2` **takes a `taskListId` directly**. A scripted call with a
non-default list's `taskListId`, `toAll=false` and `assignee.0` created the
item in that list with exactly that assignee, in one call. See
[the wire contract](../contracts/familywall.md#probe-a2--list-assignment-and-the-2-endpoints-2026-09-25).

## Decision

**`add_list_item` sends exactly one `taskcreate2`, carrying the target list
and the assignment, and never sends `taskmove`.**

The create-then-move partial-failure state this ADR's predecessor was
written to handle **no longer exists**: there is no second call to fail
between. `misfiled` stays in the write-outcome vocabulary, but its meaning
narrows to a **detected**, not caused, outcome — the create response itself
names a list other than the one requested (an upstream inconsistency, not a
partial write of this server's own making). When that happens there is still
no move and no compensation: the same reasoning ADR 0002 gave for not
auto-deleting or auto-retrying still applies, now to a state this server did
not create either.

Everyone is sent as `toAll=true` plus an `assignee.N` for every member — the
encoding probe A2 verified on `taskupdate2` and extrapolated, not directly
observed, onto `taskcreate2`. The service's readback compares the created
item's assignment against the request (decision 5 of brief 09F) and reports
a new `mismatched` outcome if the server disagrees, so an unverified
extrapolation is caught rather than silently trusted.

## Consequences

- Five write outcomes: `confirmed`, `acknowledged`, `misfiled`, `mismatched`,
  `unknown`. `mismatched` is new (brief 09F); the other four are unchanged in
  name, though `misfiled`'s cause has narrowed as described above.
- No `taskmove` call exists anywhere in `add_list_item` any more.
  `build_move_item_fields` and `build_create_item_fields` remain in
  `familywall/lists.py` (harmless, and other tests may still exercise them
  directly), but the service no longer calls either.
- A pre-upgrade `add_list_item` receipt, replayed within its 24-hour life,
  now **conflicts** rather than replays: the payload hash is computed over
  the new `taskcreate2` fields, which differ from the old `taskcreate`
  fields, so the hash comparison fails and the caller gets
  `operation_id_conflict` instead of a stale outcome. This is safe — no
  duplicate item is created either way — and is called out explicitly so it
  is not mistaken for a regression.
- The everyone encoding on `taskcreate2` is extrapolated, not live-verified;
  the lead verifies it live before merge (brief 09F's handoff target), and
  the readback's `mismatched` outcome is the safety net if the extrapolation
  is wrong.
- `set_list_item_assignees` (new, brief 09F) reuses the same
  pending/succeeded/rejected/replay receipt pattern, via a single partial
  `taskupdate2`, verifying list membership before any write since the
  endpoint carries no list ID.
