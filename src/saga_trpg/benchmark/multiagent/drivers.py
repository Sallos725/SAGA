"""Deterministic observation-only fake participants; no provider calls or hidden reasoning."""

from saga_trpg.benchmark.multiagent.models import AgentDecision, DriverConfig, Observation
from saga_trpg.experimental.multiplayer.models import ClueClaim, MessageDraft, ProposedAction


class ScriptedDriver:
    def __init__(self, strategy: str = "cooperative") -> None:
        if strategy not in ("cooperative", "brute_force", "silent", "bad_teammate"):
            raise ValueError("unknown scripted strategy")
        self.config = DriverConfig(strategy=strategy)
        self._decisions = 0

    def decide(self, observation: Observation) -> AgentDecision:
        self._decisions += 1
        view = observation.view
        if self.config.strategy == "bad_teammate" and self._decisions == 1:
            return AgentDecision(
                proposed_action=ProposedAction(
                    kind="unlock",
                    payload={
                        "item": "imaginary_key",
                        "inscription": "guess",
                        "warning": "guess",
                    },
                ),
                message_to_party=MessageDraft(text="I will try an imaginary key."),
                reason_code="invalid_probe",
            )
        known = {key: value for key, value in view.own_stats.items() if isinstance(value, str)}
        for message in observation.messages:
            for claim in message.claims:
                known[claim.key] = claim.value
        message = None
        if self.config.strategy != "silent" and view.own_stats:
            claims = tuple(
                ClueClaim(key=key, value=value)
                for key, value in view.own_stats.items()
                if isinstance(value, str)
            )
            correction = any("imaginary key" in item.text for item in observation.messages)
            text = (
                "Use only an owned silver key. Sharing my actual clue."
                if correction
                else "Sharing my inscription or trap warning."
            )
            message = MessageDraft(text=text, claims=claims)
        if view.own_inventory.get("silver_key", 0) and {"inscription", "warning"} <= known.keys():
            return AgentDecision(
                proposed_action=ProposedAction(
                    kind="unlock",
                    payload={
                        "item": "silver_key",
                        "inscription": known["inscription"],
                        "warning": known["warning"],
                    },
                ),
                reason_code="unlock",
            )
        if view.own_inventory.get("lever", 0) and not view.public_flags["braced"]:
            if self.config.strategy != "brute_force":
                return AgentDecision(
                    proposed_action=ProposedAction(kind="brace"),
                    reason_code="brace",
                    message_to_party=MessageDraft(text="I brace the gate; use the hammer next."),
                )
        if view.own_inventory.get("hammer", 0):
            if view.public_flags["braced"] or self.config.strategy == "brute_force":
                return AgentDecision(
                    proposed_action=ProposedAction(kind="force"), reason_code="force"
                )
        return AgentDecision(
            proposed_action=ProposedAction(kind="wait"),
            message_to_party=message,
            reason_code="share" if message else "wait",
        )
