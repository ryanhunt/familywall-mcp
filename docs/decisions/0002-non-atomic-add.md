# ADR 0002 — Handling a non-atomic add

Date: 2026-09-14. Status: accepted.

**Superseded by [ADR 0003](0003-single-call-add.md).** `taskcreate2` takes a
`taskListId` directly, so `add_list_item` no longer sends `taskcreate` then
`taskmove`, and the create-then-move partial-failure state this ADR was
written to handle no longer exists. Read on for the historical reasoning
(still relevant to why no compensating delete or retry was ever added).

## Context

`taskcreate` has no list parameter. Placing an item in a chosen list requires
two calls: `taskcreate`, then `taskmove(taskId, taskListId, ...)`. See the
[wire contract](../contracts/familywall.md#endpoint-signatures-from-the-web-client--2026-09-14-live-verified).

The pair is not atomic. If the create succeeds and the move does not, the item
exists and is visible to the whole family **in the default list** rather than
the one the user named. The existing three write outcomes cannot express this:
it is not `acknowledged` (which means "upstream said yes, we could not confirm")
and not `unknown` (which means "we do not know whether anything happened"). Here
we know exactly what happened and it is half-right.

`taskdelete(["taskId"])` is now live-verified, so deleting the orphan to restore
"nothing happened" is technically available. The question is whether to do it.

## Decision

**Do not automatically delete the orphan. Do not automatically retry. Report the
partial state precisely and stop.**

A new service-level outcome, `misfiled`, carries the created task's id, the list
it actually landed in (read from `taskcreate`'s own `taskListId` response
field), and the list that was requested.

### Why not auto-delete

Deletion is immediate and irreversible — no trash, no undo. The failure modes
that produce an orphan are dominated by **indeterminate** ones: a timeout, a
transport error, a dropped response. In exactly those cases the move may have
*succeeded* server-side and only the acknowledgement was lost. Deleting then
destroys an item that is correctly filed in the family's list, and does so as a
direct consequence of our own error handling.

Auto-delete would only be defensible on a **definite** refusal (`un`/`502`),
where we know the move did not take effect. That is a narrow branch guarding a
destructive action, sitting next to a much larger branch where the same action
would be data loss. A misread of which branch we are in deletes real user data.
The asymmetry is decisive: leaving an item in the wrong list is visible,
recoverable in seconds, and annoying; deleting a real item is silent and
permanent.

There is also a race: between our failed move and our compensating delete, a
family member may have already acted on the item.

### Why not auto-retry the move

`taskmove` is plausibly idempotent — moving an already-moved task to the same
list should be a no-op — and retrying it carries none of `taskcreate`'s
duplicate risk, because no second item can be created. That makes a bounded
retry genuinely attractive.

But its idempotency is **unverified**. We have the declared signature from the
web client and nothing more. This project's rule is that unverified protocol
behaviour is not built on, and the cost of being wrong is an item moved twice or
an ordering side effect on a real family's list. Recorded as a discovery ticket:
if a live check shows `taskmove` is idempotent, one bounded retry becomes
justified and this ADR should be revisited.

### What the user sees

The tool reports, plainly: the item was created, it is in the default list, it
is **not** in the list that was asked for, and here is its id. It must not
suggest calling the tool again — that would create a second item. The remedy is
to move it in the FamilyWall app, or for the caller to issue a move explicitly
once a move tool exists.

### Receipt semantics

The operation receipt records `succeeded` with the created task's id, because
the receipt's job is to stop a replay from creating a **second** item — and a
create did happen. The `misfiled` distinction lives in the service-level write
outcome, not in the receipt status. This deliberately avoids widening
`OperationReceipt.status`, whose three values already mean the right things for
replay protection.

## Consequences

- Four write outcomes: `confirmed`, `acknowledged`, `misfiled`, `unknown`.
- No compensating delete anywhere in v1. `taskdelete` stays out of the tool
  surface entirely.
- When the requested list **is** the default list, no move is issued at all, so
  the orphan case cannot arise for that path.
- `quantity` is removed from the tool surface: it is not a parameter of
  `taskcreate` and cannot be stored or read.
- A future `taskmove` idempotency check is the one piece of evidence that would
  reopen the retry decision.
