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
| P2b | 02A transport, envelope, login | `familywall/wire.py`, `familywall/client.py` | delegated |
| P2b | 02B discovery, family context, session lifecycle | `familywall/discovery.py`, `services/session.py` | not started |
| P3 | 03A list wire adapters | `familywall/lists.py` | delegated |
| P3 | 03B list selection and mutation service | `services/lists.py` | not started |
| P3 | 03C shopping MCP tools | `tools/` | not started |
| P4 | 04A calendar adapters and local ranges | `familywall/calendar.py`, `services/ranges.py` | delegated |
| P4 | 04B week service and calendar tools | `services/calendar.py`, `tools/` | not started |
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
