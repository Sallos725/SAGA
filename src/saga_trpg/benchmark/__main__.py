"""Run a RuleCheeseBench scenario with a scripted policy and save the run artifact.

    python -m saga_trpg.benchmark run benchmarks/scenarios/demo_cheese.json \
        --script commands.txt --model scripted --out runs/demo.json
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

from saga_trpg.benchmark.models import load_scenario
from saga_trpg.benchmark.rulecheese import RuleCheeseRunner, ScriptedDriver


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="saga_trpg.benchmark", description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    run = commands.add_parser("run", help="play a scenario with a scripted driver")
    run.add_argument("scenario")
    run.add_argument("--script", required=True, help="one command per line; blank lines ignored")
    run.add_argument("--model", default="scripted")
    run.add_argument("--out", help="write the run artifact JSON here")
    args = parser.parse_args(argv)
    try:
        scenario = load_scenario(args.scenario)
        lines = Path(args.script).read_text(encoding="utf-8").splitlines()
    except (OSError, ValueError) as exc:
        print(f"cannot start: {exc}", file=sys.stderr)
        return 1
    driver = ScriptedDriver([line.strip() for line in lines if line.strip()])
    try:
        result = asyncio.run(RuleCheeseRunner().run(scenario, driver=driver, model=args.model))
    except ValueError as exc:
        print(f"run aborted: {exc}", file=sys.stderr)
        return 1
    print(f"{result.scenario_id} · {result.model} · {result.outcome}")
    for key, value in result.metrics.items():
        print(f"  {key}: {value}")
    for name, verdict in result.validators.items():
        print(f"  [{'ok' if verdict else '--'}] {name}")
    for note in result.notes:
        print(f"  note: {note}")
    if args.out:
        Path(args.out).parent.mkdir(parents=True, exist_ok=True)
        Path(args.out).write_text(result.model_dump_json(indent=2) + "\n", encoding="utf-8")
        print(f"  artifact: {args.out}")
    return 0 if result.outcome != "failure" else 2


if __name__ == "__main__":
    raise SystemExit(main())
