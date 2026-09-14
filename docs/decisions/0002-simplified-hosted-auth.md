# ADR 0002 — Simplified hosted multi-user authentication

Status: **accepted** (P5/P6 implementation), 2026-09-14. Supersedes portions of
[architecture.md](../architecture.md) regarding account self-service, invitations,
and encrypted per-user credential storage.

## Context

[ADR 0001](0001-auth-and-sdk.md) established that this project would build a
self-hosted OAuth authorization server for a small group of invited family members,
each signing in with their own MCP username/password and their own FamilyWall
credentials. The original [architecture.md](../architecture.md) design included a
full self-service account flow: invitation generation, per-user account pages, and
an encrypted-at-rest credential database. This scope was reviewed against the actual
deployment constraints — a small, trusted household group with the operator already
managing configuration via `.env` — and deliberately reduced. This ADR records that
decision and its consequences.

## Evidence

1. **Deployment model:** The operator configures the entire server stack including
   all `.env` variables. They already maintain `.env` for the stdio mode (containing
   raw `FAMILYWALL_EMAIL` and `FAMILYWALL_PASSWORD`). Multi-user configuration is
   a natural extension of this pattern rather than a new responsibility.

2. **Precedent:** The sibling `halaxy-mcp` project uses a single shared login checked
   against `.env` and successfully deployed to a small team. This project extends
   that to a per-user dict of credentials in the same `.env` namespace, using the
   same security properties.

3. **Unused surface area:** Invitations, account pages, password recovery, and
   password changes are infrastructure that no invited user would exercise during
   the first deployment phase (a small household, operator-configured). Building and
   hardening an unused flow carries unacceptable cost.

4. **Actual implementation scope:** P5 and P6 together deliver static multi-user
   support: numbered `.env` variables (`FAMILYWALL_USER_1_*`, `FAMILYWALL_USER_2_*`,
   etc.) mapping each MCP login to FamilyWall credentials; a real OAuth authorization
   server backed by SQLite; and Docker/Caddy/HTTPS packaging. This was implemented
   and verified to work.

## Decision

1. **Remove self-service signup, invitations, and account pages** from the planned
   scope. Each user (up to a reasonable cap, currently 20) is operator-configured
   via `FAMILYWALL_USER_<N>_*` environment variables specifying both MCP credentials
   and FamilyWall credentials together.

2. **Keep FamilyWall credentials in plaintext `.env`** (consistent with stdio mode's
   existing `FAMILYWALL_EMAIL` and `FAMILYWALL_PASSWORD`). Do not build an
   encrypted-at-rest credential database. The operator's `.env` file is the only
   secrets store; the project does not add a second, harder-to-secure one.

3. **Harden what remains:** Implement the OAuth protocol correctly. Issue hashed
   (not plaintext) authorization codes and tokens in SQLite. Rotate refresh tokens
   on every use. Detect refresh-token replay and revoke the subject's entire token
   family. Maintain a redirect-URI allowlist. Protect the login form with a signed
   CSRF double-submit cookie. Rate-limit login attempts per source IP. Store nothing
   plaintext in the database; hash everything at rest.

4. **Validate MCP usernames** against a safe character set (letters, digits, `.`,
   `_`, `-`; 1–100 characters) and enforce uniqueness case-insensitively.

5. **Cap the number of configured users** at 20 to prevent indefinite env-var scanning
   from silently accepting a typo'd gap in the numbered sequence.

## Consequences

- **Removed:** No invitations, no account pages, no per-user encrypted credential
  storage, no password-change or account-recovery flows, no self-service anything.
  These remain design artifacts in [architecture.md](../architecture.md) but are
  explicitly not built.

- **Bound by operator knowledge:** The operator must know the FamilyWall credentials
  for each invited user upfront. The server does not accept user-entered credentials
  nor validate them at setup time; validation happens at MCP login, when the OAuth
  provider tries to authenticate the user against FamilyWall and fails openly if
  the credentials are wrong. Errors are recorded; recovery requires operator
  intervention via `.env` edit and restart.

- **Simpler deployment:** Configuration is `cp .env.example .env && edit .env` and
  `docker compose -f docker-compose.prod.yml up -d`. No database migrations for
  user accounts, no UI to secure, no forgotten-password handling.

- **Credential lifetime:** MCP login credentials (usernames and passwords) are
  configured once and never self-changeable. If a user's password must change,
  the operator edits `.env` and redeploys. This is acceptable for a small,
  operator-managed household.

- **FamilyWall credentials:** Stored plaintext in `.env` like the stdio mode. A
  future phase might add encryption if the deployment grows beyond a fully-trusted
  team, but that is not in scope for P5/P6.

- **Placeholder for OAuth-backed login:** Should a future deployment require
  user-driven account management (multi-tenant SaaS, open-to-family-friends, etc.),
  the OAuth protocol surface (`/authorize`, `/token`, `/login`) is unchanged; only
  the subject resolution in `authorize()` would change from checking `.env` to
  querying an accounts database. The MCP endpoint and client-side code remain
  unchanged.

## Not decided here

Whether CIMD (Client ID Metadata Documents) will be exercised with Claude in P6
remains unproven pending actual HTTPS deployment. DCR (dynamic client registration)
is the working assumption and both are supported by the provider.
