# Task 02A — FamilyWall transport, envelope and login

Owner: delegated. Lead reviews before integration.

## File boundary — do not edit anything else

Create:
- `src/familywall_mcp/familywall/__init__.py`
- `src/familywall_mcp/familywall/wire.py`
- `src/familywall_mcp/familywall/client.py`
- `tests/unit/test_wire.py`
- `tests/unit/test_client.py`

**Do not edit** `pyproject.toml`, `uv.lock`, `models.py`, `config.py`,
`errors.py`, `interfaces.py`, `cli.py`, any existing test, or any doc. The error
classes you need already exist in `errors.py`. If you believe you need a change
outside your boundary, stop and record it in the handoff instead.

## Facts (live-verified 2026-09-13 — do not re-derive, do not "improve")

Transport: `POST https://api.familywall.com/api/<endpoint>`, body
`application/x-www-form-urlencoded; charset=UTF-8`, every request carries
`partnerScope=Family`.

Envelope, always HTTP 200:

| Shape | Meaning |
| --- | --- |
| `{"a00": {"r": {"r": <result>}, "cn": "<endpoint>"}}` | success; unwrap `a00.r.r` |
| `{"a00": {"un": {"un": {"FiZClassId": "501", "message": "..."}}, "cn": ...}}` | not permitted — expired session, missing CSRF header |
| `{"a00": {"un": {"un": {"FiZClassId": "502", "message": "..."}}, "cn": ...}}` | request rejected — bad identifier, unparseable date |
| `{"a00": {"ex": {"ex": {"FiZClassId": "3", "message": "bad password"}}, "cn": ...}}` | application error |

A message containing `NOAUTHENT` (observed: `Api <endpoint> is not allowed by
ruleset NOAUTHENT`) means the session is gone → `SessionExpiredError`.
`FiZClassId` `501` without `NOAUTHENT` is also an auth failure. `502` and `ex`
envelopes are `UpstreamRejectedError`. Never put the upstream `message` into a
user-facing error; it may go into a log line labelled with the endpoint only.

Login (`log2in`), fields exactly:
`partnerScope=Family`, `transactional=true`, `a00identifier=<email>`,
`a00password=<password>`, `a00generateAutologinToken=true`, `a01call=log2get`.
Response `a00.r.r` carries `accountId` and `tokenCsrf` (32 chars). The response
sets cookies `AWSALB`, `AWSALBCORS`, `JSESSIONID`; **only `JSESSIONID` is
required**. Bad password returns the `ex` envelope above →
`AuthenticationError`, and it must be distinguishable from a transport failure.

Authenticated calls need **both**:
- cookie header `JSESSIONID=<value>` (send only this cookie, not the ALB ones)
- request header `tokencsrf: <tokenCsrf from login>`

Omitting `tokencsrf` returns `un`/`501`/`Wrong anti csrf token=null`.

`webset` / `webget` are **not** required — do not implement them. Do not send
analytics cookies, a constant `deviceId`, or a browser `User-Agent`; a live
probe confirmed none of them are needed.

## What to build

`wire.py` — pure, no I/O:
- `encode_form(fields: Mapping[str, str]) -> str` or rely on httpx's encoder;
  either way UTF-8 must round-trip (test with `"Müsli"` and an emoji).
- `parse_envelope(body: str | bytes, endpoint: str, key: str = "a00") -> object`
  returning the unwrapped `a00.r.r`, raising the right typed error for each of
  these six separable failures:
  1. body is not valid JSON → `InvalidEnvelopeError`
  2. body is HTML (starts with `<`, or content looks like a login page) →
     `SessionExpiredError`
  3. top-level is not an object, or the `aNN` key is missing →
     `InvalidEnvelopeError`
  4. `un` present → `SessionExpiredError` (NOAUTHENT or 501) else
     `UpstreamRejectedError`
  5. `ex` present → `UpstreamRejectedError`, except `log2in` where it is
     `AuthenticationError`
  6. `r` present but not shaped `{"r": ...}` → `InvalidEnvelopeError`
  Every raised error's `ErrorInfo.endpoint` must be set to `endpoint`.
- A `coerce_bool(value) -> bool` helper: upstream booleans are the **strings**
  `"true"`/`"false"`. Accept real bools too. Anything else raises
  `MalformedPayloadError`.

`client.py` — one class, `FamilyWallSession`, holding a `httpx.AsyncClient`:
- Constructed with a base URL and an injected `httpx.AsyncClient` (so tests can
  pass a mock transport). Do **not** create a module-level client, and do not
  use a global cookie jar.
- `async def login(self, username: str, password: str) -> None` — performs
  `log2in`, stores `JSESSIONID` and `tokenCsrf` on the instance, raises
  `AuthenticationError` on a bad password.
- `async def call(self, endpoint: str, fields: Mapping[str, str], *, extra_keys: Sequence[str] = ()) -> object`
  — sends an authenticated request and returns the unwrapped `a00.r.r`.
  `extra_keys` lets a caller also read batched replies (return them via a
  separate method or a dataclass; your choice, but keep it typed).
- Not logged in yet → raise `AuthenticationError`, do not auto-login.
- Bounded timeouts (connect and read), configurable, defaulting to something
  sane like 15s. Non-2xx HTTP → `TransportError`; 429 → `RateLimitedError`;
  `httpx.TimeoutException` / `httpx.TransportError` → `TransportError`.
- **No automatic retry of anything.** Reauthentication is a later task's
  concern; this class only reports `SessionExpiredError`.
- Nothing may log or stringify a password, cookie, or CSRF token. `__repr__`
  must not expose them.

## Tests — these are the acceptance criteria

Use `respx` (already a dev dependency) to mock httpx. Network must never be
touched; no test may be marked `live`.

Required cases:
1. Login success stores the session; a second call sends both the
   `JSESSIONID` cookie and the `tokencsrf` header, and sends **only**
   `JSESSIONID` as a cookie.
2. Login with the `ex`/`bad password` envelope raises `AuthenticationError`.
3. A response with multiple `Set-Cookie` headers still yields `JSESSIONID`.
4. A login response with no `JSESSIONID` raises `AuthenticationError`.
5. Unicode field values round-trip in the form body.
6. `un`/`501`/`NOAUTHENT` raises `SessionExpiredError`.
7. `un`/`502` raises `UpstreamRejectedError`.
8. HTML body on HTTP 200 raises `SessionExpiredError`.
9. Invalid JSON raises `InvalidEnvelopeError`.
10. Missing `a00` raises `InvalidEnvelopeError`.
11. `a00.r` without a nested `r` raises `InvalidEnvelopeError`.
12. HTTP 401, 403 and 500 raise `TransportError`; 429 raises `RateLimitedError`.
13. A transport timeout raises `TransportError`.
14. No raised error's `message`, `recovery`, or `str()` contains the upstream
    `message` text, the password, or the CSRF token. Assert this explicitly.
15. `coerce_bool` accepts `"true"`, `"false"`, `True`, `False`; rejects
    `"yes"`, `1`, `None`.

## Checks to run before handing back

```
uv run ruff check .
uv run ruff format .
uv run mypy src
uv run pytest -m 'not live'
```

`mypy` runs in strict mode and must pass with no `type: ignore` unless you
justify each one in the handoff. Then `git diff` your own changes and confirm
no secrets, no real data, and nothing outside your file boundary.

## Handoff

Write `docs/handoffs/02a-transport.md` using `docs/templates/handoff.md`:
changed files, the commands you ran and their results, anything you could not
do, and anything in this brief that turned out to be wrong.
