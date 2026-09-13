"""Console skeleton; real MCP tools are deliberately deferred to later phases."""

from __future__ import annotations

import argparse

from .config import AppConfig
from .errors import FamilyWallError


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="familywall-mcp",
        description="FamilyWall MCP foundation (no FamilyWall tools are available yet).",
    )
    parser.add_argument("--version", action="version", version="%(prog)s 0.1.0")
    subparsers = parser.add_subparsers(dest="command")
    serve = subparsers.add_parser("serve", help="validate configuration and show foundation status")
    serve.add_argument("--mode", choices=("stdio", "hosted"), help="optional mode override")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.command != "serve":
        build_parser().print_help()
        return 0
    try:
        config = AppConfig.from_env(
            {
                **__import__("os").environ,
                **({"FAMILYWALL_MODE": args.mode} if args.mode else {}),
            }
        )
    except FamilyWallError as exc:
        print(f"configuration error: {exc.info.message}")
        return 2
    mode = config.mode.value
    print(f"configuration valid ({mode}); MCP server and FamilyWall tools are deferred")
    return 0
