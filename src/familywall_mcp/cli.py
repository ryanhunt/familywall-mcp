"""Console entry point for the FamilyWall MCP server."""

from __future__ import annotations

import argparse
import asyncio
import sys


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="familywall-mcp",
        description="FamilyWall MCP server with family calendar and list management.",
    )
    parser.add_argument("--version", action="version", version="%(prog)s 0.1.0")
    subparsers = parser.add_subparsers(dest="command")
    serve = subparsers.add_parser("serve", help="start the MCP server on stdio")
    serve.add_argument("--mode", choices=("stdio", "hosted"), help="optional mode override")
    return parser


def main(argv: list[str] | None = None) -> int:
    """Main entry point for the CLI.

    Args:
        argv: Command-line arguments (defaults to sys.argv[1:]).

    Returns:
        Exit code.
    """
    args = build_parser().parse_args(argv)
    if args.command != "serve":
        build_parser().print_help()
        return 0

    # Import and run the server
    try:
        from .server import run_server

        return asyncio.run(run_server())
    except KeyboardInterrupt:
        return 130
    except Exception as exc:
        print(f"fatal error: {exc}", file=sys.stderr)
        return 1
