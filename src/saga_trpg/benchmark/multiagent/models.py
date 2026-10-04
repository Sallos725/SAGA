"""Research-only run/driver contracts. Run artifacts are judge-only, never player resources."""

from typing import Literal, Protocol

from pydantic import Field

from saga_trpg.domain.models import RollRecord, WorldState
from saga_trpg.experimental.multiplayer.models import (
    ActionProposal,
    ActionReceipt,
    ActorView,
    CampaignSession,
    EventPage,
    FrozenModel,
    MemoryRetrieval,
    MessageDraft,
    PartyMessage,
    ProposedAction,
)
from saga_trpg.runtime.journal import JournalEntry


class HiddenChecks(FrozenModel):
    clue_owners: dict[str, str] = Field(default_factory=dict)
    hidden_actor_ids: tuple[str, ...]
    optimal_commits: int


class Scenario(FrozenModel):
    schema_version: Literal[1] = 1
    scenario_id: str
    version: Literal["1"] = "1"
    public_setup: str
    initial_state: WorldState
    session: CampaignSession
    ruleset_id: Literal["experiment.sealed-ruin"] = "experiment.sealed-ruin"
    ruleset_version: Literal["1"] = "1"
    stochastic_policy: Literal["fixed-effort-2-v1"] = "fixed-effort-2-v1"
    seed: str = "multiplayer-spike-v1"
    max_decisions: int = Field(default=12, ge=1, le=100)
    hidden_checks: HiddenChecks


class DriverConfig(FrozenModel):
    provider: str = "scripted"
    model: str = "scripted-v1"
    strategy: str
    prompt_version: str = "observation-v1"


class Observation(FrozenModel):
    public_setup: str
    view: ActorView
    events: EventPage
    messages: tuple[PartyMessage, ...]
    previous_receipt: ActionReceipt | None = None


class AgentDecision(FrozenModel):
    proposed_action: ProposedAction
    message_to_party: MessageDraft | None = None
    reason_code: Literal["share", "unlock", "brace", "force", "wait", "invalid_probe"] = "wait"
    input_tokens: int | None = Field(default=None, ge=0)
    output_tokens: int | None = Field(default=None, ge=0)


class AgentDriver(Protocol):
    config: DriverConfig

    def decide(self, observation: Observation) -> AgentDecision: ...


class DecisionRecord(FrozenModel):
    decision_index: int
    participant_id: str
    observation: Observation
    decision: AgentDecision
    proposal: ActionProposal
    receipt: ActionReceipt


class ToolRecord(FrozenModel):
    event_id: str
    actor_id: str
    tool_name: Literal["dice"] = "dice"
    policy: Literal["fixed-effort-2-v1"] = "fixed-effort-2-v1"
    result: RollRecord


class ValidatorOutput(FrozenModel):
    objective_completed: bool
    legal_success: bool
    forbidden_knowledge_uses: int
    ungrounded_disclosures: int
    hidden_actor_leaks: int
    invalid_resource_attempts: int
    unsupported_mechanic_attempts: int
    turn_violations: int
    useful_clues_shared: int
    omitted_clues: int
    redundant_claims: int
    recovered_after_rejection: bool
    optimal_path: bool
    cheese_class: Literal["C0", "C1", "C2", "C3", "C4", "C5", "INVALID"]


class RunArtifact(FrozenModel):
    artifact_version: Literal[1] = 1
    audience: Literal["judge_only"] = "judge_only"
    scenario: Scenario
    runtime_version: str
    effective_ruleset_id: str
    effective_ruleset_version: str
    source_sha256: dict[str, str]
    participant_models: dict[str, DriverConfig]
    decisions: tuple[DecisionRecord, ...]
    messages: tuple[PartyMessage, ...]
    journal: tuple[JournalEntry, ...]
    tool_calls: tuple[ToolRecord, ...]
    memory_retrievals: tuple[MemoryRetrieval, ...]
    validator: ValidatorOutput
    final_state: WorldState
    final_state_hash: str
    metrics: dict[str, int | float | bool | None]
