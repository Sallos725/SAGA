"""Pure validation helpers shared by the example rulesets."""

from pydantic import BaseModel, ConfigDict

from nmos_trpg.domain.models import ActorState, WorldState


class ClosedPayload(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


def actor_in(state: WorldState, actor_id: str) -> ActorState:
    actor = state.actors.get(actor_id)
    if actor is None or actor.actor_id != actor_id:
        raise ValueError(f"unknown or inconsistent actor: {actor_id}")
    return actor


def integer_stat(actor: ActorState, key: str, *, minimum: int, maximum: int) -> int:
    value = actor.stats.get(key)
    if type(value) is not int or not minimum <= value <= maximum:
        raise ValueError(f"{actor.actor_id}.{key} must be an integer in {minimum}..{maximum}")
    return value


def boolean_stat(actor: ActorState, key: str, *, default: bool = False) -> bool:
    value = actor.stats.get(key, default)
    if type(value) is not bool:
        raise ValueError(f"{actor.actor_id}.{key} must be a boolean")
    return value
