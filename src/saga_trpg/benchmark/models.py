from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class BenchmarkScenario(BaseModel):
    scenario_id: str
    version: str = "1"
    description: str
    initial_world: dict[str, Any]
    allowed_actions: list[str] = Field(default_factory=list)
    hidden_checks: list[dict[str, Any]] = Field(default_factory=list)
    fixed_rolls: dict[str, Any] | None = None
    max_turns: int = 50


class BenchmarkRun(BaseModel):
    scenario_id: str
    model: str
    runtime_version: str
    ruleset: str
    ruleset_version: str
    seed: str
    event_ids: list[str] = Field(default_factory=list)
    metrics: dict[str, float | int | bool] = Field(default_factory=dict)
    notes: list[str] = Field(default_factory=list)
