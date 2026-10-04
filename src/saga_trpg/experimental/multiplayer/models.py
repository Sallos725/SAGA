"""Experimental v1 contracts; deliberately outside stable domain and transport APIs."""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, JsonValue, model_validator


class FrozenModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)


class Participant(FrozenModel):
    participant_id: str = Field(min_length=1)
    actor_id: str | None = None
    participant_type: Literal["human", "ai_agent", "gm", "spectator"]
    permissions: tuple[Literal["read", "propose", "message"], ...] = ("read",)
    visible_actor_ids: tuple[str, ...] = ()
    own_stat_keys: tuple[str, ...] = ()
    own_inventory_keys: tuple[str, ...] = ()


class ActionSpec(FrozenModel):
    kind: str = Field(min_length=1)
    description: str
    payload_schema: dict[str, JsonValue] = Field(default_factory=dict)
    actor_reference_fields: tuple[str, ...] = ()


class CampaignSession(FrozenModel):
    schema_version: Literal[1] = 1
    world_id: str = Field(min_length=1)
    branch_id: str = "main"
    participants: tuple[Participant, ...]
    mode: Literal["serial", "turn_based"] = "turn_based"
    turn_order: tuple[str, ...] = Field(min_length=1)
    actions: tuple[ActionSpec, ...]
    public_flag_keys: tuple[str, ...] = ()
    visibility_policy_version: Literal["allowlist-v1"] = "allowlist-v1"
    turn_policy_version: Literal["one-commit-one-turn-v1"] = "one-commit-one-turn-v1"

    @model_validator(mode="after")
    def validate_roster(self):
        ids = [p.participant_id for p in self.participants]
        players = [p for p in self.participants if p.participant_type in ("human", "ai_agent")]
        actors = [p.actor_id for p in players]
        if len(ids) != len(set(ids)) or None in actors or len(actors) != len(set(actors)):
            raise ValueError("participant and controlled actor identities must be unique")
        if len(self.turn_order) != len(set(self.turn_order)) or set(self.turn_order) != set(actors):
            raise ValueError("turn order must contain each controlled actor exactly once")
        for participant in self.participants:
            if participant in players:
                if participant.actor_id not in participant.visible_actor_ids:
                    raise ValueError("a controlled actor must be visible to its owner")
                if "propose" not in participant.permissions:
                    raise ValueError("turn participants need proposal permission")
            elif participant.actor_id is not None or "propose" in participant.permissions:
                raise ValueError("GM and spectator use separate read-only capabilities")
        if len({action.kind for action in self.actions}) != len(self.actions):
            raise ValueError("action kinds must be unique")
        return self


class TurnState(FrozenModel):
    mode: Literal["serial", "turn_based"]
    round: int
    phase: Literal["action"] = "action"
    active_actor_id: str | None
    turn_order: tuple[str, ...]
    turn_revision: int


class ProposedAction(FrozenModel):
    kind: str = Field(min_length=1)
    payload: dict[str, JsonValue] = Field(default_factory=dict)


class ActionProposal(FrozenModel):
    schema_version: Literal[1] = 1
    participant_id: str = Field(min_length=1)
    actor_id: str = Field(min_length=1)
    expected_parent_seq: int = Field(ge=0)
    turn_revision: int = Field(ge=0)
    client_action_id: str = Field(min_length=1, max_length=200)
    action: ProposedAction


class ActionReceipt(FrozenModel):
    accepted: bool
    code: Literal[
        "accepted",
        "forbidden",
        "not_active",
        "stale",
        "conflict",
        "invalid_action",
        "target_unavailable",
        "pending",
        "unavailable",
    ]
    event_id: str | None = None
    seq: int | None = None
    replayed: bool = False


class VisibleActor(FrozenModel):
    actor_id: str
    hp: int
    max_hp: int


class ActorView(FrozenModel):
    schema_version: Literal[1] = 1
    world_id: str
    branch_id: str
    actor_id: str | None
    seq: int
    turn: TurnState
    actors: tuple[VisibleActor, ...]
    own_inventory: dict[str, int]
    own_stats: dict[str, int | float | str | bool]
    public_flags: dict[str, int | bool]
    available_actions: tuple[ActionSpec, ...]
    approved_speaker_ids: tuple[str, ...]


class VisibleEvent(FrozenModel):
    seq: int
    event_id: str
    actor_id: str
    kind: str


class EventPage(FrozenModel):
    next_seq: int
    events: tuple[VisibleEvent, ...]


class ClueClaim(FrozenModel):
    key: str = Field(min_length=1, max_length=80)
    value: str = Field(max_length=200)


class MessageDraft(FrozenModel):
    text: str = Field(max_length=2000)
    claims: tuple[ClueClaim, ...] = ()


class PartyMessage(FrozenModel):
    schema_version: Literal[1] = 1
    world_id: str
    branch_id: str
    sender_actor_id: str
    recipients: tuple[str, ...]
    text: str
    claims: tuple[ClueClaim, ...] = ()
    visibility: Literal["public_party"] = "public_party"
    message_seq: int
    after_seq: int


class MemoryQuery(FrozenModel):
    world_id: str
    branch_id: str
    actor_id: str
    audience_scope: Literal["actor"] = "actor"
    as_of_event: str | None
    query: str


class RecalledMemory(FrozenModel):
    memory_id: str
    event_id: str
    text: str


class MemoryRetrieval(FrozenModel):
    query: MemoryQuery
    status: Literal["disabled", "ok", "failed"]
    hits: tuple[RecalledMemory, ...] = ()
