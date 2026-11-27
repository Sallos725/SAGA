"""Rulebook tools.

python -m saga_trpg.rulebooks check     path/to/book.json
python -m saga_trpg.rulebooks module    path/to/book.json --out book.module.json
python -m saga_trpg.rulebooks rpack-map <host dist/assets dir or js> --out rpack_map.bin
python -m saga_trpg.rulebooks risum     path/to/book.json --rpack-map rpack_map.bin
python -m saga_trpg.rulebooks charx     card.json [--rulebook book.json --rpack-map ...]
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from saga_trpg.rulebooks.charx import build_card_v3, write_charx
from saga_trpg.rulebooks.loader import RulebookError, load_rulebook
from saga_trpg.rulebooks.risu_module import build_module
from saga_trpg.rulebooks.risum import RPackError, RPackMap, extract_rpack_map, write_risum
from saga_trpg.rulebooks.scenarios import (
    ScenarioError,
    build_scenario_module,
    load_scenario,
    scenario_problems,
)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="saga_trpg.rulebooks", description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    check = commands.add_parser("check", help="validate a rulebook and list its actions")
    check.add_argument("path")
    module = commands.add_parser("module", help="write a RisuAI module JSON carrying the rulebook")
    module.add_argument("path")
    module.add_argument("--out", help="output file (default: <rulebook>.module.json)")
    rpack = commands.add_parser("rpack-map", help="extract the host's RPack table from its bundle")
    rpack.add_argument("source", help="the host's dist/assets directory or one of its .js files")
    rpack.add_argument("--out", default="rpack_map.bin")
    risum = commands.add_parser("risum", help="write a legacy binary .risum module")
    risum.add_argument("path")
    risum.add_argument("--rpack-map", required=False, help="512-byte table from `rpack-map`")
    risum.add_argument("--out", help="output file (default: <rulebook>.risum)")
    charx = commands.add_parser("charx", help="write a character card as .charx")
    charx.add_argument("card", help="a chara_card_v2/v3 JSON file")
    charx.add_argument("--rulebook", help="bundle this rulebook as the card's module")
    charx.add_argument("--rpack-map", required=False)
    charx.add_argument("--icon", help="PNG to use as the main icon (default: placeholder)")
    charx.add_argument("--out", help="output file (default: <card>.charx)")
    check_sc = commands.add_parser(
        "check-scenario", help="validate a scenario JSON (optionally against a rulebook)"
    )
    check_sc.add_argument("path")
    check_sc.add_argument("--rulebook", required=False, help="rulebook JSON to cross-check")
    sc_mod = commands.add_parser(
        "scenario-module", help="write a RisuAI module JSON carrying a scenario"
    )
    sc_mod.add_argument("path")
    sc_mod.add_argument("--out", help="output file (default: <scenario>.module.json)")
    args = parser.parse_args(argv)
    if args.command == "check-scenario":
        try:
            scenario = load_scenario(args.path)
            rulebook = load_rulebook(args.rulebook) if args.rulebook else None
            problems = scenario_problems(scenario, rulebook)
            if problems:
                raise ScenarioError("; ".join(problems))
        except (ScenarioError, RulebookError) as exc:
            print("scenario problems:", file=sys.stderr)
            for problem in str(exc).split("; "):
                print(f"  - {problem}", file=sys.stderr)
            return 1
        print(f"{scenario.id}@{scenario.version}: {scenario.title}")
        print(f"  actors: {', '.join(scenario.actors)}")
        if scenario.map is not None:
            kind = "generated" if scenario.map.generate else f"{len(scenario.map.rooms)} rooms"
            print(f"  map: {kind}")
        return 0
    if args.command == "scenario-module":
        try:
            text = Path(args.path).read_text(encoding="utf-8")
            scenario = load_scenario(args.path)
            module_data = build_scenario_module(text)
        except (OSError, ScenarioError) as exc:
            print(str(exc), file=sys.stderr)
            return 1
        out = Path(args.out) if args.out else Path(args.path).with_suffix(".module.json")
        out.write_text(
            json.dumps(module_data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        print(f"{scenario.id}@{scenario.version} -> {out}")
        return 0
    if args.command == "rpack-map":
        try:
            table = extract_rpack_map(args.source)
        except (OSError, RPackError) as exc:
            print(str(exc), file=sys.stderr)
            return 1
        Path(args.out).write_bytes(table)
        print(f"wrote {args.out} ({len(table)} bytes)")
        return 0
    if args.command == "charx":
        return _charx(args)
    try:
        spec = load_rulebook(args.path)
    except RulebookError as exc:
        print("rulebook problems:", file=sys.stderr)
        for problem in str(exc).split("; "):
            print(f"  - {problem}", file=sys.stderr)
        return 1
    if args.command == "risum":
        table = _rpack(args.rpack_map)
        if table is None:
            return 1
        text = Path(args.path).read_text(encoding="utf-8")
        out = Path(args.out) if args.out else Path(args.path).with_suffix(".risum")
        out.write_bytes(write_risum(build_module(text), table))
        print(f"{spec.id}@{spec.version} -> {out}")
        return 0
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


def _rpack(path: str | None) -> RPackMap | None:
    if not path:
        print(
            "an RPack table is required: run `python -m saga_trpg.rulebooks rpack-map "
            "<host dist/assets>` once and pass --rpack-map rpack_map.bin",
            file=sys.stderr,
        )
        return None
    try:
        return RPackMap.load(path)
    except (OSError, RPackError) as exc:
        print(f"rpack table: {exc}", file=sys.stderr)
        return None


def _charx(args: argparse.Namespace) -> int:
    try:
        card = build_card_v3(json.loads(Path(args.card).read_text(encoding="utf-8")))
    except (OSError, json.JSONDecodeError) as exc:
        print(f"card: {exc}", file=sys.stderr)
        return 1
    module = None
    table = None
    if args.rulebook:
        try:
            load_rulebook(args.rulebook)
        except RulebookError as exc:
            print(f"rulebook problems: {exc}", file=sys.stderr)
            return 1
        table = _rpack(args.rpack_map)
        if table is None:
            return 1
        module = build_module(Path(args.rulebook).read_text(encoding="utf-8"))
    icon = Path(args.icon).read_bytes() if args.icon else None
    out = Path(args.out) if args.out else Path(args.card).with_suffix(".charx")
    out.write_bytes(write_charx(card, module=module, rpack=table, icon=icon))
    print(f"{card['data']['name']} -> {out}" + (" (with rulebook module)" if module else ""))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
