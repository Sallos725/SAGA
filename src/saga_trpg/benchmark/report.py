"""Compare run artifacts of one scenario and render a Markdown report.

Runs are comparable only when scenario id and version, ruleset id and version, runtime
version and the stochastic policy match (invariant 7: benchmark runs are reproducible, so
what differs between rows is the driver, nothing else). Anything else is refused.
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

from saga_trpg.benchmark.models import BenchmarkRun

_COMPARABLE = ("scenario_id", "scenario_version", "ruleset", "ruleset_version", "runtime_version")


class IncomparableRuns(ValueError):
    """The artifacts do not share the conditions that make their results comparable."""


@dataclass(frozen=True)
class Row:
    model: str
    outcome: str
    turns: int
    attempts: int
    illegal_attempts: int
    valid_action_rate: float
    rolls: int
    validators: dict[str, bool]
    driver: str
    final_state_hash: str


def load_runs(paths: Sequence[Path | str]) -> list[BenchmarkRun]:
    return [BenchmarkRun.model_validate_json(Path(p).read_text(encoding="utf-8")) for p in paths]


def compare_runs(runs: Sequence[BenchmarkRun]) -> list[Row]:
    if not runs:
        raise IncomparableRuns("no runs")
    first = runs[0]
    for run in runs[1:]:
        for field in _COMPARABLE:
            if getattr(run, field) != getattr(first, field):
                raise IncomparableRuns(
                    f"{field} differs: {getattr(first, field)!r} vs {getattr(run, field)!r}"
                )
        if run.stochastic != first.stochastic:
            raise IncomparableRuns("stochastic policy differs between runs")
    return [
        Row(
            model=run.model,
            outcome=run.outcome,
            turns=int(run.metrics.get("turns", 0)),
            attempts=int(run.metrics.get("attempts", 0)),
            illegal_attempts=int(run.metrics.get("illegal_attempts", 0)),
            valid_action_rate=float(run.metrics.get("valid_action_rate", 0.0)),
            rolls=int(run.metrics.get("rolls", 0)),
            validators=dict(run.validators),
            driver=str(run.driver.get("name", "")),
            final_state_hash=run.final_state_hash,
        )
        for run in runs
    ]


def render_report(runs: Sequence[BenchmarkRun]) -> str:
    rows = compare_runs(runs)
    first = runs[0]
    names = list(first.validators)
    lines = [
        f"# RuleCheeseBench · {first.scenario_id} v{first.scenario_version}",
        "",
        f"- ruleset: `{first.ruleset}@{first.ruleset_version}` · runtime `{first.runtime_version}`",
        f"- stochastic: `{json.dumps(first.stochastic.model_dump(), ensure_ascii=False)}`",
        f"- runs: {len(rows)}",
        "",
        "| model | outcome | turns | attempts | illegal | valid rate | rolls | "
        + " | ".join(names)
        + " | driver | final state |",
        "| --- | --- | ---: | ---: | ---: | ---: | ---: | "
        + " | ".join("---" for _ in names)
        + " | --- | --- |",
    ]
    for row in rows:
        verdicts = " | ".join("ok" if row.validators.get(n) else "--" for n in names)
        lines.append(
            f"| {row.model} | {row.outcome} | {row.turns} | {row.attempts} | "
            f"{row.illegal_attempts} | {row.valid_action_rate:.2f} | {row.rolls} | "
            f"{verdicts} | {row.driver} | `{row.final_state_hash[:12]}` |"
        )
    lines.append("")
    lines.append(
        "Rows share scenario, ruleset, runtime and dice policy; only the driver differs. "
        "Validators are hidden from drivers. `partial` means no failure fired and the success "
        "criteria were not all met."
    )
    return "\n".join(lines) + "\n"
