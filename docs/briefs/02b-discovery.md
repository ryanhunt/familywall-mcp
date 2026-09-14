# Task 02B — Family discovery, context and session lifecycle

Owner: delegated. **Lead reviews the isolation behaviour before integration.**

Depends on task 02A (`familywall/client.py`, `familywall/wire.py`). Read the
02A handoff and the delivered `client.py` before starting; use its real public
API rather than the one this brief predicts, and record any difference.

## File boundary — do not edit anything else

Create:
- `src/familywall_mcp/familywall/discovery.py`
- `src/familywall_mcp/services/session.py`
- `tests/unit/test_discovery.py`
- `tests/unit/test_session_pool.py`
- `tests/support/discovery_fixtures.py`

**Do not edit** `pyproject.toml`, `uv.lock`, `models.py`, `config.py`,
`errors.py`, `interfaces.py`, `cli.py`, `familywall/client.py`,
`familywall/wire.py`, `familywall/lists.py`, `familywall/calendar.py`,
`services/ranges.py`, any existing test, or any doc but your own handoff.

## Facts (live-verified 2026-09-13)

`accgetallfamily` with fields `partnerScope=Family` and `a01call=prfgetProfiles`
returns `a00.r.r` as a **single object, not an array**:

```
family_id      "1234567"            bare numeric string
metaId         "family/1234567"     prefixed form
name           "<family name>"
members        [ … ]
```

Each member carries `accountId`, `firstName`, `name`, `role`, `right`, `color`,
`timeZone` (IANA, e.g. `Australia/Sydney`), `familyId`, and `isloggedaccount`
as the **string** `"true"`/`"false"`. Exactly one member has
`isloggedaccount: "true"` — that is the authenticated person.

`a01` returns profiles keyed by account ID; v1 does not need it, but the batched
call is sent because it costs nothing and the contract records it.

The derived calendar ID is `calendar/{family_id}`. It is the server's own
identifier — but **the server ignores the `calendarId` request parameter**, so
it is never an access boundary and must not be presented as a selector.

## The single-family rule — a hard requirement

`a00.r.r` was observed as one object for a one-family account. The shape for a
multi-family account is **unknown**. The rule is therefore:

- exactly one family object → build a verified `FamilyContext`
- an **array** of one → accept it, same as above
- an array of more than one, or any other shape that could imply several
  families → raise `UnsupportedConfigurationError` naming the limitation
- a missing or empty `family_id` → `MalformedPayloadError`

Never pick the first of several. Never guess. There is no evidence any
family-selection request exists, so **do not write a family-switching code
path** — not even an unused one.

## What to build in `discovery.py`

```python
class FamilyMember(DomainModel):
    account_id: str
    display_name: str
    first_name: str | None
    timezone: str | None  # IANA name as received, unvalidated
    is_authenticated_member: bool


class DiscoveredFamily(DomainModel):
    family_id: str  # bare numeric form
    family_meta_id: str  # "family/<id>"
    calendar_id: str  # derived "calendar/<id>"
    name: str
    members: tuple[FamilyMember, ...]


def build_discovery_fields() -> dict[str, str]: ...
def parse_discovery(payload: object) -> DiscoveredFamily: ...
```

`parse_discovery` enforces the single-family rule above. Import `DomainModel`
from `familywall_mcp.models`; import errors from `familywall_mcp.errors`.

`DiscoveredFamily.to_context()` (or a free function) produces the existing
`FamilyContext` from `familywall_mcp.models`, filling `account_id` from the
authenticated member. If no member has `isloggedaccount: "true"`, raise
`MalformedPayloadError` — an unverified context must never be constructed.
`UnknownFamilyContext` already exists for untrusted discovery data; use it
rather than inventing a parallel type.

## What to build in `services/session.py`

A per-principal session pool. The stdio server has one principal today, but the
isolation properties must hold now, because retrofitting them later is how
tenant bugs happen.

```python
class SessionPool:
    def __init__(
        self, *, client_factory, clock, max_idle_seconds: float = 900, max_sessions: int = 32
    ) -> None: ...
    async def acquire(self, principal: Principal) -> AuthenticatedSession: ...
    async def invalidate(self, principal: Principal) -> None: ...
    async def aclose(self) -> None: ...
```

Required behaviour, each of which is a test:

1. **Keyed by principal subject.** Two principals never share a session, a
   cookie jar, or an httpx client. Assert the underlying clients differ.
2. **One login lock per principal.** Ten concurrent `acquire` calls for the same
   principal perform exactly **one** login. Assert the login call count is 1.
   Concurrent `acquire` calls for *different* principals are not serialised
   behind each other.
3. **Credential generation.** A credential change invalidates that principal's
   session and only that principal's. Model it as a generation counter or
   credential fingerprint supplied by the credential provider; a session created
   under an old generation is never reused.
4. **Bounded read reauth.** A `SessionExpiredError` raised by a **read** may
   trigger **at most one** re-login and one retry. A second expiry propagates.
5. **Writes are never retried.** A `SessionExpiredError` from a write propagates
   immediately, with no re-login and no resend. `taskcreate` is not idempotent;
   a retried write adds a second item. Make the read/write distinction an
   explicit parameter the caller must pass, not something inferred from the
   endpoint name.
6. **Idle eviction.** A session untouched for `max_idle_seconds` is closed and
   dropped. Use the injected clock; never call `time.time()` directly.
7. **Bounded size.** Beyond `max_sessions`, the least recently used session is
   evicted and closed. No unbounded growth.
8. **`aclose` closes every client**, and is safe to call twice.
9. Nothing logs or reprs a password, cookie, or CSRF token.

The pool caches the `DiscoveredFamily` / `FamilyContext` per principal alongside
the session, so a tool call does not re-run discovery on every request; a
re-login discards the cached context and rediscovers.

## Tests — the acceptance criteria

Use fakes, not network. `tests/conftest.py` already forbids real requests —
read it and use its conventions. `tests/support/discovery_fixtures.py` holds
synthetic payloads: a normal one-family payload, a single-element array payload,
a two-family array payload, one with no authenticated member, one with a missing
`family_id`.

Cover all nine pool behaviours above plus:
10. A one-family object parses; `calendar_id == "calendar/" + family_id`.
11. A single-element array parses identically.
12. A two-element array raises `UnsupportedConfigurationError`, and the error's
    user-facing message names the limitation without leaking the family names.
13. A payload with no `isloggedaccount: "true"` member raises
    `MalformedPayloadError` and produces no `FamilyContext`.
14. A member's string booleans and IANA timezone parse correctly; an absent
    `timeZone` yields `None` rather than a default.
15. Concurrent A/B principals reading at the same time never see each other's
    cookies or family context. This is the isolation test the lead will read
    first — make it convincing.

## Checks to run before handing back

```
uv run ruff check .
uv run ruff format .
uv run mypy src
uv run pytest -m 'not live'
```

`mypy` is strict. If a check fails in a file you did not create, say so in the
handoff and do not touch that file.

## Handoff

Write `docs/handoffs/02b-discovery.md` from `docs/templates/handoff.md`, and
state explicitly whether the 02A client API matched what this brief predicted.
