"""Run a RuleCheeseBench scenario and save the run artifact.

    python -m saga_trpg.benchmark run benchmarks/scenarios/demo_cheese.json \
        --script commands.txt --model scripted --out runs/demo.json
    python -m saga_trpg.benchmark run benchmarks/scenarios/demo_cheese.json \
        --driver vertex --vertex-json path/to/service-account.json --out runs/gemini.json

    python -m saga_trpg.benchmark report runs/*.json --out runs/report.md

The Vertex driver makes one billed model call per decision; nothing is cached.
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

from pydantic import SecretStr

from saga_trpg.benchmark.models import load_scenario
from saga_trpg.benchmark.report import IncomparableRuns, load_runs, render_report
from saga_trpg.benchmark.rulecheese import RuleCheeseRunner, ScriptedDriver


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="saga_trpg.benchmark", description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    run = commands.add_parser("run", help="play a scenario with a scripted driver")
    run.add_argument("scenario")
    run.add_argument("--driver", choices=["scripted", "vertex"], default="scripted")
    run.add_argument("--script", help="scripted driver: one command per line")
    run.add_argument("--vertex-json", help="vertex driver: service-account JSON file")
    run.add_argument("--model", default=None, help="label in the artifact")
    run.add_argument("--out", help="write the run artifact JSON here")
    report = commands.add_parser("report", help="compare run artifacts of one scenario")
    report.add_argument("artifacts", nargs="+")
    report.add_argument("--out", help="write the Markdown report here")
    args = parser.parse_args(argv)
    if args.command == "report":
        try:
            text = render_report(load_runs(args.artifacts))
        except (OSError, ValueError, IncomparableRuns) as exc:
            print(f"cannot compare: {exc}", file=sys.stderr)
            return 1
        print(text, end="")
        if args.out:
            try:
                Path(args.out).parent.mkdir(parents=True, exist_ok=True)
                Path(args.out).write_text(text, encoding="utf-8")
            except OSError as exc:
                print(f"cannot write {args.out}: {exc}", file=sys.stderr)
                return 1
        return 0
    try:
        scenario = load_scenario(args.scenario)
        if args.driver == "scripted":
            if not args.script:
                raise ValueError("--script is required for the scripted driver")
            lines = Path(args.script).read_text(encoding="utf-8").splitlines()
            driver = ScriptedDriver([line.strip() for line in lines if line.strip()])
            model = args.model or "scripted"
        else:
            if not args.vertex_json:
                raise ValueError("--vertex-json is required for the vertex driver")
            from saga_trpg.benchmark.drivers import VertexDriver

            driver = VertexDriver(SecretStr(Path(args.vertex_json).read_text(encoding="utf-8")))
            model = args.model or driver.name
    except (OSError, ValueError) as exc:
        print(f"cannot start: {exc}", file=sys.stderr)
        return 1
    try:
        result = asyncio.run(RuleCheeseRunner().run(scenario, driver=driver, model=model))
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
        try:
            Path(args.out).parent.mkdir(parents=True, exist_ok=True)
            Path(args.out).write_text(result.model_dump_json(indent=2) + "\n", encoding="utf-8")
        except OSError as exc:
            print(f"cannot write {args.out}: {exc}", file=sys.stderr)
            return 1
        print(f"  artifact: {args.out}")
    return 0 if result.outcome != "failure" else 2


if __name__ == "__main__":
    raise SystemExit(main())
