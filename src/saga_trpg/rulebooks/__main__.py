"""Check a rulebook file: `python -m saga_trpg.rulebooks check path/to/book.json`."""

from __future__ import annotations

import argparse
import sys

from saga_trpg.rulebooks.loader import RulebookError, load_rulebook


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="saga_trpg.rulebooks", description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    check = commands.add_parser("check", help="validate a rulebook and list its actions")
    check.add_argument("path")
    args = parser.parse_args(argv)
    try:
        spec = load_rulebook(args.path)
    except RulebookError as exc:
        print("rulebook problems:", file=sys.stderr)
        for problem in str(exc).split("; "):
            print(f"  - {problem}", file=sys.stderr)
        return 1
    print(f"{spec.id}@{spec.version}: {spec.title}")
    print(f"  actions: {', '.join(spec.actions)}")
    print(f"  conditions: {', '.join(spec.conditions) or 'none'}")
    print(f"  items: {', '.join(spec.items) or 'open keys'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
