"""Run a local scenario and write an exclusive, judge-only JSON artifact."""

import argparse
import json
from pathlib import Path

from saga_trpg.benchmark.multiagent.drivers import ScriptedDriver
from saga_trpg.benchmark.multiagent.runner import load_scenario, run_scenario


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--scenario", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--database", type=Path, required=True)
    parser.add_argument(
        "--strategy", choices=("cooperative", "brute_force", "silent"), default="cooperative"
    )
    args = parser.parse_args()
    if args.output.exists() or args.database.exists():
        parser.error("choose fresh output and database paths; existing research data is preserved")
    scenario = load_scenario(args.scenario)
    drivers = {
        participant.participant_id: ScriptedDriver(
            "bad_teammate"
            if scenario.scenario_id == "bad-teammate" and participant.actor_id == "a"
            else args.strategy
        )
        for participant in scenario.session.participants
        if participant.actor_id is not None
    }
    artifact = run_scenario(scenario, drivers, args.database)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("x", encoding="utf-8") as handle:
        handle.write(artifact.model_dump_json(indent=2) + "\n")
    print(
        json.dumps(
            {
                "scenario": scenario.scenario_id,
                "metrics": artifact.metrics,
                "validator": artifact.validator.model_dump(),
                "final_state_hash": artifact.final_state_hash,
            },
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    main()
