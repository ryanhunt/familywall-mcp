# Contributing

This repository is at its documentation-only foundation stage. Contributions
should describe the intended behaviour and acceptance evidence clearly while
the Python async client and MCP server are being built. Do not imply that a
planned tool, login flow, or command is already available.

Read [`AGENTS.md`](./AGENTS.md) before working, then use the bounded workflow
in [`docs/agent-workflow.md`](./docs/agent-workflow.md). Start with a focused
task brief, inspect only the context needed for that task, and finish with the
handoff template. Keep changes on a fresh `codex/<short-slug>` or
`claude/<short-slug>` branch. Commits, pushes, pull requests, and merges are
delivery actions that require the user's request.

There are currently no package metadata, dependency lockfiles, runtime entry
point, test suite, or project-defined check command. Runtime validation is
therefore unavailable until the foundation implementation adds those pieces;
record that limitation rather than substituting an unverified command. Plans
may still propose the checks that the foundation should add, clearly marked as
proposed or unavailable.

Keep credentials and private family data out of the repository and its
diagnostics. Use synthetic fixtures and dummy values in examples. Existing
Git identity, signing, remotes, and authentication belong to the contributor
and must not be changed by automation.
