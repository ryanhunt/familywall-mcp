# Task 05 — Adapter, MCP tool surface and stdio server

Owner: delegated. **Lead reviews the write gate and the tool contracts, and runs
the live stdio check.**

This is the integration that makes the project runnable. Everything it depends
on is delivered, reviewed and committed. Do not modify any of it.

## File boundary

Create:
- `src/familywall_mcp/services/transport.py`
- `src/familywall_mcp/credentials.py`
- `src/familywall_mcp/tools/__init__.py`
- `src/familywall_mcp/tools/registry.py`
- `src/familywall_mcp/server.py`
- `tests/unit/test_transport_adapter.py`
- `tests/unit/test_tools.py`
- `tests/unit/test_write_gate.py`

Edit (you are the only agent working now):
- `src/familywall_mcp/config.py` — add exactly the three settings named below
- `src/familywall_mcp/cli.py` — make `serve` actually serve
- `.env.example` — add the new variables as placeholders only

**Do not edit** `pyproject.toml`, `uv.lock`, `models.py`, `errors.py`,
`interfaces.py`, anything under `familywall/`, `services/lists.py`,
`services/calendar.py`, `services/ranges.py`, `services/session.py`,
`storage/`, `tests/conftest.py`, existing tests, or any doc but your handoff.

## Verified SDK API — use exactly this

`mcp` 2.2.0 is installed. Verified by direct inspection:

```python
from mcp.server import MCPServer
server = MCPServer(name="familywall", version="0.1.0")

@server.tool(name="...", description="...", annotations=ToolAnnotations(...))
async def my_tool(...) -> SomeModel: ...

await server.run_stdio_async()
```

`ToolAnnotations` comes from `mcp.types`. Return a pydantic model from each tool
so structured output is generated automatically.

## 1. `services/transport.py` — the SessionPool adapter

`SessionPool.call(principal, endpoint, fields, read_write)` requires an explicit
`read_write: Literal["read", "write"]`. `ListTransport` and `CalendarTransport`
expect `call(endpoint, fields)`. Bridge them **without losing the distinction**:

```python
class PooledTransport:
    def __init__(self, pool: SessionPool, principal: Principal,
                 read_write: Literal["read", "write"]) -> None: ...
    async def call(self, endpoint: str, fields: Mapping[str, str]) -> object: ...
```

The mode is fixed when the adapter is constructed, so a read transport can never
send a write. Provide two factory helpers, `read_transport(...)` and
`write_transport(...)`. **Never infer the mode from the endpoint name.**

Tests: a read transport forwards `read_write="read"`; a write transport forwards
`"write"`; the fields and endpoint pass through unchanged.

## 2. `credentials.py` — stdio credential provider

Implement the existing `CredentialProvider` protocol from `interfaces.py`:

```python
class EnvCredentialProvider:
    def __init__(self, username: str, password: SecretStr) -> None: ...
    async def get_credentials(self, principal: Principal) -> FamilyWallCredentials: ...
```

It must not read `os.environ` itself — `config.py` loads the values and passes
them in, so the process boundary stays in one place. `__repr__` must not expose
the password. Test that.

## 3. `config.py` — exactly three new settings

Add to `AppConfig`, following the existing style (`extra="forbid"`,
`frozen=True`, `SecretStr` with `repr=False` for secrets):

| Field | Env var | Default | Notes |
| --- | --- | --- | --- |
| `familywall_email` | `FAMILYWALL_EMAIL` | `None` | |
| `familywall_password` | `FAMILYWALL_PASSWORD` | `None` | `SecretStr`, `repr=False` |
| `enable_writes` | `FAMILYWALL_ENABLE_WRITES` | **`False`** | parsed strictly |

`enable_writes` parsing: only the exact strings `"true"` and `"false"`
(case-insensitive) are accepted. **Anything else — including `"1"`, `"yes"`,
`"TRUE "` with whitespace, or an empty string — raises `ConfigurationError`.**
A malformed flag must never be read as enabled, and must never be silently
ignored either. Test every one of those cases.

Add a method `require_familywall_credentials()` that returns the pair or raises
`ConfigurationError` when either is missing. Do not change any existing field,
validator or error message. `from_env` must keep loading only documented
`FAMILYWALL_` variables and must never dump the environment.

## 4. `tools/registry.py` — the tool surface

Six tools. Every one returns a frozen pydantic model.

**Read tools** — `ToolAnnotations(readOnlyHint=True)`:

1. `get_connection_status()` → family display name, member count, the
   authenticated member's timezone, and whether writes are enabled. **Never**
   returns credentials, cookies, tokens, account IDs or the member list.
2. `list_shopping_lists()` → id, name, type (raw string preserved), total and
   remaining counts.
3. `get_list_items(list_id: str)` → the items of one list.
4. `get_week_overview(reference_date: str | None, timezone: str | None, week_starts_on: ...)`
   → the `WeekOverview`. A `None` timezone falls back to the authenticated
   member's timezone from discovery, and the resolved timezone is reported in
   the output. Never hard-code a timezone.

**Write tools** — `ToolAnnotations(readOnlyHint=False, destructiveHint=False, idempotentHint=False)`:

5. `add_list_item(text: str, list_id: str | None, quantity: str | None, idempotency_key: str | None)`
6. `set_list_item_checked(item_id: str, checked: bool, idempotency_key: str | None)`

Rules binding on every tool:

- **No tool accepts a username, password, subject, principal, user id or family
  id.** The principal comes from config; there is no user-switching parameter.
- Every response names the family the answer came from, so the caller can see
  which household replied.
- Errors are returned as the safe `ErrorInfo` shape via `FamilyWallError.as_dict()`.
  Never surface an upstream `message`, a raw body, a cookie or a token.
- Ambiguous list selection returns the candidates and writes nothing.
- A write reports its outcome as `confirmed`, `acknowledged` or `unknown`, and an
  `unknown` result must say plainly that the item's fate is unknown and was not
  retried.
- A written `quantity` is reported as **unverified**, because no quantity is
  readable back from this API.
- `idempotency_key` is optional. When absent, generate a fresh UUID4 per call,
  and state in the tool description that without a caller-supplied key a retry
  will create a duplicate item, because `taskcreate` is not idempotent.

## 5. The write gate — a safety property, test it hard

When `enable_writes` is `False`:

- `add_list_item` and `set_list_item_checked` must **refuse before doing
  anything**, returning a clear error saying writes are disabled and how to
  enable them.
- The refusal must happen **before** any upstream request. Prove it with a test
  asserting the fake transport recorded **zero** calls.
- `get_connection_status` reports `writes_enabled: false`.
- The two write tools must still be *registered* and listed, so a client can see
  they exist — they refuse when called, rather than vanishing.

When `enable_writes` is `True`, both tools work normally.

`tests/unit/test_write_gate.py` covers: refusal with zero upstream calls for
both tools; both tools still appear in `list_tools()`; the enabled path reaches
the service; and the malformed-flag config cases from section 3.

## 6. `server.py` and `cli.py`

`server.py` builds the `MCPServer`, constructs the config, session pool,
credential provider, services and tool registry, registers the tools, and runs
stdio. Own the lifecycle: close the pool and the httpx client on shutdown.

`cli.py`: `familywall-mcp serve` currently prints a placeholder. Make it start
the stdio server. Keep the existing `--mode` flag and the existing behaviour of
printing a safe message and returning exit code 2 on `ConfigurationError`.
**Nothing may be printed to stdout except MCP protocol traffic** once the server
starts — stdio is the transport, so any stray `print` corrupts the stream. Send
diagnostics to stderr.

## Tests

No network. No test may be marked `live`. Inject fakes for the pool and
services. Cover, at minimum:

1. Read transport forwards `"read"`; write transport forwards `"write"`.
2. Each of the six tools returns its model and names the family.
3. No tool schema accepts a credential, subject, principal or family parameter —
   assert this by inspecting the registered input schemas, so a future tool
   cannot quietly add one.
4. Write gate: both refusals with zero upstream calls; both tools still listed.
5. `enable_writes` parsing accepts `true`/`false` in any case and raises on
   `"1"`, `"yes"`, `""` and `"maybe"`.
6. A `FamilyWallError` from a service becomes a safe error response containing no
   upstream message text.
7. An ambiguous list selection returns candidates and sends no write.
8. An `unknown` write outcome is reported as unknown and is not retried.
9. `get_week_overview` with no timezone uses the discovered member timezone and
   reports which timezone it resolved to.
10. `EnvCredentialProvider.__repr__` does not expose the password.

## Checks

```
uv run ruff check .
uv run ruff format .
uv run mypy src
uv run pytest -m 'not live'
scripts/check
```

The suite is at 252 tests and must stay green. `mypy` is strict.

## Handoff

Write `docs/handoffs/05-stdio-integration.md` from `docs/templates/handoff.md`.
State plainly any acceptance criterion you did not cover with a test and why.
Do not defer a testable criterion to "integration testing".
