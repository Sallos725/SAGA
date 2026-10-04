"""Allowlisted, detached audience data; never serialize an authoritative result to a player."""

from saga_trpg.domain.models import WorldState
from saga_trpg.experimental.multiplayer.models import (
    ActionProposal,
    ActorView,
    CampaignSession,
    EventPage,
    Participant,
    VisibleActor,
    VisibleEvent,
)
from saga_trpg.experimental.multiplayer.session import turn_at
from saga_trpg.runtime.journal import JournalEntry


def project_state(
    state: WorldState, session: CampaignSession, participant: Participant
) -> ActorView:
    actors = tuple(
        VisibleActor(actor_id=key, hp=actor.hp, max_hp=actor.max_hp)
        for key, actor in sorted(state.actors.items())
        if key in participant.visible_actor_ids and key == actor.actor_id
    )
    visible_ids = tuple(actor.actor_id for actor in actors)
    own = state.actors.get(participant.actor_id or "")
    if own is not None and own.actor_id != participant.actor_id:
        own = None
    turn = turn_at(session, state.seq)
    turn = turn.model_copy(
        update={
            "active_actor_id": turn.active_actor_id
            if turn.active_actor_id in visible_ids
            else None,
            "turn_order": tuple(actor for actor in turn.turn_order if actor in visible_ids),
        }
    )
    return ActorView(
        world_id=state.world_id,
        branch_id=session.branch_id,
        actor_id=participant.actor_id,
        seq=state.seq,
        turn=turn,
        actors=actors,
        own_inventory={
            key: value
            for key, value in own.inventory.items()
            if key in participant.own_inventory_keys
        }
        if own
        else {},
        own_stats={
            key: value for key, value in own.stats.items() if key in participant.own_stat_keys
        }
        if own
        else {},
        public_flags={
            key: value
            for key, value in state.flags.items()
            if key in session.public_flag_keys and type(value) in (int, bool)
        },
        available_actions=(
            tuple(action.model_copy(deep=True) for action in session.actions)
            if "propose" in participant.permissions
            else ()
        ),
        approved_speaker_ids=visible_ids,
    )


def project_events(
    entries: list[JournalEntry], participant: Participant, after_seq: int
) -> EventPage:
    events = []
    for entry in entries:
        event = entry.committed.event
        if entry.committed.seq <= after_seq or event.actor_id not in participant.visible_actor_ids:
            continue
        if event.kind != "experiment.multiplayer.action":
            continue
        proposal = ActionProposal.model_validate(event.payload)
        events.append(
            VisibleEvent(
                seq=entry.committed.seq,
                event_id=event.event_id or "",
                actor_id=event.actor_id,
                kind=proposal.action.kind,
            )
        )
    return EventPage(next_seq=entries[-1].committed.seq if entries else 0, events=tuple(events))
