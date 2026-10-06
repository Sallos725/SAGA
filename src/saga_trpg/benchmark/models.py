"""Benchmark scenario and run artifacts (schema v2).

A scenario pins a registered ruleset, an initial world, the public setup text, the stochastic
policy and declarative hidden validators. A run records everything needed to reproduce and
inspect it: the journal, the final state and its hash, metrics and validator verdicts.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class Closed(BaseModel):
    model_config = ConfigDict(extra="forbid")


class SeedPolicy(Closed):
    mode: Literal["seed"]
    seed: str = Field(min_length=1)


class TapePolicy(Closed):
    mode: Literal["tape"]
    rolls: dict[str, list[list[int]]]


class EventMatch(Closed):
    kind: str | None = None
    actor_id: str | None = None
    payload: dict[str, Any] = Field(default_factory=dict)


class ActorValidator(Closed):
    id: str
    kind: Literal["actor"]
    actor: str
    field: Literal["hp", "max_hp"] | str
    op: Literal["==", "!=", "<", "<=", ">", ">="]
    value: int | str | bool


class StateValidator(Closed):
    id: str
    kind: Literal["state"]
    path: str  # dotted path into flags / quest_state / simulated_time
    equals: Any


class EventCountValidator(Closed):
    id: str
    kind: Literal["event_count"]
    match: EventMatch
    min: int = 0
    max: int | None = None


class EventOrderValidator(Closed):
    id: str
    kind: Literal["event_order"]
    before: EventMatch
    after: EventMatch


class IllegalAttemptsValidator(Closed):
    id: str
    kind: Literal["illegal_attempts"]
    max: int = 0


class RecordValidator(Closed):
    id: str
    kind: Literal["record"]
    collection: str
    field: str
    equals: Any


Validator = (
    ActorValidator
    | StateValidator
    | EventCountValidator
    | EventOrderValidator
    | IllegalAttemptsValidator
    | RecordValidator
)


class BenchmarkScenario(Closed):
    scenario_id: str = Field(pattern=r"^[a-z0-9][a-z0-9.-]{0,60}$")
    version: str = "2"
    description: str
    ruleset_id: str
    ruleset_version: str
    setup_text: str = ""
    initial_world: dict[str, Any]
    stochastic: SeedPolicy | TapePolicy = Field(
        default_factory=lambda: SeedPolicy(mode="seed", seed="bench")
    )
    max_turns: int = Field(default=30, ge=1, le=1000)
    max_attempts: int | None = Field(default=None, ge=1, le=10_000)
    validators: list[Validator] = Field(default_factory=list)
    success: list[str] = Field(default_factory=list)
    failure: list[str] = Field(default_factory=list)

    @property
    def attempt_budget(self) -> int:
        """Decisions a driver may make, legal or not; default twice the turn budget."""
        return self.max_attempts or self.max_turns * 2

    @model_validator(mode="after")
    def _consistent(self) -> BenchmarkScenario:
        ids = [item.id for item in self.validators]
        if len(ids) != len(set(ids)):
            raise ValueError("validator ids must be unique")
        for name, listed in (("success", self.success), ("failure", self.failure)):
            unknown = sorted(set(listed) - set(ids))
            if unknown:
                raise ValueError(f"{name} refers to unknown validator(s): {', '.join(unknown)}")
        return self


def validate_scenario(scenario: BenchmarkScenario, registry: Any) -> BenchmarkScenario:
    """Check the pinned ruleset against the registry the run will use (imports included)."""
    try:
        registry.get(scenario.ruleset_id, scenario.ruleset_version)
    except ValueError as exc:
        raise ValueError(str(exc)) from None
    return scenario


def load_scenario(path: Path | str, registry: Any | None = None) -> BenchmarkScenario:
    scenario = BenchmarkScenario.model_validate(json.loads(Path(path).read_text(encoding="utf-8")))
    if registry is None:
        from saga_trpg.modules.catalog import RulesetRegistry

        registry = RulesetRegistry()
    return validate_scenario(scenario, registry)


class BenchmarkRun(BaseModel):
    """The reproducibility bundle for one run; everything a reviewer needs to replay it."""

    schema_version: Literal[2] = 2
    scenario_id: str
    scenario_version: str
    model: str
    runtime_version: str
    ruleset: str
    ruleset_version: str
    stochastic: SeedPolicy | TapePolicy
    seed: str | None = None
    initial_world: dict[str, Any]
    journal: list[dict[str, Any]] = Field(default_factory=list)
    event_ids: list[str] = Field(default_factory=list)
    final_state: dict[str, Any]
    final_state_hash: str
    metrics: dict[str, float | int | bool] = Field(default_factory=dict)
    validators: dict[str, bool] = Field(default_factory=dict)
    outcome: Literal["success", "partial", "failure"]
    driver: dict[str, Any] = Field(default_factory=dict)
    notes: list[str] = Field(default_factory=list)
