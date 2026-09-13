# Handoff — P0 contracts, SDK decision and plan rewrite

Date: 2026-09-13. Branch: `claude/p0-implementation-plan`. Not committed.

## Objective

Work through the phase-level plan ("plan for a plan") by completing P0 discovery
and turning the result into a concrete implementation plan.

## Changed files

| File | Change |
| --- | --- |
| `docs/contracts/familywall.md` | New. Endpoint-level wire contract with evidence levels |
| `docs/contracts/calendar.md` | New. Calendar contract and the recurrence/all-day gap |
| `docs/decisions/0001-auth-and-sdk.md` | New. ADR pinning `mcp` 2.2.0 and the embedded authorization server |
| `docs/compatibility.md` | New. What is actually verified, and by what means |
| `docs/PROGRESS.md` | New. Resumable phase tracker |
| `docs/implementation-plan.md` | Rewritten around P0's findings; adds phase P2a |
| `docs/tasks.md` | 00A/00B/00C marked done; new card 02P; 02B/03B/04A/04B corrected |
| `docs/handoffs/p0-contracts.md` | This handoff |
| `README.md` | Status updated; links to the new documents |

No source, dependency or test file was modified.

## Evidence

**Source reconnaissance** of `familywall-api@c85bb152` and `halaxy-mcp@205c6ceb`
by delegated cheap agents, with file:line citations recorded in the contracts.

**Executed spike** in a disposable virtualenv outside the project tree:

- `mcp` 2.2.0 is the current installable release; it imports on Python 3.12 and
  3.14.7.
- `MCPServer` with a `token_verifier` mounts `/mcp` and
  `/.well-known/oauth-protected-resource/mcp`.
- The same server with an `OAuthAuthorizationServerProvider` additionally mounts
  `/.well-known/oauth-authorization-server`, `/authorize`, `/token`, `/register`
  and `/revoke`.
- `@server.tool()` on a function returning a pydantic model emits an
  `output_schema` automatically.
- Protocol model fields are snake_case (`tool.input_schema`).
- `get_access_token()` inside a handler returns an `AccessToken` carrying
  `subject`, `scopes` and `client_id`.

**Validation:** `scripts/check` passes — ruff check and format, mypy on 6 source
files, 18 tests, build, and a `detect-secrets` scan against the baseline. A
targeted grep confirms no analytics-cookie or device-ID literals from the
TypeScript source were copied into this repository.

## Findings that changed the plan

1. Family scope is session-global. No list or calendar request carries a family
   ID, and no family-selection call exists. V1 serves one family and refuses when
   an account has several. Multi-family work moved to P8, contingent on evidence
   that selection is possible at all.
2. The SDK provides the OAuth endpoint surface, so P5 is storage and policy over
   a ten-method provider protocol. Authlib was considered and rejected as
   duplicated surface area. This contradicts the documentation-based research
   pass, which reported the opposite; the disagreement is recorded in ADR 0001
   so it is not silently re-litigated.
3. Calendar reads are much weaker than the lists surface: six proven fields, and
   no evidence for all-day encoding, recurrence, occurrence identity or
   cancellation. P4 has two mutually exclusive designs depending on the answer.
4. A single read-only live probe answers most of the remaining risk, so it is
   promoted ahead of all feature work as phase P2a / task 02P.

## Limitations

- Everything remains source-derived. No FamilyWall call has been made by this
  project, and no MCP client has connected to it.
- Claude's preferred Client ID Metadata Document registration was not exercised;
  dynamic client registration is the chosen path, CIMD is a P6 fallback.
- The probe's calendar value depends on a test week being built in the FamilyWall
  UI beforehand. Without a known expected answer it proves little.
- Unrelated observation, not addressed here: `pyproject.toml` pins mypy to
  `python_version = "3.12"` while the local interpreter is 3.14.7.

## Next task

**02P — read-only live probe** (`docs/tasks.md`). Lead only; needs a FamilyWall
test family and credentials supplied outside chat and Git. It is blocking for P4
and strongly advisable before P2b.

If no account is available, the next delegable work is **05A** (SQLite migrations
and encrypted storage), which depends only on P1 and ADR 0001, followed by
**02A**/**03A**, whose contracts are `source-tested`. P4 stays blocked.
