# Build progress

Resumable state for this project. A new session should read this file, then the
outputs of the last completed row, and continue at the first row that is not
`done`. See [implementation plan](implementation-plan.md) and
[task cards](tasks.md).

Current branch: `claude/p2-live-probe-and-client`.

## Delivery target for this pass

Local **stdio** MCP server the user can run and test against their own
FamilyWall account. Hosted OAuth (P5), containers and HTTPS (P6) are explicitly
deferred until the stdio server works end to end.

## Phase status

| Phase | Task | Output | Status |
| --- | --- | --- | --- |
| P0 | 00A/00B/00C contracts, calendar, ADR | `docs/contracts/`, `docs/decisions/0001-auth-and-sdk.md` | done |
| P1 | 01A/01B foundation and offline harness | PR #1 | done |
| P2a | **02P read-only live probe** | contracts + `docs/compatibility.md` updated; commit `2323362` | **done 2026-09-13** |
| P2b | 02A transport, envelope, login | `familywall/wire.py`, `familywall/client.py` | **done**: `01e6ba8` |
| P2b | 02B discovery, family context, session lifecycle | `familywall/discovery.py`, `services/session.py` | **done**: `4ce6815` |
| P3 | 03A list wire adapters | `familywall/lists.py` | **done**: `7a655d5` |
| P3 | 03B list selection and mutation service | `services/lists.py`, `storage/memory.py` | **done**: `1382d8f` |
| P3 | 03C shopping MCP tools | `tools/` | not started |
| P4 | 04A calendar adapters and local ranges | `familywall/calendar.py`, `services/ranges.py` | **done**: `e4170c2` |
| P4 | 04B weekly overview service | `services/calendar.py` | delegated |
| P3/P4 | MCP tool surface and stdio server | `tools/`, `server.py` | lead, next |
| — | stdio server entry point and local smoke test | `server.py`, `cli.py` | not started |
| P5 | Storage, invites, OAuth | — | deferred until stdio works |
| P6 | Containers and HTTPS | — | deferred until stdio works |
| P7 | Acceptance and release | — | not started |

## What 02P settled, and what it changed

The read-only live probe on 2026-09-13 answered every blocking question and
**corrected four source-derived claims**. Full detail is in
[familywall.md](contracts/familywall.md#live-verification--02p-probe-2026-09-13)
and [calendar.md](contracts/calendar.md#live-verified-summary-2026-09-13).

Decisions now binding on implementation:

1. **P4 takes the "consume expanded occurrences" branch.** The server expands
   recurring occurrences. No recurrence library, no local expansion. The
   conditional sub-task in the original plan is cancelled, not deferred.
2. **All-day dates are read verbatim, never timezone-converted.** This is the
   single highest-risk normalisation rule in the project.
3. **Interval bounds are a half-open overlap** (`start < to && end > from`), so
   the boundary adapter is an identity match rather than a correction layer.
4. **`calendarId` is ignored by the server** and is not an access boundary. No
   tool may accept a caller-supplied calendar ID and imply it selects anything.
5. **Auth is `JSESSIONID` cookie + mandatory `tokencsrf` header.** No
   `webset`/`webget`, no analytics cookies, no `deviceId`, no `User-Agent`
   requirement.
6. **Failure envelopes are `aNN.un.un` and `aNN.ex.ex`**, both at HTTP 200.
   Session expiry is `un`/`501`/`NOAUTHENT`, not 401 and not HTML.
7. **`quantity` is not readable.** A tool that writes one cannot verify it, and
   must say so rather than imply success.

## Review findings worth carrying forward

Delegated implementation was reviewed rather than accepted on report. Every task
needed at least one correction round, and three defect classes recurred:

1. **Silently skipped requirements.** 02B shipped `_reauth_and_retry` as dead
   code that nothing called, so the bounded-read-reauth and never-retry-writes
   rules did not exist; 03B deferred four receipt-state-machine criteria to
   "integration testing" that were plain unit tests. Both reported high passing
   test counts truthfully. Numbering the required tests in the brief is what
   made the absences checkable.
2. **Mock-shaped correctness.** 03B called a `taskgetlist` endpoint that does
   not exist; its fake transport accepted it and all 234 tests passed. A green
   mock suite cannot tell you whether the real API would answer. **Every
   endpoint string must be checked against the contract, and the local stdio
   smoke test is the real gate for this phase.**
3. **Exception types that do not match the codebase.** 03B caught the Python
   builtins `TimeoutError`/`ConnectionError`/`OSError`, but the transport raises
   the project's own `TransportError`, which subclasses none of them — so a lost
   write would have propagated instead of yielding `unknown`. Caught only by
   checking the class hierarchy directly.

## Still `pending-live`

Recorded so they are not quietly dropped:

- `taskcreate` and `taskmark` response shapes — both are writes and 02P was
  read-only. Answered by a controlled P3 live add/check/uncheck in a disposable
  test list, with the user's explicit go-ahead.
- Whether `quantity` is writable at all.
- Multi-day all-day encoding — needs a deliberately created test event.
- Multi-family discovery shape — the probe account has one family, so the
  single-family refusal must fail closed on anything unexpected.

## Resume instructions

1. Read this table and the two contract files' live-verified sections.
2. Check `git log --oneline` for the last landed commit.
3. Continue at the first row that is not `done`. Each delegated task has a
   self-contained brief in `docs/briefs/` and a handoff in `docs/handoffs/`.
