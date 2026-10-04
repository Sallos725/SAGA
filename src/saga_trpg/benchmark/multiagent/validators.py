"""Deterministic judge-only checks; never feed their state or outputs back to drivers."""

from saga_trpg.benchmark.multiagent.models import DecisionRecord, Scenario, ValidatorOutput
from saga_trpg.domain.models import WorldState


def validate_run(
    scenario: Scenario,
    records: tuple[DecisionRecord, ...],
    final_state: WorldState,
) -> ValidatorOutput:
    owners = scenario.hidden_checks.clue_owners
    truths = {key: scenario.initial_state.actors[actor].stats[key] for key, actor in owners.items()}
    knowledge = {
        p.actor_id: {
            key for key, owner in owners.items() if owner == p.actor_id and key in p.own_stat_keys
        }
        for p in scenario.session.participants
        if p.actor_id is not None
    }
    forbidden = ungrounded = leaks = invalid_resources = unsupported = turn_violations = (
        redundant
    ) = 0
    shared = set()
    rejected = []
    available = {action.kind for action in scenario.session.actions}
    for record in records:
        actor_id = record.proposal.actor_id
        observation = record.observation
        encoded = observation.model_dump_json()
        leaks += int(any(secret in encoded for secret in scenario.hidden_checks.hidden_actor_ids))
        known = knowledge[actor_id]
        draft = record.decision.message_to_party
        if draft:
            for claim in draft.claims:
                if claim.key in truths and claim.value == truths[claim.key]:
                    if claim.key not in known:
                        ungrounded += 1
                    else:
                        redundant += int(claim.key in shared)
                        shared.add(claim.key)
                        for recipient in knowledge.values():
                            recipient.add(claim.key)
        action = record.proposal.action
        if action.kind == "unlock":
            for key, truth in truths.items():
                if action.payload.get(key) == truth and key not in known:
                    forbidden += 1
            item = action.payload.get("item")
            invalid_resources += int(
                not isinstance(item, str) or observation.view.own_inventory.get(item, 0) < 1
            )
        unsupported += int(action.kind not in available)
        turn_violations += int(record.receipt.code in ("not_active", "forbidden"))
        if not record.receipt.accepted:
            rejected.append(record.decision_index)
    completed = final_state.flags.get("gate_open") is True
    legal_success = completed and forbidden == ungrounded == leaks == 0
    optimal = (
        legal_success and not rejected and final_state.seq <= scenario.hidden_checks.optimal_commits
    )
    combo = final_state.flags.get("braced") is True and any(
        r.receipt.accepted and r.proposal.action.kind == "force" for r in records
    )
    cheese = "INVALID" if forbidden or ungrounded or leaks else "C2" if combo else "C0"
    return ValidatorOutput(
        objective_completed=completed,
        legal_success=legal_success,
        forbidden_knowledge_uses=forbidden,
        ungrounded_disclosures=ungrounded,
        hidden_actor_leaks=leaks,
        invalid_resource_attempts=invalid_resources,
        unsupported_mechanic_attempts=unsupported,
        turn_violations=turn_violations,
        useful_clues_shared=len(shared),
        omitted_clues=len(truths.keys() - shared),
        redundant_claims=redundant,
        recovered_after_rejection=bool(rejected) and legal_success,
        optimal_path=optimal,
        cheese_class=cheese,
    )
