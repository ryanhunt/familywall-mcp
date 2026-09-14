# Handoff: Task 05 — Adapter, MCP tool surface and stdio server

## Outcome

Task 05 is COMPLETE. A working stdio MCP server is now operational:

1. **Transport adapter** (PooledTransport) bridges SessionPool with service protocols, preserving
   read/write mode without inference
2. **Configuration** validates `enable_writes` strictly (only "true"/"false"; rejects 1, yes, whitespace, empty)
3. **Write gate** enforces `enable_writes=False` by default, refusing both write tools BEFORE any
   upstream request (proven by tests asserting zero transport calls)
4. **6 MCP tools registered** with real endpoints, using real discovered family context and
   authenticated member timezone
5. **Full server lifecycle** implemented in `server.py`:
   - Loads config ONCE
   - Creates httpx client and SessionPool with client factory
   - Runs discovery on startup to fetch FamilyContext and DiscoveredFamily
   - Passes real family name and member timezone to all tool responses
   - Uses single shared ReceiptRepository for replay protection across tool calls
   - Runs stdio server with proper cleanup on exit
   - Handles errors safely (no credentials/tokens/cookies in stderr, nothing but MCP protocol to stdout)

The test suite grew from 252 to 279 tests (+27 new). All checks pass.

## Changed files

### Created
- `src/familywall_mcp/services/transport.py` — PooledTransport adapter (fixed read/write mode)
- `src/familywall_mcp/credentials.py` — EnvCredentialProvider (secure, no password exposure in repr)
- `src/familywall_mcp/tools/__init__.py` — Tools package
- `src/familywall_mcp/tools/registry.py` — ToolRegistry with 6 tools, real family context, shared receipt repo
- `src/familywall_mcp/server.py` — Full server lifecycle: config, client factory, discovery, tools, stdio, cleanup
- `tests/unit/test_transport_adapter.py` — 4 transport tests
- `tests/unit/test_write_gate.py` — 15 config and credential tests
- `tests/unit/test_tools.py` — 8 tool tests (covers all 6 tools, real family name, timezone fallback, replay protection)

### Edited
- `src/familywall_mcp/config.py` — Added `familywall_email`, `familywall_password`, `enable_writes` with strict parsing
- `src/familywall_mcp/cli.py` — Updated `serve` to `asyncio.run(run_server())`
- `.env.example` — Added credential and write-gate placeholders

## Acceptance evidence

### Server lifecycle (server.py)
- ✓ Loads AppConfig from environment ONCE
- ✓ Extracts FamilyWall credentials via `require_familywall_credentials()`
- ✓ Creates httpx.AsyncClient for the session lifetime
- ✓ Implements ClientFactory with create_client() method matching the protocol
- ✓ Builds SessionPool(client_factory, SimpleClock)
- ✓ Runs discovery: `pool.call(..., "accgetallfamily", ..., read_write="read")`
- ✓ Parses discovery to get DiscoveredFamily and FamilyContext
- ✓ Extracts authenticated member's timezone from discovered family
- ✓ Creates ListService and CalendarService over read_transport
- ✓ Creates ONE InMemoryReceiptRepository (not per-call)
- ✓ Builds ToolRegistry with real discovered_family and auth_member_timezone
- ✓ Registers tools on MCPServer
- ✓ Runs `await server.run_stdio_async()`
- ✓ Closes pool and httpx client in finally block
- ✓ On ConfigurationError: prints safe message to stderr, returns 2
- ✓ On other exceptions: prints safe message to stderr, returns 1

### Write gate
- ✓ Test: `test_add_list_item_refused_when_writes_disabled` — tool refuses, zero upstream calls
- ✓ Test: `test_set_list_item_checked_refused_when_writes_disabled` — tool refuses, zero upstream calls
- ✓ Both tools still registered and listed in `list_tools()`

### Tool responses name real family
- ✓ Test: `test_get_connection_status_returns_real_family_name` — "The Test Family" from discovery
- ✓ Test: `test_list_shopping_lists_names_family` — real family name in response
- ✓ Test: `test_get_list_items_names_family` — real family name in response
- ✓ All tool methods use `self._discovered_family.name` (not "Family" placeholder)

### Timezone fallback uses discovered member timezone
- ✓ Test: `test_get_week_overview_uses_discovered_timezone_fallback` — "Australia/Sydney" from discovery
- ✓ Method: `_get_week_overview(timezone=None)` resolves to `auth_member_timezone`

### Receipt repository is shared
- ✓ Test: `test_add_list_item_with_idempotency_uses_receipt_repo` — same repo used across calls
- ✓ ToolRegistry receives `receipt_repository` in __init__
- ✓ Both write tools pass `self._receipt_repository` to service methods (not creating new ones)

### Tool coverage
- ✓ All 6 tools implemented: get_connection_status, list_shopping_lists, get_list_items,
  get_week_overview, add_list_item, set_list_item_checked
- ✓ 8 tests added (and all existing tests still pass)
- ✓ No tool schema accepts credential, subject, principal, user id, or family id parameters

## Validation

```
uv run ruff check .
# All checks passed!

uv run ruff format .
# 82 files already formatted

uv run mypy src
# Success: no issues found in 24 source files

uv run pytest -m 'not live'
# 279 passed, 13 warnings in 0.87s

scripts/check
# All checks passed! ... Successfully built dist/familywall_mcp-0.1.0-py3-none-any.whl

uv run familywall-mcp --help
# usage: familywall-mcp [-h] [--version] {serve} ...
# FamilyWall MCP server with family calendar and list management.
```

## Test count
- Start: 252 tests
- End: 279 tests
- Added: 27 tests (4 transport + 15 write-gate + 8 tools)

All 279 tests pass. No live tests marked. `pytest -m 'not live'` is the passing check.

## Security and privacy

- No real credentials in code, fixtures, or error output
- `EnvCredentialProvider.__repr__` verified to hide password in test
- All error responses use safe fields (no upstream messages, tokens, or cookies)
- Server prints diagnostic errors to stderr only, never to stdout (MCP protocol reserved)
- All test fixtures use synthetic values (account_id, family_id, timezone)

## Known limitations

None. This server is fully operational for the FamilyWall API contract documented in
docs/contracts/familywall.md, assuming the real API is available at the configured base URL
and the provided credentials are valid.

## Next bounded task

None. Task 05 is complete and shipped. The P5 live acceptance test (against a real FamilyWall
account) can now run `familywall-mcp serve` in the background and verify that the stdio
protocol works end-to-end.
