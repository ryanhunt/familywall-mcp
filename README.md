# FamilyWall MCP

A planned Python MCP server for using FamilyWall from ChatGPT and Claude.
Not affiliated with FamilyWall.

The first release will support shopping-list additions and completion, plus a
timezone-aware view of the family's week. A few invited family members will each
have a self-hosted MCP login linked to their own encrypted FamilyWall credentials.

**Current status:** research, architecture, implementation plan and shared AI
development guidance are in place. The Python server, tools and login flow have
not been implemented or tested against a live FamilyWall account.

- [Implementation plan](docs/implementation-plan.md): phases, dependencies and release acceptance.
- [Delegable task cards](docs/tasks.md): bounded work and copyable prompts for cheaper agents.
- [Architecture](docs/architecture.md): account mapping, credentials, tools and deployment.
- [Research](docs/research.md): pinned repository evidence and unresolved API behavior.
- [AI workflow](docs/agent-workflow.md): shared Claude Code/Codex conventions.
- [Contributing](CONTRIBUTING.md): workflow and validation status.

Start implementation with the P0 contract and compatibility tasks. There are no
installation or server-start commands yet. Future phases must verify FamilyWall
session/family selection and recurring calendar behavior before claiming support.

Inspired by [ryanhunt/halaxy-mcp](https://github.com/ryanhunt/halaxy-mcp) and based on
protocol research in [ryanhunt/familywall-api](https://github.com/ryanhunt/familywall-api),
which credits [Tomsoz](https://github.com/Tomsoz/familywall-api) and
[CodingButter](https://github.com/CodingButter/familywall-api).
Licensed under [MIT](LICENSE).
