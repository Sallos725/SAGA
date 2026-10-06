"""Rulebook tools.

python -m saga_trpg.rulebooks check  path/to/book.json
python -m saga_trpg.rulebooks module path/to/book.json --out book.module.json
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from saga_trpg.rulebooks.loader import RulebookError, load_rulebook
from saga_trpg.rulebooks.risu_module import build_module


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="saga_trpg.rulebooks", description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    check = commands.add_parser("check", help="validate a rulebook and list its actions")
    check.add_argument("path")
    module = commands.add_parser("module", help="write a RisuAI module JSON carrying the rulebook")
    module.add_argument("path")
    module.add_argument("--out", help="output file (default: <rulebook>.module.json)")
    args = parser.parse_args(argv)
    try:
        spec = load_rulebook(args.path)
    except RulebookError as exc:
        print("rulebook problems:", file=sys.stderr)
        for problem in str(exc).split("; "):
            print(f"  - {problem}", file=sys.stderr)
        return 1
    if args.command == "module":
        text = Path(args.path).read_text(encoding="utf-8")
        out = Path(args.out) if args.out else Path(args.path).with_suffix(".module.json")
        out.write_text(
            json.dumps(build_module(text), ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        print(f"{spec.id}@{spec.version} -> {out}")
        print("Import it in PocketRisu: Settings → Modules → Import, then enable it.")
        return 0
    print(f"{spec.id}@{spec.version}: {spec.title}")
    print(f"  actions: {', '.join(spec.actions)}")
    print(f"  conditions: {', '.join(spec.conditions) or 'none'}")
    print(f"  items: {', '.join(spec.items) or 'open keys'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
