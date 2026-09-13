# Task brief

## Objective

Deliver a reproducible, typed Python foundation and offline validation harness for
the FamilyWall MCP project, without inventing upstream API or OAuth contracts.

## Scope

- In scope: 01A package/config/contracts and 01B harness/CI/documentation.
- Files or components allowed to change: package metadata, `src/`, `tests/`, CI,
  scripts, dummy environment example, and project guidance/status docs.
- Out of scope: FamilyWall endpoints, live HTTP, MCP tools, OAuth, persistence,
  credentials, and deployment.

## Context and evidence

- Relevant repository files: `AGENTS.md`, `docs/tasks.md`, `docs/architecture.md`,
  and `docs/implementation-plan.md` P1.
- Sibling or external conventions consulted: none; P0 SDK compatibility is not
  resolved, so the MCP SDK is intentionally deferred.
- Assumptions: Python 3.12+ and `uv` are the supported runtime/toolchain.
- Open questions that could change the result: exact MCP SDK and OAuth provider
  release remain P0 decisions.

## Acceptance criteria

- [x] Explicit stdio and hosted configuration with fail-closed hosted settings.
- [x] Typed principals, verified family context, safe errors, envelopes, and
  transport/credential/storage protocols.
- [x] Offline harness rejects unexpected calls and external network by default;
  CI runs frozen install, checks, build, and secret detection.

## Validation

- `uv sync --frozen --group dev` succeeded in a fresh temporary environment.
- `scripts/check` passed Ruff lint/format, mypy, 16 offline tests, build, and
  the detect-secrets baseline scan.
- Evidence: committed lockfile, substantive config/envelope tests, and a clean
  secret/scope review.

## Security and privacy

- Synthetic data and dummy credentials only.
- Sensitive data or state that must remain local: real `.env`, keys, cookies,
  credentials, account data, and live test state.
- Review points: no environment dumps, raw upstream body errors, or real data.

## Handoff target

P2 transport/session handshake after P0 contracts are complete.
