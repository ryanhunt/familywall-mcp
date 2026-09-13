# Handoff

## Outcome

Phase 1 foundation tasks 01A and 01B are implemented on the current branch. The
repository has a src-layout package, deterministic configuration/contracts,
offline test doubles, project checks, and low-permission CI. No real FamilyWall
endpoint, MCP SDK, OAuth provider, or tool is claimed.

## Changed files

- `pyproject.toml`, `uv.lock`: package metadata, minimal runtime dependency,
  development checks, and locked dependency graph.
- `src/familywall_mcp/`: config, safe errors, domain models, protocols, and CLI
  foundation skeleton.
- `tests/`: network-blocking fixture, synthetic envelopes, fake cookies/clock,
  request capture, rejecting fake upstream, and substantive config/error tests.
- `scripts/check`, `.github/workflows/check.yml`: reproducible local/CI checks,
  frozen install, build, and secret detection.
- `.env.example`, `README.md`, `CONTRIBUTING.md`, `AGENTS.md`, `docs/tasks.md`:
  dummy settings, actual commands, truthful status, and task evidence.
- `docs/briefs/phase1-foundation.md`, `docs/attribution.md`: completed task brief
  and attribution decision (no third-party source copied in Phase 1).

## Acceptance evidence

- Criterion: hosted config requires public URL and a minimum-length auth key;
  tests cover missing, invalid, and valid settings without rendering secrets.
- Criterion: unverified family contexts cannot be passed as verified contexts;
  malformed success/error envelopes fail validation.
- Criterion: fake upstream rejects calls outside its allow-list; autouse pytest
  fixture blocks external sockets unless explicit live opt-in is set.
- Criterion: CI has read-only repository permissions and uses locked installation.

## Validation

- Command and result: `uv sync --frozen --group dev` succeeded in a fresh
  temporary environment using the committed lockfile.
- Command and result: `scripts/check` passed: Ruff lint and format, mypy, 18
  offline pytest cases, wheel/sdist build, and the detect-secrets baseline scan.
- Command and result: the detect-secrets scan passed with generated/cache paths
  excluded; the baseline records two reviewed false positives: a source-contract
  keyword in `docs/research.md` and a dummy short key in `tests/unit/test_config.py`.
  No live network or credentials were used.

## Security and privacy review

- Credentials, tokens, cookies, real family data, generated state, and `.env`
  contents are absent from the diff and fixtures.
- Synthetic values use `.invalid` URLs, `synthetic-*` IDs, and dummy key text only.

## Known limitations

- P0 has not resolved the exact MCP SDK/OAuth provider, so the SDK is deferred.
- The CLI validates settings only; it does not start a server or expose tools.
- Runtime FamilyWall transport, endpoint contracts, persistence, OAuth, and live
  acceptance remain future tasks.

## Next bounded task

P2 task 02A: transport, login, envelope parsing, and safe session errors after P0
wire-contract evidence is available.
