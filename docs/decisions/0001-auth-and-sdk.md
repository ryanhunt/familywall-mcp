# ADR 0001 — MCP SDK and self-hosted OAuth

Status: **accepted** (P0 task 00C), 2026-09-13. Supersedes the placeholder
assumptions in [research](../research.md) about SDK import paths and OAuth
components.

## Context

The hosted service must be a remote MCP server that both Claude and ChatGPT can
add as a custom connector, where each invited family member signs in as
themselves. The owner requires the identity provider to be self-hosted; an
external IdP is not an acceptable fallback. Before P5 can be scoped we must know
which OAuth responsibilities the MCP SDK discharges and which we build.

## Evidence

Two independent sources were used, and they disagreed. Where they conflict, the
executed spike wins and the disagreement is recorded below.

**Spike (primary).** A disposable virtualenv on Python 3.12 outside the project
tree, installing the SDK from PyPI and exercising it. Results:

| Observation | Result |
| --- | --- |
| Latest installable `mcp` | **2.2.0** (with `mcp-types` 2.2.0) |
| Modern server class | `mcp.server.MCPServer` |
| Structured output | `@server.tool()` on a function returning a pydantic model emits a JSON Schema `output_schema` automatically |
| Protocol field naming | **snake_case** (`tool.input_schema`, not `inputSchema`) |
| Resource-server hook | `mcp.server.auth.provider.TokenVerifier` |
| Authorization-server hook | `mcp.server.auth.provider.OAuthAuthorizationServerProvider` |
| Subject inside a handler | `mcp.server.auth.middleware.auth_context.get_access_token()` returns an `AccessToken` with `token`, `client_id`, `scopes`, `expires_at`, `resource`, `subject`, `claims` |
| Routes with `token_verifier` only | `/mcp`, `/.well-known/oauth-protected-resource/mcp` |
| Routes with `auth_server_provider` | adds `/.well-known/oauth-authorization-server`, `/authorize`, `/token`, `/register`, `/revoke` |

**Documentation review (secondary).** MCP authorization spec revision
`2026-07-28`; PKCE and the RFC 8707 `resource` parameter required; RFC 9728
protected-resource metadata and RFC 8414 authorization-server metadata required;
`MCP-Protocol-Version` declared per request over Streamable HTTP. Both Claude and
ChatGPT support RFC 7591 dynamic client registration; ChatGPT requires Streamable
HTTP over HTTPS and will not accept a bare bearer token; Claude's documented
callback is `https://claude.ai/api/mcp/auth_callback`.

### Recorded disagreement

The documentation pass reported the current release as 2.1.0 and concluded the
SDK ships **no** authorization-server implementation. The spike installed 2.2.0
and mounted a full set of authorization-server routes from a provider instance.
The documentation conclusion is therefore rejected on the version number and
substantially wrong on capability: the SDK supplies the OAuth *protocol surface*
(endpoints, metadata, PKCE plumbing) and delegates *storage and policy* to the
provider implementation. Anyone revisiting this must re-run the spike rather than
trust either summary.

## Decision

1. **Pin `mcp` 2.2.0** and the modern `MCPServer` API. Do not use v1 `FastMCP`
   import paths, including those in the Halaxy reference, which predate this API.
2. **Embed the authorization server in this service** by implementing
   `OAuthAuthorizationServerProvider` over the project's SQLite storage. Do not
   add Authlib. Authlib would duplicate the endpoint surface the SDK already
   mounts and add a second set of security-relevant code to review; the work we
   actually own — clients, codes, tokens, rotation, revocation — is storage and
   policy, which Authlib does not do for us.
3. **Enable dynamic client registration** (`ClientRegistrationOptions`) and
   **revocation** (`RevocationOptions`). Both target clients support DCR, and it
   avoids per-client manual provisioning for a household-sized deployment.
   Registration is open by protocol necessity; abuse is bounded by the fact that
   registering a client grants nothing without a user login, plus rate limiting
   and retention limits on the client table.
4. **Use `AccessToken.subject` as the MCP user ID** — a random, stable, opaque
   identifier issued by this service. It is the only key linking a request to an
   encrypted FamilyWall credential record. `client_id` is explicitly *not* an
   identity.
5. **Transport:** Streamable HTTP via `MCPServer.streamable_http_app()` mounted
   as an ASGI app behind Caddy for the hosted service; stdio for local developer
   checks only. SSE is not offered.
6. **Record the negotiated `MCP-Protocol-Version` per client** in
   [compatibility](../compatibility.md) rather than asserting one.

## Consequences

- P5 is scoped as *provider implementation plus storage*, not as building OAuth
  endpoints from scratch. The provider's ten methods (`get_client`,
  `register_client`, `authorize`, `load_authorization_code`,
  `exchange_authorization_code`, `load_refresh_token`, `exchange_refresh_token`,
  `load_access_token`, `revoke_token`, `exchange_identity_assertion`) become the
  storage interface's required surface.
- The login/consent HTML pages remain ours: `authorize` hands control to our
  session flow and receives a subject back. This is where the Halaxy reference's
  regression history (consent phishing, reflected XSS, expiring login state,
  login throttling) applies as a test list.
- Tools must read identity from `get_access_token()` per request and never from
  a server-lifetime variable, because one process serves several users.
- Any FamilyWall model that already uses camelCase field names must be checked
  against the SDK's snake_case protocol fields during P3/P4 tool work.
- A future SDK upgrade can change the provider protocol. The pin is exact and an
  upgrade is a reviewed change with the spike re-run, not a routine bump.

## Not decided here

Client ID Metadata Documents (Claude's preferred registration mode) were not
exercised; DCR is the chosen path and CIMD stays `pending-live` for P6. Whether
either client accepts this server end to end is unproven until a real HTTPS
endpoint exists, and remains the principal P6 risk.
