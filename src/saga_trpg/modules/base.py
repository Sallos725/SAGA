from __future__ import annotations

from typing import Protocol

from saga_trpg.domain.events import GameEvent
from saga_trpg.domain.models import RuleResult, WorldState
from saga_trpg.ports.dice import DicePort


class RuleModule(Protocol):
    name: str
    version: str

    def apply(self, *, state: WorldState, event: GameEvent, dice: DicePort) -> RuleResult: ...
