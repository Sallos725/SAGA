"""RuleCheeseBench runner: a driver plays a scenario through the production runtime.

The runner owns only what ADR 0003 allows benchmark code to own: the scenario, the dice
policy, the driver and the validators. Rules, journaling and replay are the same objects
interactive play uses. The driver sees public information only; validators and the
scenario's success/failure lists are never part of its observation.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any, Protocol

from pydantic import BaseModel, Field

from saga_trpg.adapters.storage.sqlite import RUNTIME_VERSION
from saga_trpg.benchmark.dice import TapeDice, TapeExhausted
from saga_trpg.benchmark.models import (
    BenchmarkRun,
    BenchmarkScenario,
    SeedPolicy,
    validate_scenario,
)
from saga_trpg.benchmark.validators import evaluate_validators
from saga_trpg.domain.models import ActorState, WorldState
from saga_trpg.modules.catalog import RulesetRegistry
from saga_trpg.rulebooks.commands import CommandError, command_help, parse_command
from saga_trpg.rulebooks.display import render_display
from saga_trpg.rulebooks.engine import DataRules
from saga_trpg.runtime.engine import RuntimeEngine
from saga_trpg.runtime.hashing import stable_model_hash
from saga_trpg.runtime.journal import InMemoryJournal
from saga_trpg.runtime.rng import DeterministicDice


class Observation(BaseModel):
    """Everything the driver may know before choosing its next command."""

    scenario_id: str
    setup_text: str
    commands: str
    turn: int
    turns_left: int
    attempts_left: int
    display: dict[str, Any]
    last_command: str | None = None
    last_facts: list[str] = Field(default_factory=list)
    last_error: str | None = None


class Driver(Protocol):
    async def decide(self, observation: Observation) -> str | None:
        """Return the next command, or None to stop early."""


class ScriptedDriver:
    """Plays a fixed list of commands; used for tests and for replaying a recorded policy."""

    def __init__(self, commands: Sequence[str]) -> None:
        self._commands = list(commands)
        self._next = 0

    async def decide(self, observation: Observation) -> str | None:
        if self._next >= len(self._commands):
            return None
        command = self._commands[self._next]
        self._next += 1
        return command


def _world_from(scenario: BenchmarkScenario) -> WorldState:
    data = dict(scenario.initial_world)
    data.setdefault("world_id", f"bench-{scenario.scenario_id}")
    actors = {}
    for actor_id, actor in (data.get("actors") or {}).items():
        actors[actor_id] = ActorState.model_validate({"actor_id": actor_id, **actor})
    data["actors"] = actors
    return WorldState.model_validate(data)


class RuleCheeseRunner:
    def __init__(self, registry: RulesetRegistry | None = None) -> None:
        self.registry = registry or RulesetRegistry()

    async def run(self, scenario: BenchmarkScenario, *, driver: Driver, model: str) -> BenchmarkRun:
        validate_scenario(scenario, self.registry)
        definition = self.registry.get(scenario.ruleset_id, scenario.ruleset_version)
        rules = definition.rules
        if not isinstance(rules, DataRules):
            raise ValueError("benchmark scenarios run on data rulebooks (commands need a spec)")
        spec = rules.spec
        initial = _world_from(scenario)
        if isinstance(scenario.stochastic, SeedPolicy):
            dice: Any = DeterministicDice(scenario.stochastic.seed)
            seed: str | None = scenario.stochastic.seed
        else:
            dice, seed = TapeDice(scenario.stochastic.rolls), None
        engine = RuntimeEngine(
            initial_state=initial, rules=rules, dice=dice, journal=InMemoryJournal()
        )
        commands = command_help(spec)
        illegal = 0
        notes: list[str] = []
        last_command: str | None = None
        last_facts: list[str] = []
        last_error: str | None = None
        turn = 0
        attempts = 0
        budget = scenario.attempt_budget
        while turn < scenario.max_turns and attempts < budget:
            observation = Observation(
                scenario_id=scenario.scenario_id,
                setup_text=scenario.setup_text,
                commands=commands,
                turn=turn + 1,
                turns_left=scenario.max_turns - turn,
                attempts_left=budget - attempts,
                display=render_display(spec, engine.state),
                last_command=last_command,
                last_facts=list(last_facts),
                last_error=last_error,
            )
            command = await driver.decide(observation)
            if command is None:
                break
            attempts += 1
            last_command, last_error = command, None
            try:
                event = parse_command(spec, actor_id="player", text=command)
                outcome = engine.dispatch(event.model_copy(update={"event_id": f"turn-{turn + 1}"}))
            except TapeExhausted:
                # The scenario's tape cannot serve this roll: the run is not comparable.
                raise
            except (CommandError, ValueError) as exc:
                # A rejected action never reaches the journal; the driver learns why.
                illegal += 1
                last_error = str(exc).split("\n")[0]
                last_facts = []
                notes.append(f"attempt {attempts} rejected: {command!r}: {last_error}")
                continue
            turn += 1
            last_facts = list(outcome.entry.result.facts)
        entries = engine.journal.entries()
        verdicts = evaluate_validators(
            scenario.validators, state=engine.state, entries=entries, illegal_attempts=illegal
        )
        failed = any(not verdicts[name] for name in scenario.failure)
        if not scenario.success:
            notes.append("no success criteria declared; outcome is unscored (partial)")
        succeeded = bool(scenario.success) and all(verdicts[name] for name in scenario.success)
        if failed:
            outcome_label = "failure"
        elif succeeded:
            outcome_label = "success"
        else:
            outcome_label = "partial"
        return BenchmarkRun(
            scenario_id=scenario.scenario_id,
            scenario_version=scenario.version,
            model=model,
            runtime_version=RUNTIME_VERSION,
            ruleset=scenario.ruleset_id,
            ruleset_version=scenario.ruleset_version,
            stochastic=scenario.stochastic,
            seed=seed,
            initial_world=initial.model_dump(mode="json"),
            journal=[entry.model_dump(mode="json") for entry in entries],
            event_ids=[entry.committed.event.event_id for entry in entries],
            final_state=engine.state.model_dump(mode="json"),
            final_state_hash=stable_model_hash(engine.state),
            metrics={
                "turns": turn,
                "attempts": attempts,
                "illegal_attempts": illegal,
                "valid_action_rate": (turn / attempts) if attempts else 0.0,
                "rolls": sum(len(entry.result.rolls) for entry in entries),
            },
            validators=verdicts,
            outcome=outcome_label,
            driver=driver.info() if hasattr(driver, "info") else {"name": type(driver).__name__},
            notes=notes,
        )
